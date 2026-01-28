import json
import sys
import argparse
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager


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


def main():
    parser = argparse.ArgumentParser(description="分析元数据文件")

    # 搜索选项
    parser.add_argument(
        "query", nargs="?", default="", help="搜索查询（在所有字段中搜索）"
    )
    parser.add_argument("--search-field", help="指定搜索字段")

    # 过滤选项
    parser.add_argument(
        "--missing", help="查找缺失指定字段的条目（逗号分隔，如：cover,lyrics,album）"
    )
    parser.add_argument(
        "--filter", help="输出时过滤字段（逗号分隔，如：title,artists,album）"
    )

    # 提取选项
    parser.add_argument("--fields", help="提取指定字段（逗号分隔）")
    parser.add_argument(
        "--line", help="按行号读取（逗号分隔或范围，如：1,3,5 或 1-10）"
    )

    args = parser.parse_args()

    # 从环境变量获取元数据文件路径
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    if not metadata_file_path:
        EventEmitter.error("Missing GITMUSIC_METADATA_FILE environment variable")
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    all_entries = metadata_mgr.load_all()

    EventEmitter.log("info", f"Loaded {len(all_entries)} metadata entries")

    # 按行号过滤
    if args.line:
        line_nums = set()
        for part in args.line.split(","):
            part = part.strip()
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                try:
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    line_nums.update(range(start, end + 1))
                except ValueError:
                    EventEmitter.error(f"Invalid line range: {part}")
                    return
            else:
                try:
                    line_nums.add(int(part.strip()))
                except ValueError:
                    EventEmitter.error(f"Invalid line number: {part}")
                    return

        selected_entries = []
        for idx, entry in enumerate(all_entries, 1):
            if idx in line_nums:
                selected_entries.append(entry)

        all_entries = selected_entries
        EventEmitter.log("info", f"Selected {len(all_entries)} entries by line numbers")

    # 搜索
    if args.query or args.search_field:
        all_entries = search_entries(
            all_entries,
            args.query,
            args.search_field,
            False,
        )
        EventEmitter.log("info", f"Found {len(all_entries)} entries matching search")

    # 过滤缺失字段
    if args.missing:
        missing_fields = [f.strip() for f in args.missing.split(",")]
        all_entries = filter_missing_fields(all_entries, missing_fields)
        EventEmitter.log(
            "info", f"Found {len(all_entries)} entries missing specified fields"
        )

    # 提取字段
    if args.fields:
        fields_to_extract = [f.strip() for f in args.fields.split(",")]
        all_entries = extract_fields(all_entries, fields_to_extract)
        EventEmitter.log(
            "info", f"Extracted specified fields from {len(all_entries)} entries"
        )

    # 默认输出结果
    artifacts = {
        "count": len(all_entries),
        "entries": all_entries[:100],  # 限制输出数量
        "truncated": len(all_entries) > 100,
    }

    # 截断提示由CLI统一显示，此处不再发送警告消息
    # if len(all_entries) > 100:
    #     EventEmitter.log("warn", f"Showing first 100 of {len(all_entries)} entries")

    EventEmitter.result(
        "ok",
        message=f"Found {len(all_entries)} matching entries",
        artifacts=artifacts,
    )


if __name__ == "__main__":
    main()
