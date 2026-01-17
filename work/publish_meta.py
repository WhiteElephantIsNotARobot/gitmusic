import os
import json
import hashlib
import sys
from pathlib import Path
from datetime import datetime

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.audio import AudioIO
from libgitmusic.metadata import MetadataManager

def main():
    # 从环境变量获取路径
    work_dir_path = os.environ.get("GITMUSIC_WORK_DIR")
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")

    if not all([work_dir_path, cache_root_path, metadata_file_path]):
        EventEmitter.error("Missing required environment variables")
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    work_dir = Path(work_dir_path)
    cache_root = Path(cache_root_path)

    existing_entries = {e['audio_oid']: e for e in metadata_mgr.load_all()}
    files = list(work_dir.glob("*.mp3"))
    EventEmitter.phase_start("analyze", total_items=len(files))

    for i, f in enumerate(files):
        name = f.stem
        artists = [a.strip() for a in name.split(" - ", 1)[0].split("/")] if " - " in name else ["Unknown"]
        title = name.split(" - ", 1)[1] if " - " in name else name

        audio_oid = AudioIO.get_audio_hash(f)
        existing = existing_entries.get(audio_oid)

        diff = {}
        if not existing:
            diff = {"status": "new", "title": title, "artists": artists}
        else:
            if existing.get('title') != title: diff['title'] = f"{existing.get('title')} -> {title}"
            if set(existing.get('artists', [])) != set(artists): diff['artists'] = f"{existing.get('artists')} -> {artists}"

        if diff:
            EventEmitter.item_event(f.name, "diff", message=json.dumps(diff, ensure_ascii=False))

        EventEmitter.batch_progress("analyze", i + 1, len(files))

    EventEmitter.result("ok", message="Analysis completed")

if __name__ == "__main__":
    main()
