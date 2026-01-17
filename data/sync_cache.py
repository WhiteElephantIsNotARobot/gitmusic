import os
import sys
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.transport import TransportAdapter

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--direction', choices=['both', 'upload', 'download'], default='both')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--retries', type=int, default=3)
    args = parser.parse_args()

    # 从环境变量获取路径和配置
    user = os.environ.get("GITMUSIC_REMOTE_USER")
    host = os.environ.get("GITMUSIC_REMOTE_HOST")
    remote_root = os.environ.get("GITMUSIC_REMOTE_DATA_ROOT", "/srv/music/data")
    cache_root = Path(os.environ.get("GITMUSIC_CACHE_ROOT", ""))

    if not all([user, host, cache_root]):
        EventEmitter.error("Missing required environment variables or config")
        return

    transport = TransportAdapter(user, host, remote_root)
    EventEmitter.phase_start("sync_analyze")

    local_files = {str(p.relative_to(cache_root)).replace('\\', '/') for p in cache_root.rglob("*") if p.is_file() and p.suffix in ['.mp3', '.jpg']}
    remote_files = set(transport.list_remote_files("objects") + transport.list_remote_files("covers"))

    to_upload = local_files - remote_files
    to_download = remote_files - local_files

    def run_sync(items, method, phase):
        if not items: return
        EventEmitter.phase_start(phase, total_items=len(items))
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for i, rel_path in enumerate(items):
                EventEmitter.item_event(rel_path, phase)
                # 这里 TransportAdapter 内部应支持 timeout/retries，目前简化处理
                method(cache_root / rel_path if phase == "upload" else rel_path, rel_path if phase == "upload" else cache_root / rel_path)
                EventEmitter.batch_progress(phase, i + 1, len(items))

    if args.direction in ["upload", "both"]: run_sync(to_upload, transport.upload, "upload")
    if args.direction in ["download", "both"]: run_sync(to_download, transport.download, "download")

if __name__ == "__main__":
    main()

    logger.info(f"需要上传: {len(to_upload)} 个文件")
    logger.info(f"需要下载: {len(to_download)} 个文件")

    # 上传（带进度条）
    if to_upload and args.direction in ['both', 'upload']:
        logger.info("开始上传...")

        upload_tasks = []
        for rel_path in to_upload:
            local_path = local_root / rel_path
            remote_path = f"{args.remote_root}/{rel_path}"
            upload_tasks.append((local_path, remote_path))

        success = 0
        total = len(upload_tasks)

        # 创建进度条
        progress_mgr.set_progress(0, total, "上传中")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = []
            for local, remote in upload_tasks:
                # 提交任务，不显示队列日志
                future = executor.submit(sync_upload, args.user, args.host, local, remote, args.retries, args.timeout)
                futures.append(future)

            completed = 0
            for future in as_completed(futures):
                completed += 1
                if future.result():
                    success += 1
                else:
                    logger.error(f"✗ 上传失败")
                progress_mgr.set_progress(completed, total, "上传中")

        logger.info(f"上传完成: {success}/{total}")

    # 下载（带进度条）
    if to_download and args.direction in ['both', 'download']:
        logger.info("开始下载...")

        download_tasks = []
        for rel_path in to_download:
            remote_path = f"{args.remote_root}/{rel_path}"
            local_path = local_root / rel_path
            download_tasks.append((remote_path, local_path))

        success = 0
        total = len(download_tasks)

        # 创建进度条
        progress_mgr.set_progress(0, total, "下载中")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = []
            for remote, local in download_tasks:
                # 提交任务，不显示队列日志
                future = executor.submit(sync_download, args.user, args.host, remote, local, args.retries, args.timeout)
                futures.append(future)

            completed = 0
            for future in as_completed(futures):
                completed += 1
                if future.result():
                    success += 1
                else:
                    logger.error(f"✗ 下载失败")
                progress_mgr.set_progress(completed, total, "下载中")

        logger.info(f"下载完成: {success}/{total}")

    if not to_upload and not to_download:
        logger.info("无需同步，数据已一致")

    # 关闭进度条
    progress_mgr.close()


if __name__ == "__main__":
    main()
