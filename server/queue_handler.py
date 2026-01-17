#!/usr/bin/env python3
"""
队列处理脚本
监听队列并触发成品生成
"""

import os
import json
import logging
import subprocess
import time
from pathlib import Path
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler('/srv/music/repo/server/queue_handler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

QUEUE_FILE = '/srv/music/repo/server/queue.jsonl'
REPO_DIR = '/srv/music/repo'
CREATE_RELEASE_SCRIPT = '/srv/music/repo/release/create_release.py'


def process_queue():
    """处理队列中的请求"""
    if not os.path.exists(QUEUE_FILE):
        logger.info("队列文件不存在，等待新请求...")
        return

    with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not lines:
        logger.info("队列为空，等待新请求...")
        return

    # 清空队列文件
    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        f.write('')

    for line in lines:
        try:
            request = json.loads(line.strip())
            logger.info(f"处理请求: {request}")

            # 切换到仓库目录
            os.chdir(REPO_DIR)

            # 运行生成脚本
            cmd = ['/usr/bin/python3', CREATE_RELEASE_SCRIPT, '--mode', 'server']
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"请求处理成功: {request}")
            else:
                logger.error(f"请求处理失败: {request}")
                logger.error(f"错误输出: {result.stderr}")

        except json.JSONDecodeError as e:
            logger.error(f"解析队列行失败: {line}, 错误: {e}")
        except Exception as e:
            logger.error(f"处理请求时发生错误: {e}")


def main():
    """主循环"""
    logger.info("队列处理器启动...")

    while True:
        try:
            process_queue()
            time.sleep(5)  # 每5秒检查一次队列
        except KeyboardInterrupt:
            logger.info("队列处理器停止")
            break
        except Exception as e:
            logger.error(f"主循环错误: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
