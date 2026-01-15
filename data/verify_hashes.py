#!/usr/bin/env python3
"""
校验 cache/data 中文件哈希值是否符合文件名（oid）
"""

import hashlib
import json
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
                # 如果描述改变，需要关闭并重新创建
                if self.progress_bar.desc != desc:
                    self.progress_bar.close()
                    self.progress_bar = tqdm(total=total, desc=desc, unit="file",
                                           bar_format='{desc} {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]',
                                           file=sys.stdout)
                else:
                    self.progress_bar.total = total
                    self.progress_bar.update(current - self.progress_bar.n)

    def close(self):
        """关闭进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.close()
                self.progress_bar = None


# 创建全局进度管理器
progress_mgr = BottomProgressBar()


# 配置日志（使用默认处理器，不自定义）
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def verify_file_hash(file_path, expected_hash):
    """校验单个文件的哈希值"""
    try:
        with open(file_path, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        if file_hash == expected_hash:
            return True, file_hash
        else:
            return False, file_hash
    except Exception as e:
        logger.error(f"读取文件失败 {file_path}: {e}")
        return None, None


def main():
    """主函数"""
    cache_root = Path(__file__).parent.parent.parent / "cache"
    objects_root = cache_root / 'objects' / 'sha256'
    covers_root = cache_root / 'covers' / 'sha256'

    if not cache_root.exists():
        logger.error(f"cache 目录不存在: {cache_root}")
        return

    logger.info("开始校验 cache 中的文件哈希...")

    # 校验音频文件
    audio_files = list(objects_root.glob('*/*.mp3'))
    audio_ok = 0
    audio_fail = 0

    if audio_files:
        progress_mgr.set_progress(0, len(audio_files), "校验音频")

        for idx, file_path in enumerate(audio_files, 1):
            expected_hash = file_path.stem
            ok, actual_hash = verify_file_hash(file_path, expected_hash)
            if ok:
                audio_ok += 1
            elif ok is False:
                audio_fail += 1
                logger.error(f"音频哈希不匹配: {file_path.name}, 期望: {expected_hash}, 实际: {actual_hash}")
            else:
                logger.error(f"音频文件读取失败: {file_path.name}")
            progress_mgr.set_progress(idx, len(audio_files), "校验音频")

        # 音频校验完成，关闭进度条
        progress_mgr.close()

    # 校验封面文件
    cover_files = list(covers_root.glob('*/*.jpg'))
    cover_ok = 0
    cover_fail = 0

    if cover_files:
        logger.info(f"开始校验 {len(cover_files)} 个封面文件...")
        progress_mgr.set_progress(0, len(cover_files), "校验封面")

        for idx, file_path in enumerate(cover_files, 1):
            expected_hash = file_path.stem
            ok, actual_hash = verify_file_hash(file_path, expected_hash)
            if ok:
                cover_ok += 1
            elif ok is False:
                cover_fail += 1
                logger.error(f"封面哈希不匹配: {file_path.name}, 期望: {expected_hash}, 实际: {actual_hash}")
            else:
                logger.error(f"封面文件读取失败: {file_path.name}")
            progress_mgr.set_progress(idx, len(cover_files), "校验封面")

        # 封面校验完成，关闭进度条
        progress_mgr.close()
        logger.info(f"封面校验完成")

    # 打印汇总信息（在进度条关闭后）
    logger.info(f"找到 {len(audio_files)} 个音频文件")
    logger.info(f"找到 {len(cover_files)} 个封面文件")

    logger.info(f"\n校验结果:")
    logger.info(f"音频文件: 成功 {audio_ok}/{len(audio_files)}, 失败 {audio_fail}")
    logger.info(f"封面文件: 成功 {cover_ok}/{len(cover_files)}, 失败 {cover_fail}")

    if audio_fail == 0 and cover_fail == 0:
        logger.info("所有文件哈希校验通过！")
    else:
        logger.error("部分文件哈希校验失败！")


if __name__ == "__main__":
    main()
