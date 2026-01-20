import os
import subprocess
import tempfile
import hashlib
import threading
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.id3 import APIC, TIT2, TPE1, TALB, TDRC, USLT, TXXX

from .events import EventEmitter
from .hash_utils import HashUtils


class AudioIO:
    """音频 I/O 库，负责音频流分离、哈希计算、封面处理及原子写入"""

    @staticmethod
    def extract_audio_stream(
        src_path: Path, out_path: Path, ffmpeg_params: Optional[list] = None
    ) -> Path:
        """
        从容器文件分离纯音频流

        Args:
            src_path: 源文件路径
            out_path: 输出文件路径
            ffmpeg_params: ffmpeg参数列表

        Returns:
            输出文件路径
        """
        params = ffmpeg_params or [
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "mp3",
            "-map_metadata",
            "-1",
            "-id3v2_version",
            "0",
            "-write_id3v1",
            "0",
        ]

        cmd = ["ffmpeg", "-i", str(src_path)] + params + [str(out_path)]

        process = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=False,
            )

            stderr_lines = []

            # 读取stderr的辅助函数（实时打印进度）
            def read_stderr():
                try:
                    while True:
                        line = process.stderr.readline()
                        if not line:
                            break
                        # 解码为字符串并移除尾部换行符
                        line_str = line.decode("utf-8", errors="replace").rstrip("\n")
                        stderr_lines.append(line_str)
                        # 实时打印ffmpeg进度信息（CLI会将其显示为灰色文本）
                        if line_str:
                            print(line_str, flush=True)
                except Exception:
                    pass

            # 启动stderr读取线程
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            # 等待进程结束
            returncode = process.wait(timeout=300)  # 5分钟超时

            # 等待stderr线程结束
            stderr_thread.join(timeout=5)

            if returncode != 0:
                stderr_data = "".join(stderr_lines)
                raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr_data)

            EventEmitter.item_event(str(src_path), "extracted", f"to {out_path}")
            return out_path

        except subprocess.TimeoutExpired:
            if process:
                process.terminate()
                process.wait(timeout=5)
            raise RuntimeError("FFmpeg提取音频流超时（5分钟）")
        except subprocess.CalledProcessError as e:
            stderr_data = e.stderr if isinstance(e.stderr, str) else "Unknown error"
            EventEmitter.error(f"Failed to extract audio stream: {stderr_data}")
            raise

    @staticmethod
    def get_audio_hash(path: Path, ffmpeg_params: Optional[list] = None) -> str:
        """
        计算纯净音频流的 SHA256 哈希（移除所有元数据）

        Args:
            path: 音频文件路径
            ffmpeg_params: ffmpeg参数列表

        Returns:
            音频哈希 (sha256:hexdigest)
        """
        # 使用HashUtils计算哈希
        return HashUtils.hash_audio_frames(path, ffmpeg_params, record_tooling=True)

    @staticmethod
    def extract_cover(audio_path: Path) -> Optional[bytes]:
        """从音频文件中提取封面图片数据"""
        # 优先尝试使用 mutagen 提取
        try:
            audio = MP3(audio_path, ID3=ID3)
            if audio.tags:
                for tag in audio.tags.values():
                    if isinstance(tag, APIC):
                        return tag.data
        except Exception as e:
            EventEmitter.log("debug", f"Mutagen failed to extract cover: {str(e)}")

        # 备选方案：使用 ffmpeg 提取
        cmd = [
            "ffmpeg",
            "-i",
            str(audio_path),
            "-map",
            "0:v",
            "-map",
            "-0:V",
            "-c",
            "copy",
            "-f",
            "image2",
            "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, check=True)
            if result.stdout:
                return result.stdout
        except Exception as e:
            EventEmitter.log("debug", f"FFmpeg failed to extract cover: {str(e)}")

        EventEmitter.log("warn", f"No cover found in {audio_path.name}")
        return None

    @staticmethod
    def compress_cover(
        cover_data: bytes, max_width: int = 800, quality: int = 85
    ) -> bytes:
        """
        压缩封面图片

        Args:
            cover_data: 原始封面数据
            max_width: 最大宽度（保持宽高比）
            quality: JPEG质量 (1-100)

        Returns:
            压缩后的封面数据
        """
        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(cover_data)
            tmp_path = tmp.name

        try:
            # 使用ffmpeg压缩
            out_path = tmp_path + ".compressed.jpg"
            cmd = [
                "ffmpeg",
                "-i",
                tmp_path,
                "-vf",
                f"scale={max_width}:-1",
                "-q:v",
                str(quality),
                out_path,
            ]

            subprocess.run(cmd, check=True, capture_output=True)

            with open(out_path, "rb") as f:
                compressed_data = f.read()

            # 清理临时文件
            os.unlink(tmp_path)
            os.unlink(out_path)

            original_size = len(cover_data)
            compressed_size = len(compressed_data)
            savings = 100 * (1 - compressed_size / original_size)

            EventEmitter.log(
                "info",
                f"Cover compressed: {original_size} -> {compressed_size} bytes ({savings:.1f}% saved)",
            )

            return compressed_data

        except Exception as e:
            EventEmitter.log("warn", f"Failed to compress cover: {str(e)}")
            # 返回原始数据
            os.unlink(tmp_path)
            return cover_data

    @staticmethod
    def atomic_write(content: bytes, target_path: Path):
        """原子性写入文件"""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path_str = tempfile.mkstemp(dir=target_path.parent, suffix=".tmp")
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(content)
            os.replace(temp_path, target_path)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise e

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """清理文件名中的非法字符，统一替换为下划线"""
        for char in ["<", ">", ":", '"', "/", "\\", "|", "?", "*"]:
            filename = filename.replace(char, "_")
        return filename

    @staticmethod
    def embed_metadata(
        src_audio: Path, metadata: dict, cover_data: Optional[bytes], out_path: Path
    ):
        """
        嵌入元数据和封面并生成最终音频文件

        Args:
            src_audio: 源音频文件路径（无元数据的纯净音频）
            metadata: 元数据字典
            cover_data: 封面图片数据（可选）
            out_path: 输出文件路径
        """
        # 先复制原始音频到目标路径（原子写入）
        with open(src_audio, "rb") as f:
            AudioIO.atomic_write(f.read(), out_path)

        # 使用 mutagen 写入标签
        try:
            audio = MP3(out_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()

            tags = audio.tags
            if tags is not None:
                # 标题
                if title := metadata.get("title"):
                    tags.add(TIT2(encoding=3, text=title))

                # 艺术家
                if artists := metadata.get("artists"):
                    if isinstance(artists, list):
                        artists_text = "/".join(artists)
                    else:
                        artists_text = str(artists)
                    tags.add(TPE1(encoding=3, text=artists_text))

                # 专辑
                if album := metadata.get("album"):
                    tags.add(TALB(encoding=3, text=album))

                # 日期
                if date := metadata.get("date"):
                    tags.add(TDRC(encoding=3, text=date))

                # 歌词
                if uslt := metadata.get("uslt"):
                    tags.add(USLT(encoding=3, lang="eng", desc="", text=uslt))

                # 元数据哈希，用于增量更新检测
                if metadata_hash := metadata.get("metadata_hash"):
                    tags.add(TXXX(encoding=3, desc="METADATA_HASH", text=metadata_hash))

                # 封面
                if cover_data:
                    tags.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,  # 封面
                            desc="Front Cover",
                            data=cover_data,
                        )
                    )

            audio.save()
            EventEmitter.item_event(str(out_path), "metadata_embedded", "")

        except Exception as e:
            EventEmitter.error(
                f"Failed to embed metadata: {str(e)}", {"path": str(out_path)}
            )
            raise e

    @staticmethod
    def verify_local(path: Path, expected_oid: str) -> bool:
        """
        验证本地文件哈希

        Args:
            path: 文件路径
            expected_oid: 期望的对象ID

        Returns:
            是否匹配
        """
        return HashUtils.verify_hash(path, expected_oid)

    @staticmethod
    def verify_remote(
        remote_path: str, expected_oid: str, user: str, host: str, remote_root: str
    ) -> bool:
        """
        验证远端文件哈希（通过SSH执行sha256sum）

        Args:
            remote_path: 远端相对路径
            expected_oid: 期望的对象ID
            user: SSH用户名
            host: SSH主机
            remote_root: 远端根目录

        Returns:
            是否匹配
        """
        if not expected_oid.startswith("sha256:"):
            EventEmitter.log("warn", f"Unexpected hash format: {expected_oid}")
            return False

        expected_hex = expected_oid[7:]
        full_remote_path = f"{remote_root}/{remote_path}"

        cmd = ["ssh", f"{user}@{host}", f"sha256sum {full_remote_path}"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            # sha256sum输出格式: "hexdigest  filename"
            parts = result.stdout.strip().split()
            if parts:
                actual_hex = parts[0]

                if actual_hex == expected_hex:
                    EventEmitter.item_event(remote_path, "remote_verified", "")
                    return True
                else:
                    EventEmitter.error(
                        f"Remote hash mismatch for {remote_path}",
                        {"expected": expected_hex, "actual": actual_hex},
                    )
                    return False
        except subprocess.CalledProcessError as e:
            EventEmitter.error(
                f"Failed to verify remote file: {e.stderr}", {"path": remote_path}
            )
            return False

        return False

    @staticmethod
    def generate_release_filename(metadata: dict) -> str:
        """
        生成发布文件名

        Args:
            metadata: 元数据字典

        Returns:
            清理后的文件名
        """
        artists = metadata.get("artists", ["Unknown"])
        title = metadata.get("title", "Untitled")

        if isinstance(artists, list):
            artist_str = ", ".join(artists)
        else:
            artist_str = str(artists)

        filename = f"{artist_str} - {title}.mp3"
        return AudioIO.sanitize_filename(filename)
