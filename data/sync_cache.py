import os
import sys
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.transport import TransportAdapter


def main():
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

    # 分析阶段
    EventEmitter.phase_start("sync_analyze")

    # 声明变量（在try块外也可访问）
    to_upload = []
    to_download = []
    to_upload_audio_list = []
    to_upload_covers_list = []
    to_download_audio_list = []
    to_download_covers_list = []

    try:
        transport = TransportAdapter(str(user), str(host), str(remote_root))

        # 列出本地文件（分别统计音频和封面）
        local_audio = set()
        local_covers = set()

        for p in cache_root.rglob("*"):
            if p.is_file():
                rel_path = str(p.relative_to(cache_root)).replace("\\", "/")
                if p.suffix == ".mp3":
                    local_audio.add(rel_path)
                elif p.suffix == ".jpg":
                    local_covers.add(rel_path)

        EventEmitter.log("info", f"本地音频数: {len(local_audio)}")
        EventEmitter.log("info", f"本地封面数: {len(local_covers)}")

        # 列出远程文件（分别统计音频和封面）
        remote_objects = set(transport.list_remote_files("objects"))
        remote_covers = set(transport.list_remote_files("covers"))

        # 远程文件路径已经是相对路径，不需要转换
        remote_audio = {f for f in remote_objects if f.endswith(".mp3")}
        remote_covers_set = {f for f in remote_covers if f.endswith(".jpg")}

        EventEmitter.log("info", f"远程音频数: {len(remote_audio)}")
        EventEmitter.log("info", f"远程封面数: {len(remote_covers_set)}")

        # 合并本地和远程文件集（用于计算差异）
        local_files = local_audio.union(local_covers)
        remote_files = remote_audio.union(remote_covers_set)

        # 计算待上传的文件（分类）
        to_upload_all = list(local_files - remote_files)
        to_upload_audio = [f for f in to_upload_all if f.endswith(".mp3")]
        to_upload_covers = [f for f in to_upload_all if f.endswith(".jpg")]

        # 计算待下载的文件（分类）
        to_download_all = list(remote_files - local_files)
        to_download_audio = [f for f in to_download_all if f.endswith(".mp3")]
        to_download_covers = [f for f in to_download_all if f.endswith(".jpg")]

        # 准备详细的分析结果
        analysis_result = {
            "local": {
                "audio": len(local_audio),
                "covers": len(local_covers),
                "total": len(local_files),
            },
            "remote": {
                "audio": len(remote_audio),
                "covers": len(remote_covers_set),
                "total": len(remote_files),
            },
            "to_upload": {
                "audio": len(to_upload_audio),
                "covers": len(to_upload_covers),
                "total": len(to_upload_all),
            },
            "to_download": {
                "audio": len(to_download_audio),
                "covers": len(to_download_covers),
                "total": len(to_download_all),
            },
        }

        EventEmitter.result(
            "ok",
            "同步分析完成",
            analysis_result,
        )

        # 保存分类后的待上传/下载列表供后续使用
        to_upload = to_upload_all
        to_download = to_download_all
        # 保存分类列表
        to_upload_audio_list = to_upload_audio
        to_upload_covers_list = to_upload_covers
        to_download_audio_list = to_download_audio
        to_download_covers_list = to_download_covers

    except Exception as e:
        EventEmitter.error(f"同步分析失败: {str(e)}")
        return 1

    # 如果是dry-run模式，只显示结果
    if args.dry_run:
        EventEmitter.log("info", f"Dry-run模式，不执行实际同步")
        EventEmitter.log(
            "info",
            f"待上传: {len(to_upload)} 个文件（音频: {len(to_upload_audio_list)}，封面: {len(to_upload_covers_list)}）",
        )
        EventEmitter.log(
            "info",
            f"待下载: {len(to_download)} 个文件（音频: {len(to_download_audio_list)}，封面: {len(to_download_covers_list)}）",
        )
        return 0

    # 执行同步
    total_processed = 0
    total_errors = 0

    def sync_with_retry(method, src_path, dst_path, max_retries: int) -> bool:
        """带重试机制的同步操作"""
        for attempt in range(max_retries + 1):
            try:
                method(src_path, dst_path)
                return True
            except Exception as e:
                if attempt < max_retries:
                    wait_time = 2**attempt  # 指数退避
                    EventEmitter.log(
                        "warn",
                        f"同步失败，{wait_time}s后重试 ({attempt + 1}/{max_retries}): {str(e)}",
                    )
                    time.sleep(wait_time)
                else:
                    EventEmitter.error(
                        f"同步失败，已达最大重试次数: {str(e)}",
                        {"src": str(src_path), "dst": str(dst_path)},
                    )
        return False

    # 上传文件
    if args.direction in ["upload", "both"] and to_upload:
        EventEmitter.phase_start("upload", total_items=len(to_upload))

        def upload_task(rel_path: str):
            local_path = cache_root / rel_path
            try:
                if sync_with_retry(
                    transport.upload, local_path, rel_path, args.retries
                ):
                    EventEmitter.item_event(rel_path, "uploaded", "")
                    return True
                else:
                    return False
            except Exception as e:
                EventEmitter.error(f"上传失败: {str(e)}", {"file": rel_path})
                return False

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(upload_task, rel_path): rel_path
                for rel_path in to_upload
            }

            processed = 0
            errors = 0
            for future in as_completed(futures):
                rel_path = futures[future]
                try:
                    if future.result():
                        processed += 1
                    else:
                        errors += 1
                except Exception as e:
                    EventEmitter.error(f"上传任务异常: {str(e)}", {"file": rel_path})
                    errors += 1

                EventEmitter.batch_progress(
                    "upload", processed + errors, len(to_upload)
                )

        total_processed += processed
        total_errors += errors
        EventEmitter.log("info", f"上传完成: {processed} 成功, {errors} 失败")

    # 下载文件
    if args.direction in ["download", "both"] and to_download:
        EventEmitter.phase_start("download", total_items=len(to_download))

        def download_task(rel_path: str):
            local_path = cache_root / rel_path
            try:
                if sync_with_retry(
                    transport.download, rel_path, local_path, args.retries
                ):
                    EventEmitter.item_event(rel_path, "downloaded", "")
                    return True
                else:
                    return False
            except Exception as e:
                EventEmitter.error(f"下载失败: {str(e)}", {"file": rel_path})
                return False

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(download_task, rel_path): rel_path
                for rel_path in to_download
            }

            processed = 0
            errors = 0
            for future in as_completed(futures):
                rel_path = futures[future]
                try:
                    if future.result():
                        processed += 1
                    else:
                        errors += 1
                except Exception as e:
                    EventEmitter.error(f"下载任务异常: {str(e)}", {"file": rel_path})
                    errors += 1

                EventEmitter.batch_progress(
                    "download", processed + errors, len(to_download)
                )

        total_processed += processed
        total_errors += errors
        EventEmitter.log("info", f"下载完成: {processed} 成功, {errors} 失败")

    # 最终结果
    if total_errors == 0:
        EventEmitter.result("ok", f"同步完成，处理 {total_processed} 个文件")
        return 0
    else:
        EventEmitter.result(
            "warn", f"同步完成，{total_processed} 成功, {total_errors} 失败"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
