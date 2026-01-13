#!/usr/bin/env python3
"""
增量同步本地 cache 与远端 data
远端有本地无 → 下载
本地有远端无 → 上传
使用 rsync 实现高效同步
"""

import subprocess
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def sync_with_rsync(remote_user, remote_host, local_root, remote_root, direction, workers=4):
    """使用 rsync 同步目录"""
    try:
        ssh_target = f"{remote_user}@{remote_host}"

        if direction == 'upload':
            # 本地 → 远端
            # 使用 --dry-run 先检查
            dry_cmd = [
                'rsync', '-avz', '--dry-run',
                '--progress', '--human-readable',
                '--exclude', '.gitkeep',
                '--max-size=500M',  # 限制单个文件大小
                '--workers', str(workers),
                str(local_root) + '/',
                f'{ssh_target}:{remote_root}/'
            ]

            logger.info("检查需要上传的文件...")
            result = subprocess.run(dry_cmd, capture_output=True, text=True)

            # 解析 rsync 输出，统计文件数
            files_to_upload = len([line for line in result.stdout.split('\n') if line and not line.startswith('sending incremental')])

            if files_to_upload == 0:
                logger.info("没有需要上传的文件")
                return True

            logger.info(f"准备上传 {files_to_upload} 个文件，开始同步...")

            # 实际执行
            sync_cmd = [
                'rsync', '-avz',
                '--progress', '--human-readable',
                '--exclude', '.gitkeep',
                '--max-size=500M',
                '--workers', str(workers),
                str(local_root) + '/',
                f'{ssh_target}:{remote_root}/'
            ]

            result = subprocess.run(sync_cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info("上传完成")
                return True
            else:
                logger.error(f"上传失败: {result.stderr}")
                return False

        else:
            # 远端 → 本地
            dry_cmd = [
                'rsync', '-avz', '--dry-run',
                '--progress', '--human-readable',
                '--exclude', '.gitkeep',
                '--max-size=500M',
                '--workers', str(workers),
                f'{ssh_target}:{remote_root}/',
                str(local_root) + '/'
            ]

            logger.info("检查需要下载的文件...")
            result = subprocess.run(dry_cmd, capture_output=True, text=True)

            files_to_download = len([line for line in result.stdout.split('\n') if line and not line.startswith('sending incremental')])

            if files_to_download == 0:
                logger.info("没有需要下载的文件")
                return True

            logger.info(f"准备下载 {files_to_download} 个文件，开始同步...")

            sync_cmd = [
                'rsync', '-avz',
                '--progress', '--human-readable',
                '--exclude', '.gitkeep',
                '--max-size=500M',
                '--workers', str(workers),
                f'{ssh_target}:{remote_root}/',
                str(local_root) + '/'
            ]

            result = subprocess.run(sync_cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info("下载完成")
                return True
            else:
                logger.error(f"下载失败: {result.stderr}")
                return False

    except Exception as e:
        logger.error(f"rsync 同步失败: {e}")
        return False


def get_remote_file_list(remote_user, remote_host, remote_base):
    """获取远端文件列表（备用方法）"""
    try:
        ssh_target = f"{remote_user}@{remote_host}"
        cmd = [
            'ssh', ssh_target,
            f'find {remote_base} -type f 2>/dev/null | sed "s|{remote_base}/||"'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return set(line.strip() for line in result.stdout.split('\n') if line.strip())
    except Exception as e:
        logger.error(f"获取远端文件列表失败: {e}")
        return set()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="增量同步 cache 与远端 data（使用 rsync）")
    parser.add_argument("-u", "--user", required=True, help="远程用户名")
    parser.add_argument("-H", "--host", required=True, help="远程主机")
    parser.add_argument("--local-root", default=str(Path(__file__).parent.parent.parent / "cache" / "data"), help="本地 cache/data 目录")
    parser.add_argument("--remote-root", default="/srv/music/data", help="远端 data 目录")
    parser.add_argument("--workers", type=int, default=4, help="rsync 并行工作进程数")
    parser.add_argument("--direction", choices=['both', 'upload', 'download'], default='both', help="同步方向")
    parser.add_argument("--use-fallback", action="store_true", help="使用备用的逐文件同步模式（当 rsync 不可用时）")

    args = parser.parse_args()

    local_root = Path(args.local_root)
    if not local_root.exists():
        logger.error(f"本地目录不存在: {local_root}")
        return

    logger.info("开始同步...")

    if not args.use_fallback:
        # 使用 rsync 模式
        logger.info("使用 rsync 同步模式")

        if args.direction in ['both', 'upload']:
            logger.info("→ 上传到远端...")
            sync_with_rsync(args.user, args.host, local_root, args.remote_root, 'upload', args.workers)

        if args.direction in ['both', 'download']:
            logger.info("← 从远端下载...")
            sync_with_rsync(args.user, args.host, local_root, args.remote_root, 'download', args.workers)

    else:
        # 备用模式：逐文件同步
        logger.info("使用备用的逐文件同步模式")

        # 获取文件列表
        logger.info("获取远端文件列表...")
        remote_files = get_remote_file_list(args.user, args.host, args.remote_root)
        logger.info(f"远端文件数: {len(remote_files)}")

        logger.info("获取本地文件列表...")
        local_files = get_local_file_list(local_root)
        logger.info(f"本地文件数: {len(local_files)}")

        # 计算差异
        to_upload = local_files - remote_files  # 本地有，远端无
        to_download = remote_files - local_files  # 远端有，本地无

        logger.info(f"需要上传: {len(to_upload)} 个文件")
        logger.info(f"需要下载: {len(to_download)} 个文件")

        # 上传
        if to_upload and args.direction in ['both', 'upload']:
            logger.info("开始上传...")
            from concurrent.futures import ThreadPoolExecutor, as_completed

            upload_tasks = []
            for rel_path in to_upload:
                local_path = local_root / rel_path
                remote_path = f"{args.remote_root}/{rel_path}"
                upload_tasks.append((local_path, remote_path))

            success = 0
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(sync_upload, args.user, args.host, local, remote)
                          for local, remote in upload_tasks]
                for future in as_completed(futures):
                    if future.result():
                        success += 1

            logger.info(f"上传完成: {success}/{len(upload_tasks)}")

        # 下载
        if to_download and args.direction in ['both', 'download']:
            logger.info("开始下载...")
            from concurrent.futures import ThreadPoolExecutor, as_completed

            download_tasks = []
            for rel_path in to_download:
                remote_path = f"{args.remote_root}/{rel_path}"
                local_path = local_root / rel_path
                download_tasks.append((remote_path, local_path))

            success = 0
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(sync_download, args.user, args.host, remote, local)
                          for remote, local in download_tasks]
                for future in as_completed(futures):
                    if future.result():
                        success += 1

            logger.info(f"下载完成: {success}/{len(download_tasks)}")

        if not to_upload and not to_download:
            logger.info("无需同步，数据已一致")


def get_local_file_list(local_base):
    """获取本地文件列表"""
    local_files = set()
    for root, dirs, files in Path(local_base).rglob('*'):
        if root.is_dir() and files:
            for f in files:
                rel_path = Path(root).relative_to(local_base) / f
                local_files.add(str(rel_path.as_posix()))
    return local_files


def sync_upload(remote_user, remote_host, local_path, remote_path):
    """上传单个文件（备用方法）"""
    try:
        ssh_target = f"{remote_user}@{remote_host}"

        # 创建远程目录
        remote_dir = Path(remote_path).parent
        mkdir_cmd = ['ssh', ssh_target, f'mkdir -p {remote_dir}']
        subprocess.run(mkdir_cmd, check=True, capture_output=True)

        # 上传文件
        scp_cmd = ['scp', str(local_path), f'{ssh_target}:{remote_path}']
        subprocess.run(scp_cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        logger.error(f"上传失败 {local_path} -> {remote_path}: {e}")
        return False


def sync_download(remote_user, remote_host, remote_path, local_path):
    """下载单个文件（备用方法）"""
    try:
        ssh_target = f"{remote_user}@{remote_host}"

        # 创建本地目录
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # 下载文件
        scp_cmd = ['scp', f'{ssh_target}:{remote_path}', str(local_path)]
        subprocess.run(scp_cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        logger.error(f"下载失败 {remote_path} -> {local_path}: {e}")
        return False


if __name__ == "__main__":
    main()
