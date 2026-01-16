#!/usr/bin/env python3
"""
yt-dlp 下载脚本
从网易云音乐等平台下载音乐，直接入库到 metadata.jsonl
"""

import os
import json
import hashlib
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
import logging
import sys
import threading
from io import StringIO

# 尝试导入 mutagen 和 tqdm
try:
    from mutagen import File
    from mutagen.id3 import ID3, APIC
except ImportError:
    print("错误: mutagen 库未安装", file=sys.stderr)
    exit(1)

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


def extract_audio_stream(audio_path):
    """提取纯净音频流并计算哈希（完全移除ID3标签）"""
    try:
        cmd = [
            'ffmpeg', '-i', str(audio_path), '-map', '0:a:0',
            '-c', 'copy', '-f', 'mp3',
            '-map_metadata', '-1',  # 移除元数据
            '-id3v2_version', '0',  # 不写入ID3v2标签
            '-write_id3v1', '0',    # 不写入ID3v1标签
            'pipe:1'
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        audio_data = result.stdout
        audio_hash = hashlib.sha256(audio_data).hexdigest()
        return audio_data, audio_hash
    except subprocess.CalledProcessError as e:
        logger.error(f"提取音频流失败 {audio_path}: {e}")
        raise


def parse_lrc_lyrics(lrc_data):
    """解析LRC格式歌词，保留时间戳"""
    if not lrc_data:
        return None
    
    lines = lrc_data.split('\n')
    lyrics_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 保留时间戳 [00:00.00]
        # 只移除空行和纯时间戳行
        import re
        # 检查是否是纯时间戳行（只有时间戳没有内容）
        if re.match(r'^\[\d{2}:\d{2}\.\d{2}\]\s*$', line):
            continue
        
        lyrics_lines.append(line)
    
    return '\n'.join(lyrics_lines) if lyrics_lines else None


def download_cover(url, cache_root):
    """下载封面图片并保存到cache"""
    if not url:
        return None
    
    try:
        # 下载图片
        cmd = ['curl', '-s', url]
        result = subprocess.run(cmd, capture_output=True, check=True)
        cover_data = result.stdout
        
        if not cover_data or len(cover_data) < 100:
            return None
        
        # 计算哈希
        cover_hash = hashlib.sha256(cover_data).hexdigest()
        cover_oid = f"sha256:{cover_hash}"
        
        # 保存到cache
        hash_hex = cover_hash
        subdir = hash_hex[:2]
        target_dir = cache_root / 'covers' / 'sha256' / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{hash_hex}.jpg"
        
        if not target_path.exists():
            with open(target_path, 'wb') as f:
                f.write(cover_data)
            logger.info(f"下载封面: {cover_oid[:16]}...")
        
        return cover_oid
    except Exception as e:
        logger.error(f"下载封面失败: {e}")
        return None


def download_audio(url, cache_root, audio_oid):
    """下载音频文件并保存到cache"""
    if not url or not audio_oid:
        return None
    
    try:
        # 下载音频
        cmd = ['curl', '-s', url]
        result = subprocess.run(cmd, capture_output=True, check=True)
        audio_data = result.stdout
        
        if not audio_data or len(audio_data) < 1000:
            return None
        
        # 保存到cache
        hash_hex = audio_oid.replace('sha256:', '')
        subdir = hash_hex[:2]
        target_dir = cache_root / 'objects' / 'sha256' / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{hash_hex}.mp3"
        
        if not target_path.exists():
            with open(target_path, 'wb') as f:
                f.write(audio_data)
            logger.info(f"下载音频: {audio_oid[:16]}...")
        
        return audio_oid
    except Exception as e:
        logger.error(f"下载音频失败: {e}")
        return None


def load_metadata(metadata_file):
    """加载现有 metadata.jsonl"""
    if not metadata_file.exists():
        return []

    metadata_list = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metadata_list.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return metadata_list


def save_metadata(metadata_file, metadata_list):
    """保存 metadata.jsonl（保持原始顺序）"""
    # 写入临时文件
    temp_file = metadata_file.with_suffix('.tmp')
    with open(temp_file, 'w', encoding='utf-8') as f:
        for item in metadata_list:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    # 原子替换
    shutil.move(str(temp_file), str(metadata_file))


def process_url(url, cache_root, metadata_file):
    """处理单个URL"""
    try:
        logger.info(f"获取音乐信息: {url}")
        
        # 使用 yt-dlp 获取信息
        cmd = ['python', '-m', 'yt_dlp', '-j', url]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        
        # 解析JSON
        info = json.loads(result.stdout)
        
        # 提取基本信息
        title = info.get('title', '未知')
        artists = info.get('creators', [])
        album = info.get('album', '')
        duration = info.get('duration', 0)
        
        # 提取歌词
        lyrics_lrc = None
        if 'subtitles' in info and 'lyrics' in info['subtitles']:
            lyrics_data = info['subtitles']['lyrics'][0].get('data', '')
            lyrics_lrc = parse_lrc_lyrics(lyrics_data)
        
        # 提取封面URL
        cover_url = info.get('thumbnail')
        
        # 选择最高质量的音频格式（exhigh: 320kbps）
        audio_url = None
        for fmt in info.get('formats', []):
            if fmt.get('format_id') == 'exhigh':
                audio_url = fmt.get('url')
                break
        
        if not audio_url:
            # 如果没有exhigh，选择第一个音频格式
            for fmt in info.get('formats', []):
                if fmt.get('vcodec') == 'none':
                    audio_url = fmt.get('url')
                    break
        
        if not audio_url:
            logger.error("未找到可用的音频格式")
            return False
        
        # 下载封面
        cover_oid = None
        if cover_url:
            cover_oid = download_cover(cover_url, cache_root)
        
        # 下载音频并计算哈希
        logger.info("下载音频文件...")
        temp_audio = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        temp_audio_path = Path(temp_audio.name)
        temp_audio.close()
        
        # 下载音频到临时文件
        cmd = ['curl', '-s', '-o', str(temp_audio_path), audio_url]
        subprocess.run(cmd, check=True)
        
        # 提取音频流并计算哈希
        audio_data, audio_hash = extract_audio_stream(temp_audio_path)
        audio_oid = f"sha256:{audio_hash}"
        
        # 保存音频到cache
        download_audio(audio_url, cache_root, audio_oid)
        
        # 清理临时文件
        temp_audio_path.unlink(missing_ok=True)
        
        # 检查是否已存在
        metadata_list = load_metadata(metadata_file)
        existing = None
        for item in metadata_list:
            if item.get('audio_oid') == audio_oid:
                existing = item
                break
        
        now = datetime.now().isoformat() + 'Z'
        
        if existing:
            # 更新现有条目
            existing['title'] = title
            existing['artists'] = artists
            if album:
                existing['album'] = album
            if duration:
                existing['duration'] = duration
            if lyrics_lrc:
                existing['uslt'] = lyrics_lrc
            if cover_oid:
                existing['cover_oid'] = cover_oid
            existing['updated_at'] = now
            
            logger.info(f"更新元数据: {title}")
        else:
            # 新增条目
            new_item = {
                'audio_oid': audio_oid,
                'title': title,
                'artists': artists,
                'created_at': now,
                'updated_at': now
            }
            
            if album:
                new_item['album'] = album
            if duration:
                new_item['duration'] = duration
            if lyrics_lrc:
                new_item['uslt'] = lyrics_lrc
            if cover_oid:
                new_item['cover_oid'] = cover_oid
            
            metadata_list.append(new_item)
            logger.info(f"新增元数据: {title}")
        
        # 保存metadata
        save_metadata(metadata_file, metadata_list)
        
        return True
        
    except Exception as e:
        logger.error(f"处理失败: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description='使用 yt-dlp 下载音乐并入库')
    parser.add_argument('url', help='音乐URL（支持网易云音乐等平台）')
    parser.add_argument('--cache-root', default=str(Path(__file__).parent.parent.parent / 'cache'),
                       help='cache 根目录，默认 ../cache')
    args = parser.parse_args()

    # 确定目录
    repo_root = Path(__file__).parent.parent
    metadata_file = repo_root / "metadata.jsonl"
    cache_root = Path(args.cache_root)

    if not metadata_file.exists():
        logger.error(f"metadata.jsonl 不存在: {metadata_file}")
        return

    # 处理URL
    success = process_url(args.url, cache_root, metadata_file)

    # 关闭进度条
    progress_mgr.close()

    if success:
        logger.info("\n✓ 下载完成！")
        logger.info("接下来可以:")
        logger.info("1. 运行 repo/release/create_release_local.py 生成成品")
        logger.info("2. 运行 repo/data/sync_cache.py 同步到远端")
        logger.info("3. 手动执行 git add/commit/push")
    else:
        logger.error("\n✗ 下载失败")


if __name__ == "__main__":
    main()
