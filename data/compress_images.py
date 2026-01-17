import os
import sys
import hashlib
import subprocess
import shutil
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager
from libgitmusic.audio import AudioIO

def compress_to_jpg(input_path, output_path, quality=2):
    """使用 ffmpeg 将图片压缩为 JPG（平衡画质与体积）"""
    cmd = [
        'ffmpeg', '-i', str(input_path),
        '-vf', "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
        '-q:v', str(quality),
        '-f', 'image2',
        '-y', str(output_path)
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--quality', type=int, default=2, help="FFmpeg 压缩质量 (1-31, 越小质量越高)")
    args = parser.parse_args()

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    quality = args.quality

    if not all([cache_root_path, metadata_file_path]):
        EventEmitter.error("Missing required environment variables (GITMUSIC_CACHE_ROOT, GITMUSIC_METADATA_FILE)")
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    cache_root = Path(cache_root_path)
    covers_root = cache_root / "covers" / "sha256"

    entries = metadata_mgr.load_all()
    EventEmitter.phase_start("compress_images", total_items=len(entries))

    updated_count = 0
    for i, entry in enumerate(entries):
        if not entry.get('cover_oid'):
            EventEmitter.batch_progress("compress_images", i + 1, len(entries))
            continue

        old_hash = entry['cover_oid'].split(":")[1]
        old_path = covers_root / old_hash[:2] / f"{old_hash}.jpg"

        if old_path.exists() and old_path.stat().st_size > 500 * 1024:
            EventEmitter.item_event(old_path.name, "compressing")
            temp_path = old_path.with_suffix('.tmp.jpg')

            if compress_to_jpg(old_path, temp_path, quality):
                new_hash = hashlib.sha256(temp_path.read_bytes()).hexdigest()
                if new_hash != old_hash:
                    new_dir = covers_root / new_hash[:2]
                    new_dir.mkdir(parents=True, exist_ok=True)
                    new_path = new_dir / f"{new_hash}.jpg"
                    shutil.move(str(temp_path), str(new_path))

                    entry['cover_oid'] = f"sha256:{new_hash}"
                    updated_count += 1
                    EventEmitter.item_event(old_path.name, "success", message=f"New OID: {new_hash[:12]}")
                else:
                    temp_path.unlink()

        EventEmitter.batch_progress("compress_images", i + 1, len(entries))

    if updated_count > 0:
        metadata_mgr.save_all(entries)
        EventEmitter.result("ok", message=f"Compressed {updated_count} images")
    else:
        EventEmitter.result("ok", message="No images needed compression")

if __name__ == "__main__":
    main()
