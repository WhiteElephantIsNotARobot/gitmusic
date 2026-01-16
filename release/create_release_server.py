#!/usr/bin/env python3
"""
服务器端成品生成脚本 - 健壮版
1. 强制剥离原始标签以修复 embedded null byte 错误
2. 使用唯一临时文件确保多线程安全
3. 同步元数据中的时间戳
"""

import os
import json
import hashlib
import shutil
import tempfile
import subprocess
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
    """嵌入元数据到音频文件（强制重构模式）"""
    tmp_clean = None
    try:
        # 1. 使用 ffmpeg 剥离所有原始标签，生成一个绝对干净的流
        # 这能彻底解决 mutagen 遇到的 embedded null byte 问题
        fd, tmp_clean_path = tempfile.mkstemp(suffix='.clean.mp3')
        os.close(fd)
        tmp_clean = Path(tmp_clean_path)

        cmd = [
            'ffmpeg', '-y', '-i', str(audio_path),
            '-map', '0:a', '-c', 'copy',
            '-map_metadata', '-1',
            str(tmp_clean)
        ]
        subprocess.run(cmd, capture_output=True, check=True)

        # 2. 使用 mutagen 在干净的文件上构建新标签
        audio = MP3(str(tmp_clean))
        audio.add_tags()
        
        artists = metadata.get('artists', [])
        if artists:
            audio.tags.add(TPE1(encoding=3, text=artists if isinstance(artists, list) else [artists]))
        
        title = metadata.get('title', '未知')
        audio.tags.add(TIT2(encoding=3, text=title))
        
        album = metadata.get('album') or title
        audio.tags.add(TALB(encoding=3, text=album))
        
        date = metadata.get('date')
        if date:
            audio.tags.add(TDRC(encoding=3, text=date))
            
        uslt = metadata.get('uslt')
        if uslt:
            audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt))
            
        if cover_path and cover_path.exists():
            with open(cover_path, 'rb') as f:
                cover_data = f.read()
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
        
        audio.save()

        # 3. 读取处理后的数据
        with open(tmp_clean, 'rb') as f:
            data = f.read()

        return data

    except Exception as e:
        logger.error(f"嵌入元数据失败: {e}")
        raise
    finally:
        if tmp_clean and tmp_clean.exists():
            tmp_clean.unlink()


def get_work_filename(metadata):
    """生成文件名：艺术家 - 标题.mp3"""
    artists = metadata.get('artists', [])
    title = metadata.get('title', '未知')
    artist_str = ', '.join(artists) if artists else ""
    return f"{artist_str} - {title}.mp3" if artist_str else f"{title}.mp3"


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
    path = data_root / ('objects' if 'objects' in str(oid) or not 'covers' in str(oid) else 'covers') / 'sha256' / hash_hex[:2] / f"{hash_hex}{'.jpg' if 'covers' in str(oid) else '.mp3'}"
    # 修正路径逻辑
    if 'cover' in str(oid):
        path = data_root / 'covers' / 'sha256' / hash_hex[:2] / f"{hash_hex}.jpg"
    else:
        path = data_root / 'objects' / 'sha256' / hash_hex[:2] / f"{hash_hex}.mp3"
    return path if path.exists() else None


def process_single_item(item, data_root, releases_root):
    """处理单个条目"""
    try:
        audio_oid = item.get('audio_oid')
        if not audio_oid: return False

        filename = sanitize_filename(get_work_filename(item))
        dest_path = releases_root / filename

        audio_path = find_object(audio_oid, data_root)
        if not audio_path:
            logger.warning(f"音频缺失: {audio_oid}")
            return False

        cover_oid = item.get('cover_oid')
        cover_path = find_object(cover_oid, data_root) if cover_oid else None

        # 生成成品数据
        data = embed_metadata(audio_path, item, cover_path)

        # 写入唯一临时文件，防止多线程冲突
        tmp_dest = dest_path.with_suffix(f".{audio_oid[7:15]}.tmp")
        with open(tmp_dest, 'wb') as f:
            f.write(data)

        # 原子替换
        shutil.move(str(tmp_dest), str(dest_path))

        # 同步时间戳
        dt_str = item.get('updated_at') or item.get('created_at')
        if dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            ts = dt.timestamp()
            os.utime(dest_path, (ts, ts))

        logger.info(f"✓ 生成: {filename}")
        return True

    except Exception as e:
        logger.error(f"处理失败 {item.get('title', '未知')}: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-root', default='/srv/music/data')
    parser.add_argument('--releases-root', default='/srv/music/data/releases')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    releases_root = Path(args.releases_root)
    metadata_file = Path(__file__).parent.parent / 'metadata.jsonl'

    if not metadata_file.exists(): return
    releases_root.mkdir(parents=True, exist_ok=True)

    with open(metadata_file, 'r', encoding='utf-8') as f:
        metadata_list = [json.loads(line) for line in f if line.strip()]

    logger.info(f"开始生成 {len(metadata_list)} 个条目...")
    success = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_single_item, item, data_root, releases_root) for item in metadata_list]
        for future in as_completed(futures):
            if future.result(): success += 1

    logger.info(f"完成！成功: {success}/{len(metadata_list)}")


if __name__ == "__main__":
    main()
