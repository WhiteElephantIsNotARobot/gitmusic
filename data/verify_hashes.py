import sys
import os
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.hash_utils import HashUtils


def main():
    import argparse

    parser = argparse.ArgumentParser(description="校验文件哈希完整性")
    parser.add_argument(
        "--mode",
        choices=["local", "server", "release"],
        default="local",
        help="校验模式 (local|server|release)",
    )
    parser.add_argument(
        "--path",
        help="指定校验路径（可选）",
    )
    args = parser.parse_args()

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    if not cache_root_path:
        EventEmitter.error("Missing required environment variable GITMUSIC_CACHE_ROOT")
        return 1

    cache_root = Path(cache_root_path)

    # 初始化release模式数据
    release_mode_data = []

    if args.mode == "release":
        # release模式：比对release目录与metadata.jsonl
        release_dir_path = os.environ.get("GITMUSIC_RELEASE_DIR")
        metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")

        if not release_dir_path or not metadata_file_path:
            EventEmitter.error(
                "Missing environment variables for release mode",
                {
                    "release_dir": release_dir_path if release_dir_path else "missing",
                    "metadata_file": metadata_file_path
                    if metadata_file_path
                    else "missing",
                },
            )
            return 1

        # 导入MetadataManager和AudioIO
        from libgitmusic.metadata import MetadataManager
        from libgitmusic.audio import AudioIO

        release_dir = Path(release_dir_path)
        metadata_mgr = MetadataManager(Path(metadata_file_path))

        # 加载所有元数据条目
        all_entries = metadata_mgr.load_all()
        EventEmitter.log("info", f"Loaded {len(all_entries)} metadata entries")

        # 为每个条目生成预期文件名
        files_to_check = []
        for entry in all_entries:
            if not entry.get("audio_oid"):
                continue

            # 生成文件名
            raw_filename = f"{'/'.join(entry['artists'])} - {entry['title']}.mp3"
            filename = AudioIO.sanitize_filename(raw_filename)
            expected_path = release_dir / filename

            files_to_check.append(
                {
                    "path": expected_path,
                    "expected_oid": entry["audio_oid"],
                    "entry": entry,
                    "type": "audio",
                }
            )

        # 如果没有找到文件，返回错误
        if not files_to_check:
            EventEmitter.result(
                "ok", message="No release files to verify (metadata empty)"
            )
            return 0

        EventEmitter.log("info", f"Will verify {len(files_to_check)} release files")
        # 将文件列表转换为Path对象列表，用于统一的处理循环
        files = [item["path"] for item in files_to_check]
        release_mode_data = files_to_check  # 存储额外的数据供后续使用
    elif args.path:
        # 指定路径模式
        search_root = Path(args.path)
        files = list(search_root.rglob("*.mp3")) + list(search_root.rglob("*.jpg"))
    else:
        # 默认local模式：检查对象存储
        objects_dir = cache_root / "objects" / "sha256"
        covers_dir = cache_root / "covers" / "sha256"

        # 查找所有 mp3 和 jpg 文件
        files = list(objects_dir.rglob("*.mp3")) + list(covers_dir.rglob("*.jpg"))

        # 如果没有找到文件，尝试在 cache 根目录查找（向后兼容）
        if not files:
            files = list(cache_root.rglob("*.mp3")) + list(cache_root.rglob("*.jpg"))

    if not files:
        EventEmitter.result("ok", message="No files found to verify")
        return 0

    EventEmitter.phase_start("verify", total_items=len(files))

    errors = []
    for i, f in enumerate(files):
        # 根据模式确定预期的OID
        if args.mode == "release" and release_mode_data:
            # release模式：使用metadata中的expected_oid
            file_data = release_mode_data[i]
            expected_oid = file_data["expected_oid"]
            display_name = f.name
        else:
            # 其他模式：文件名是十六进制哈希值，需要转换为 sha256:hexdigest 格式
            hex_hash = f.stem
            expected_oid = f"sha256:{hex_hash}"
            display_name = f.name

        EventEmitter.item_event(display_name, "checking")

        # 使用 HashUtils 验证哈希
        if HashUtils.verify_hash(f, expected_oid):
            EventEmitter.item_event(display_name, "success")
        else:
            # 对于release模式，记录更多信息
            if args.mode == "release" and release_mode_data:
                entry_info = release_mode_data[i].get("entry", {})
                errors.append((display_name, expected_oid, entry_info))
            else:
                errors.append(
                    (
                        display_name,
                        expected_oid.split(":")[1]
                        if ":" in expected_oid
                        else expected_oid,
                        {},
                    )
                )
            # HashUtils.verify_hash 内部已经发送了 error 事件

        EventEmitter.batch_progress("verify", i + 1, len(files))

    if errors:
        # 构建条目列表供CLI显示
        entries = []
        for error_item in errors:
            if len(error_item) == 3:
                filename, hash_info, entry_info = error_item
                # 判断是release模式还是其他模式
                if entry_info:  # release模式，hash_info是完整的expected_oid
                    expected_hash = hash_info
                    # 从entry_info中提取更多信息
                    artist = ", ".join(entry_info.get("artists", []))
                    title = entry_info.get("title", "")
                    message = f"Hash mismatch for {artist} - {title}"
                else:
                    # 其他模式，hash_info是hex_hash
                    expected_hash = f"sha256:{hash_info}"
                    message = f"Hash mismatch for {filename}"
            else:
                # 向后兼容：二元组格式
                filename, hex_hash = error_item
                expected_hash = f"sha256:{hex_hash}"
                message = f"Hash mismatch for {filename}"
                entry_info = {}

            entries.append(
                {
                    "filename": filename,
                    "expected_hash": expected_hash,
                    "status": "hash_mismatch",
                    "message": message,
                    "entry": entry_info if entry_info else None,
                }
            )

        EventEmitter.result(
            "error",
            message=f"Verification failed for {len(errors)} files",
            artifacts={
                "failed_files": [x[0] for x in errors],  # 保持向后兼容
                "details": errors,  # 保持向后兼容
                "entries": entries,  # 新增，供CLI格式化显示
                "count": len(errors),
                "truncated": False,
            },
        )
        return 1
    else:
        EventEmitter.result("ok", message="All files verified successfully")
        return 0


if __name__ == "__main__":
    sys.exit(main())
