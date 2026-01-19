import datetime
import hashlib
from pathlib import Path
from send2trash import send2trash
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.id3._frames import TPE1, TIT2, TALB, TDRC, USLT, TXXX, APIC
from ..audio import AudioIO
from ..events import EventEmitter


def extract_metadata_from_file(audio_path: Path) -> dict:
    """从MP3文件读取ID3标签元数据"""
    metadata = {}

    try:
        audio = MP3(audio_path, ID3=ID3)

        if not audio.tags:
            EventEmitter.log("debug", f"文件没有ID3标签: {audio_path.name}")
            return _get_default_metadata(audio_path)

        # 标题 (TIT2)
        tit2_frames = audio.tags.getall("TIT2")
        if tit2_frames and tit2_frames[0].text:
            metadata["title"] = tit2_frames[0].text[0].strip()

        # 艺术家 (TPE1)
        tpe1_frames = audio.tags.getall("TPE1")
        if tpe1_frames and tpe1_frames[0].text:
            artists_text = tpe1_frames[0].text[0].strip()
            if artists_text:
                # 尝试多种分隔符：斜杠、逗号、分号
                separators = ["/", ",", ";", "&"]
                for sep in separators:
                    if sep in artists_text:
                        artists = [
                            a.strip() for a in artists_text.split(sep) if a.strip()
                        ]
                        if artists:  # 确保分割后不为空
                            metadata["artists"] = artists
                            break
                else:
                    # 没有分隔符，直接作为单个艺术家
                    metadata["artists"] = [artists_text]

        # 专辑 (TALB)
        talb_frames = audio.tags.getall("TALB")
        if talb_frames and talb_frames[0].text:
            metadata["album"] = talb_frames[0].text[0].strip()

        # 日期 (TDRC)
        tdrc_frames = audio.tags.getall("TDRC")
        if tdrc_frames and tdrc_frames[0].text:
            metadata["date"] = tdrc_frames[0].text[0].strip()

        # 歌词 (USLT)
        uslt_frames = audio.tags.getall("USLT")
        if uslt_frames and uslt_frames[0].text:
            metadata["uslt"] = uslt_frames[0].text.strip()

    except Exception as e:
        EventEmitter.log("warn", f"无法读取ID3标签 {audio_path}: {str(e)}")

    # 设置默认值（如果缺失）
    return _ensure_required_metadata(metadata, audio_path)


def _get_default_metadata(audio_path: Path) -> dict:
    """获取默认元数据（当文件没有标签时）"""
    # 尝试从文件名解析
    name = audio_path.stem
    if " - " in name:
        artists_str, title = name.split(" - ", 1)
        # 清理艺术家字符串中的非法字符
        artists = [
            a.strip() for a in artists_str.replace("_", "/").split("/") if a.strip()
        ]
        return {"title": title.strip(), "artists": artists if artists else ["Unknown"]}
    else:
        return {"title": name, "artists": ["Unknown"]}


def _ensure_required_metadata(metadata: dict, audio_path: Path) -> dict:
    """确保元数据包含必需的字段"""
    if "title" not in metadata or not metadata["title"]:
        metadata["title"] = audio_path.stem

    if "artists" not in metadata or not metadata["artists"]:
        metadata["artists"] = ["Unknown"]

    return metadata


def publish_logic(metadata_mgr, repo_root, changed_only=False, progress_callback=None):
    """Publish 命令的核心业务逻辑"""
    work_dir = repo_root / "work"
    files = list(work_dir.glob("*.mp3"))

    if not files:
        return [], "工作目录为空"

    existing_entries = {e["audio_oid"]: e for e in metadata_mgr.load_all()}
    to_process = []

    for i, f in enumerate(files):
        # 从ID3标签读取元数据
        metadata = extract_metadata_from_file(f)
        title = metadata.get("title", f.stem)
        artists = metadata.get("artists", ["Unknown"])

        audio_oid = AudioIO.get_audio_hash(f)
        existing = existing_entries.get(audio_oid)
        is_changed = False
        reason = ""

        # 需要比较的所有字段
        fields_to_compare = ["title", "artists", "album", "date", "uslt"]

        if not existing:
            is_changed = True
            reason = "New File"
            field_changes = {}
            for field in fields_to_compare:
                if field in metadata:
                    field_changes[field] = {"old": None, "new": metadata[field]}
        else:
            field_changes = {}
            for field in fields_to_compare:
                existing_value = existing.get(field)
                new_value = metadata.get(field)

                # 特殊处理艺术家字段：比较集合
                if field == "artists":
                    if set(existing_value or []) != set(new_value or []):
                        field_changes[field] = {"old": existing_value, "new": new_value}
                # 其他字段直接比较
                elif existing_value != new_value:
                    field_changes[field] = {"old": existing_value, "new": new_value}

            if field_changes:
                is_changed = True
                reason = "Metadata Mismatch"
            else:
                field_changes = None

        if not changed_only or is_changed:
            to_process.append(
                {
                    "path": f,
                    "audio_oid": audio_oid,
                    "title": title,
                    "artists": artists,
                    "album": metadata.get("album"),
                    "date": metadata.get("date"),
                    "uslt": metadata.get("uslt"),
                    "is_changed": is_changed,
                    "reason": reason,
                    "field_changes": field_changes if is_changed else None,
                    "existing": existing,
                }
            )

        # 调用进度回调
        if progress_callback:
            progress_callback(i + 1, len(files))

    return to_process, None


def execute_publish(metadata_mgr, repo_root, items, progress_callback=None):
    """执行真正的发布动作"""
    for item in items:
        if progress_callback:
            progress_callback(item["path"].name)

        # 1. 封面
        cover_data = AudioIO.extract_cover(item["path"])
        cover_oid = None
        if cover_data:
            cover_hash = hashlib.sha256(cover_data).hexdigest()
            cover_oid = f"sha256:{cover_hash}"
            cover_path = (
                repo_root
                / "cache"
                / "covers"
                / "sha256"
                / cover_hash[:2]
                / f"{cover_hash}.jpg"
            )
            AudioIO.atomic_write(cover_data, cover_path)

        # 2. 音频
        audio_hash = item["audio_oid"].split(":")[1]
        obj_path = (
            repo_root
            / "cache"
            / "objects"
            / "sha256"
            / audio_hash[:2]
            / f"{audio_hash}.mp3"
        )
        if not obj_path.exists():
            with open(item["path"], "rb") as f:
                AudioIO.atomic_write(f.read(), obj_path)

        # 3. 元数据
        entry = item["existing"] or {
            "audio_oid": item["audio_oid"],
            "created_at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }

        # 更新所有元数据字段（仅当有值时）
        entry.update({"cover_oid": cover_oid})

        # 基本字段
        if "title" in item:
            entry["title"] = item["title"]
        if "artists" in item:
            entry["artists"] = item["artists"]

        # 可选字段（仅当有值时更新）
        if item.get("album"):
            entry["album"] = item["album"]
        if item.get("date"):
            entry["date"] = item["date"]
        if item.get("uslt"):
            entry["uslt"] = item["uslt"]
        metadata_mgr.update_entry(item["audio_oid"], entry)

        # 4. 清理
        send2trash(str(item["path"]))
