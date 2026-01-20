#!/usr/bin/env python3
"""
同步本地与远程缓存 - 兼容性包装器
调用 libgitmusic.commands.sync 中的逻辑
"""

import os
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.commands.sync import sync_logic
from libgitmusic.transport import TransportAdapter


def main():
    import argparse

    parser = argparse.ArgumentParser(description="同步本地与远程缓存")
    parser.add_argument(
        "--direction",
        choices=["both", "upload", "download"],
        default="both",
        help="同步方向 (both|upload|download)",
    )
    parser.add_argument("--workers", type=int, default=4, help="并行线程数")
    parser.add_argument("--timeout", type=int, default=60, help="单文件超时时间（秒）")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数")
    parser.add_argument("--dry-run", action="store_true", help="仅显示差异，不执行同步")
    args = parser.parse_args()

    # 从环境变量获取路径和配置
    user = os.environ.get("GITMUSIC_REMOTE_USER")
    host = os.environ.get("GITMUSIC_REMOTE_HOST")
    remote_root = os.environ.get("GITMUSIC_REMOTE_DATA_ROOT", "/srv/music/data")
    cache_root_str = os.environ.get("GITMUSIC_CACHE_ROOT", "")

    if not all([user, host, cache_root_str]):
        EventEmitter.error(
            "缺少必要的环境变量或配置",
            {
                "user": user if user else "missing",
                "host": host if host else "missing",
                "cache_root": cache_root_str if cache_root_str else "missing",
            },
        )
        return 1

    cache_root = Path(cache_root_str)
    if not cache_root.exists():
        EventEmitter.error(f"缓存目录不存在: {cache_root}")
        return 1

    # 创建传输适配器
    transport = TransportAdapter(str(user), str(host), str(remote_root))

    # 调用库函数
    exit_code = sync_logic(
        cache_root=cache_root,
        transport=transport,
        direction=args.direction,
        workers=args.workers,
        retries=args.retries,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
