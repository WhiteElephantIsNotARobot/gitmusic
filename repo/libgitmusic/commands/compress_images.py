from pathlib import Path
from typing import List, Dict, Tuple, Optional

from ..events import EventEmitter
from ..audio import AudioIO
from ..metadata import MetadataManager
from ..object_store import ObjectStore
from ..hash_utils import HashUtils


def compress_images_logic(
    metadata_mgr: MetadataManager,
    object_store: ObjectStore,
    min_size_kb: int = 500,
) -> Tuple[List[Dict], Optional[str]]:
    """
    Compress images命令的核心业务逻辑

    Args:
        metadata_mgr: 元数据管理器
        object_store: 对象存储
        min_size_kb: 最小文件大小（KB）

    Returns:
        (需要压缩的条目列表, 错误消息)
    """
    entries = metadata_mgr.load_all()
    EventEmitter.phase_start("compress_images", total_items=len(entries))

    entries_to_compress = []

    for i, entry in enumerate(entries):
        if not entry.get("cover_oid"):
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        cover_oid = entry["cover_oid"]
        cover_path = object_store.get_cover_path(cover_oid)

        if not cover_path or not cover_path.exists():
            EventEmitter.log(
                "warn",
                f"Cover file not found for OID: {cover_oid}, entry: {entry.get('title', 'unknown')}",
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        # 检查文件大小是否超过阈值
        file_size_kb = cover_path.stat().st_size / 1024
        if file_size_kb < min_size_kb:
            EventEmitter.item_event(
                cover_path.name,
                "skipped",
                f"Size: {file_size_kb:.1f}KB < {min_size_kb}KB",
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        # 读取原始封面数据
        try:
            with open(cover_path, "rb") as f:
                original_data = f.read()
        except Exception as e:
            EventEmitter.error(
                f"Failed to read cover file: {str(e)}", {"path": str(cover_path)}
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        entries_to_compress.append(
            {
                "entry": entry,
                "cover_path": cover_path,
                "original_data": original_data,
                "original_oid": cover_oid,
            }
        )

        EventEmitter.batch_progress("compress_images", i + 1, len(entries))

    return entries_to_compress, None


def execute_compress_images(
    entries_to_compress: List[Dict],
    metadata_mgr: MetadataManager,
    object_store: ObjectStore,
    progress_callback=None,
) -> Tuple[int, int]:
    """
    执行压缩动作

    Args:
        entries_to_compress: 需要压缩的条目列表
        metadata_mgr: 元数据管理器
        object_store: 对象存储
        progress_callback: 进度回调函数

    Returns:
        (成功压缩数, 总数)
    """
    if not entries_to_compress:
        EventEmitter.result("ok", message="No images needed compression")
        return 0, 0

    EventEmitter.phase_start("compress_execute", total_items=len(entries_to_compress))

    updated_count = 0
    for i, item in enumerate(entries_to_compress):
        entry = item["entry"]
        cover_path = item["cover_path"]
        original_data = item["original_data"]
        original_oid = item["original_oid"]

        EventEmitter.item_event(
            cover_path.name, "compressing", f"Original: {len(original_data)} bytes"
        )

        # 压缩封面（使用默认参数：质量85，最大宽度800）
        try:
            compressed_data = AudioIO.compress_cover(
                original_data,
                max_width=800,
                quality=85,
            )
        except Exception as e:
            EventEmitter.error(
                f"Failed to compress cover: {str(e)}", {"path": str(cover_path)}
            )
            EventEmitter.batch_progress(
                "compress_execute", i + 1, len(entries_to_compress)
            )
            continue

        # 检查压缩是否有效（大小减少）
        if len(compressed_data) >= len(original_data):
            EventEmitter.item_event(
                cover_path.name,
                "skipped",
                f"No size reduction: {len(compressed_data)} >= {len(original_data)} bytes",
            )
            EventEmitter.batch_progress(
                "compress_execute", i + 1, len(entries_to_compress)
            )
            continue

        # 计算新哈希
        original_hex = (
            original_oid.split(":")[1] if ":" in original_oid else original_oid
        )
        new_oid = HashUtils.hash_bytes(compressed_data, "sha256")

        # 如果哈希相同，跳过（压缩未改变内容）
        if new_oid == original_oid:
            EventEmitter.item_event(
                cover_path.name, "skipped", "Hash unchanged after compression"
            )
            EventEmitter.batch_progress(
                "compress_execute", i + 1, len(entries_to_compress)
            )
            continue

        # 存储新封面
        try:
            new_cover_oid = object_store.store_cover(
                compressed_data, compute_hash=False
            )

            # 验证存储成功
            if new_cover_oid != new_oid:
                EventEmitter.error(
                    f"Hash mismatch after storage: expected {new_oid}, got {new_cover_oid}",
                    {
                        "original_oid": original_oid,
                        "new_oid": new_oid,
                        "stored_oid": new_cover_oid,
                    },
                )
                EventEmitter.batch_progress(
                    "compress_execute", i + 1, len(entries_to_compress)
                )
                continue

            # 更新元数据条目
            entry["cover_oid"] = new_cover_oid
            updated_count += 1

            size_reduction = 100 * (1 - len(compressed_data) / len(original_data))
            EventEmitter.item_event(
                cover_path.name,
                "success",
                f"Compressed: {len(original_data)} → {len(compressed_data)} bytes ({size_reduction:.1f}% saved)",
            )

        except Exception as e:
            EventEmitter.error(
                f"Failed to store compressed cover: {str(e)}",
                {"original_oid": original_oid},
            )
            EventEmitter.batch_progress(
                "compress_execute", i + 1, len(entries_to_compress)
            )
            continue

        EventEmitter.batch_progress("compress_execute", i + 1, len(entries_to_compress))

        if progress_callback:
            progress_callback(i + 1, len(entries_to_compress))

    # 保存更新的元数据
    if updated_count > 0:
        metadata_mgr.save_all([item["entry"] for item in entries_to_compress])

    return updated_count, len(entries_to_compress)
