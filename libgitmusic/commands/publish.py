import datetime
import hashlib
from pathlib import Path
from send2trash import send2trash
from ..audio import AudioIO
from ..events import EventEmitter

def publish_logic(metadata_mgr, repo_root, changed_only=False):
    """Publish 命令的核心业务逻辑"""
    work_dir = repo_root.parent / "work"
    files = list(work_dir.glob("*.mp3"))

    if not files:
        return [], "工作目录为空"

    existing_entries = {e['audio_oid']: e for e in metadata_mgr.load_all()}
    to_process = []

    for f in files:
        name = f.stem
        if " - " in name:
            artists_str, title = name.split(" - ", 1)
            artists = [a.strip() for a in artists_str.split("/")]
        else:
            artists = ["Unknown"]
            title = name

        audio_oid = AudioIO.get_audio_hash(f)
        existing = existing_entries.get(audio_oid)
        is_changed = False
        reason = ""

        if not existing:
            is_changed = True
            reason = "New File"
        else:
            if existing.get('title') != title or set(existing.get('artists', [])) != set(artists):
                is_changed = True
                reason = "Metadata Mismatch"

        if not changed_only or is_changed:
            to_process.append({
                "path": f,
                "audio_oid": audio_oid,
                "title": title,
                "artists": artists,
                "is_changed": is_changed,
                "reason": reason,
                "existing": existing
            })

    return to_process, None

def execute_publish(metadata_mgr, repo_root, items, progress_callback=None):
    """执行真正的发布动作"""
    for item in items:
        if progress_callback:
            progress_callback(item['path'].name)

        # 1. 封面
        cover_data = AudioIO.extract_cover(item['path'])
        cover_oid = None
        if cover_data:
            cover_hash = hashlib.sha256(cover_data).hexdigest()
            cover_oid = f"sha256:{cover_hash}"
            cover_path = repo_root.parent / "cache" / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
            AudioIO.atomic_write(cover_data, cover_path)

        # 2. 音频
        audio_hash = item['audio_oid'].split(":")[1]
        obj_path = repo_root.parent / "cache" / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"
        if not obj_path.exists():
            with open(item['path'], 'rb') as f:
                AudioIO.atomic_write(f.read(), obj_path)

        # 3. 元数据
        entry = item['existing'] or {
            "audio_oid": item['audio_oid'],
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        }
        entry.update({
            "cover_oid": cover_oid,
            "title": item['title'],
            "artists": item['artists']
        })
        metadata_mgr.update_entry(item['audio_oid'], entry)

        # 4. 清理
        send2trash(str(item['path']))
