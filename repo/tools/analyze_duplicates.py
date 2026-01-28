import json
import sys
import os
from pathlib import Path
from collections import Counter, defaultdict

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager


def main():
    # 从环境变量获取元数据文件路径
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    if not metadata_file_path:
        EventEmitter.error(
            "Missing required environment variable GITMUSIC_METADATA_FILE"
        )
        return 1

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    items = metadata_mgr.load_all()
    EventEmitter.phase_start("analyze_duplicates")

    # 1. 按 audio_oid 统计
    audio_oid_counter = Counter(item.get("audio_oid") for item in items)
    duplicates_audio = {
        oid: count for oid, count in audio_oid_counter.items() if count > 1
    }

    # 2. 按文件名统计
    filename_counter = Counter()
    for item in items:
        artists = item.get("artists", [])
        title = item.get("title", "未知")
        filename = f"{', '.join(artists)} - {title}.mp3"
        filename_counter[filename] += 1

    duplicates_filename = {
        name: count for name, count in filename_counter.items() if count > 1
    }

    EventEmitter.result(
        "ok",
        message="Duplicate analysis completed",
        artifacts={
            "duplicates_audio": duplicates_audio,
            "duplicates_filename": duplicates_filename,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
