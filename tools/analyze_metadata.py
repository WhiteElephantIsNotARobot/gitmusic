import json
import sys
import argparse
import os
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('query', nargs='?')
    parser.add_argument('--search-field', help="指定搜索字段")
    parser.add_argument('--read-fields', help="读取指定字段 (逗号分隔)")
    parser.add_argument('--line', help="按行号读取 (逗号分隔)")
    parser.add_argument('--missing', help="查找缺失指定字段的条目 (逗号分隔)")
    args = parser.parse_args()

    # 从环境变量获取路径
    metadata_file = os.environ.get("GITMUSIC_METADATA_FILE")
    if not metadata_file:
        EventEmitter.error("Missing GITMUSIC_METADATA_FILE environment variable")
        return

    metadata_mgr = MetadataManager(Path(metadata_file))
    items = metadata_mgr.load_all()

    results = []

    # 1. 按行号读取
    if args.line:
        line_nums = [int(n.strip()) for n in args.line.split(',')]
        for idx, item in enumerate(items, 1):
            if idx in line_nums:
                results.append(item)

    # 2. 缺失字段过滤
    elif args.missing:
        missing_fields = [f.strip() for f in args.missing.split(',')]
        for item in items:
            if any(not item.get(f) for f in missing_fields):
                results.append(item)

    # 3. 搜索逻辑
    else:
        query = args.query or ""
        for item in items:
            if args.search_field:
                val = str(item.get(args.search_field, "")).lower()
                if query.lower() in val:
                    results.append(item)
            elif query.lower() in json.dumps(item, ensure_ascii=False).lower():
                results.append(item)

    # 4. 字段提取
    if args.read_fields:
        fields = [f.strip() for f in args.read_fields.split(',')]
        final_results = []
        for item in results:
            final_results.append({f: item.get(f) for f in fields})
        results = final_results

    EventEmitter.result("ok", message=f"Found {len(results)} matches", artifacts={"matches": results})

if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()
