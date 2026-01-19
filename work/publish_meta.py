import os
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager
from libgitmusic.commands.publish import publish_logic, execute_publish


def main():
    import argparse

    parser = argparse.ArgumentParser(description="发布工作目录中的音频文件到库中")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只分析不执行，显示将要发布的文件",
    )
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="只发布有变化的文件（新文件或元数据改变）",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="发布后不清理工作目录的文件",
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
        sys.exit(1)

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    repo_root = Path(cache_root_path).parent  # 假设 cache 在 repo 根目录下
    work_dir = Path(work_dir_path)

    # 阶段 1: 扫描工作目录
    EventEmitter.log("info", "扫描工作目录中的音频文件")

    # 先统计文件数量用于进度显示
    files = list(work_dir.glob("*.mp3"))
    if not files:
        EventEmitter.result("ok", message="工作目录为空")
        return

    EventEmitter.phase_start("scan", total_items=len(files))

    try:
        # 修改publish_logic以支持进度回调
        def scan_progress_callback(processed, total):
            EventEmitter.batch_progress("scan", processed, total)

        items, error = publish_logic(
            metadata_mgr, repo_root, args.changed_only, scan_progress_callback
        )
        if error:
            EventEmitter.error(f"扫描失败: {error}")
            sys.exit(1)
    except Exception as e:
        EventEmitter.error(f"扫描过程中发生错误: {str(e)}")
        sys.exit(1)
    except Exception as e:
        EventEmitter.error(f"扫描过程中发生错误: {str(e)}")
        sys.exit(1)
    except Exception as e:
        EventEmitter.error(f"扫描过程中发生错误: {str(e)}")
        sys.exit(1)

    if not items:
        EventEmitter.result("ok", message="没有需要处理的文件")
        return

    # 统计信息
    total_files = len(items)
    new_files = sum(1 for item in items if not item.get("existing"))
    changed_files = sum(1 for item in items if item.get("is_changed", False))

    EventEmitter.item_event(
        "scan_results",
        "complete",
        message=f"找到 {total_files} 个文件 (新文件: {new_files}, 变更文件: {changed_files})",
    )

    # 显示将要处理的文件
    EventEmitter.phase_start("preview", total_items=len(items))
    for i, item in enumerate(items):
        status = (
            "new"
            if not item.get("existing")
            else "changed"
            if item.get("is_changed")
            else "unchanged"
        )
        reason = item.get("reason", "")

        # 构建字段变更信息
        field_changes = item.get("field_changes")
        field_change_str = ""
        if field_changes:
            changes = []
            for field, change in field_changes.items():
                old_val = change.get("old")
                new_val = change.get("new")
                if isinstance(old_val, list):
                    old_val = ", ".join(old_val)
                if isinstance(new_val, list):
                    new_val = ", ".join(new_val)
                if old_val is None:
                    changes.append(f"{field}: {new_val}")
                else:
                    changes.append(f"{field}: {old_val} -> {new_val}")
            if changes:
                field_change_str = " | " + " | ".join(changes)

        EventEmitter.item_event(
            item["path"].name,
            status,
            message=f"{reason} - 标题: {item['title']}, 艺术家: {', '.join(item['artists'])}{field_change_str}",
        )
        EventEmitter.batch_progress("preview", i + 1, len(items))

    # 如果是干跑模式，只显示预览
    if args.dry_run:
        artifacts = {
            "total_files": total_files,
            "new_files": new_files,
            "changed_files": changed_files,
            "files": [
                {
                    "path": str(item["path"]),
                    "title": item["title"],
                    "artists": item["artists"],
                    "status": "new"
                    if not item.get("existing")
                    else "changed"
                    if item.get("is_changed")
                    else "unchanged",
                    "reason": item.get("reason", ""),
                    "field_changes": item.get("field_changes"),
                }
                for item in items
            ],
        }
        EventEmitter.result(
            "ok",
            message=f"干跑模式: 将处理 {total_files} 个文件",
            artifacts=artifacts,
        )
        return

    # 阶段 2: 执行发布
    EventEmitter.phase_start("publish", total_items=len(items))
    published_count = 0
    errors = []

    def progress_callback(filename):
        EventEmitter.item_event(filename, "processing")

    try:
        # 临时修改 execute_publish 以支持不清理
        original_execute = execute_publish
        if args.no_cleanup:
            # 创建不清理的版本
            def execute_without_cleanup(
                metadata_mgr, repo_root, items, progress_callback=None
            ):
                for item in items:
                    if progress_callback:
                        progress_callback(item["path"].name)

                    # 1. 封面
                    from libgitmusic.audio import AudioIO
                    import hashlib

                    cover_data = AudioIO.extract_cover(item["path"])
                    cover_oid = None
                    if cover_data:
                        cover_hash = hashlib.sha256(cover_data).hexdigest()
                        cover_oid = f"sha256:{cover_hash}"
                        cover_path = (
                            repo_root.parent
                            / "cache"
                            / "covers"
                            / "sha256"
                            / cover_hash[:2]
                            / f"{cover_hash}.jpg"
                        )
                        AudioIO.atomic_write(cover_data, cover_path)

                    # 2. 音频
                    audio_hash = item["audio_oid"].split(":")[1]
                    obj_path = (
                        repo_root.parent
                        / "cache"
                        / "objects"
                        / "sha256"
                        / audio_hash[:2]
                        / f"{audio_hash}.mp3"
                    )
                    if not obj_path.exists():
                        with open(item["path"], "rb") as f:
                            AudioIO.atomic_write(f.read(), obj_path)

                    # 3. 元数据
                    import datetime

                    entry = item.get("existing") or {
                        "audio_oid": item["audio_oid"],
                        "created_at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                    entry.update(
                        {
                            "cover_oid": cover_oid,
                            "title": item["title"],
                            "artists": item["artists"],
                        }
                    )
                    metadata_mgr.update_entry(item["audio_oid"], entry)

                    # 不清理工作目录文件
                    EventEmitter.item_event(
                        item["path"].name, "published", "文件已发布但保留在工作目录"
                    )

            execute_publish_func = execute_without_cleanup
        else:
            execute_publish_func = original_execute

        # 执行发布
        execute_publish_func(metadata_mgr, repo_root, items, progress_callback)
        published_count = len(items)

    except Exception as e:
        EventEmitter.error(f"发布过程中发生错误: {str(e)}")
        errors.append(str(e))

    EventEmitter.phase_start("summary")
    EventEmitter.log("info", "发布完成")

    # 阶段 3: 验证元数据
    if published_count > 0:
        try:
            entry_count = len(metadata_mgr.load_all())
            EventEmitter.item_event(
                "metadata", "verified", f"元数据包含 {entry_count} 个条目"
            )
        except Exception as e:
            EventEmitter.error(f"验证元数据失败: {str(e)}")
            errors.append(f"元数据验证失败: {str(e)}")

    # 生成结果
    if errors:
        EventEmitter.result(
            "error",
            message=f"发布完成但有错误: 成功 {published_count}/{total_files}",
            artifacts={
                "published_count": published_count,
                "total_files": total_files,
                "errors": errors,
            },
        )
    else:
        EventEmitter.result(
            "ok",
            message=f"发布成功: {published_count} 个文件已发布",
            artifacts={
                "published_count": published_count,
                "total_files": total_files,
                "new_files": new_files,
                "changed_files": changed_files,
            },
        )


if __name__ == "__main__":
    main()
