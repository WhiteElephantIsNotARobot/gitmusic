#!/usr/bin/env python3
"""
清理孤立对象脚本
根据 metadata.jsonl 比对 cache/data 中的文件，删除不在数据库引用的对象
"""

import json
import shutil
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def load_metadata(metadata_file):
    """加载 metadata.jsonl，提取所有引用的 OID"""
    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return set(), set()

    audio_oids = set()
    cover_oids = set()

    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                audio_oid = item.get('audio_oid')
                if audio_oid:
                    audio_oids.add(audio_oid)
                cover_oid = item.get('cover_oid')
                if cover_oid:
                    cover_oids.add(cover_oid)
            except json.JSONDecodeError:
                continue

    return audio_oids, cover_oids


def find_orphaned_objects(cache_root, referenced_oids, data_type):
    """查找 cache 中未被引用的对象"""
    if data_type == 'audio':
        search_dir = cache_root / 'objects' / 'sha256'
        ext = '.mp3'
    else:
        search_dir = cache_root / 'covers' / 'sha256'
        ext = '.jpg'

    if not search_dir.exists():
        return []

    orphaned = []
    for subdir in search_dir.iterdir():
        if not subdir.is_dir():
            continue
        for file_path in subdir.glob(f'*{ext}'):
            oid = f"sha256:{file_path.stem}"
            if oid not in referenced_oids:
                orphaned.append(file_path)

    return orphaned


def cleanup_orphaned(cache_root, audio_oids, cover_oids, dry_run=True):
    """清理孤立对象"""
    # 查找孤立的音频文件
    orphaned_audio = find_orphaned_objects(cache_root, audio_oids, 'audio')
    logger.info(f"发现 {len(orphaned_audio)} 个孤立的音频文件")

    # 查找孤立的封面文件
    orphaned_covers = find_orphaned_objects(cache_root, cover_oids, 'cover')
    logger.info(f"发现 {len(orphaned_covers)} 个孤立的封面文件")

    total = len(orphaned_audio) + len(orphaned_covers)

    if total == 0:
        logger.info("没有发现孤立对象，无需清理")
        return 0

    if dry_run:
        logger.info("=== 干运行模式，仅列出将删除的文件 ===")
        for path in orphaned_audio:
            logger.info(f"[音频] {path}")
        for path in orphaned_covers:
            logger.info(f"[封面] {path}")
        logger.info(f"总计将删除 {total} 个文件")
        return 0

    # 执行删除
    deleted = 0
    for path in orphaned_audio:
        try:
            path.unlink()
            logger.info(f"删除音频: {path}")
            deleted += 1
        except Exception as e:
            logger.error(f"删除失败 {path}: {e}")

    for path in orphaned_covers:
        try:
            path.unlink()
            logger.info(f"删除封面: {path}")
            deleted += 1
        except Exception as e:
            logger.error(f"删除失败 {path}: {e}")

    # 清理空目录
    for subdir in (cache_root / 'objects' / 'sha256').iterdir():
        if subdir.is_dir() and not any(subdir.iterdir()):
            subdir.rmdir()
            logger.info(f"删除空目录: {subdir}")

    for subdir in (cache_root / 'covers' / 'sha256').iterdir():
        if subdir.is_dir() and not any(subdir.iterdir()):
            subdir.rmdir()
            logger.info(f"删除空目录: {subdir}")

    return deleted


def main():
    import argparse

    parser = argparse.ArgumentParser(description="清理 cache 中不在 metadata.jsonl 中的孤立对象")
    parser.add_argument("--cache-root", default=str(Path(__file__).parent.parent.parent / "cache"),
                       help="cache 根目录，默认 ../cache")
    parser.add_argument("--metadata", help="指定 metadata.jsonl 路径（可选）")
    parser.add_argument("--dry-run", action="store_true", help="干运行模式，仅列出将删除的文件")
    parser.add_argument("--confirm", action="store_true", help="确认执行删除（默认为干运行）")
    args = parser.parse_args()

    cache_root = Path(args.cache_root)
    if not cache_root.exists():
        logger.error(f"cache 目录不存在: {cache_root}")
        return

    # 确定 metadata 文件
    if args.metadata:
        metadata_file = Path(args.metadata)
    else:
        repo_root = Path(__file__).parent.parent
        metadata_file = repo_root / "metadata.jsonl"

    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return

    logger.info(f"加载 metadata: {metadata_file}")
    audio_oids, cover_oids = load_metadata(metadata_file)
    logger.info(f"metadata 中引用: {len(audio_oids)} 个音频, {len(cover_oids)} 个封面")

    # 执行清理
    dry_run = not args.confirm
    if dry_run:
        logger.info("=== 干运行模式（使用 --confirm 执行实际删除）===")
    else:
        logger.info("=== 执行删除模式 ===")

    deleted = cleanup_orphaned(cache_root, audio_oids, cover_oids, dry_run)

    if dry_run:
        logger.info("干运行完成，未删除任何文件")
    else:
        logger.info(f"清理完成，共删除 {deleted} 个文件")


if __name__ == "__main__":
    main()
