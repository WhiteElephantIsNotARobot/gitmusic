from pathlib import Path
from typing import Dict, List, Optional, Tuple
from ..events import EventEmitter
from ..hash_utils import HashUtils
from ..metadata import MetadataManager
from ..audio import AudioIO


def verify_local_cache(
    cache_root: Path, audio_oids: Optional[List[str]] = None
) -> Tuple[List, int]:
    """
    验证本地缓存文件哈希完整性

    Args:
        cache_root: 本地缓存根目录
        audio_oids: 可选，指定要校验的音频对象ID列表（如["sha256:abc123"]）

    Returns:
        (错误列表, 验证文件数)
    """
    objects_dir = cache_root / "objects" / "sha256"
    covers_dir = cache_root / "covers" / "sha256"

    # 如果指定了audio_oids，只校验这些音频对象
    if audio_oids:
        # 提取哈希部分
        target_hashes = set()
        for oid in audio_oids:
            if oid.startswith("sha256:"):
                target_hashes.add(oid[7:])  # 去掉sha256:前缀
            else:
                target_hashes.add(oid)

        # 只查找匹配的音频文件
        files = []
        for hex_hash in target_hashes:
            # 构建文件路径: objects/sha256/aa/oid.mp3
            subdir = hex_hash[:2]
            mp3_path = objects_dir / subdir / f"{hex_hash}.mp3"
            if mp3_path.exists():
                files.append(mp3_path)
            else:
                EventEmitter.log("warn", f"音频对象不存在: {hex_hash}")
    else:
        # 查找所有 mp3 和 jpg 文件
        files = list(objects_dir.rglob("*.mp3")) + list(covers_dir.rglob("*.jpg"))

        # 如果没有找到文件，尝试在 cache 根目录查找（向后兼容）
        if not files:
            files = list(cache_root.rglob("*.mp3")) + list(cache_root.rglob("*.jpg"))

    errors = []
    for i, f in enumerate(files):
        hex_hash = f.stem
        expected_oid = f"sha256:{hex_hash}"
        display_name = f.name

        EventEmitter.item_event(display_name, "checking")

        if HashUtils.verify_hash(f, expected_oid):
            EventEmitter.item_event(display_name, "success")
        else:
            errors.append((display_name, hex_hash, {}))

        EventEmitter.batch_progress("verify", i + 1, len(files))

    return errors, len(files)


def verify_release_files(
    release_dir: Path, metadata_mgr: MetadataManager
) -> Tuple[List, int]:
    """
    验证发布目录文件与元数据的一致性

    Args:
        release_dir: 发布目录
        metadata_mgr: 元数据管理器

    Returns:
        (错误列表, 验证文件数)
    """
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

    if not files_to_check:
        return [], 0

    EventEmitter.log("info", f"Will verify {len(files_to_check)} release files")

    errors = []
    for i, file_data in enumerate(files_to_check):
        f = file_data["path"]
        expected_oid = file_data["expected_oid"]
        display_name = f.name

        EventEmitter.item_event(display_name, "checking")

        if HashUtils.verify_hash(f, expected_oid):
            EventEmitter.item_event(display_name, "success")
        else:
            errors.append((display_name, expected_oid, file_data["entry"]))

        EventEmitter.batch_progress("verify", i + 1, len(files_to_check))

    return errors, len(files_to_check)


def verify_custom_path(search_root: Path) -> Tuple[List, int]:
    """
    验证指定路径下的文件

    Args:
        search_root: 搜索根目录

    Returns:
        (错误列表, 验证文件数)
    """
    files = list(search_root.rglob("*.mp3")) + list(search_root.rglob("*.jpg"))

    errors = []
    for i, f in enumerate(files):
        hex_hash = f.stem
        expected_oid = f"sha256:{hex_hash}"
        display_name = f.name

        EventEmitter.item_event(display_name, "checking")

        if HashUtils.verify_hash(f, expected_oid):
            EventEmitter.item_event(display_name, "success")
        else:
            errors.append((display_name, hex_hash, {}))

        EventEmitter.batch_progress("verify", i + 1, len(files))

    return errors, len(files)


def verify_logic(
    cache_root: Path,
    metadata_file: Path,
    mode: str = "local",
    custom_path: Optional[Path] = None,
    release_dir: Optional[Path] = None,
    audio_oids: Optional[List[str]] = None,
) -> int:
    """
    Verify 命令的核心业务逻辑

    Args:
        cache_root: 本地缓存根目录
        metadata_file: 元数据文件路径
        mode: 校验模式 (local, server, release)
        custom_path: 自定义校验路径
        release_dir: 发布目录路径（release模式必需）
        audio_oids: 可选，指定要校验的音频对象ID列表

    Returns:
        退出码 (0=成功, 1=失败)
    """
    if mode == "release":
        if not release_dir:
            EventEmitter.error("Release模式需要指定release_dir参数")
            return 1

        metadata_mgr = MetadataManager(metadata_file)
        errors, total = verify_release_files(release_dir, metadata_mgr)

    elif custom_path:
        errors, total = verify_custom_path(custom_path)

    else:
        # 默认local模式
        errors, total = verify_local_cache(cache_root, audio_oids)

    if total == 0:
        EventEmitter.result("ok", message="No files found to verify")
        return 0

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
