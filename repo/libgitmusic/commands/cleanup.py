import subprocess
from pathlib import Path
from typing import Dict, List, Tuple
from send2trash import send2trash

from ..events import EventEmitter
from ..metadata import MetadataManager
from ..object_store import ObjectStore
from ..transport import TransportAdapter


def analyze_orphaned_files(
    metadata_mgr: MetadataManager, object_store: ObjectStore, mode: str = "local"
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """
    分析孤立文件

    Args:
        metadata_mgr: 元数据管理器
        object_store: 对象存储
        mode: 分析模式 (local, server, both)

    Returns:
        (本地孤立文件列表, 远端孤立文件列表)
    """
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
    if mode in ["server", "both"]:
        EventEmitter.item_event("remote_scan", "starting")
        # 注意：此函数需要外部提供 transport 对象
        # 扫描远端逻辑需要在调用时提供
        pass

    return orphaned_files, remote_orphaned


def scan_remote_orphaned(
    transport: TransportAdapter, remote_data_root: str, referenced_oids: set
) -> List[Tuple[str, str]]:
    """
    扫描远端孤立文件

    Args:
        transport: 传输适配器
        remote_data_root: 远端数据根目录
        referenced_oids: 引用的OID集合

    Returns:
        远端孤立文件列表 (类型, 相对路径)
    """
    remote_orphaned = []
    try:
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

    return remote_orphaned


def delete_local_orphaned(orphaned_files: List[Path]) -> int:
    """
    删除本地孤立文件

    Args:
        orphaned_files: 本地孤立文件列表

    Returns:
        删除的文件数
    """
    if not orphaned_files:
        return 0

    EventEmitter.phase_start("cleanup_delete_local", total_items=len(orphaned_files))

    deleted_count = 0
    for i, f in enumerate(orphaned_files):
        EventEmitter.item_event(f.name, "deleting_local")
        try:
            send2trash(str(f))
            deleted_count += 1
        except Exception as e:
            EventEmitter.error(f"Failed to delete local file {f}: {str(e)}")
        EventEmitter.batch_progress("cleanup_delete_local", i + 1, len(orphaned_files))

    return deleted_count


def delete_remote_orphaned(
    remote_orphaned: List[Tuple[str, str]],
    remote_user: str,
    remote_host: str,
    remote_data_root: str,
) -> int:
    """
    删除远端孤立文件

    Args:
        remote_orphaned: 远端孤立文件列表
        remote_user: 远端用户名
        remote_host: 远端主机
        remote_data_root: 远端数据根目录

    Returns:
        删除的文件数
    """
    if not remote_orphaned:
        return 0

    EventEmitter.phase_start("cleanup_delete_remote", total_items=len(remote_orphaned))

    deleted_count = 0
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
            EventEmitter.error(f"Failed to delete remote file {remote_file}: {str(e)}")

        EventEmitter.batch_progress(
            "cleanup_delete_remote", i + 1, len(remote_orphaned)
        )

    return deleted_count


def cleanup_logic(
    metadata_file: Path,
    cache_root: Path,
    mode: str = "local",
    confirm: bool = False,
    dry_run: bool = False,
    remote_user: str = "",
    remote_host: str = "",
    remote_data_root: str = "",
) -> int:
    """
    Cleanup 命令的核心业务逻辑

    Args:
        metadata_file: 元数据文件路径
        cache_root: 缓存根目录
        mode: 清理模式 (local, server, both)
        confirm: 确认执行删除操作
        dry_run: 仅显示分析结果
        remote_user: 远端用户名
        remote_host: 远端主机
        remote_data_root: 远端数据根目录

    Returns:
        退出码 (0=成功, 1=错误)
    """
    metadata_mgr = MetadataManager(metadata_file)
    object_store = ObjectStore(cache_root)

    # 分析孤立文件
    orphaned_files, remote_orphaned = analyze_orphaned_files(
        metadata_mgr, object_store, mode
    )

    # 如果需要扫描远端
    if mode in ["server", "both"]:
        if not all([remote_user, remote_host, remote_data_root]):
            EventEmitter.error("服务器模式需要提供远程连接参数")
            return 1

        transport = TransportAdapter(remote_user, remote_host, remote_data_root)
        # 重新加载 referenced_oids（需要从metadata中提取）
        entries = metadata_mgr.load_all()
        referenced_oids = set()
        for e in entries:
            audio_oid = e.get("audio_oid", "")
            if audio_oid and ":" in audio_oid:
                referenced_oids.add(audio_oid.split(":", 1)[1])
            cover_oid = e.get("cover_oid", "")
            if cover_oid and ":" in cover_oid:
                referenced_oids.add(cover_oid.split(":", 1)[1])

        remote_orphaned = scan_remote_orphaned(
            transport, remote_data_root, referenced_oids
        )

    # 统计总数
    total_orphaned = len(orphaned_files) + len(remote_orphaned)

    # 如果没有孤儿文件，返回
    if total_orphaned == 0:
        EventEmitter.result("ok", message="No orphaned files found")
        return 0

    # 如果是dry-run或未确认，显示统计信息
    if dry_run or not confirm:
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
        return 0

    # 执行清理
    deleted_count = 0

    # 删除本地文件
    deleted_count += delete_local_orphaned(orphaned_files)

    # 删除远端文件
    if mode in ["server", "both"]:
        deleted_count += delete_remote_orphaned(
            remote_orphaned, remote_user, remote_host, remote_data_root
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
    return 0
