#!/usr/bin/env python3
"""
本地成品生成脚本
从 cache/objects 读取纯净音频，嵌入 metadata 和封面，生成艺术家 - 标题.mp3 到 release 目录
"""

import os
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
import logging
import sys
import threading
from io import StringIO

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

# 尝试导入 mutagen
try:
    from mutagen import File
    from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, USLT, APIC
    from mutagen.mp3 import MP3
except ImportError:
    logger.error("错误: mutagen 库未安装")
    exit(1)


def load_metadata():
    """加载 metadata.jsonl"""
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"
    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return {}

    metadata = {}
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                metadata[item['audio_oid']] = item
    return metadata


def get_cache_path(audio_oid):
    """根据 audio_oid 获取 cache 中的音频文件路径"""
    # sha256:abc123... -> abc123...
    oid_hex = audio_oid.split(':', 1)[1] if ':' in audio_oid else audio_oid
    subdir = oid_hex[:2]
    cache_root = Path(__file__).parent.parent.parent / "cache"
    return cache_root / "objects" / "sha256" / subdir / f"{oid_hex}.mp3"


def get_cover_path(cover_oid):
    """根据 cover_oid 获取 cache 中的封面文件路径"""
    if not cover_oid:
        return None
    oid_hex = cover_oid.split(':', 1)[1] if ':' in cover_oid else cover_oid
    subdir = oid_hex[:2]
    cache_root = Path(__file__).parent.parent.parent / "cache"
    return cache_root / "covers" / "sha256" / subdir / f"{oid_hex}.jpg"


def create_release_item(metadata_item, output_dir):
    """生成单个成品文件"""
    audio_oid = metadata_item['audio_oid']
    artists = metadata_item.get('artists', [])
    title = metadata_item.get('title', 'Unknown')

    # 构建文件名: 艺术家 - 标题.mp3
    if isinstance(artists, list):
        artist_str = ', '.join(artists)
    else:
        artist_str = str(artists)

    filename = f"{artist_str} - {title}.mp3"
    # 移除文件名中的非法字符及空字符
    filename = filename.replace('\x00', '')
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')

    output_path = output_dir / filename

    # 检查音频文件是否存在
    audio_path = get_cache_path(audio_oid)
    if not audio_path.exists():
        logger.error(f"音频文件不存在: {audio_oid}")
        return False

    # 检查封面文件
    cover_oid = metadata_item.get('cover_oid')
    cover_path = get_cover_path(cover_oid) if cover_oid else None
    if cover_oid and not cover_path.exists():
        logger.warning(f"封面文件不存在: {cover_oid}")
        cover_path = None

    # 创建临时文件
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        # 复制音频到临时文件
        shutil.copy2(audio_path, temp_path)

        # 使用 mutagen 嵌入 metadata
        audio = MP3(temp_path)

        # 确保有 ID3 标签
        if audio.tags is None:
            audio.add_tags()

        # 清除现有 ID3 标签
        audio.delete()

        # 添加新的标签
        # 艺术家
        if artists:
            audio.tags.add(TPE1(encoding=3, text=artists if isinstance(artists, list) else [artists]))

        # 标题
        if title:
            audio.tags.add(TIT2(encoding=3, text=title))

        # 专辑（如果没有，使用标题填充）
        album = metadata_item.get('album')
        if not album:
            album = title
        if album:
            audio.tags.add(TALB(encoding=3, text=album))

        # 日期
        date = metadata_item.get('date')
        if date:
            audio.tags.add(TDRC(encoding=3, text=date))

        # 歌词
        uslt = metadata_item.get('uslt')
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

        # 原子替换：先写入最终路径的临时文件，再移动
        final_temp = output_path.with_suffix('.mp3.part')
        shutil.move(temp_path, final_temp)
        final_temp.replace(output_path)

        # 设置文件时间为元数据中的更新/创建时间
        try:
            dt_str = metadata_item.get('updated_at') or metadata_item.get('created_at')
            if dt_str:
                # 解析 ISO 格式时间 (处理 Z 结尾)
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                ts = dt.timestamp()
                os.utime(output_path, (ts, ts))
        except Exception as e:
            logger.warning(f"设置文件时间失败 {filename}: {e}")

        logger.info(f"✓ 生成: {filename}")
        return True

    except Exception as e:
        logger.error(f"生成失败 {filename}: {e}")
        if temp_path.exists():
            temp_path.unlink()
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="本地生成成品文件")
    parser.add_argument("--oid", help="指定 audio_oid，只生成单个文件")
    parser.add_argument("--output", default=str(Path(__file__).parent.parent.parent / "release"),
                       help="输出目录，默认 ../release")

    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata()
    if not metadata:
        logger.error("metadata.jsonl 为空或不存在")
        return

    if args.oid:
        # 生成单个文件
        if args.oid not in metadata:
            logger.error(f"未找到 audio_oid: {args.oid}")
            return
        success = create_release_item(metadata[args.oid], output_dir)
        if success:
            logger.info("单个文件生成完成")
        else:
            logger.error("单个文件生成失败")
    else:
        # 生成所有文件
        success_count = 0
        total_count = len(metadata)
        logger.info(f"开始生成 {total_count} 个成品文件...")

        # 创建进度条（固定描述，不随文件变化）
        progress_mgr.set_progress(0, total_count, "生成中")

        for idx, (oid, item) in enumerate(metadata.items(), 1):
            # 只更新进度，不更新描述
            progress_mgr.set_progress(idx, total_count, "生成中")

            if create_release_item(item, output_dir):
                success_count += 1

        # 关闭进度条
        progress_mgr.close()

        logger.info(f"生成完成: {success_count}/{total_count} 成功")


if __name__ == "__main__":
    main()
