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
    """嵌入元数据到音频文件（彻底重构标签模式）"""
    tmp_path = None
    try:
        # 确保路径是干净的字符串
        audio_path_str = os.path.abspath(str(audio_path))
        
        # 创建临时文件
        fd, path_str = tempfile.mkstemp(suffix='.mp3')
        os.close(fd)
        tmp_path = Path(path_str)

        # 复制音频到临时文件
        shutil.copy2(audio_path_str, str(tmp_path))

        # 1. 彻底删除所有原始标签
        from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, USLT, APIC
        try:
            tags = ID3(str(tmp_path))
            tags.delete()
        except Exception:
            pass
        
        # 2. 创建全新的标签对象
        tags = ID3()
        
        # 清洗元数据字符串
        def clean(s):
            return str(s).replace('\0', '') if s else ""

        artists = metadata.get('artists', [])
        if artists:
            clean_artists = [clean(a) for a in artists] if isinstance(artists, list) else [clean(artists)]
            tags.add(TPE1(encoding=3, text=clean_artists))
        
        title = clean(metadata.get('title') or "未知")
        tags.add(TIT2(encoding=3, text=title))
        
        album = clean(metadata.get('album')) or title
        tags.add(TALB(encoding=3, text=album))
        
        date = clean(metadata.get('date'))
        if date:
            tags.add(TDRC(encoding=3, text=date))
        
        uslt = clean(metadata.get('uslt'))
        if uslt:
            tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt))
        
        if cover_path and cover_path.exists():
            with open(str(cover_path), 'rb') as f:
                cover_data = f.read()
            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
        
        # 3. 保存全新标签
        tags.save(str(tmp_path))

        with open(str(tmp_path), 'rb') as f:
            data = f.read()

        os.unlink(str(tmp_path))
        return data

    except Exception as e:
        logger.error(f"嵌入元数据失败: {e}")
        if tmp_path and tmp_path.exists():
            os.unlink(str(tmp_path))
        raise


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
    parser.add_argument('--workers', type=int, default=4)
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
