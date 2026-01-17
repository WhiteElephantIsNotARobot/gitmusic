import os
import json
import hashlib
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone
import logging
import sys

# 尝试导入 mutagen
try:
    from mutagen import File
    from mutagen.id3 import ID3, APIC
except ImportError:
    print("错误: mutagen 库未安装", file=sys.stderr)
    exit(1)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# FFmpeg 版本和参数配置
FFMPEG_VERSION = "6.1.1"  # 固定版本
FFMPEG_EXTRACT_AUDIO_CMD = [
    'ffmpeg', '-i', '{input}', '-map', '0:a:0',
    '-c', 'copy', '-f', 'mp3',
    '-map_metadata', '-1',
    '-id3v2_version', '0',
    '-write_id3v1', '0',
    'pipe:1'
]

# 记录 FFmpeg 版本
try:
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    ffmpeg_version_output = result.stdout.split('\n')[0] if result.stdout else "Unknown"
    logger.info(f"FFmpeg 版本: {ffmpeg_version_output}")
    logger.info(f"配置的 FFmpeg 版本: {FFMPEG_VERSION}")
except Exception as e:
    logger.warning(f"无法获取 FFmpeg 版本: {e}")


def extract_audio_stream(audio_path):
    """提取纯净音频流并计算哈希（完全移除ID3标签）"""
    try:
        cmd = [arg.format(input=str(audio_path)) for arg in FFMPEG_EXTRACT_AUDIO_CMD]
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
        import re
        if re.match(r'^\[\d{2}:\d{2}\.\d{2}\]\s*$', line):
            continue
        lyrics_lines.append(line)
    return '\n'.join(lyrics_lines) if lyrics_lines else None


def download_cover(url, cache_root):
    """下载封面图片并保存到cache"""
    if not url:
        return None
    try:
        cmd = ['curl', '-s', url]
        result = subprocess.run(cmd, capture_output=True, check=True)
        cover_data = result.stdout
        if not cover_data or len(cover_data) < 100:
            return None
        cover_hash = hashlib.sha256(cover_data).hexdigest()
        cover_oid = f"sha256:{cover_hash}"

        target_path = cache_root / 'covers' / 'sha256' / cover_hash[:2] / f"{cover_hash}.jpg"
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if not target_path.exists():
            with open(target_path, 'wb') as f:
                f.write(cover_data)
            logger.info(f"下载封面: {cover_oid[:16]}...")
        return cover_oid
    except Exception as e:
        logger.error(f"下载封面失败: {e}")
        return None


def download_audio_with_ytdlp(url, cache_root, max_retries=5):
    """使用 yt-dlp 下载音频文件（直接显示进度）"""
    work_dir = cache_root.parent / 'work'
    work_dir.mkdir(parents=True, exist_ok=True)
    temp_path = work_dir / 'temp_download.mp3'

    for attempt in range(max_retries):
        try:
            temp_path.unlink(missing_ok=True)
            # 不捕获输出，直接让 yt-dlp 打印到终端
            cmd = ['python', '-m', 'yt_dlp', '-o', str(temp_path), url]
            subprocess.run(cmd, check=True)

            if not temp_path.exists() or temp_path.stat().st_size < 1000:
                continue

            audio_data, audio_hash = extract_audio_stream(temp_path)
            audio_oid = f"sha256:{audio_hash}"

            target_path = cache_root / 'objects' / 'sha256' / audio_hash[:2] / f"{audio_hash}.mp3"
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if not target_path.exists():
                shutil.move(str(temp_path), str(target_path))
                logger.info(f"保存音频: {audio_oid[:16]}...")
            else:
                temp_path.unlink(missing_ok=True)
            return audio_oid
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"下载音频失败: {e}")
                return None
            logger.warning(f"重试中... ({attempt+1}/{max_retries})")
    return None


def process_url(url, cache_root, metadata_file, max_retries=5):
    """处理单个URL"""
    try:
        logger.info(f"获取音乐信息: {url}")
        info_cmd = ['python', '-m', 'yt_dlp', '-j', url]
        info_res = subprocess.run(info_cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        info = json.loads(info_res.stdout)

        title = info.get('title', '未知')
        artists = info.get('creators', [])
        album = info.get('album', '')

        # 提取日期
        date_str = None
        upload_date = info.get('upload_date')
        if upload_date and len(upload_date) == 8:
            date_str = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        lyrics_lrc = None
        if 'subtitles' in info and 'lyrics' in info['subtitles']:
            lyrics_lrc = parse_lrc_lyrics(info['subtitles']['lyrics'][0].get('data', ''))

        cover_oid = download_cover(info.get('thumbnail'), cache_root)

        logger.info("开始下载音频...")
        audio_oid = download_audio_with_ytdlp(url, cache_root, max_retries)
        if not audio_oid:
            return False

        # 加载并更新 metadata
        metadata_list = []
        if metadata_file.exists():
            with open(metadata_file, 'r', encoding='utf-8') as f:
                metadata_list = [json.loads(l) for l in f if l.strip()]

        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        existing = next((item for item in metadata_list if item.get('audio_oid') == audio_oid), None)

        if existing:
            existing.update({'title': title, 'artists': artists})
            if album: existing['album'] = album
            if date_str: existing['date'] = date_str
            if lyrics_lrc: existing['uslt'] = lyrics_lrc
            if cover_oid: existing['cover_oid'] = cover_oid
            logger.info(f"更新元数据: {title}")
        else:
            new_item = {
                'audio_oid': audio_oid,
                'cover_oid': cover_oid,
                'title': title,
                'artists': artists,
                'album': album,
                'date': date_str,
                'uslt': lyrics_lrc,
                'created_at': now
            }
            # 移除None值
            new_item = {k: v for k, v in new_item.items() if v is not None}
            metadata_list.append(new_item)
            logger.info(f"新增元数据: {title}")

        temp_meta = metadata_file.with_suffix('.tmp')
        with open(temp_meta, 'w', encoding='utf-8') as f:
            for item in metadata_list:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        shutil.move(str(temp_meta), str(metadata_file))
        return True
    except Exception as e:
        logger.error(f"处理失败: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='使用 yt-dlp 下载音乐并入库')
    parser.add_argument('url', help='音乐URL')
    parser.add_argument('--cache-root', default=str(Path(__file__).parent.parent.parent / 'cache'))
    parser.add_argument('--retries', type=int, default=5)
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    metadata_file = repo_root / "metadata.jsonl"
    cache_root = Path(args.cache_root)

    if process_url(args.url, cache_root, metadata_file, args.retries):
        logger.info("✓ 下载完成！")
    else:
        logger.error("✗ 下载失败")

if __name__ == "__main__":
    main()
