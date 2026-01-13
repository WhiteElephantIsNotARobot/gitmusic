#!/usr/bin/env python3
"""
工作目录发布脚本
扫描 work/ 目录中的 MP3 文件，计算音频哈希并更新 metadata.jsonl
"""

import os
import json
import hashlib
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入 mutagen
try:
    from mutagen import File
    from mutagen.id3 import ID3, APIC
except ImportError:
    logger.error("错误: mutagen 库未安装")
    exit(1)


def extract_audio_stream(audio_path):
    """提取纯净音频流并计算哈希（完全移除ID3标签）"""
    try:
        cmd = [
            'ffmpeg', '-i', str(audio_path), '-map', '0:a:0',
            '-c', 'copy', '-f', 'mp3',
            '-map_metadata', '-1',  # 移除元数据
            '-id3v2_version', '0',  # 不写入ID3v2标签
            '-write_id3v1', '0',    # 不写入ID3v1标签
            'pipe:1'
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        audio_data = result.stdout
        audio_hash = hashlib.sha256(audio_data).hexdigest()
        return audio_data, audio_hash
    except subprocess.CalledProcessError as e:
        logger.error(f"提取音频流失败 {audio_path}: {e}")
        raise


def extract_cover(audio_path):
    """提取封面图片并计算哈希"""
    try:
        cmd = [
            'ffmpeg', '-i', str(audio_path), '-map', '0:v',
            '-map', '-0:V', '-c', 'copy', '-f', 'image2', 'pipe:1'
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        if result.stdout:
            cover_data = result.stdout
            cover_hash = hashlib.sha256(cover_data).hexdigest()
            return cover_data, cover_hash
        return None, None
    except subprocess.CalledProcessError:
        return None, None


def split_artists(artists_str):
    """拆分艺术家字符串"""
    if not artists_str:
        return []
    # 先按 ' & ' 拆分，再按 ', ' 拆分
    parts = artists_str.split(' & ')
    artist_list = []
    for part in parts:
        artist_list.extend([p.strip() for p in part.split(', ') if p.strip()])
    return artist_list


def parse_metadata(audio_path):
    """解析元数据"""
    try:
        audio = File(audio_path)
        metadata = {}

        if hasattr(audio, 'tags') and audio.tags:
            # 艺术家
            if 'TPE1' in audio.tags:
                artists = audio.tags['TPE1']
                if isinstance(artists, list):
                    artist_list = []
                    for a in artists:
                        artist_list.extend(split_artists(str(a)))
                    metadata["artists"] = artist_list
                else:
                    metadata["artists"] = split_artists(str(artists))

            # 标题
            if 'TIT2' in audio.tags:
                title = audio.tags['TIT2']
                metadata["title"] = str(title[0]) if isinstance(title, list) else str(title)

            # 专辑
            if 'TALB' in audio.tags:
                album = audio.tags['TALB']
                metadata["album"] = str(album[0]) if isinstance(album, list) else str(album)

            # 日期
            if 'TDRC' in audio.tags:
                date = audio.tags['TDRC']
                metadata["date"] = str(date[0]) if isinstance(date, list) else str(date)

            # 歌词
            if isinstance(audio.tags, ID3):
                for frame in audio.tags.values():
                    if frame.FrameID == 'USLT':
                        text = frame.text.decode('utf-8') if hasattr(frame.text, 'decode') else str(frame.text)
                        metadata["uslt"] = text
                        break

        # 如果没有标题，从文件名推断
        if 'title' not in metadata or 'artists' not in metadata:
            filename = Path(audio_path).stem
            if ' - ' in filename:
                parts = filename.split(' - ', 1)
                if len(parts) == 2:
                    if 'artists' not in metadata:
                        metadata["artists"] = [parts[0].strip()]
                    if 'title' not in metadata:
                        metadata["title"] = parts[1].strip()

        return metadata
    except Exception as e:
        logger.error(f"解析元数据失败 {audio_path}: {e}")
        return {}


def load_metadata(metadata_file):
    """加载现有 metadata.jsonl"""
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


def save_metadata(metadata_file, metadata_list):
    """保存 metadata.jsonl（按 audio_oid 排序）"""
    # 按 audio_oid 排序
    metadata_list.sort(key=lambda x: x.get('audio_oid', ''))

    # 写入临时文件
    temp_file = metadata_file.with_suffix('.tmp')
    with open(temp_file, 'w', encoding='utf-8') as f:
        for item in metadata_list:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    # 原子替换
    shutil.move(str(temp_file), str(metadata_file))


def process_file(audio_file, metadata_list, cache_root):
    """处理单个文件：计算哈希，更新或新增条目"""
    try:
        logger.info(f"处理: {audio_file.name}")

        # 提取音频流和哈希
        audio_data, audio_hash = extract_audio_stream(audio_file)
        audio_oid = f"sha256:{audio_hash}"

        # 提取封面和哈希
        cover_data, cover_hash = extract_cover(audio_file)
        new_cover_oid = f"sha256:{cover_hash}" if cover_hash else None

        # 解析元数据
        metadata = parse_metadata(audio_file)

        # 查找是否已存在
        existing = None
        for item in metadata_list:
            if item.get('audio_oid') == audio_oid:
                existing = item
                break

        now = datetime.utcnow().isoformat() + 'Z'

        if existing:
            # 更新现有条目 - 全部重写字段
            logger.info(f"更新条目: {metadata.get('title', '未知')}")
            
            # 保留不变的字段
            existing['audio_oid'] = audio_oid
            existing['created_at'] = existing.get('created_at', now)
            
            # 更新所有可能变化的字段，空值则删除字段
            for field in ['title', 'artists', 'album', 'date', 'uslt']:
                value = metadata.get(field)
                if value:
                    existing[field] = value
                elif field in existing:
                    del existing[field]
            
            existing['updated_at'] = now
            
            # 处理封面
            if new_cover_oid:
                existing['cover_oid'] = new_cover_oid
                # 保存新封面到 cache
                if cover_data:
                    save_to_cache(cover_data, new_cover_oid, cache_root, 'covers')
            else:
                # 如果新条目没有封面，删除旧的封面引用
                if 'cover_oid' in existing:
                    del existing['cover_oid']
        else:
            # 新增条目
            logger.info(f"新增条目: {metadata.get('title', '未知')}")
            new_item = {
                'audio_oid': audio_oid,
                'title': metadata.get('title', '未知'),
                'artists': metadata.get('artists', []),
                'album': metadata.get('album', ''),
                'date': metadata.get('date', ''),
                'uslt': metadata.get('uslt', ''),
                'created_at': now,
                'updated_at': now
            }
            if new_cover_oid:
                new_item['cover_oid'] = new_cover_oid
            metadata_list.append(new_item)

            # 保存音频和封面到 cache
            save_to_cache(audio_data, audio_oid, cache_root, 'objects')
            if cover_data:
                save_to_cache(cover_data, new_cover_oid, cache_root, 'covers')

        return True
    except Exception as e:
        logger.error(f"处理失败 {audio_file.name}: {e}")
        return False


def save_to_cache(data, oid, cache_root, data_type):
    """保存数据到 cache"""
    if not data or not oid:
        return

    hash_hex = oid.replace('sha256:', '')
    subdir = hash_hex[:2]

    if data_type == 'objects':
        target_dir = cache_root / 'data' / 'objects' / 'sha256' / subdir
        ext = '.mp3'
    else:
        target_dir = cache_root / 'data' / 'covers' / 'sha256' / subdir
        ext = '.jpg'

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{hash_hex}{ext}"

    # 只在不存在时写入
    if not target_path.exists():
        with open(target_path, 'wb') as f:
            f.write(data)
        logger.info(f"保存到 cache: {data_type}/{subdir}/{hash_hex[:8]}...")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='扫描 work 目录并更新 metadata.jsonl')
    parser.add_argument('files', nargs='*', help='指定要处理的文件（可选）')
    parser.add_argument('--no-git', action='store_true', help='不自动执行 git 操作')
    args = parser.parse_args()

    # 确定工作目录
    repo_root = Path(__file__).parent.parent
    work_dir = repo_root.parent / "work"
    metadata_file = repo_root / "metadata.jsonl"
    cache_root = repo_root.parent / "cache"

    if not work_dir.exists():
        logger.error(f"work 目录不存在: {work_dir}")
        return

    # 获取要处理的文件
    if args.files:
        audio_files = [Path(f) for f in args.files if Path(f).exists()]
    else:
        # 扫描 work 目录
        audio_files = []
        for ext in ['*.mp3', '*.flac', '*.m4a', '*.wav', '*.ogg']:
            audio_files.extend(work_dir.glob(ext))

    if not audio_files:
        logger.error("未找到音频文件")
        return

    logger.info(f"找到 {len(audio_files)} 个音频文件")

    # 加载现有 metadata
    metadata_list = load_metadata(metadata_file)
    logger.info(f"现有 metadata 条目数: {len(metadata_list)}")

    # 处理每个文件
    processed = []
    for audio_file in audio_files:
        if process_file(audio_file, metadata_list, cache_root):
            processed.append(audio_file)

    if not processed:
        logger.error("没有文件被成功处理")
        return

    # 保存 metadata
    save_metadata(metadata_file, metadata_list)
    logger.info(f"Metadata 已保存到: {metadata_file}")
    logger.info(f"处理成功: {len(processed)}/{len(audio_files)} 个文件")

    # 删除 work 中的文件
    for f in processed:
        f.unlink()
        logger.info(f"已删除: {f.name}")

    # Git 操作
    if not args.no_git:
        logger.info("执行 git 操作...")
        os.chdir(repo_root)

        # 添加
        subprocess.run(['git', 'add', 'metadata.jsonl'], check=False)

        # 提交
        if processed:
            commit_msg = f"Update metadata ({len(processed)} files)"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=False)

        # 推送
        subprocess.run(['git', 'push'], check=False)

        logger.info("Git 操作完成")

    logger.info("\n完成！")
    logger.info("接下来可以:")
    logger.info("1. 运行 repo/release/create_release_local.py 生成成品")
    logger.info("2. 运行 repo/data/sync_cache.py 同步到远端")


if __name__ == "__main__":
    main()
