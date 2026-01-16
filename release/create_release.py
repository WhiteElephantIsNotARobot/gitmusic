#!/usr/bin/env python3
"""
成品生成脚本 (统一版)
支持 Local 和 Server 两种模式，确保元数据和封面嵌入逻辑完全一致。
"""

import os
import json
import hashlib
import shutil
import tempfile
import subprocess
import logging
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入 mutagen 和 tqdm
try:
    from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, USLT, APIC, TXXX
    from mutagen.mp3 import MP3
except ImportError:
    logger.error("错误: mutagen 库未安装")
    exit(1)

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def get_metadata_hash(item):
    """计算元数据条目的哈希值，用于增量对比"""
    # 排除掉时间戳字段，只针对内容哈希
    content = {k: v for k, v in item.items() if k not in ['created_at', 'updated_at']}
    s = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def sanitize_filename(filename):
    """清理文件名中的非法字符及空字符"""
    filename = filename.replace('\x00', '')
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    return filename.strip()


def get_work_filename(metadata):
    """生成文件名：艺术家 - 标题.mp3"""
    artists = metadata.get('artists', [])
    title = metadata.get('title', '未知')
    artist_str = ', '.join(artists) if isinstance(artists, list) else str(artists)
    return f"{artist_str} - {title}.mp3" if artist_str else f"{title}.mp3"


def embed_metadata(audio_path, metadata, cover_path=None):
    """嵌入元数据到音频文件（稳健临时文件模式）"""
    tmp_file = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp3')
        os.close(fd)
        tmp_file = Path(tmp_path)
        shutil.copy2(audio_path, tmp_file)

        audio = MP3(tmp_file)
        if audio.tags is None:
            audio.add_tags()
        audio.delete()

        # 基础标签
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
            
        # 封面嵌入 (关键修复：确保 server 模式也能正确找到封面)
        if cover_path and cover_path.exists():
            with open(cover_path, 'rb') as f:
                cover_data = f.read()
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
        
        # 嵌入元数据哈希用于增量对比
        meta_hash = get_metadata_hash(metadata)
        audio.tags.add(TXXX(encoding=3, desc='METADATA_HASH', text=[meta_hash]))
        
        audio.save()
        with open(tmp_file, 'rb') as f:
            return f.read()
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()


def process_single_item(item, data_root, releases_root):
    """处理单个条目"""
    try:
        audio_oid = item.get('audio_oid')
        if not audio_oid: return False

        filename = sanitize_filename(get_work_filename(item))
        dest_path = releases_root / filename

        # 查找音频
        hash_hex = audio_oid[7:]
        audio_path = data_root / 'objects' / 'sha256' / hash_hex[:2] / f"{hash_hex}.mp3"
        if not audio_path.exists():
            return False

        # 查找封面
        cover_oid = item.get('cover_oid')
        cover_path = None
        if cover_oid:
            c_hash = cover_oid[7:]
            cover_path = data_root / 'covers' / 'sha256' / c_hash[:2] / f"{c_hash}.jpg"

        data = embed_metadata(audio_path, item, cover_path)

        # 原子写入
        tmp_dest = dest_path.with_suffix(f".{audio_oid[7:15]}.tmp")
        with open(tmp_dest, 'wb') as f:
            f.write(data)
        shutil.move(str(tmp_dest), str(dest_path))

        # 同步时间戳
        dt_str = item.get('created_at')
        if dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            ts = dt.timestamp()
            os.utime(dest_path, (ts, ts))

        return True
    except Exception as e:
        logger.error(f"处理失败 {item.get('title', '未知')}: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="成品生成工具 (统一版)")
    parser.add_argument('--mode', choices=['local', 'server'], required=True, help="运行模式")
    parser.add_argument('--data-root', help="数据根目录 (objects/covers 所在)")
    parser.add_argument('--output', help="成品输出目录")
    parser.add_argument('--workers', type=int, help="并行线程数")
    args = parser.parse_args()

    # 自动推断路径
    repo_root = Path(__file__).parent.parent
    if args.mode == 'server':
        data_root = Path(args.data_root or '/srv/music/data')
        output_dir = Path(args.output or '/srv/music/data/releases')
        workers = args.workers or 4
        incremental = True
    else:
        data_root = repo_root.parent / 'cache'
        output_dir = Path(args.output or repo_root.parent / 'release')
        workers = args.workers or 1
        incremental = False

    metadata_file = repo_root / 'metadata.jsonl'
    if not metadata_file.exists():
        logger.error("metadata.jsonl 不存在")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    with open(metadata_file, 'r', encoding='utf-8') as f:
        metadata_list = [json.loads(line) for line in f if line.strip()]

    target_filenames = {sanitize_filename(get_work_filename(item)): get_metadata_hash(item) for item in metadata_list}

    if incremental:
        # Server 模式：增量更新 + 自动清理
        logger.info("扫描现有成品文件...")
        existing_files = list(output_dir.glob('*.mp3'))
        files_to_delete = []
        valid_hashes = set()

        for f in existing_files:
            try:
                audio = MP3(f)
                meta_hash = str(audio.tags['TXXX:METADATA_HASH'].text[0]) if audio.tags and 'TXXX:METADATA_HASH' in audio.tags else ""
                if f.name not in target_filenames or target_filenames[f.name] != meta_hash:
                    files_to_delete.append(f)
                else:
                    valid_hashes.add(meta_hash)
            except:
                files_to_delete.append(f)

        if files_to_delete:
            logger.info(f"清理 {len(files_to_delete)} 个过时文件...")
            for f in files_to_delete: f.unlink()

        to_generate = [item for item in metadata_list if get_metadata_hash(item) not in valid_hashes]
    else:
        # Local 模式：全量生成 (先清空)
        logger.info(f"正在清空输出目录: {output_dir}")
        for f in output_dir.glob('*.mp3'): f.unlink()
        to_generate = metadata_list

    if not to_generate:
        logger.info("所有成品已是最新。")
        return

    logger.info(f"开始生成 {len(to_generate)} 个条目 (模式: {args.mode})...")
    success = 0
    
    if args.mode == 'local' and tqdm:
        pbar = tqdm(total=len(to_generate), desc="生成中")
        for item in to_generate:
            if process_single_item(item, data_root, output_dir): success += 1
            pbar.update(1)
        pbar.close()
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_single_item, item, data_root, output_dir) for item in to_generate]
            for future in as_completed(futures):
                if future.result(): success += 1

    logger.info(f"完成！成功: {success}/{len(to_generate)}")


if __name__ == "__main__":
    main()
