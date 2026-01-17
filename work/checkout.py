import os
import json
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.audio import AudioIO
from libgitmusic.metadata import MetadataManager

def main():
    # 解析命令行参数 (简化版，实际会更复杂)
    query = sys.argv[1] if len(sys.argv) > 1 else ""

    repo_root = Path(__file__).parent.parent
    metadata_mgr = MetadataManager(repo_root / "metadata.jsonl")
    work_dir = repo_root.parent / "work"
    cache_root = repo_root.parent / "cache"

    all_entries = metadata_mgr.load_all()

    # 过滤逻辑
    to_checkout = []
    for entry in all_entries:
        if query.lower() in entry.get('title', '').lower() or query in entry.get('audio_oid', ''):
            to_checkout.append(entry)

    EventEmitter.phase_start("checkout", total_items=len(to_checkout))

    for i, entry in enumerate(to_checkout):
        raw_filename = f"{'/'.join(entry['artists'])} - {entry['title']}.mp3"
        filename = AudioIO.sanitize_filename(raw_filename)
        out_path = work_dir / filename

        EventEmitter.item_event(filename, "processing")

        # 查找音频和封面
        audio_hash = entry['audio_oid'].split(":")[1]
        src_audio = cache_root / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"

        cover_data = None
        if entry.get('cover_oid'):
            cover_hash = entry['cover_oid'].split(":")[1]
            cover_path = cache_root / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
            if cover_path.exists():
                with open(cover_path, 'rb') as f:
                    cover_data = f.read()

        if src_audio.exists():
            AudioIO.embed_metadata(src_audio, entry, cover_data, out_path)
            EventEmitter.item_event(filename, "success")
        else:
            EventEmitter.error(f"Source missing for {entry['audio_oid']}")

        EventEmitter.batch_progress("checkout", i + 1, len(to_checkout))

    EventEmitter.result("ok", message=f"Checked out {len(to_checkout)} files")

if __name__ == "__main__":
    main()
def get_work_filename(metadata):
    """生成工作目录文件名：艺术家 - 标题.mp3"""
    artists = metadata.get('artists', [])
    title = metadata.get('title', '未知')

    if artists:
        artist_str = ', '.join(artists)
        filename = f"{artist_str} - {title}.mp3"
    else:
        filename = f"{title}.mp3"

    # 彻底移除空字符并清理非法路径字符，防止产生意外的子目录
    filename = filename.replace('\x00', '')
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    return filename.strip()


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

    # 嵌入标签并保存
    logger.info(f"嵌入标签并保存到: {dest_path.name}")
    if embed_tags(audio_path, cover_path, target_metadata, dest_path):
        logger.info(f"✓ 检出成功: {dest_path.name}")
        return True
    else:
        return False


def checkout_by_title(title_pattern, work_dir, cache_root):
    """按标题模式检出（单个）"""
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


def find_missing_fields(metadata):
    """检查条目缺少哪些字段"""
    missing = []
    if 'cover_oid' not in metadata:
        missing.append('cover')
    if 'uslt' not in metadata:
        missing.append('uslt')
    if 'album' not in metadata:
        missing.append('album')
    if 'date' not in metadata:
        missing.append('date')
    return missing


def filter_metadata_by_missing(metadata_list, missing_fields):
    """过滤出缺少指定字段的条目"""
    field_map = {
        'cover': 'cover_oid',
        'uslt': 'uslt',
        'album': 'album',
        'date': 'date'
    }

    filtered = []
    for item in metadata_list:
        has_missing = False
        for field in missing_fields:
            if field in field_map:
                if field_map[field] not in item:
                    has_missing = True
        if has_missing:
            filtered.append(item)
    return filtered


def batch_checkout_by_missing(missing_fields, work_dir, cache_root, max_count=None):
    """批量检出缺少指定字段的条目"""
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"
    if not metadata_file.exists():
        logger.error("metadata.jsonl 不存在")
        return 0

    # 加载所有 metadata
    metadata_list = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metadata_list.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # 过滤出缺少字段的条目
    filtered = filter_metadata_by_missing(metadata_list, missing_fields)

    if max_count:
        filtered = filtered[:max_count]

    if not filtered:
        logger.info(f"没有发现缺少 {', '.join(missing_fields)} 的条目")
        return 0

    logger.info(f"找到 {len(filtered)} 个缺少 {', '.join(missing_fields)} 的条目")

    # 批量检出
    success_count = 0
    progress_mgr.set_progress(0, len(filtered), "检出中")

    for idx, item in enumerate(filtered, 1):
        # 只更新进度，不更新描述
        progress_mgr.set_progress(idx, len(filtered), "检出中")

        if checkout_by_oid(item['audio_oid'], work_dir, cache_root):
            success_count += 1

    progress_mgr.close()
    return success_count


def batch_checkout_by_pattern(title_pattern, work_dir, cache_root, max_count=None):
    """批量检出匹配标题模式的条目"""
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"
    if not metadata_file.exists():
        logger.error("metadata.jsonl 不存在")
        return 0

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
        return 0

    if max_count:
        matches = matches[:max_count]

    logger.info(f"找到 {len(matches)} 个匹配项")

    # 批量检出
    success_count = 0
    progress_mgr.set_progress(0, len(matches), "检出中")

    for idx, item in enumerate(matches, 1):
        # 只更新进度，不更新描述
        progress_mgr.set_progress(idx, len(matches), "检出中")

        if checkout_by_oid(item['audio_oid'], work_dir, cache_root):
            success_count += 1

    progress_mgr.close()
    return success_count


def main():
    import argparse

    parser = argparse.ArgumentParser(description='检出文件到 work 目录（支持批量和条件检出）')

    # 基本检出模式
    parser.add_argument('identifier', nargs='?', help='audio_oid 或标题模式（可选）')

    # 批量检出模式
    parser.add_argument('--batch', action='store_true', help='批量检出模式')
    parser.add_argument('--missing', nargs='+', choices=['cover', 'uslt', 'album', 'date'],
                       help='检出缺少指定字段的条目（如 --missing cover uslt）')
    parser.add_argument('--pattern', help='批量检出匹配标题模式的条目')
    parser.add_argument('--max', type=int, help='最大检出数量限制')

    # 其他选项
    parser.add_argument('--cache-root', default=str(Path(__file__).parent.parent.parent / 'cache'),
                       help='cache 根目录，默认 ../cache')

    args = parser.parse_args()

    # 确定目录
    repo_root = Path(__file__).parent.parent
    work_dir = repo_root.parent / "work"
    cache_root = Path(args.cache_root)

    if not work_dir.exists():
        work_dir.mkdir(parents=True, exist_ok=True)

    # 检查参数组合
    if not args.batch and not args.identifier:
        logger.error("请指定检出方式：")
        logger.error("  单个检出: python checkout.py <oid|标题>")
        logger.error("  批量检出: python checkout.py --batch --missing cover uslt")
        logger.error("  模式检出: python checkout.py --batch --pattern '关键词'")
        return

    # 执行检出
    success_count = 0
    total_count = 0

    if args.batch:
        # 批量模式
        if args.missing:
            # 按缺失字段检出
            success_count = batch_checkout_by_missing(args.missing, work_dir, cache_root, args.max)
            total_count = success_count
        elif args.pattern:
            # 按模式检出
            success_count = batch_checkout_by_pattern(args.pattern, work_dir, cache_root, args.max)
            total_count = success_count
        else:
            logger.error("批量模式需要指定 --missing 或 --pattern")
            return
    else:
        # 单个检出
        total_count = 1
        identifier = args.identifier
        if identifier.startswith('sha256:'):
            success = checkout_by_oid(identifier, work_dir, cache_root)
        else:
            success = checkout_by_title(identifier, work_dir, cache_root)
        success_count = 1 if success else 0

    # 结果汇总
    if success_count > 0:
        logger.info(f"\n✓ 检出完成！成功: {success_count}/{total_count}")
        logger.info(f"文件位于: {work_dir}")
        logger.info("编辑后可运行: python repo/work/publish_meta.py")
    else:
        logger.error(f"\n✗ 检出失败或未找到匹配项")


if __name__ == "__main__":
    main()
