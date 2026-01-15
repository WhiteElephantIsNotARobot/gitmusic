#!/usr/bin/env python3
"""
压缩图片转 JPG 并重算哈希，更新 metadata.jsonl 中的 cover_oid
"""

import hashlib
import json
import shutil
import subprocess
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
    """底部固定进度条管理器（简化版，不清除进度条）"""
    def __init__(self):
        self.progress_bar = None
        self.lock = threading.Lock()

    def set_progress(self, current, total, desc=""):
        """设置进度"""
        with self.lock:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total, desc=desc, unit="file",
                                       bar_format='{desc} {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]',
                                       file=sys.stdout)
            else:
                self.progress_bar.total = total
                self.progress_bar.set_description(desc)
                self.progress_bar.update(current - self.progress_bar.n)

    def close(self):
        """关闭进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.close()
                self.progress_bar = None


# 创建全局进度管理器
progress_mgr = BottomProgressBar()


class LogHandler(logging.Handler):
    """自定义日志处理器"""
    def emit(self, record):
        msg = self.format(record)
        progress_mgr.log(msg)


# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', handlers=[LogHandler()])
logger = logging.getLogger(__name__)


def compress_to_jpg(input_path, output_path):
    """使用 ffmpeg 将图片压缩为 JPG（确保文件变小）"""
    try:
        # 使用更激进的压缩参数
        # -q:v 5-8: 质量值越大，文件越小（2-31，推荐5-8）
        # -scale 600x600: 缩小尺寸
        cmd = [
            'ffmpeg', '-i', str(input_path),
            '-vf', 'scale=600:600:force_original_aspect_ratio=decrease',
            '-q:v', '6',  # 更高的质量值 = 更小的文件
            '-f', 'image2',
            str(output_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"压缩图片失败 {input_path}: {e}")
        return False


def calculate_hash(file_path):
    """计算文件 SHA256 哈希"""
    try:
        with open(file_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        logger.error(f"计算哈希失败 {file_path}: {e}")
        return None


def main():
    """主函数"""
    cache_root = Path(__file__).parent.parent.parent / "cache"
    covers_root = cache_root / 'covers' / 'sha256'
    metadata_file = Path(__file__).parent.parent / "metadata.jsonl"

    if not covers_root.exists():
        logger.error(f"covers 目录不存在: {covers_root}")
        return

    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return

    # 读取 metadata
    metadata_list = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                metadata_list.append(json.loads(line))

    logger.info(f"读取到 {len(metadata_list)} 条元数据")

    # 处理每个封面
    updated_count = 0
    total_to_process = len([m for m in metadata_list if 'cover_oid' in m])

    if total_to_process > 0:
        progress_mgr.set_progress(0, total_to_process, "压缩中")

        processed = 0
        for metadata in metadata_list:
            if 'cover_oid' not in metadata:
                continue

            processed += 1
            old_oid = metadata['cover_oid'].replace('sha256:', '')
            old_path = covers_root / old_oid[:2] / f"{old_oid}.jpg"

            if not old_path.exists():
                logger.warning(f"封面文件不存在: {old_path}")
                progress_mgr.set_progress(processed, total_to_process, "压缩中")
                continue

            # 检查是否已经是 JPG 且大小合适
            if old_path.stat().st_size < 200 * 1024:  # 小于 200KB 跳过
                progress_mgr.set_progress(processed, total_to_process, "压缩中")
                continue

            # 压缩为 JPG
            temp_path = old_path.with_suffix('.tmp.jpg')
            if compress_to_jpg(old_path, temp_path):
                # 检查压缩后文件是否更小
                old_size = old_path.stat().st_size
                new_size = temp_path.stat().st_size

                if new_size >= old_size:
                    # 压缩后没有变小，删除临时文件，跳过
                    logger.warning(f"压缩后未变小 ({old_size/1024:.1f}KB -> {new_size/1024:.1f}KB)，跳过")
                    temp_path.unlink(missing_ok=True)
                    progress_mgr.set_progress(processed, total_to_process, "压缩中")
                    continue

                # 计算新哈希
                new_hash = calculate_hash(temp_path)
                if not new_hash:
                    temp_path.unlink(missing_ok=True)
                    progress_mgr.set_progress(processed, total_to_process, "压缩中")
                    continue

                # 如果哈希相同，说明已经是优化后的 JPG，跳过
                if new_hash == old_oid:
                    temp_path.unlink(missing_ok=True)
                    progress_mgr.set_progress(processed, total_to_process, "压缩中")
                    continue

                # 移动到新位置
                new_dir = covers_root / new_hash[:2]
                new_dir.mkdir(parents=True, exist_ok=True)
                new_path = new_dir / f"{new_hash}.jpg"

                shutil.move(str(temp_path), str(new_path))

                # 更新 metadata
                metadata['cover_oid'] = f"sha256:{new_hash}"
                metadata['updated_at'] = metadata.get('updated_at', metadata.get('created_at', ''))

                logger.info(f"压缩并更新封面: {old_oid[:8]}... -> {new_hash[:8]}... ({old_size/1024:.1f}KB → {new_size/1024:.1f}KB)")
                updated_count += 1

            progress_mgr.set_progress(processed, total_to_process, "压缩中")

    if updated_count > 0:
        # 写回 metadata.jsonl（临时文件 + 原子替换）
        temp_metadata = metadata_file.with_suffix('.tmp.jsonl')
        with open(temp_metadata, 'w', encoding='utf-8') as f:
            for metadata in metadata_list:
                f.write(json.dumps(metadata, ensure_ascii=False) + '\n')

        shutil.move(str(temp_metadata), str(metadata_file))
        logger.info(f"已更新 {updated_count} 条元数据的 cover_oid")
    else:
        logger.info("没有需要更新的封面")

    # 关闭进度条
    progress_mgr.close()


if __name__ == "__main__":
    main()
