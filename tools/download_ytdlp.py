import os
import sys
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.audio import AudioIO
from libgitmusic.hash_utils import HashUtils
from libgitmusic.object_store import ObjectStore


def fetch_metadata(url: str) -> Optional[Dict]:
    """使用 yt-dlp 获取视频元数据"""
    cmd = ["yt-dlp", "--dump-json", "--skip-download", url]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", check=True
        )
        info = json.loads(result.stdout)
        return {
            "title": info.get("title", "Unknown"),
            "artists": info.get("creators") or [info.get("uploader", "Unknown")],
            "duration": info.get("duration"),
            "bitrate": info.get("abr"),
            "url": url,
            "description": info.get("description"),
            "upload_date": info.get("upload_date"),
            "thumbnail": info.get("thumbnail"),
        }
    except subprocess.CalledProcessError as e:
        EventEmitter.error(f"Failed to fetch metadata: {e.stderr}")
        return None
    except json.JSONDecodeError as e:
        EventEmitter.error(f"Failed to parse metadata JSON: {str(e)}")
        return None


def download_audio(
    url: str, output_dir: Path, extract_cover: bool = True
) -> Tuple[Optional[Path], Optional[Dict]]:
    """
    下载音频并返回文件路径和元数据

    Args:
        url: 视频URL
        output_dir: 输出目录
        extract_cover: 是否提取封面

    Returns:
        (文件路径, 元数据字典) 或 (None, None) 如果失败
    """
    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp(prefix="gitmusic_download_"))
    try:
        # 1. 下载音频（最高质量，MP3格式）
        cmd = [
            "yt-dlp",
            "-x",  # 提取音频
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",  # 最佳质量
            "--output",
            str(temp_dir / "%(title)s.%(ext)s"),
            "--no-playlist",
            url,
        ]

        EventEmitter.item_event(url, "downloading")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        # 查找下载的文件
        mp3_files = list(temp_dir.glob("*.mp3"))
        if not mp3_files:
            EventEmitter.error(f"No audio file downloaded from {url}")
            return None, None

        downloaded_file = mp3_files[0]

        # 2. 获取元数据
        metadata = fetch_metadata(url)
        if not metadata:
            EventEmitter.log("warn", f"Using fallback metadata for {url}")
            metadata = {
                "title": downloaded_file.stem,
                "artists": ["Unknown"],
                "url": url,
            }

        # 3. 计算音频哈希
        EventEmitter.item_event(downloaded_file.name, "calculating_hash")
        audio_oid = AudioIO.get_audio_hash(downloaded_file)
        metadata["audio_oid"] = audio_oid

        # 4. 提取封面
        cover_data = None
        if extract_cover:
            cover_data = AudioIO.extract_cover(downloaded_file)
            if cover_data:
                cover_oid = HashUtils.hash_bytes(cover_data, "sha256")
                metadata["cover_oid"] = cover_oid
                EventEmitter.item_event(
                    downloaded_file.name, "cover_extracted", f"OID: {cover_oid[:16]}..."
                )
            else:
                EventEmitter.log("info", f"No cover found in {downloaded_file.name}")

        # 5. 生成目标文件名
        artists = metadata.get("artists", ["Unknown"])
        if isinstance(artists, list):
            artist_str = " & ".join(artists)
        else:
            artist_str = str(artists)

        title = metadata.get("title", "Unknown")
        raw_filename = f"{artist_str} - {title}.mp3"
        safe_filename = AudioIO.sanitize_filename(raw_filename)
        target_path = output_dir / safe_filename

        # 处理文件名冲突
        counter = 1
        while target_path.exists():
            safe_filename = AudioIO.sanitize_filename(
                f"{artist_str} - {title} ({counter}).mp3"
            )
            target_path = output_dir / safe_filename
            counter += 1

        # 6. 移动文件到输出目录
        shutil.move(str(downloaded_file), str(target_path))

        # 7. 嵌入元数据（如果需要）
        if cover_data:
            # 重新嵌入封面到最终文件
            try:
                AudioIO.embed_metadata(target_path, metadata, cover_data, target_path)
                EventEmitter.item_event(target_path.name, "metadata_embedded", "")
            except Exception as e:
                EventEmitter.log("warn", f"Failed to embed metadata: {str(e)}")

        EventEmitter.item_event(
            target_path.name,
            "download_complete",
            f"Audio OID: {audio_oid[:16]}...",
        )

        return target_path, metadata

    except subprocess.CalledProcessError as e:
        EventEmitter.error(
            f"Download failed for {url}: {e.stderr}", {"url": url, "error": e.stderr}
        )
        return None, None
    except Exception as e:
        EventEmitter.error(
            f"Unexpected error downloading {url}: {str(e)}", {"url": url}
        )
        return None, None
    finally:
        # 清理临时目录
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="下载音频文件并提取元数据（支持YouTube等平台）"
    )
    parser.add_argument("url", nargs="?", help="视频URL")
    parser.add_argument("--batch-file", help="包含URL列表的文件路径（每行一个URL）")
    parser.add_argument("--no-cover", action="store_true", help="不提取封面")
    parser.add_argument(
        "--metadata-only", action="store_true", help="仅获取元数据，不下载"
    )
    parser.add_argument("--no-preview", action="store_true", help="跳过元数据预览")
    parser.add_argument("--limit", type=int, help="最大下载数量（批量模式）")
    args = parser.parse_args()

    # 获取输出目录 - 仅从环境变量获取
    work_dir_path = os.environ.get("GITMUSIC_WORK_DIR")
    if not work_dir_path:
        EventEmitter.error("Missing GITMUSIC_WORK_DIR environment variable.")
        return

    output_dir = Path(work_dir_path)

    output_dir.mkdir(parents=True, exist_ok=True)

    # 收集URL
    urls = []
    if args.batch_file:
        batch_file = Path(args.batch_file)
        if not batch_file.exists():
            EventEmitter.error(f"Batch file not found: {batch_file}")
            return

        with open(batch_file, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip().startswith("http")]

        if not urls:
            EventEmitter.error(f"No valid URLs found in batch file: {batch_file}")
            return
    elif args.url:
        urls = [args.url]
    else:
        EventEmitter.error("No URL provided. Specify URL or --batch-file.")
        return

    # 限制数量
    if args.limit and args.limit > 0:
        urls = urls[: args.limit]

    # 元数据仅模式
    if args.metadata_only:
        EventEmitter.phase_start("fetch_metadata", total_items=len(urls))
        metadata_list = []

        for i, url in enumerate(urls):
            EventEmitter.item_event(url, "fetching_metadata")
            metadata = fetch_metadata(url)

            if metadata:
                metadata_list.append(metadata)
                EventEmitter.item_event(
                    url,
                    "metadata_fetched",
                    f"Title: {metadata.get('title', 'Unknown')}",
                )
            else:
                EventEmitter.item_event(
                    url, "metadata_failed", "Failed to fetch metadata"
                )

            EventEmitter.batch_progress("fetch_metadata", i + 1, len(urls))

        EventEmitter.result(
            "ok",
            message=f"Fetched metadata for {len(metadata_list)}/{len(urls)} URLs",
            artifacts={"metadata": metadata_list},
        )
        return

    # 完整下载模式
    EventEmitter.phase_start("download", total_items=len(urls))

    successful_downloads = []
    failed_downloads = []

    for i, url in enumerate(urls):
        EventEmitter.item_event(url, "processing")

        file_path, metadata = download_audio(
            url, output_dir, extract_cover=not args.no_cover
        )

        if file_path and metadata:
            successful_downloads.append(
                {
                    "url": url,
                    "file": str(file_path),
                    "audio_oid": metadata.get("audio_oid"),
                    "cover_oid": metadata.get("cover_oid"),
                    "title": metadata.get("title"),
                    "artists": metadata.get("artists"),
                }
            )
        else:
            failed_downloads.append(url)
            EventEmitter.item_event(url, "failed", "Download failed")

        EventEmitter.batch_progress("download", i + 1, len(urls))

    # 生成结果
    artifacts = {
        "successful": successful_downloads,
        "failed": failed_downloads,
        "total_urls": len(urls),
        "output_dir": str(output_dir),
    }

    if failed_downloads:
        EventEmitter.result(
            "warn",
            message=f"Download completed with {len(failed_downloads)} failures",
            artifacts=artifacts,
        )
    else:
        EventEmitter.result(
            "ok",
            message=f"Successfully downloaded {len(successful_downloads)} files",
            artifacts=artifacts,
        )


if __name__ == "__main__":
    main()
