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

# FFmpeg 版本和参数配置
FFMPEG_VERSION = "6.1.1"  # 固定版本
FFMPEG_COMPRESS_IMAGE_CMD = [
    'ffmpeg', '-i', '{input}',
    '-vf', "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
    '-q:v', '2',
    '-f', 'image2',
    '-y', '{output}'
]

# 记录 FFmpeg 版本
try:
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    ffmpeg_version_output = result.stdout.split('\n')[0] if result.stdout else "Unknown"
    logger.info(f"FFmpeg 版本: {ffmpeg_version_output}")
    logger.info(f"配置的 FFmpeg 版本: {FFMPEG_VERSION}")
except Exception as e:
    logger.warning(f"无法获取 FFmpeg 版本: {e}")


def compress_to_jpg(input_path, output_path):
    """使用 ffmpeg 将图片压缩为 JPG（平衡画质与体积）"""
    try:
        cmd = [arg.format(input=str(input_path), output=str(output_path)) for arg in FFMPEG_COMPRESS_IMAGE_CMD]
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
    # 只统计真正需要检查的文件
    items_with_cover = [m for m in metadata_list if 'cover_oid' in m]
    total_to_process = len(items_with_cover)

    if total_to_process > 0:
        progress_mgr.set_progress(0, total_to_process, "检查封面")

        for idx, metadata in enumerate(items_with_cover, 1):
            old_oid = metadata['cover_oid'].replace('sha256:', '')
            old_path = covers_root / old_oid[:2] / f"{old_oid}.jpg"

            if not old_path.exists():
                progress_mgr.set_progress(idx, total_to_process, "检查封面")
                continue

            # 检查是否已经是高质量且大小合适
            # 只有大于 500KB 的才尝试压缩
            if old_path.stat().st_size < 500 * 1024:
                progress_mgr.set_progress(idx, total_to_process, "检查封面")
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
                    progress_mgr.set_progress(idx, total_to_process, "压缩中")
                    continue

                # 计算新哈希
                new_hash = calculate_hash(temp_path)
                if not new_hash:
                    temp_path.unlink(missing_ok=True)
                    progress_mgr.set_progress(idx, total_to_process, "压缩中")
                    continue

                # 如果哈希相同，说明已经是优化后的 JPG，跳过
                if new_hash == old_oid:
                    temp_path.unlink(missing_ok=True)
                    progress_mgr.set_progress(idx, total_to_process, "压缩中")
                    continue

                # 移动到新位置
                new_dir = covers_root / new_hash[:2]
                new_dir.mkdir(parents=True, exist_ok=True)
                new_path = new_dir / f"{new_hash}.jpg"

                shutil.move(str(temp_path), str(new_path))

                # 更新 metadata
                metadata['cover_oid'] = f"sha256:{new_hash}"

                logger.info(f"压缩并更新封面: {old_oid[:8]}... -> {new_hash[:8]}... ({old_size/1024:.1f}KB → {new_size/1024:.1f}KB)")
                updated_count += 1

            progress_mgr.set_progress(idx, total_to_process, "检查封面")

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
