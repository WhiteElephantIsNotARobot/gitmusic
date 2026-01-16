#!/usr/bin/env python3
"""
通用 metadata 分析脚本
支持搜索匹配、字段读取、行号读取等操作
"""

import json
import sys
from pathlib import Path


def load_metadata(metadata_file):
    """逐行加载 metadata"""
    if not metadata_file.exists():
        print(f"错误: 文件不存在 - {metadata_file}")
        return []

    items = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    item = json.loads(line)
                    item['_line'] = line_num  # 保存行号
                    items.append(item)
                except json.JSONDecodeError:
                    continue
    return items


def search_items(items, query, field=None):
    """搜索匹配的条目"""
    results = []
    query_lower = query.lower()

    for item in items:
        if field:
            # 搜索指定字段
            value = str(item.get(field, '')).lower()
            if query_lower in value:
                results.append(item)
        else:
            # 搜索所有文本字段
            text = json.dumps(item, ensure_ascii=False).lower()
            if query_lower in text:
                results.append(item)

    return results


def read_fields(items, fields, output_format='text'):
    """读取指定字段"""
    results = []
    for item in items:
        row = {}
        for field in fields:
            if field == '_line':
                row['_line'] = item.get('_line', '')
            else:
                row[field] = item.get(field, '')
        results.append(row)

    if output_format == 'json':
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for row in results:
            parts = [f"{k}: {v}" for k, v in row.items()]
            print(" | ".join(parts))

    return results


def read_by_line(metadata_file, line_nums):
    """按行号读取"""
    results = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        for line_num in line_nums:
            if 1 <= line_num <= len(lines):
                line = lines[line_num - 1].strip()
                if line:
                    try:
                        item = json.loads(line)
                        item['_line'] = line_num
                        results.append(item)
                    except json.JSONDecodeError:
                        continue
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="元数据分析与搜索工具 (增强版)")
    parser.add_argument('query', nargs='?', help="搜索关键词 (标题或艺术家)")
    parser.add_argument('--file', help="手动指定 metadata.jsonl 路径")
    parser.add_argument('--search', help="搜索标题或艺术家 (同 positional query)")
    parser.add_argument('--field', help="配合 --search 指定搜索字段")
    parser.add_argument('--read', help="读取指定字段 (逗号分隔，如 title,artists)")
    parser.add_argument('--line', help="按行号读取 (逗号分隔，如 1,3,5)")
    parser.add_argument('--missing', nargs='+', choices=['cover', 'uslt', 'album', 'date'],
                       help="查找缺少指定字段的条目")
    parser.add_argument('--count', action='store_true', help="显示总条目数")
    parser.add_argument('--stats', action='store_true', help="显示详细统计信息")
    args = parser.parse_args()

    # 自动定位路径
    repo_root = Path(__file__).parent.parent
    metadata_file = Path(args.file) if args.file else repo_root / "metadata.jsonl"
    cache_root = repo_root.parent / "cache"

    if not metadata_file.exists():
        print(f"错误: 找不到元数据库 {metadata_file}")
        return

    items = load_metadata(metadata_file)
    if not items:
        print("错误: 元数据列表为空")
        return

    # 1. 处理 --count
    if args.count:
        print(f"总条目数: {len(items)}")
        return

    # 2. 处理 --line
    if args.line:
        line_nums = [int(n.strip()) for n in args.line.split(',')]
        results = read_by_line(metadata_file, line_nums)
        for item in results:
            line = item.pop('_line', '')
            print(f"[{line}] {json.dumps(item, ensure_ascii=False)}")
        return

    # 3. 处理 --read
    if args.read:
        fields = [f.strip() for f in args.read.split(',')]
        read_fields(items, fields)
        return

    # 4. 处理 --missing
    if args.missing:
        print(f"查找缺少 {', '.join(args.missing)} 的条目:")
        found = 0
        for item in items:
            missing_in_item = []
            for field in args.missing:
                f_key = 'cover_oid' if field == 'cover' else field
                if f_key not in item or not item[f_key]:
                    missing_in_item.append(field)

            if missing_in_item:
                line = item.get('_line', '')
                print(f"[{line}] {', '.join(item.get('artists', []))} - {item.get('title')} (缺失: {', '.join(missing_in_item)})")
                found += 1
        print(f"\n共找到 {found} 个条目。")
        return

    # 5. 处理搜索 (query 或 --search)
    search_query = args.query or args.search
    if search_query:
        results = search_items(items, search_query, args.field)
        print(f"找到 {len(results)} 个匹配项:\n")
        for item in results:
            line = item.pop('_line', '')
            print(f"[{line}] 标题: {item.get('title')}")
            print(f"    艺术家: {', '.join(item.get('artists', []))}")
            print(f"    Audio OID: {item.get('audio_oid')}")

            cover_oid = item.get('cover_oid')
            if cover_oid:
                c_hash = cover_oid.replace('sha256:', '')
                c_path = cache_root / 'covers' / 'sha256' / c_hash[:2] / f"{c_hash}.jpg"
                status = "本地存在" if c_path.exists() else "本地缺失"
                size = f"({c_path.stat().st_size/1024:.1f}KB)" if c_path.exists() else ""
                print(f"    封面 OID: {cover_oid} [{status} {size}]")
            else:
                print("    封面: 无")
            print("-" * 30)
        return

    # 6. 默认统计 (--stats 或无参数)
    print(f"元数据库概况: {metadata_file}")
    print(f"总条目数: {len(items)}")
    has_cover = len([i for i in items if 'cover_oid' in i])
    print(f"带封面条目: {has_cover} ({has_cover/len(items)*100:.1f}%)")

    missing_covers = 0
    for item in items:
        if 'cover_oid' in item:
            c_hash = item['cover_oid'].replace('sha256:', '')
            if not (cache_root / 'covers' / 'sha256' / c_hash[:2] / f"{c_hash}.jpg").exists():
                missing_covers += 1
    if missing_covers > 0:
        print(f"本地缺失封面文件: {missing_covers} 个 (请运行 sync_cache.py)")
if __name__ == "__main__":
    main()
