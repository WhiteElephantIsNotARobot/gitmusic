from pathlib import Path
from ..audio import AudioIO

def checkout_logic(metadata_mgr, query="", missing_fields=None, limit=0):
    """Checkout 命令的过滤逻辑"""
    all_entries = metadata_mgr.load_all()
    to_checkout = []
    missing_fields = missing_fields or []

    for entry in all_entries:
        if query:
            if query.lower() not in entry.get('title', '').lower() and \
               query not in entry.get('audio_oid', '') and \
               not any(query.lower() in a.lower() for a in entry.get('artists', [])):
                continue

        if missing_fields:
            is_missing = False
            for field in missing_fields:
                val = entry.get(field)
                if not val or (isinstance(val, list) and not val):
                    is_missing = True
                    break
            if not is_missing:
                continue

        to_checkout.append(entry)

    if limit > 0:
        to_checkout = to_checkout[:limit]

    return to_checkout

def execute_checkout(repo_root, items, force=False, progress_callback=None):
    """执行检出动作"""
    work_dir = repo_root.parent / "work"
    cache_root = repo_root.parent / "cache"
    results = []

    for entry in items:
        raw_filename = f"{'/'.join(entry['artists'])} - {entry['title']}.mp3"
        filename = AudioIO.sanitize_filename(raw_filename)
        out_path = work_dir / filename

        if out_path.exists() and not force:
            results.append((filename, "skipped"))
            continue

        if progress_callback:
            progress_callback(filename)

        audio_hash = entry['audio_oid'].split(":")[1]
        src_audio = cache_root / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"

        cover_data = None
        if entry.get('cover_oid'):
            cover_hash = entry['cover_oid'].split(":")[1]
            cover_path = cache_root / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
            if cover_path.exists():
                with open(cover_path, 'rb') as f:
                    cover_data = f.read()

        if src_audio.exists():
            AudioIO.embed_metadata(src_audio, entry, cover_data, out_path)
            results.append((filename, "success"))
        else:
            results.append((filename, "error: source missing"))

    return results
