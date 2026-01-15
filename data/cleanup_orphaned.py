#!/usr/bin/env python3
"""
清理孤立对象脚本
根据 metadata.jsonl 比对 cache/data 中的文件，删除不在数据库引用的对象
"""

import json
import shutil
from pathlib import Path
import logging
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


def load_metadata(metadata_file):
    """加载 metadata.jsonl，提取所有引用的 OID"""
    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return set(), set()

    audio_oids = set()
    cover_oids = set()

    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                audio_oid = item.get('audio_oid')
                if audio_oid:
                    audio_oids.add(audio_oid)
                cover_oid = item.get('cover_oid')
                if cover_oid:
                    cover_oids.add(cover_oid)
            except json.JSONDecodeError:
                continue

    return audio_oids, cover_oids


def find_orphaned_objects(base_dir, referenced_oids, data_type, is_remote=False, remote_user=None, remote_host=None):
    """查找未被引用的对象（支持本地和远程）"""
    if data_type == 'audio':
        rel_path = 'objects/sha256'
        ext = '.mp3'
    else:
        rel_path = 'covers/sha256'
        ext = '.jpg'

    orphaned = []
    
    if is_remote:
        try:
            ssh_target = f"{remote_user}@{remote_host}"
            remote_path = f"{base_dir}/{rel_path}"
            # 获取远程文件列表
            cmd = ['ssh', ssh_target, f"find {remote_path} -name '*{ext}' 2>/dev/null"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                full_path = line.strip()
                if not full_path: continue
                filename = Path(full_path).stem
                oid = f"sha256:{filename}"
                if oid not in referenced_oids:
                    orphaned.append(full_path)
        except Exception as e:
            logger.error(f"获取远端文件列表失败: {e}")
    else:
        search_dir = base_dir / rel_path
        if not search_dir.exists():
            return []
        for subdir in search_dir.iterdir():
            if not subdir.is_dir():
                continue
            for file_path in subdir.glob(f'*{ext}'):
                oid = f"sha256:{file_path.stem}"
                if oid not in referenced_oids:
                    orphaned.append(file_path)

    return orphaned


def cleanup_orphaned(cache_root, audio_oids, cover_oids, dry_run=True, remote_user=None, remote_host=None, remote_root=None):
    """清理孤立对象（本地和可选的远程）"""
    # 1. 本地清理
    logger.info("检查本地孤立对象...")
    orphaned_audio_local = find_orphaned_objects(cache_root, audio_oids, 'audio')
    orphaned_covers_local = find_orphaned_objects(cache_root, cover_oids, 'cover')
    
    # 2. 远程清理
    orphaned_audio_remote = []
    orphaned_covers_remote = []
    if remote_host:
        logger.info(f"检查远端孤立对象 ({remote_host})...")
        orphaned_audio_remote = find_orphaned_objects(remote_root, audio_oids, 'audio', True, remote_user, remote_host)
        orphaned_covers_remote = find_orphaned_objects(remote_root, cover_oids, 'cover', True, remote_user, remote_host)

    total_local = len(orphaned_audio_local) + len(orphaned_covers_local)
    total_remote = len(orphaned_audio_remote) + len(orphaned_covers_remote)

    if total_local == 0 and total_remote == 0:
        logger.info("没有发现孤立对象，无需清理")
        return 0, 0

    if dry_run:
        logger.info("=== 干运行模式，仅列出将删除的文件 ===")
        if total_local > 0:
            logger.info(f"本地待删除 ({total_local}):")
            for p in orphaned_audio_local: logger.info(f"  [音频] {p}")
            for p in orphaned_covers_local: logger.info(f"  [封面] {p}")
        if total_remote > 0:
            logger.info(f"远端待删除 ({total_remote}):")
            for p in orphaned_audio_remote: logger.info(f"  [远程] {p}")
        return 0, 0

    # 执行本地删除
    deleted_local = 0
    if total_local > 0:
        progress_mgr.set_progress(0, total_local, "本地清理")
        for idx, path in enumerate(orphaned_audio_local + orphaned_covers_local, 1):
            try:
                path.unlink()
                logger.info(f"✓ 删除本地: {path.name}")
                deleted_local += 1
            except Exception as e:
                logger.error(f"✗ 删除本地失败 {path.name}: {e}")
            progress_mgr.set_progress(idx, total_local, "本地清理")

    # 执行远程删除
    deleted_remote = 0
    if total_remote > 0:
        progress_mgr.set_progress(0, total_remote, "远端清理")
        ssh_target = f"{remote_user}@{remote_host}"
        for idx, path in enumerate(orphaned_audio_remote + orphaned_covers_remote, 1):
            try:
                cmd = ['ssh', ssh_target, f"rm '{path}'"]
                subprocess.run(cmd, check=True, capture_output=True)
                logger.info(f"✓ 删除远端: {Path(path).name}")
                deleted_remote += 1
            except Exception as e:
                logger.error(f"✗ 删除远端失败 {Path(path).name}: {e}")
            progress_mgr.set_progress(idx, total_remote, "远端清理")

    # 清理本地空目录
    for root in [cache_root / 'objects/sha256', cache_root / 'covers/sha256']:
        if root.exists():
            for subdir in root.iterdir():
                if subdir.is_dir() and not any(subdir.iterdir()):
                    try:
                        subdir.rmdir()
                        logger.info(f"清理空目录: {subdir.name}")
                    except: pass

    return deleted_local, deleted_remote


def main():
    import argparse

    parser = argparse.ArgumentParser(description="清理本地和远端 cache 中不在 metadata.jsonl 中的孤立对象")
    parser.add_argument("--cache-root", default=str(Path(__file__).parent.parent.parent / "cache"),
                       help="本地 cache 根目录")
    parser.add_argument("-u", "--user", help="远程用户名")
    parser.add_argument("-H", "--host", help="远程主机")
    parser.add_argument("--remote-root", default="/srv/music/data", help="远端 data 根目录")
    parser.add_argument("--metadata", help="指定 metadata.jsonl 路径")
    parser.add_argument("--confirm", action="store_true", help="确认执行删除")
    args = parser.parse_args()

    cache_root = Path(args.cache_root)
    # ... 保持 metadata 加载逻辑不变 ...
    if args.metadata:
        metadata_file = Path(args.metadata)
    else:
        repo_root = Path(__file__).parent.parent
        metadata_file = repo_root / "metadata.jsonl"

    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return

    audio_oids, cover_oids = load_metadata(metadata_file)
    logger.info(f"metadata 中引用: {len(audio_oids)} 个音频, {len(cover_oids)} 个封面")

    dry_run = not args.confirm
    del_local, del_remote = cleanup_orphaned(cache_root, audio_oids, cover_oids, dry_run, args.user, args.host, args.remote_root)

    progress_mgr.close()
    if not dry_run:
        logger.info(f"清理完成: 本地删除 {del_local} 个, 远端删除 {del_remote} 个")


if __name__ == "__main__":
    main()
