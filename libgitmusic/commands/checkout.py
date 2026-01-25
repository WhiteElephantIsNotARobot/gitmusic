from pathlib import Path
from ..audio import AudioIO


def checkout_logic(
    metadata_mgr, query="", missing_fields=None, limit=0, search_field=None, line=None
):
    """Checkout 命令的过滤逻辑"""
    all_entries = metadata_mgr.load_all()
    to_checkout = []
    missing_fields = missing_fields or []

    # 按行号过滤
    if line:
        line_nums = set()
        for part in str(line).split(","):
            part = part.strip()
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                try:
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    line_nums.update(range(start, end + 1))
                except ValueError:
                    return []  # 无效的行号范围，返回空列表
            else:
                try:
                    line_nums.add(int(part.strip()))
                except ValueError:
                    return []  # 无效的行号，返回空列表

        # 过滤条目
        filtered_by_line = []
        for idx, entry in enumerate(all_entries, 1):
            if idx in line_nums:
                filtered_by_line.append(entry)
        all_entries = filtered_by_line

    for entry in all_entries:
        if query:
            if search_field:
                # 仅在指定字段搜索
                field_value = entry.get(search_field, "")
                if isinstance(field_value, list):
                    field_value = ", ".join(field_value)
                if query.lower() not in str(field_value).lower():
                    continue
            else:
                # 在多个字段中搜索
                if (
                    query.lower() not in entry.get("title", "").lower()
                    and query not in entry.get("audio_oid", "")
                    and not any(
                        query.lower() in a.lower() for a in entry.get("artists", [])
                    )
                ):
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

        audio_hash = entry["audio_oid"].split(":")[1]
        src_audio = (
            cache_root / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"
        )

        cover_data = None
        if entry.get("cover_oid"):
            cover_hash = entry["cover_oid"].split(":")[1]
            cover_path = (
                cache_root / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
            )
            if cover_path.exists():
                with open(cover_path, "rb") as f:
                    cover_data = f.read()

        if src_audio.exists():
            AudioIO.embed_metadata(src_audio, entry, cover_data, out_path)
            results.append((filename, "success"))
        else:
            results.append((filename, "error: source missing"))

    return results
