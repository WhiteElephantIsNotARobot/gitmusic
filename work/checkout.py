#!/usr/bin/env python3
"""
检出脚本
按 audio_oid 或标题从 cache 检出，嵌入标签和封面后保存到 work 目录
"""

import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def find_in_cache(oid, cache_root, data_type):
    """在 cache 中查找对象"""
    if not oid or not oid.startswith('sha256:'):
        return None

    hash_hex = oid[7:]
    subdir = hash_hex[:2]

    if data_type == 'audio':
        path = cache_root / 'data' / 'objects' / 'sha256' / subdir / f"{hash_hex}.mp3"
    else:
        path = cache_root / 'data' / 'covers' / 'sha256' / subdir / f"{hash_hex}.jpg"

    return path if path.exists() else None


def embed_tags(audio_path, cover_path, metadata, output_path):
    """嵌入标签和封面到音频文件"""
    try:
        # 先复制音频文件
        shutil.copy2(audio_path, output_path)

        # 使用 mutagen 嵌入标签
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, TPE1, TIT2, TALB, TDRC, USLT, APIC

        audio = MP3(output_path)

        # 确保有 ID3 标签
        if audio.tags is None:
            audio.add_tags()

        # 清除现有标签
        audio.delete()

        # 添加标签
        title = metadata.get('title', '')
        artists = metadata.get('artists', [])
        album = metadata.get('album', '')
        date = metadata.get('date', '')
        uslt = metadata.get('uslt', '')

        if title:
            audio.tags.add(TIT2(encoding=3, text=title))
        if artists:
            audio.tags.add(TPE1(encoding=3, text=artists))
        if not album:
            album = title  # 没有专辑时使用标题
        if album:
            audio.tags.add(TALB(encoding=3, text=album))
        if date:
            audio.tags.add(TDRC(encoding=3, text=date))
        if uslt:
            audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt))

        # 添加封面
        if cover_path and cover_path.exists():
            with open(cover_path, 'rb') as f:
                cover_data = f.read()
            audio.tags.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=cover_data
            ))

        audio.save()
        return True
    except Exception as e:
        logger.error(f"嵌入标签失败: {e}")
        if output_path.exists():
            output_path.unlink()
def get_work_filename(metadata):
    """生成工作目录文件名：艺术家 - 标题.mp3"""
    artists = metadata.get('artists', [])
    title = metadata.get('title', '未知')

    if artists:
        artist_str = ', '.join(artists)
        return f"{artist_str} - {title}.mp3"
    else:
        return f"{title}.mp3"


def checkout_by_oid(oid, work_dir, cache_root):
    """按 audio_oid 检出并嵌入标签"""
    # 查找音频文件
    audio_path = find_in_cache(oid, cache_root, 'audio')
    if not audio_path:
        logger.error(f"在 cache 中未找到音频: {oid}")
        return False

    # 从 metadata.jsonl 获取元数据
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"
    if not metadata_file.exists():
        logger.error("metadata.jsonl 不存在")
        return False

    # 查找匹配的 metadata
    target_metadata = None
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                item = json.loads(line)
                if item.get('audio_oid') == oid:
                    target_metadata = item
                    break
            except json.JSONDecodeError:
                continue

    if not target_metadata:
        logger.error(f"在 metadata.jsonl 中未找到 OID: {oid}")
        return False

    # 查找封面
    cover_oid = target_metadata.get('cover_oid')
    cover_path = None
    if cover_oid:
        cover_path = find_in_cache(cover_oid, cache_root, 'cover')
        if cover_path:
            logger.info(f"找到封面: {cover_oid[:16]}...")
        else:
            logger.warning(f"封面不存在: {cover_oid}")

    # 生成目标文件名
    filename = get_work_filename(target_metadata)
    dest_path = work_dir / filename

    # 避免覆盖
    if dest_path.exists():
        base = dest_path.stem
        ext = dest_path.suffix
        for i in range(1, 100):
            new_name = f"{base}_{i}{ext}"
            new_path = work_dir / new_name
            if not new_path.exists():
                dest_path = new_path
                break

    # 嵌入标签并保存
    logger.info(f"嵌入标签并保存到: {dest_path.name}")
    if embed_tags(audio_path, cover_path, target_metadata, dest_path):
        logger.info(f"✓ 检出成功: {dest_path.name}")
        return True
    else:
        return False


def checkout_by_title(title_pattern, work_dir, cache_root):
    """按标题模式检出"""
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"
    if not metadata_file.exists():
        logger.error("metadata.jsonl 不存在")
        return False

    # 查找匹配条目
    matches = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                item = json.loads(line)
                title = item.get('title', '').lower()
                if title_pattern.lower() in title:
                    matches.append(item)
            except json.JSONDecodeError:
                continue

    if not matches:
        logger.error(f"未找到匹配标题: {title_pattern}")
        return False

    if len(matches) > 1:
        logger.info(f"找到 {len(matches)} 个匹配项:")
        for i, m in enumerate(matches[:5], 1):
            artists = ', '.join(m.get('artists', []))
            logger.info(f"  {i}. {artists} - {m.get('title')}")
        logger.info("将检出第一个匹配项")

    return checkout_by_oid(matches[0]['audio_oid'], work_dir, cache_root)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='检出文件到 work 目录（嵌入标签和封面）')
    parser.add_argument('identifier', help='audio_oid 或标题模式')
    parser.add_argument('--cache-root', default=str(Path(__file__).parent.parent.parent / 'cache'),
                       help='cache 根目录，默认 ../cache')
    args = parser.parse_args()

    # 确定目录
    repo_root = Path(__file__).parent.parent
    work_dir = repo_root.parent / "work"
    cache_root = Path(args.cache_root)

    if not work_dir.exists():
        work_dir.mkdir(parents=True, exist_ok=True)

    # 判断是 OID 还是标题
    identifier = args.identifier
    if identifier.startswith('sha256:'):
        success = checkout_by_oid(identifier, work_dir, cache_root)
    else:
        success = checkout_by_title(identifier, work_dir, cache_root)

    if success:
        logger.info("\n检出成功！")
        logger.info(f"文件位于: {work_dir}")
        logger.info("编辑后可运行: python repo/work/publish_meta.py")
    else:
        logger.error("检出失败")


if __name__ == "__main__":
    main()
