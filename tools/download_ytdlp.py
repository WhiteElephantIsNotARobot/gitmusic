import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.audio import AudioIO

def fetch_metadata(url):
    cmd = ["yt-dlp", "--dump-json", "--skip-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode == 0:
        info = json.loads(result.stdout)
        return {
            "title": info.get("title"),
            "artists": info.get("creators") or [info.get("uploader")],
            "duration": info.get("duration"),
            "bitrate": info.get("abr"),
            "url": url
        }
    return None

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('url', nargs='?')
    parser.add_argument('--batch-file', help="包含 URL 的文件路径")
    parser.add_argument('--fetch', action='store_true', help="仅获取元数据")
    parser.add_argument('--no-preview', action='store_true')
    args = parser.parse_args()

    urls = []
    if args.batch_file:
        with open(args.batch_file, 'r') as f:
            urls = [line.strip() for line in f if line.strip().startswith("http")]
    elif args.url:
        urls = [args.url]

    if not urls:
        EventEmitter.error("No URLs provided")
        return

    EventEmitter.phase_start("fetch", total_items=len(urls))
    results = []
    for url in urls:
        meta = fetch_metadata(url)
        if meta:
            results.append(meta)
            EventEmitter.item_event(url, "fetched", message=f"{meta['title']} ({meta['duration']}s)")

    EventEmitter.result("ok", message="Fetch completed", artifacts={"metadata_list": results})


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
