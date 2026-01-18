import sys
import os
import subprocess
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.transport import TransportAdapter


def main():
    import argparse

    # 解析参数
    parser = argparse.ArgumentParser(description="清理孤立文件")
    parser.add_argument(
        "--mode",
        choices=["local", "server", "both"],
        default="local",
        help="清理模式 (local|server|both)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认执行删除操作（必须指定才会删除）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示分析结果（默认行为，显式指定）",
    )
    args = parser.parse_args()

    confirm = args.confirm
    mode = args.mode

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    remote_user = os.environ.get("GITMUSIC_REMOTE_USER", "white_elephant")
    remote_host = os.environ.get("GITMUSIC_REMOTE_HOST", "debian-server")
    remote_data_root = os.environ.get("GITMUSIC_REMOTE_DATA_ROOT", "/srv/music/data")

    if not cache_root_path or not metadata_file_path:
        EventEmitter.error(
            "Missing required environment variables (GITMUSIC_CACHE_ROOT, GITMUSIC_METADATA_FILE)"
        )
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    cache_root = Path(cache_root_path)
    object_store = ObjectStore(cache_root)

    # 1. 加载所有引用的 OID
    EventEmitter.item_event("metadata", "loading", "Loading metadata references")
    entries = metadata_mgr.load_all()
    referenced_oids = set()
    for e in entries:
        # 提取哈希部分（去除 sha256: 前缀）
        audio_oid = e.get("audio_oid", "")
        if audio_oid and ":" in audio_oid:
            referenced_oids.add(audio_oid.split(":", 1)[1])

        cover_oid = e.get("cover_oid", "")
        if cover_oid and ":" in cover_oid:
            referenced_oids.add(cover_oid.split(":", 1)[1])

    EventEmitter.log(
        "info", f"Found {len(referenced_oids)} referenced OIDs in metadata"
    )

    # 2. 扫描本地缓存目录
    orphaned_files = []
    if mode in ["local", "both"]:
        # 扫描音频对象目录
        for obj_file in object_store.objects_dir.rglob("*.mp3"):
            if obj_file.is_file():
                file_hash = obj_file.stem  # 文件名就是哈希值
                if file_hash not in referenced_oids:
                    orphaned_files.append(obj_file)

        # 扫描封面目录
        for cover_file in object_store.covers_dir.rglob("*.jpg"):
            if cover_file.is_file():
                file_hash = cover_file.stem  # 文件名就是哈希值
                if file_hash not in referenced_oids:
                    orphaned_files.append(cover_file)

    EventEmitter.item_event(
        "analysis",
        "done",
        message=f"Found {len(orphaned_files)} orphaned files locally",
    )

    # 3. 如果需要，扫描远端
    remote_orphaned = []
    if mode in ["remote", "both"]:
        EventEmitter.item_event("remote_scan", "starting")
        try:
            transport = TransportAdapter(remote_user, remote_host, remote_data_root)

            # 获取远端音频文件列表
            remote_audio_files = transport.list_remote_files("objects/sha256")
            for remote_file in remote_audio_files:
                if remote_file.endswith(".mp3"):
                    file_hash = Path(remote_file).stem
                    if file_hash not in referenced_oids:
                        remote_orphaned.append(("audio", remote_file))

            # 获取远端封面文件列表
            remote_cover_files = transport.list_remote_files("covers/sha256")
            for remote_file in remote_cover_files:
                if remote_file.endswith(".jpg"):
                    file_hash = Path(remote_file).stem
                    if file_hash not in referenced_oids:
                        remote_orphaned.append(("cover", remote_file))

            EventEmitter.item_event(
                "remote_scan",
                "done",
                message=f"Found {len(remote_orphaned)} orphaned files remotely",
            )
        except Exception as e:
            EventEmitter.error(f"Failed to scan remote files: {str(e)}")

    # 如果没有孤儿文件，返回
    total_orphaned = len(orphaned_files) + len(remote_orphaned)
    if total_orphaned == 0:
        EventEmitter.result("ok", message="No orphaned files found")
        return

    # 4. 显示统计信息
    if not confirm:
        # 构建条目列表供CLI显示
        entries = []
        for f in orphaned_files:
            entries.append(
                {
                    "type": "local",
                    "file": str(f),
                    "name": f.name,
                    "path": str(
                        f.relative_to(cache_root) if f.is_relative_to(cache_root) else f
                    ),
                }
            )
        for file_type, remote_file in remote_orphaned:
            entries.append(
                {
                    "type": "remote",
                    "file": remote_file,
                    "name": Path(remote_file).name,
                    "file_type": file_type,
                }
            )

        artifacts = {
            "local_orphaned_count": len(orphaned_files),
            "remote_orphaned_count": len(remote_orphaned),
            "total_orphaned_count": total_orphaned,
            "entries": entries,  # CLI会显示这个字段
        }
        if orphaned_files:
            artifacts["local_orphaned_files"] = [str(f) for f in orphaned_files]
        if remote_orphaned:
            artifacts["remote_orphaned_files"] = [f[1] for f in remote_orphaned]

        EventEmitter.result(
            "warn",
            message=f"Found {total_orphaned} orphaned files. Run with --confirm to delete.",
            artifacts=artifacts,
        )
        return

    # 5. 执行清理
    deleted_count = 0

    # 删除本地文件
    if orphaned_files:
        EventEmitter.phase_start(
            "cleanup_delete_local", total_items=len(orphaned_files)
        )
        for i, f in enumerate(orphaned_files):
            EventEmitter.item_event(f.name, "deleting_local")
            try:
                # 使用 send2trash 安全删除
                from send2trash import send2trash

                send2trash(str(f))
                deleted_count += 1
            except Exception as e:
                EventEmitter.error(f"Failed to delete local file {f}: {str(e)}")
            EventEmitter.batch_progress(
                "cleanup_delete_local", i + 1, len(orphaned_files)
            )

    # 删除远端文件
    if remote_orphaned:
        EventEmitter.phase_start(
            "cleanup_delete_remote", total_items=len(remote_orphaned)
        )
        for i, (file_type, remote_file) in enumerate(remote_orphaned):
            EventEmitter.item_event(Path(remote_file).name, "deleting_remote")
            try:
                # 构建完整远端路径
                if file_type == "audio":
                    full_path = f"{remote_data_root}/objects/sha256/{remote_file}"
                else:  # cover
                    full_path = f"{remote_data_root}/covers/sha256/{remote_file}"

                # 通过SSH删除
                subprocess.run(
                    ["ssh", f"{remote_user}@{remote_host}", f"rm -f {full_path}"],
                    check=True,
                    capture_output=True,
                )
                deleted_count += 1
            except subprocess.CalledProcessError as e:
                EventEmitter.error(
                    f"Failed to delete remote file {remote_file}: {e.stderr.decode() if e.stderr else str(e)}"
                )
            except Exception as e:
                EventEmitter.error(
                    f"Failed to delete remote file {remote_file}: {str(e)}"
                )

            EventEmitter.batch_progress(
                "cleanup_delete_remote", i + 1, len(remote_orphaned)
            )

    EventEmitter.result(
        "ok",
        message=f"Cleaned up {deleted_count} orphaned files",
        artifacts={
            "deleted_count": deleted_count,
            "local_deleted": len(orphaned_files),
            "remote_deleted": len(remote_orphaned),
        },
    )


if __name__ == "__main__":
    main()
