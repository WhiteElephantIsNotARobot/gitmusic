#!/usr/bin/env python3
"""
服务器端成品生成脚本
从 /srv/music/data/objects 生成成品到 /srv/music/data/releases/
"""

import os
import json
import hashlib
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入 mutagen
try:
    from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, USLT, APIC
    from mutagen.mp3 import MP3
except ImportError:
    logger.error("错误: mutagen 库未安装")
    exit(1)


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
        if 'tmp_path' in locals() and tmp_path.exists():
            os.unlink(tmp_path)
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
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    return filename


def find_object(oid, data_root):
    """在 data 目录中查找对象"""
    if not oid or not oid.startswith('sha256:'):
        return None

    hash_hex = oid[7:]
    subdir = hash_hex[:2]

    # 查找音频
    audio_path = data_root / 'objects' / 'sha256' / subdir / f"{hash_hex}.mp3"
    if audio_path.exists():
        return audio_path

    # 查找封面
    cover_path = data_root / 'covers' / 'sha256' / subdir / f"{hash_hex}.jpg"
    if cover_path.exists():
        return cover_path

    return None


def process_single_item(item, data_root, releases_root):
    """处理单个 metadata 条目"""
    try:
        audio_oid = item.get('audio_oid')
        if not audio_oid:
            return False

        # 生成文件名
        filename = get_work_filename(item)
        filename = sanitize_filename(filename)
        dest_path = releases_root / filename

        # 查找音频文件
        audio_path = find_object(audio_oid, data_root)
        if not audio_path:
            logger.warning(f"音频文件不存在: {audio_oid}")
            return False

        # 查找封面文件
        cover_oid = item.get('cover_oid')
        cover_path = None
        if cover_oid:
            cover_path = find_object(cover_oid, data_root)

        # 嵌入元数据
        data = embed_metadata(audio_path, item, cover_path)

        # 写入临时文件
        temp_path = dest_path.with_suffix('.tmp')
        with open(temp_path, 'wb') as f:
            f.write(data)

        # 原子替换
        shutil.move(str(temp_path), str(dest_path))

        # 设置文件时间
        try:
            dt_str = item.get('updated_at') or item.get('created_at')
            if dt_str:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                ts = dt.timestamp()
                os.utime(dest_path, (ts, ts))
        except Exception:
            pass

        logger.info(f"✓ 生成: {filename}")
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
    parser.add_argument('--data-root', default='/srv/music/data', help='数据根目录')
    parser.add_argument('--releases-root', default='/srv/music/data/releases', help='成品目录')
    parser.add_argument('--metadata', help='指定 metadata.jsonl 路径（可选）')
    parser.add_argument('--workers', type=int, default=1, help='并行工作线程数')
    args = parser.parse_args()

    data_root = Path(args.data_root)
    releases_root = Path(args.releases_root)

    if args.metadata:
        metadata_file = Path(args.metadata)
    else:
        metadata_file = Path(__file__).parent.parent / 'metadata.jsonl'

    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return

    if not data_root.exists():
        logger.error(f"数据目录不存在: {data_root}")
        return

    releases_root.mkdir(parents=True, exist_ok=True)

    # 加载 metadata
    metadata_list = load_metadata(metadata_file)
    logger.info(f"加载 {len(metadata_list)} 个条目")

    # 并行处理
    success_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_single_item, item, data_root, releases_root) for item in metadata_list]
        for future in as_completed(futures):
            if future.result():
                success_count += 1

    logger.info(f"完成！成功: {success_count}/{len(metadata_list)}")


if __name__ == "__main__":
    main()
