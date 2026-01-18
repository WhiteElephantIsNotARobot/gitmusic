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
    import argparse

    parser = argparse.ArgumentParser(
        description="检出文件到 work 目录（支持批量和条件检出）"
    )

    # 基本检出模式
    parser.add_argument("identifier", nargs="?", help="audio_oid 或标题模式（可选）")

    # 批量检出模式
    parser.add_argument("--batch", action="store_true", help="批量检出模式")
    parser.add_argument(
        "--missing",
        nargs="+",
        choices=["cover", "uslt", "album", "date"],
        help="检出缺少指定字段的条目（如 --missing cover uslt）",
    )
    parser.add_argument("--pattern", help="批量检出匹配标题模式的条目")
    parser.add_argument("--max", type=int, help="最大检出数量限制")

    # 强制覆盖选项
    parser.add_argument(
        "-f", "--force", action="store_true", help="强制覆盖已存在的文件"
    )

    args = parser.parse_args()

    # 从环境变量获取路径
    work_dir_path = os.environ.get("GITMUSIC_WORK_DIR")
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")

    if not work_dir_path or not cache_root_path or not metadata_file_path:
        EventEmitter.error(
            "Missing required environment variables (GITMUSIC_WORK_DIR, GITMUSIC_CACHE_ROOT, GITMUSIC_METADATA_FILE)"
        )
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    work_dir = Path(work_dir_path)
    cache_root = Path(cache_root_path)

    # 导入 ObjectStore
    from libgitmusic.object_store import ObjectStore

    object_store = ObjectStore(cache_root)
    all_entries = metadata_mgr.load_all()

    # 确定要检出的条目
    to_checkout = []

    if args.batch:
        # 批量模式
        if args.missing:
            # 按缺失字段检出
            field_map = {
                "cover": "cover_oid",
                "uslt": "uslt",
                "album": "album",
                "date": "date",
            }
            for entry in all_entries:
                has_missing = False
                for field in args.missing:
                    if field in field_map:
                        if field_map[field] not in entry:
                            has_missing = True
                if has_missing:
                    to_checkout.append(entry)

        elif args.pattern:
            # 按模式检出
            pattern = args.pattern.lower()
            for entry in all_entries:
                title = entry.get("title", "").lower()
                if pattern in title:
                    to_checkout.append(entry)
        else:
            EventEmitter.error("批量模式需要指定 --missing 或 --pattern")
            return
    else:
        # 单个检出模式
        identifier = args.identifier
        if not identifier:
            EventEmitter.error("请指定检出方式")
            EventEmitter.error("  单个检出: python checkout.py <oid|标题>")
            EventEmitter.error(
                "  批量检出: python checkout.py --batch --missing cover uslt"
            )
            EventEmitter.error(
                "  模式检出: python checkout.py --batch --pattern '关键词'"
            )
            return

        # 查找匹配条目
        for entry in all_entries:
            if (
                identifier.lower() in entry.get("title", "").lower()
                or identifier in entry.get("audio_oid", "")
                or any(
                    identifier.lower() in artist.lower()
                    for artist in entry.get("artists", [])
                )
            ):
                to_checkout.append(entry)

    # 限制数量
    if args.max and args.max > 0:
        to_checkout = to_checkout[: args.max]

    if not to_checkout:
        EventEmitter.result("error", message="未找到匹配的条目")
        return

    # 冲突检测：检查work目录中是否已存在目标文件
    conflicts = []
    for entry in to_checkout:
        raw_filename = f"{'/'.join(entry['artists'])} - {entry['title']}.mp3"
        filename = AudioIO.sanitize_filename(raw_filename)
        out_path = work_dir / filename
        if out_path.exists():
            conflicts.append(
                {"filename": filename, "path": str(out_path), "entry": entry}
            )

    # 如果存在冲突且没有--force选项，返回冲突信息
    if conflicts and not args.force:
        EventEmitter.result(
            "conflict",
            message=f"发现 {len(conflicts)} 个文件冲突（使用 -f 强制覆盖）",
            artifacts={
                "conflicts": conflicts,
                "total_conflicts": len(conflicts),
                "suggestion": "运行 publish --preview 查看未处理的改动",
            },
        )
        return

    EventEmitter.phase_start("checkout", total_items=len(to_checkout))

    success_count = 0
    for i, entry in enumerate(to_checkout):
        # 生成文件名
        raw_filename = f"{'/'.join(entry['artists'])} - {entry['title']}.mp3"
        filename = AudioIO.sanitize_filename(raw_filename)
        out_path = work_dir / filename

        EventEmitter.item_event(filename, "processing")

        try:
            # 使用 ObjectStore.copy_to_workdir
            object_store.copy_to_workdir(
                entry["audio_oid"], out_path, entry, entry.get("cover_oid")
            )
            success_count += 1
            EventEmitter.item_event(filename, "success")
        except Exception as e:
            EventEmitter.error(f"检出失败 {filename}: {str(e)}", {"entry": entry})

        EventEmitter.batch_progress("checkout", i + 1, len(to_checkout))

    EventEmitter.result(
        "ok", message=f"成功检出 {success_count}/{len(to_checkout)} 个文件"
    )


if __name__ == "__main__":
    main()
