#!/usr/bin/env python3
"""
服务器端成品生成脚本
从 /srv/music/cache 生成成品到 /srv/music/releases/
"""

import os
import json
import hashlib
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import logging
import sys
import threading
from io import StringIO

# 尝试导入 mutagen
try:
    from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, USLT, APIC
    from mutagen.mp3 import MP3
except ImportError:
    print("错误: mutagen 库未安装")
    exit(1)

# 尝试导入 tqdm
try:
    from tqdm import tqdm
except ImportError:
    print("错误: tqdm 库未安装，请运行 pip install tqdm", file=sys.stderr)
    exit(1)


class BottomProgressBar:
    """底部固定进度条管理器（支持日志平滑滚动）"""
    def __init__(self):
        self.progress_bar = None
        self.lock = threading.Lock()

    def set_progress(self, current, total, desc=""):
        """设置进度"""
        with self.lock:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total, desc=desc, unit="file",
                                       bar_format='{desc} {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]',
                                       file=sys.stdout, dynamic_ncols=True)
            else:
                if self.progress_bar.desc != desc:
                    self.progress_bar.set_description(desc)
                self.progress_bar.total = total
                self.progress_bar.n = current
                self.progress_bar.refresh()

    def write_log(self, message):
        """通过tqdm安全地打印日志，不破坏进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.write(message)
            else:
                print(message)

    def close(self):
        """关闭进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.close()
                self.progress_bar = None


# 创建全局进度管理器
progress_mgr = BottomProgressBar()


class TqdmLogHandler(logging.Handler):
    """将日志重定向到tqdm.write的处理器"""
    def emit(self, record):
        try:
            msg = self.format(record)
            progress_mgr.write_log(msg)
        except Exception:
            self.handleError(record)


# 配置日志
handler = TqdmLogHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)


def embed_metadata(audio_path, metadata, cover_path=None):
    """嵌入元数据到音频文件"""
    try:
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            tmp_path = Path(tmp.name)

        # 复制音频到临时文件
        shutil.copy2(audio_path, tmp_path)

        # 使用 mutagen 嵌入 metadata
        audio = MP3(tmp_path)

        # 确保有 ID3 标签
        if audio.tags is None:
            audio.add_tags()

        # 清除现有 ID3 标签
        audio.delete()

        # 添加新的标签
        # 艺术家
        artists = metadata.get('artists', [])
        if artists:
            audio.tags.add(TPE1(encoding=3, text=artists if isinstance(artists, list) else [artists]))

        # 标题
        title = metadata.get('title')
        if title:
            audio.tags.add(TIT2(encoding=3, text=title))

        # 专辑（如果没有，使用标题填充）
        album = metadata.get('album')
        if not album:
            album = title
        if album:
            audio.tags.add(TALB(encoding=3, text=album))

        # 日期
        date = metadata.get('date')
        if date:
            audio.tags.add(TDRC(encoding=3, text=date))

        # 歌词
        uslt = metadata.get('uslt')
        if uslt:
            audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt))

        # 封面
        if cover_path and cover_path.exists():
            with open(cover_path, 'rb') as f:
                cover_data = f.read()
            audio.tags.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # Cover (front)
                desc='Cover',
                data=cover_data
            ))

        # 保存
        audio.save()

        # 读取结果数据
        with open(tmp_path, 'rb') as f:
            data = f.read()

        os.unlink(tmp_path)
        return data

    except Exception as e:
        logger.error(f"嵌入元数据失败: {e}")
        raise


def get_work_filename(metadata):
    """生成文件名：艺术家 - 标题.mp3"""
    artists = metadata.get('artists', [])
    title = metadata.get('title', '未知')

    if artists:
        artist_str = ', '.join(artists)
        return f"{artist_str} - {title}.mp3"
    else:
        return f"{title}.mp3"


def sanitize_filename(filename):
    """清理文件名中的非法字符"""
    # 替换 Windows 非法字符
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    return filename


def find_object(oid, cache_root):
    """在 cache 目录中查找对象"""
    if not oid or not oid.startswith('sha256:'):
        return None

    hash_hex = oid[7:]
    subdir = hash_hex[:2]

    # 查找音频
    audio_path = cache_root / 'objects' / 'sha256' / subdir / f"{hash_hex}.mp3"
    if audio_path.exists():
        return audio_path

    # 查找封面
    cover_path = cache_root / 'covers' / 'sha256' / subdir / f"{hash_hex}.jpg"
    if cover_path.exists():
        return cover_path

    return None


def process_single_item(item, cache_root, releases_root):
    """处理单个 metadata 条目"""
    try:
        audio_oid = item.get('audio_oid')
        if not audio_oid:
            return False

        # 查找音频文件
        audio_path = find_object(audio_oid, cache_root)
        if not audio_path:
            logger.warning(f"音频文件不存在: {audio_oid}")
            return False

        # 查找封面文件
        cover_oid = item.get('cover_oid')
        cover_path = None
        if cover_oid:
            cover_path = find_object(cover_oid, cache_root)

        # 生成文件名
        filename = get_work_filename(item)
        filename = sanitize_filename(filename)
        dest_path = releases_root / filename

        # 检查是否已存在且内容相同
        if dest_path.exists():
            # 计算现有文件的哈希
            with open(dest_path, 'rb') as f:
                existing_hash = hashlib.sha256(f.read()).hexdigest()

            with open(audio_path, 'rb') as f:
                new_hash = hashlib.sha256(f.read()).hexdigest()

            if existing_hash == new_hash:
                logger.info(f"已存在且相同: {filename}")
                return True

        # 嵌入元数据
        logger.info(f"生成: {filename}")
        data = embed_metadata(audio_path, item, cover_path)

        # 写入临时文件
        temp_path = dest_path.with_suffix('.tmp')
        with open(temp_path, 'wb') as f:
            f.write(data)

        # 原子替换
        shutil.move(str(temp_path), str(dest_path))
        logger.info(f"完成: {filename}")

        return True

    except Exception as e:
        logger.error(f"处理失败 {item.get('title', '未知')}: {e}")
        return False


def load_metadata(metadata_file):
    """加载 metadata.jsonl"""
    if not metadata_file.exists():
        return []

    metadata_list = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metadata_list.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return metadata_list


def main():
    import argparse

    parser = argparse.ArgumentParser(description='服务器端生成成品')
    parser.add_argument('--cache-root', default='/srv/music/cache', help='cache 根目录')
    parser.add_argument('--releases-root', default='/srv/music/releases', help='成品目录')
    parser.add_argument('--metadata', help='指定 metadata.jsonl 路径（可选）')
    parser.add_argument('--only-changed', action='store_true', help='只处理自上次运行后更改的条目')
    args = parser.parse_args()

    cache_root = Path(args.cache_root)
    releases_root = Path(args.releases_root)

    if args.metadata:
        metadata_file = Path(args.metadata)
    else:
        # 尝试从当前目录或上级目录查找
        metadata_file = Path('metadata.jsonl')
        if not metadata_file.exists():
            metadata_file = Path('../metadata.jsonl')

    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return

    if not cache_root.exists():
        logger.error(f"cache 目录不存在: {cache_root}")
        return

    releases_root.mkdir(parents=True, exist_ok=True)

    # 加载 metadata
    metadata_list = load_metadata(metadata_file)
    logger.info(f"加载 {len(metadata_list)} 个条目")

    # 如果只处理更改的条目，需要记录状态
    if args.only_changed:
        # 这里简化处理：处理所有条目
        # 实际可以记录最后处理时间或哈希
        pass

    # 处理每个条目
    success_count = 0
    total_count = len(metadata_list)

    if total_count > 0:
        # 创建进度条（固定描述，不随文件变化）
        progress_mgr.set_progress(0, total_count, "生成中")

        for idx, item in enumerate(metadata_list, 1):
            # 只更新进度，不更新描述
            progress_mgr.set_progress(idx, total_count, "生成中")

            if process_single_item(item, cache_root, releases_root):
                success_count += 1

        # 关闭进度条
        progress_mgr.close()

    logger.info(f"\n完成！成功: {success_count}/{total_count}")
    logger.info(f"成品目录: {releases_root}")


if __name__ == "__main__":
    main()
