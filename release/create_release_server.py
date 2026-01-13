#!/usr/bin/env python3
"""
服务器端成品生成脚本
从 /srv/music/data/objects 生成成品到 /srv/music/data/releases/
"""

import os
import json
import hashlib
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def embed_metadata(audio_path, metadata, cover_path=None):
    """嵌入元数据到音频文件"""
    try:
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            tmp_path = tmp.name

        # 使用 ffmpeg 嵌入标签
        cmd = [
            'ffmpeg', '-i', str(audio_path),
            '-c', 'copy',  # 复制流，不重新编码
            '-metadata', f"title={metadata.get('title', '')}",
            '-metadata', f"artist={', '.join(metadata.get('artists', []))}",
            '-metadata', f"album={metadata.get('album', '')}",
            '-metadata', f"date={metadata.get('date', '')}",
        ]

        # 如果有封面，嵌入封面
        if cover_path and cover_path.exists():
            cmd.extend(['-i', str(cover_path), '-map', '0:a:0', '-map', '1:0'])
            cmd.extend(['-c:v', 'copy', '-id3v2_version', '3', '-write_id3v1', '1'])

        cmd.append(tmp_path)

        result = subprocess.run(cmd, capture_output=True, check=True)

        # 读取结果
        with open(tmp_path, 'rb') as f:
            data = f.read()

        os.unlink(tmp_path)
        return data

    except subprocess.CalledProcessError as e:
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
    parser.add_argument('--data-root', default='/srv/music/data', help='数据根目录')
    parser.add_argument('--releases-root', default='/srv/music/data/releases', help='成品目录')
    parser.add_argument('--metadata', help='指定 metadata.jsonl 路径（可选）')
    parser.add_argument('--only-changed', action='store_true', help='只处理自上次运行后更改的条目')
    args = parser.parse_args()

    data_root = Path(args.data_root)
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

    if not data_root.exists():
        logger.error(f"data 目录不存在: {data_root}")
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
    for item in metadata_list:
        if process_single_item(item, data_root, releases_root):
            success_count += 1

    logger.info(f"\n完成！成功: {success_count}/{len(metadata_list)}")
    logger.info(f"成品目录: {releases_root}")


if __name__ == "__main__":
    main()
