#!/usr/bin/env python3
"""
分析 metadata.jsonl 中的重复项和统计信息
用于排查为什么510条数据只生成了508个release文件
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def load_metadata():
    """加载 metadata.jsonl"""
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"
    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return []

    metadata_list = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metadata_list.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"JSON解析错误: {line}")
                    continue
    return metadata_list


def analyze_duplicates(metadata_list):
    """分析重复项"""
    logger.info("=" * 60)
    logger.info("开始分析重复项...")
    logger.info("=" * 60)
    
    # 1. 按 audio_oid 统计
    audio_oid_counter = Counter(item.get('audio_oid') for item in metadata_list)
    duplicates_audio = {oid: count for oid, count in audio_oid_counter.items() if count > 1}
    
    if duplicates_audio:
        logger.error(f"\n❌ 发现 {len(duplicates_audio)} 个重复的 audio_oid:")
        for oid, count in sorted(duplicates_audio.items()):
            logger.error(f"  {oid}: {count} 次")
            # 显示重复的条目详情
            for item in metadata_list:
                if item.get('audio_oid') == oid:
                    logger.error(f"    - {item.get('title', '未知')} / {item.get('artists', [])} / created: {item.get('created_at')}")
    else:
        logger.info("✅ audio_oid 无重复")
    
    # 2. 按 cover_oid 统计
    cover_oid_counter = Counter(item.get('cover_oid') for item in metadata_list if item.get('cover_oid'))
    duplicates_cover = {oid: count for oid, count in cover_oid_counter.items() if count > 1}
    
    if duplicates_cover:
        logger.warning(f"\n⚠️  发现 {len(duplicates_cover)} 个重复的 cover_oid:")
        for oid, count in sorted(duplicates_cover.items()):
            logger.warning(f"  {oid}: {count} 次")
    else:
        logger.info("✅ cover_oid 无重复")
    
    # 3. 按文件名（艺术家 - 标题）统计
    filename_counter = Counter()
    filename_map = defaultdict(list)
    
    for item in metadata_list:
        artists = item.get('artists', [])
        title = item.get('title', '未知')
        if isinstance(artists, list):
            artist_str = ', '.join(artists)
        else:
            artist_str = str(artists)
        filename = f"{artist_str} - {title}.mp3"
        filename_counter[filename] += 1
        filename_map[filename].append(item)
    
    duplicates_filename = {name: count for name, count in filename_counter.items() if count > 1}
    
    if duplicates_filename:
        logger.error(f"\n❌ 发现 {len(duplicates_filename)} 个重复的文件名:")
        for filename, count in sorted(duplicates_filename.items()):
            logger.error(f"  {filename}: {count} 次")
            for item in filename_map[filename]:
                logger.error(f"    - audio_oid: {item.get('audio_oid')} / created: {item.get('created_at')}")
    else:
        logger.info("✅ 文件名无重复")
    
    return {
        'duplicates_audio': duplicates_audio,
        'duplicates_cover': duplicates_cover,
        'duplicates_filename': duplicates_filename
    }


def analyze_orphans(metadata_list):
    """分析孤立条目（有audio_oid但文件不存在）"""
    logger.info("\n" + "=" * 60)
    logger.info("分析孤立条目...")
    logger.info("=" * 60)
    
    cache_root = Path(__file__).parent.parent.parent / "cache"
    objects_dir = cache_root / "objects" / "sha256"
    
    orphaned_items = []
    missing_audio = 0
    
    for item in metadata_list:
        audio_oid = item.get('audio_oid')
        if not audio_oid:
            continue
        
        # 检查音频文件是否存在
        hash_hex = audio_oid.replace('sha256:', '')
        subdir = hash_hex[:2]
        audio_path = objects_dir / subdir / f"{hash_hex}.mp3"
        
        if not audio_path.exists():
            orphaned_items.append(item)
            missing_audio += 1
    
    if orphaned_items:
        logger.error(f"\n❌ 发现 {missing_audio} 个条目对应的音频文件不存在:")
        for item in orphaned_items[:10]:  # 只显示前10个
            logger.error(f"  {item.get('audio_oid')} - {item.get('title', '未知')}")
        if len(orphaned_items) > 10:
            logger.error(f"  ... 还有 {len(orphaned_items) - 10} 个")
    else:
        logger.info("✅ 所有条目的音频文件都存在")
    
    return orphaned_items


def analyze_coverage(metadata_list):
    """分析生成release的覆盖率"""
    logger.info("\n" + "=" * 60)
    logger.info("分析 release 生成覆盖率...")
    logger.info("=" * 60)
    
    release_dir = Path(__file__).parent.parent.parent / "release"
    
    if not release_dir.exists():
        logger.warning(f"release 目录不存在: {release_dir}")
        return
    
    # 统计release文件
    release_files = list(release_dir.glob("*.mp3"))
    logger.info(f"release 目录中文件数: {len(release_files)}")
    
    # 构建release文件名集合
    release_names = {f.name for f in release_files}
    
    # 构建metadata预期文件名集合
    expected_names = set()
    for item in metadata_list:
        artists = item.get('artists', [])
        title = item.get('title', '未知')
        if isinstance(artists, list):
            artist_str = ', '.join(artists)
        else:
            artist_str = str(artists)
        # 清理文件名非法字符
        filename = f"{artist_str} - {title}.mp3"
        for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
            filename = filename.replace(char, '_')
        expected_names.add(filename)
    
    # 分析差异
    missing_in_release = expected_names - release_names
    extra_in_release = release_names - expected_names
    
    if missing_in_release:
        logger.error(f"\n❌ metadata中有但release中缺失 {len(missing_in_release)} 个文件:")
        for name in sorted(list(missing_in_release)[:10]):
            logger.error(f"  {name}")
        if len(missing_in_release) > 10:
            logger.error(f"  ... 还有 {len(missing_in_release) - 10} 个")
    
    if extra_in_release:
        logger.warning(f"\n⚠️  release中有但metadata中不存在 {len(extra_in_release)} 个文件:")
        for name in sorted(list(extra_in_release)[:10]):
            logger.warning(f"  {name}")
    
    if not missing_in_release and not extra_in_release:
        logger.info("✅ release文件与metadata完全匹配")
    
    logger.info(f"\n统计:")
    logger.info(f"  metadata条目数: {len(metadata_list)}")
    logger.info(f"  预期release文件数: {len(expected_names)}")
    logger.info(f"  实际release文件数: {len(release_files)}")
    logger.info(f"  差异: {len(release_files) - len(expected_names)}")


def show_duplicate_details(metadata_list):
    """显示重复文件名的详细信息"""
    from collections import defaultdict
    
    filename_map = defaultdict(list)
    
    for item in metadata_list:
        artists = item.get('artists', [])
        title = item.get('title', '未知')
        if isinstance(artists, list):
            artist_str = ', '.join(artists)
        else:
            artist_str = str(artists)
        filename = f"{artist_str} - {title}.mp3"
        filename_map[filename].append(item)
    
    logger.info("\n" + "=" * 60)
    logger.info("重复文件名详细信息")
    logger.info("=" * 60)
    
    for filename, items in sorted(filename_map.items()):
        if len(items) > 1:
            logger.error(f"\n文件名: {filename}")
            logger.error(f"重复次数: {len(items)}")
            for idx, item in enumerate(items, 1):
                logger.error(f"  条目 {idx}:")
                logger.error(f"    audio_oid: {item.get('audio_oid')}")
                logger.error(f"    title: {item.get('title')}")
                logger.error(f"    artists: {item.get('artists')}")
                logger.error(f"    created_at: {item.get('created_at')}")
                logger.error(f"    updated_at: {item.get('updated_at')}")
                logger.error(f"    cover_oid: {item.get('cover_oid', '无')}")


def main():
    """主函数"""
    logger.info("开始分析 metadata.jsonl...")
    
    # 加载数据
    metadata_list = load_metadata()
    logger.info(f"加载到 {len(metadata_list)} 条 metadata")
    
    if not metadata_list:
        logger.error("没有数据可分析")
        return
    
    # 分析重复项
    duplicates = analyze_duplicates(metadata_list)
    
    # 分析孤立条目
    orphans = analyze_orphans(metadata_list)
    
    # 分析覆盖率
    analyze_coverage(metadata_list)
    
    # 显示重复文件名详情
    if duplicates['duplicates_filename']:
        show_duplicate_details(metadata_list)
    
    # 总结
    logger.info("\n" + "=" * 60)
    logger.info("分析总结")
    logger.info("=" * 60)
    
    total_issues = 0
    
    if duplicates['duplicates_audio']:
        total_issues += sum(duplicates['duplicates_audio'].values()) - len(duplicates['duplicates_audio'])
        logger.error(f"❌ audio_oid 重复: {len(duplicates['duplicates_audio'])} 组")
    
    if duplicates['duplicates_filename']:
        total_issues += sum(duplicates['duplicates_filename'].values()) - len(duplicates['duplicates_filename'])
        logger.error(f"❌ 文件名重复: {len(duplicates['duplicates_filename'])} 组")
    
    if orphans:
        logger.error(f"❌ 孤立条目: {len(orphans)} 个")
        total_issues += len(orphans)
    
    if total_issues == 0:
        logger.info("✅ 未发现明显问题")
        logger.info("可能原因:")
        logger.info("  - create_release_local.py 运行时指定了 --oid 参数")
        logger.info("  - 某些条目在生成时被跳过（如封面缺失）")
        logger.info("  - 文件名冲突导致覆盖")
    else:
        logger.error(f"\n发现 {total_issues} 个潜在问题")
        logger.info("\n建议操作:")
        logger.info("  1. 检查重复项并清理 metadata.jsonl")
        logger.info("  2. 运行 repo/data/cleanup_orphaned.py 清理孤立文件")
        logger.info("  3. 重新运行 create_release_local.py")


if __name__ == "__main__":
    main()
