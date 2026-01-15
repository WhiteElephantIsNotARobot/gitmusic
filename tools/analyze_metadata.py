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
    if len(sys.argv) < 2:
        print("用法:")
        print("  python analyze_metadata.py <metadata_file>")
        print("  python analyze_metadata.py <metadata_file> --search <query> [--field <field_name>]")
        print("  python analyze_metadata.py <metadata_file> --read <field1,field2,...>")
        print("  python analyze_metadata.py <metadata_file> --line <line1,line2,...>")
        print("  python analyze_metadata.py <metadata_file> --count")
        print("\n示例:")
        print("  python analyze_metadata.py metadata.jsonl --search \"Aero Chord\"")
        print("  python analyze_metadata.py metadata.jsonl --search \"Shadows\" --field title")
        print("  python analyze_metadata.py metadata.jsonl --read title,artists,cover_oid")
        print("  python analyze_metadata.py metadata.jsonl --line 1,3,5")
        print("  python analyze_metadata.py metadata.jsonl --count")
        return

    metadata_path = Path(sys.argv[1])
    if not metadata_path.exists():
        print(f"错误: 文件不存在 - {metadata_path}")
        return

    # 解析参数
    if len(sys.argv) > 2:
        mode = sys.argv[2]

        if mode == "--search":
            query = sys.argv[3] if len(sys.argv) > 3 else ""
            field = None
            if len(sys.argv) > 5 and sys.argv[4] == "--field":
                field = sys.argv[5]

            items = load_metadata(metadata_path)
            results = search_items(items, query, field)

            print(f"找到 {len(results)} 个匹配项:")
            for item in results:
                line = item.pop('_line', '')
                if field:
                    # 只输出指定字段
                    print(f"[{line}] {field}: {item.get(field, '')}")
                else:
                    # 输出完整条目
                    print(f"\n[{line}] {json.dumps(item, ensure_ascii=False)}")

        elif mode == "--read":
            fields_str = sys.argv[3] if len(sys.argv) > 3 else ""
            fields = [f.strip() for f in fields_str.split(',')]

            items = load_metadata(metadata_path)
            read_fields(items, fields)

        elif mode == "--line":
            lines_str = sys.argv[3] if len(sys.argv) > 3 else ""
            line_nums = [int(n.strip()) for n in lines_str.split(',')]

            results = read_by_line(metadata_path, line_nums)
            for item in results:
                line = item.pop('_line', '')
                print(f"[{line}] {json.dumps(item, ensure_ascii=False)}")

        elif mode == "--count":
            items = load_metadata(metadata_path)
            print(f"总条目数: {len(items)}")

        else:
            print(f"未知模式: {mode}")

    else:
        # 默认：显示所有条目
        items = load_metadata(metadata_path)
        for item in items:
            line = item.pop('_line', '')
            artists = ', '.join(item.get('artists', []))
            title = item.get('title', '')
            print(f"[{line}] {artists} - {title}")


if __name__ == "__main__":
    main()
