import os
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from ..events import EventEmitter
from ..audio import AudioIO
from ..hash_utils import HashUtils


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
    url: str, output_dir: Path, extract_cover: bool = True, no_preview: bool = False
) -> Tuple[Optional[Path], Optional[Dict]]:
    """
    下载音频并返回文件路径和元数据

    Args:
        url: 视频URL
        output_dir: 输出目录
        extract_cover: 是否提取封面
        no_preview: 是否隐藏预览信息

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

        if not no_preview:
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


def download_logic(
    urls: List[str],
    output_dir: Path,
    extract_cover: bool = True,
    metadata_only: bool = False,
    no_preview: bool = False,
    limit: Optional[int] = None,
) -> Tuple[List[Dict], List[str], Optional[str]]:
    """
    Download命令的核心业务逻辑

    Args:
        urls: URL列表
        output_dir: 输出目录
        extract_cover: 是否提取封面
        metadata_only: 是否仅获取元数据
        no_preview: 是否隐藏预览信息
        limit: 最大下载数量

    Returns:
        (成功下载列表, 失败URL列表, 错误消息)
    """
    # 限制数量
    if limit and limit > 0:
        urls = urls[:limit]

    if metadata_only:
        # 元数据仅模式
        EventEmitter.phase_start("fetch_metadata", total_items=len(urls))
        metadata_list = []

        for i, url in enumerate(urls):
            if not no_preview:
                EventEmitter.item_event(url, "fetching_metadata")
            metadata = fetch_metadata(url)

            if metadata:
                metadata_list.append(metadata)
                if not no_preview:
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

        return metadata_list, [], None

    else:
        # 完整下载模式
        EventEmitter.phase_start("download", total_items=len(urls))

        successful_downloads = []
        failed_downloads = []

        for i, url in enumerate(urls):
            if not no_preview:
                EventEmitter.item_event(url, "processing")

            file_path, metadata = download_audio(
                url, output_dir, extract_cover=extract_cover, no_preview=no_preview
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

        return successful_downloads, failed_downloads, None


def execute_download(
    successful_downloads: List[Dict],
    failed_downloads: List[str],
    output_dir: Path,
    metadata_only: bool = False,
    progress_callback=None,
) -> None:
    """
    执行下载动作（主要是输出结果）

    Args:
        successful_downloads: 成功下载列表
        failed_downloads: 失败URL列表
        output_dir: 输出目录
        metadata_only: 是否仅获取元数据
        progress_callback: 进度回调函数
    """
    if metadata_only:
        if not successful_downloads:
            EventEmitter.result("warn", message="Failed to fetch metadata for any URLs")
        else:
            EventEmitter.result(
                "ok",
                message=f"Fetched metadata for {len(successful_downloads)} URLs",
                artifacts={"metadata": successful_downloads},
            )
    else:
        artifacts = {
            "successful": successful_downloads,
            "failed": failed_downloads,
            "total_urls": len(successful_downloads) + len(failed_downloads),
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
