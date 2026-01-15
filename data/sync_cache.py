#!/usr/bin/env python3
"""
增量同步本地 cache 与远端 data
远端有本地无 → 下载
本地有远端无 → 上传
使用 SCP 实现同步，带重试机制
"""

import subprocess
from pathlib import Path
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import threading
from io import StringIO

# 尝试导入 tqdm
try:
    from tqdm import tqdm
except ImportError:
    print("错误: tqdm 库未安装，请运行 pip install tqdm", file=sys.stderr)
    exit(1)


class BottomProgressBar:
    """底部固定进度条管理器（支持日志平滑滚动）"""
    def __init__(self):
        self.progress_bar = None
        self.lock = threading.Lock()

    def set_progress(self, current, total, desc=""):
        """设置进度"""
        with self.lock:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total, desc=desc, unit="file",
                                       bar_format='{desc} {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]',
                                       file=sys.stdout, dynamic_ncols=True)
            else:
                if self.progress_bar.desc != desc:
                    self.progress_bar.set_description(desc)
                self.progress_bar.total = total
                self.progress_bar.n = current
                self.progress_bar.refresh()

    def write_log(self, message):
        """通过tqdm安全地打印日志，不破坏进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.write(message)
            else:
                print(message)

    def close(self):
        """关闭进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.close()
                self.progress_bar = None


# 创建全局进度管理器
progress_mgr = BottomProgressBar()


class TqdmLogHandler(logging.Handler):
    """将日志重定向到tqdm.write的处理器"""
    def emit(self, record):
        try:
            msg = self.format(record)
            progress_mgr.write_log(msg)
        except Exception:
            self.handleError(record)


# 配置日志
handler = TqdmLogHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)


def get_remote_file_list(remote_user, remote_host, remote_base):
    """获取远端文件列表"""
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


def get_local_file_list(local_base):
    """获取本地文件列表"""
    local_files = set()
    for item in Path(local_base).rglob('*'):
        if item.is_file():
            rel_path = item.relative_to(local_base)
            local_files.add(str(rel_path.as_posix()))
    return local_files


def sync_upload(remote_user, remote_host, local_path, remote_path, max_retries=3, timeout=60):
    """上传单个文件（带重试机制）"""
    ssh_target = f"{remote_user}@{remote_host}"

    for attempt in range(max_retries):
        try:
            # 创建远程目录（5秒超时）
            remote_dir = '/'.join(remote_path.split('/')[:-1])
            mkdir_cmd = ['ssh', ssh_target, f'mkdir -p {remote_dir}']
            subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=5, check=False)

            # 上传文件（可配置超时，默认60秒）
            scp_cmd = ['scp', str(local_path), f'{ssh_target}:{remote_path}']
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=timeout)

            if result.returncode == 0:
                logger.info(f"✓ 上传成功: {local_path.name}")
                return True
            else:
                if attempt < max_retries - 1:
                    logger.warning(f"上传失败（尝试 {attempt+1}/{max_retries}），重试中...: {result.stderr.strip()[:100]}")
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error(f"上传失败（已重试 {max_retries} 次）: {result.stderr.strip()[:100]}")
                    return False

        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                logger.warning(f"上传超时（尝试 {attempt+1}/{max_retries}），重试中...")
                time.sleep(2 ** attempt)
            else:
                logger.error(f"上传超时（已重试 {max_retries} 次，超时{timeout}s）: {local_path} -> {remote_path}")
                return False
        except Exception as e:
            logger.error(f"上传异常: {local_path} -> {remote_path}: {e}")
            return False

    return False


def sync_download(remote_user, remote_host, remote_path, local_path, max_retries=3, timeout=60):
    """下载单个文件（带重试机制）"""
    ssh_target = f"{remote_user}@{remote_host}"

    for attempt in range(max_retries):
        try:
            # 创建本地目录（5秒超时）
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # 下载文件（可配置超时，默认60秒）
            scp_cmd = ['scp', f'{ssh_target}:{remote_path}', str(local_path)]
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=timeout)

            if result.returncode == 0:
                logger.info(f"✓ 下载成功: {local_path.name}")
                return True
            else:
                if attempt < max_retries - 1:
                    logger.warning(f"下载失败（尝试 {attempt+1}/{max_retries}），重试中...: {result.stderr.strip()[:100]}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"下载失败（已重试 {max_retries} 次）: {result.stderr.strip()[:100]}")
                    return False

        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                logger.warning(f"下载超时（尝试 {attempt+1}/{max_retries}），重试中...")
                time.sleep(2 ** attempt)
            else:
                logger.error(f"下载超时（已重试 {max_retries} 次，超时{timeout}s）: {remote_path} -> {local_path}")
                return False
        except Exception as e:
            logger.error(f"下载异常: {remote_path} -> {local_path}: {e}")
            return False

    return False


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="增量同步 cache 与远端 data（使用 SCP）")
    parser.add_argument("-u", "--user", required=True, help="远程用户名")
    parser.add_argument("-H", "--host", required=True, help="远程主机")
    parser.add_argument("--local-root", default=str(Path(__file__).parent.parent.parent / "cache"), help="本地 cache 根目录")
    parser.add_argument("--remote-root", default="/srv/music/data", help="远端 data 根目录")
    parser.add_argument("--workers", type=int, default=1, help="并行工作进程数")
    parser.add_argument("--direction", choices=['both', 'upload', 'download'], default='both', help="同步方向")
    parser.add_argument("--retries", type=int, default=3, help="每个文件的重试次数")
    parser.add_argument("--timeout", type=int, default=60, help="文件传输超时时间（秒），默认60秒")

    args = parser.parse_args()

    local_root = Path(args.local_root)
    if not local_root.exists():
        logger.error(f"本地目录不存在: {local_root}")
        return

    logger.info("开始同步...")
    logger.info(f"本地 cache: {local_root}")
    logger.info(f"远端 data: {args.user}@{args.host}:{args.remote_root}")

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
