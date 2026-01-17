import json
import sys
from pathlib import Path
from collections import Counter, defaultdict

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager

def main():
    repo_root = Path(__file__).parent.parent
    metadata_mgr = MetadataManager(repo_root / "metadata.jsonl")

    items = metadata_mgr.load_all()
    EventEmitter.phase_start("analyze_duplicates")

    # 1. 按 audio_oid 统计
    audio_oid_counter = Counter(item.get('audio_oid') for item in items)
    duplicates_audio = {oid: count for oid, count in audio_oid_counter.items() if count > 1}

    # 2. 按文件名统计
    filename_counter = Counter()
    for item in items:
        artists = item.get('artists', [])
        title = item.get('title', '未知')
        filename = f"{', '.join(artists)} - {title}.mp3"
        filename_counter[filename] += 1

    duplicates_filename = {name: count for name, count in filename_counter.items() if count > 1}

    EventEmitter.result("ok", message="Duplicate analysis completed", artifacts={
        "duplicates_audio": duplicates_audio,
        "duplicates_filename": duplicates_filename
    })

if __name__ == "__main__":
    main()


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
