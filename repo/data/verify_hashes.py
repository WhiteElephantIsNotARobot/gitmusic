#!/usr/bin/env python3
"""
校验文件哈希完整性 - 兼容性包装器
调用 libgitmusic.commands.verify 中的逻辑
"""

import os
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.commands.verify import verify_logic


def main():
    import argparse

    parser = argparse.ArgumentParser(description="校验文件哈希完整性")
    parser.add_argument(
        "--mode",
        choices=["local", "server", "release"],
        default="local",
        help="校验模式 (local|server|release)",
    )
    parser.add_argument(
        "--path",
        help="指定校验路径（可选）",
    )
    args = parser.parse_args()

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    if not cache_root_path:
        EventEmitter.error("Missing required environment variable GITMUSIC_CACHE_ROOT")
        return 1

    cache_root = Path(cache_root_path)
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    if not metadata_file_path:
        EventEmitter.error(
            "Missing required environment variable GITMUSIC_METADATA_FILE"
        )
        return 1

    metadata_file = Path(metadata_file_path)
    release_dir = None

    if args.mode == "release":
        release_dir_path = os.environ.get("GITMUSIC_RELEASE_DIR")
        if not release_dir_path:
            EventEmitter.error(
                "Missing environment variable GITMUSIC_RELEASE_DIR for release mode"
            )
            return 1
        release_dir = Path(release_dir_path)

    custom_path = Path(args.path) if args.path else None

    # 调用库函数
    exit_code = verify_logic(
        cache_root=cache_root,
        metadata_file=metadata_file,
        mode=args.mode,
        custom_path=custom_path,
        release_dir=release_dir,
    )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
