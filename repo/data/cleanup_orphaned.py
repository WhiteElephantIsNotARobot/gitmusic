#!/usr/bin/env python3
"""
清理孤立文件 - 兼容性包装器
调用 libgitmusic.commands.cleanup 中的逻辑
"""

import os
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.commands.cleanup import cleanup_logic


def main():
    import argparse

    # 解析参数
    parser = argparse.ArgumentParser(description="清理孤立文件")
    parser.add_argument(
        "--mode",
        choices=["local", "server", "both"],
        default="local",
        help="清理模式 (local|server|both)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认执行删除操作（必须指定才会删除）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示分析结果（默认行为，显式指定）",
    )
    args = parser.parse_args()

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    remote_user = os.environ.get("GITMUSIC_REMOTE_USER", "white_elephant")
    remote_host = os.environ.get("GITMUSIC_REMOTE_HOST", "debian-server")
    remote_data_root = os.environ.get("GITMUSIC_REMOTE_DATA_ROOT", "/srv/music/data")

    if not cache_root_path or not metadata_file_path:
        EventEmitter.error(
            "Missing required environment variables (GITMUSIC_CACHE_ROOT, GITMUSIC_METADATA_FILE)"
        )
        return 1

    cache_root = Path(cache_root_path)
    metadata_file = Path(metadata_file_path)

    # 调用库函数
    exit_code = cleanup_logic(
        metadata_file=metadata_file,
        cache_root=cache_root,
        mode=args.mode,
        confirm=args.confirm,
        dry_run=args.dry_run,
        remote_user=remote_user,
        remote_host=remote_host,
        remote_data_root=remote_data_root,
    )

    return exit_code


if __name__ == "__main__":
    main()
