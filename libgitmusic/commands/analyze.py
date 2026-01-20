import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter, defaultdict

from ..events import EventEmitter
from ..metadata import MetadataManager


def calculate_statistics(entries: List[Dict]) -> Dict[str, Any]:
    """计算元数据统计信息"""
    stats = {
        "total_entries": len(entries),
        "fields_present": {},
        "artists_count": 0,
        "with_cover": 0,
        "with_lyrics": 0,
        "with_album": 0,
        "with_date": 0,
    }

    if not entries:
        return stats

    # 统计字段存在情况
    field_names = [
        "audio_oid",
        "cover_oid",
        "title",
        "artists",
        "album",
        "date",
        "uslt",
        "created_at",
    ]
    for field in field_names:
        count = sum(1 for entry in entries if entry.get(field))
        stats["fields_present"][field] = {
            "count": count,
            "percentage": round(100 * count / len(entries), 2),
        }

    # 统计艺术家数量
    unique_artists = set()
    for entry in entries:
        artists = entry.get("artists", [])
        if isinstance(artists, list):
            unique_artists.update(artists)
        elif artists:
            unique_artists.add(str(artists))
    stats["artists_count"] = len(unique_artists)
    stats["unique_artists"] = list(unique_artists)[:20]  # 只显示前20个

    # 其他统计
    stats["with_cover"] = sum(1 for entry in entries if entry.get("cover_oid"))
    stats["with_lyrics"] = sum(1 for entry in entries if entry.get("uslt"))
    stats["with_album"] = sum(1 for entry in entries if entry.get("album"))
    stats["with_date"] = sum(1 for entry in entries if entry.get("date"))

    return stats


def search_entries(
    entries: List[Dict],
    query: str,
    search_field: Optional[str] = None,
    case_sensitive: bool = False,
) -> List[Dict]:
    """搜索元数据条目"""
    if not query:
        return entries

    results = []
    query_lower = query if case_sensitive else query.lower()

    for entry in entries:
        if search_field:
            # 搜索特定字段
            field_value = entry.get(search_field, "")
            if isinstance(field_value, list):
                field_value = ", ".join(field_value)
            field_str = str(field_value)

            if not case_sensitive:
                field_str = field_str.lower()

            if query_lower in field_str:
                results.append(entry)
        else:
            # 搜索所有字段
            entry_json = json.dumps(entry, ensure_ascii=False)
            if not case_sensitive:
                entry_json = entry_json.lower()

            if query_lower in entry_json:
                results.append(entry)

    return results


def filter_missing_fields(entries: List[Dict], missing_fields: List[str]) -> List[Dict]:
    """过滤出缺少指定字段的条目"""
    if not missing_fields:
        return entries

    field_map = {
        "cover": "cover_oid",
        "lyrics": "uslt",
        "album": "album",
        "date": "date",
        "artists": "artists",
        "title": "title",
    }

    actual_fields = [field_map.get(f, f) for f in missing_fields]

    filtered = []
    for entry in entries:
        missing = any(not entry.get(field) for field in actual_fields)
        if missing:
            filtered.append(entry)

    return filtered


def extract_fields(entries: List[Dict], fields: List[str]) -> List[Dict]:
    """提取指定字段"""
    if not fields:
        return entries

    extracted = []
    for entry in entries:
        extracted_entry = {}
        for field in fields:
            if field in entry:
                extracted_entry[field] = entry[field]
        extracted.append(extracted_entry)

    return extracted


def find_duplicates(entries: List[Dict]) -> Dict[str, Any]:
    """查找重复项"""
    # 1. 按 audio_oid 统计
    audio_oid_counter = Counter(entry.get("audio_oid") for entry in entries)
    duplicates_audio = {
        oid: count for oid, count in audio_oid_counter.items() if count > 1
    }

    # 2. 按文件名统计
    filename_counter = Counter()
    for entry in entries:
        artists = entry.get("artists", [])
        title = entry.get("title", "未知")
        if isinstance(artists, list):
            artist_str = ", ".join(artists)
        else:
            artist_str = str(artists)
        filename = f"{artist_str} - {title}.mp3"
        filename_counter[filename] += 1

    duplicates_filename = {
        name: count for name, count in filename_counter.items() if count > 1
    }

    # 3. 按标题和艺术家组合统计
    title_artist_counter = Counter()
    for entry in entries:
        artists = entry.get("artists", [])
        title = entry.get("title", "")
        if isinstance(artists, list):
            key = f"{title}::{'|'.join(sorted(artists))}"
        else:
            key = f"{title}::{artists}"
        title_artist_counter[key] += 1

    duplicates_title_artist = {
        key: count for key, count in title_artist_counter.items() if count > 1
    }

    return {
        "duplicates_audio": duplicates_audio,
        "duplicates_filename": duplicates_filename,
        "duplicates_title_artist": duplicates_title_artist,
    }


def analyze_logic(
    metadata_mgr: MetadataManager,
    query: str = "",
    search_field: Optional[str] = None,
    missing_fields: Optional[str] = None,
    fields_to_extract: Optional[str] = None,
    line_filter: Optional[str] = None,
    mode: str = "search",  # 'search', 'stats', 'duplicates'
) -> Tuple[List[Dict], Dict[str, Any], Optional[str]]:
    """
    Analyze命令的核心业务逻辑

    Args:
        metadata_mgr: 元数据管理器
        query: 搜索查询
        search_field: 指定搜索字段
        missing_fields: 缺失字段过滤（逗号分隔）
        fields_to_extract: 提取字段（逗号分隔）
        line_filter: 行号过滤器
        mode: 分析模式 ('search', 'stats', 'duplicates')

    Returns:
        (过滤后的条目, 分析结果, 错误消息)
    """
    # 加载所有元数据
    all_entries = metadata_mgr.load_all()
    EventEmitter.log("info", f"Loaded {len(all_entries)} metadata entries")

    # 按行号过滤
    if line_filter:
        line_nums = set()
        for part in line_filter.split(","):
            part = part.strip()
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                try:
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    line_nums.update(range(start, end + 1))
                except ValueError:
                    return [], {}, f"Invalid line range: {part}"
            else:
                try:
                    line_nums.add(int(part.strip()))
                except ValueError:
                    return [], {}, f"Invalid line number: {part}"

        selected_entries = []
        for idx, entry in enumerate(all_entries, 1):
            if idx in line_nums:
                selected_entries.append(entry)

        all_entries = selected_entries
        EventEmitter.log("info", f"Selected {len(all_entries)} entries by line numbers")

    # 根据模式处理
    if mode == "duplicates":
        # 重复项分析模式
        EventEmitter.phase_start("analyze_duplicates")
        analysis_results = find_duplicates(all_entries)

        # 获取重复项的详细信息
        detailed_duplicates = {}
        for category, dup_dict in analysis_results.items():
            detailed_duplicates[category] = {}
            for key, count in dup_dict.items():
                # 查找对应的条目
                matching_entries = []
                for entry in all_entries:
                    if category == "duplicates_audio":
                        if entry.get("audio_oid") == key:
                            matching_entries.append(entry)
                    elif category == "duplicates_filename":
                        artists = entry.get("artists", [])
                        title = entry.get("title", "未知")
                        if isinstance(artists, list):
                            artist_str = ", ".join(artists)
                        else:
                            artist_str = str(artists)
                        filename = f"{artist_str} - {title}.mp3"
                        if filename == key:
                            matching_entries.append(entry)
                    elif category == "duplicates_title_artist":
                        title, artists_str = key.split("::", 1)
                        artists_list = artists_str.split("|")
                        entry_title = entry.get("title", "")
                        entry_artists = entry.get("artists", [])
                        if isinstance(entry_artists, list):
                            entry_artists_sorted = sorted(entry_artists)
                        else:
                            entry_artists_sorted = [str(entry_artists)]

                        if (
                            entry_title == title
                            and sorted(artists_list) == entry_artists_sorted
                        ):
                            matching_entries.append(entry)

                if matching_entries:
                    detailed_duplicates[category][key] = {
                        "count": count,
                        "entries": matching_entries[:10],  # 限制显示数量
                    }

        return [], {"duplicates": detailed_duplicates}, None

    elif mode == "stats":
        # 统计模式
        EventEmitter.phase_start("analyze_stats")
        analysis_results = calculate_statistics(all_entries)
        return all_entries, {"statistics": analysis_results}, None

    else:
        # 搜索模式（默认）
        EventEmitter.phase_start("analyze_search")

        # 搜索
        if query or search_field:
            all_entries = search_entries(
                all_entries,
                query,
                search_field,
                False,
            )
            EventEmitter.log(
                "info", f"Found {len(all_entries)} entries matching search"
            )

        # 过滤缺失字段
        if missing_fields:
            missing_fields_list = [f.strip() for f in missing_fields.split(",")]
            all_entries = filter_missing_fields(all_entries, missing_fields_list)
            EventEmitter.log(
                "info", f"Found {len(all_entries)} entries missing specified fields"
            )

        # 提取字段
        if fields_to_extract:
            fields_list = [f.strip() for f in fields_to_extract.split(",")]
            all_entries = extract_fields(all_entries, fields_list)
            EventEmitter.log(
                "info", f"Extracted specified fields from {len(all_entries)} entries"
            )

        return all_entries, {}, None


def execute_analyze(
    entries: List[Dict],
    analysis_results: Dict[str, Any],
    mode: str = "search",
    progress_callback=None,
) -> None:
    """
    执行分析动作（主要是输出结果）

    Args:
        entries: 过滤后的条目
        analysis_results: 分析结果
        mode: 分析模式
        progress_callback: 进度回调函数
    """
    if mode == "duplicates":
        duplicates = analysis_results.get("duplicates", {})

        total_duplicates = 0
        for category, dup_dict in duplicates.items():
            for key, info in dup_dict.items():
                total_duplicates += info["count"] - 1  # 每个重复组计算重复次数

        if total_duplicates == 0:
            EventEmitter.result("ok", message="No duplicates found")
        else:
            EventEmitter.result(
                "warn",
                message=f"Found {total_duplicates} duplicate instances",
                artifacts={"duplicates": duplicates},
            )

    elif mode == "stats":
        stats = analysis_results.get("statistics", {})
        EventEmitter.result(
            "ok",
            message=f"Statistics for {stats.get('total_entries', 0)} entries",
            artifacts={"statistics": stats},
        )

    else:
        # 搜索模式
        if not entries:
            EventEmitter.result("ok", message="No entries found matching criteria")
        else:
            artifacts = {
                "count": len(entries),
                "entries": entries[:100],  # 限制输出数量
                "truncated": len(entries) > 100,
            }

            EventEmitter.result(
                "ok",
                message=f"Found {len(entries)} matching entries",
                artifacts=artifacts,
            )
