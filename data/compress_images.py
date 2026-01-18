import os
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager
from libgitmusic.audio import AudioIO
from libgitmusic.object_store import ObjectStore


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quality",
        type=int,
        default=85,
        help="JPEG压缩质量 (1-100, 越高质量越好)",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=800,
        help="最大宽度（保持宽高比）",
    )
    parser.add_argument(
        "--min-size-kb",
        type=int,
        default=500,
        help="最小文件大小（KB），低于此值的图片不压缩",
    )
    args = parser.parse_args()

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")

    if not cache_root_path or not metadata_file_path:
        EventEmitter.error(
            "Missing required environment variables (GITMUSIC_CACHE_ROOT, GITMUSIC_METADATA_FILE)"
        )
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    cache_root = Path(cache_root_path)
    object_store = ObjectStore(cache_root)

    entries = metadata_mgr.load_all()
    EventEmitter.phase_start("compress_images", total_items=len(entries))

    updated_count = 0
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
        if file_size_kb < args.min_size_kb:
            EventEmitter.item_event(
                cover_path.name,
                "skipped",
                f"Size: {file_size_kb:.1f}KB < {args.min_size_kb}KB",
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

        EventEmitter.item_event(
            cover_path.name, "compressing", f"Original: {len(original_data)} bytes"
        )

        # 压缩封面
        try:
            compressed_data = AudioIO.compress_cover(
                original_data,
                max_width=args.max_width,
                quality=args.quality,
            )
        except Exception as e:
            EventEmitter.error(
                f"Failed to compress cover: {str(e)}", {"path": str(cover_path)}
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        # 检查压缩是否有效（大小减少）
        if len(compressed_data) >= len(original_data):
            EventEmitter.item_event(
                cover_path.name,
                "skipped",
                f"No size reduction: {len(compressed_data)} >= {len(original_data)} bytes",
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        # 计算新哈希
        from libgitmusic.hash_utils import HashUtils

        original_hex = cover_oid.split(":")[1] if ":" in cover_oid else cover_oid
        new_oid = HashUtils.hash_bytes(compressed_data, "sha256")

        # 如果哈希相同，跳过（压缩未改变内容）
        if new_oid == cover_oid:
            EventEmitter.item_event(
                cover_path.name, "skipped", "Hash unchanged after compression"
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
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
                        "original_oid": cover_oid,
                        "new_oid": new_oid,
                        "stored_oid": new_cover_oid,
                    },
                )
                EventEmitter.batch_progress("compress_images", i + 1, len(entries))
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
                {"original_oid": cover_oid},
            )
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        EventEmitter.batch_progress("compress_images", i + 1, len(entries))

    if updated_count > 0:
        metadata_mgr.save_all(entries)
        EventEmitter.result(
            "ok",
            message=f"Compressed {updated_count} images",
            artifacts={"updated_count": updated_count, "total_entries": len(entries)},
        )
    else:
        EventEmitter.result("ok", message="No images needed compression")


if __name__ == "__main__":
    main()
