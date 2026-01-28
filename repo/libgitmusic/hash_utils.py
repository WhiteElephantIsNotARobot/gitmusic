import hashlib
import subprocess
import json
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from .events import EventEmitter


class HashUtils:
    """哈希计算工具类，统一音频和文件哈希计算，记录tooling日志"""

    # 默认ffmpeg参数，用于提取纯净音频流
    DEFAULT_FFMPEG_PARAMS = [
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

    @classmethod
    def get_ffmpeg_version(cls) -> str:
        """获取ffmpeg版本信息"""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, check=True
            )
            first_line = result.stdout.split("\n")[0]
            return first_line.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return "unknown"

    @classmethod
    def hash_audio_frames(
        cls,
        path: Path,
        ffmpeg_params: Optional[list] = None,
        record_tooling: bool = True,
    ) -> str:
        """
        计算纯净音频流的SHA256哈希

        Args:
            path: 音频文件路径
            ffmpeg_params: ffmpeg参数列表，如果为None则使用默认参数
            record_tooling: 是否记录tooling日志

        Returns:
            音频哈希 (sha256:hexdigest)
        """
        params = ffmpeg_params or cls.DEFAULT_FFMPEG_PARAMS
        cmd = ["ffmpeg", "-i", str(path)] + params + ["pipe:1"]

        if record_tooling:
            # 记录tooling信息
            tooling_info = {
                "ffmpeg_version": cls.get_ffmpeg_version(),
                "ffmpeg_params": params,
                "input_file": str(path),
                "hash_type": "sha256_audio_frames",
            }
            EventEmitter.log(
                "debug", f"Audio hashing tooling: {json.dumps(tooling_info)}"
            )

        try:
            # 使用Popen实时读取输出
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,  # 行缓冲
                universal_newlines=False,  # 二进制模式，因为stdout是音频数据
            )

            # 存储stdout数据用于哈希计算
            stdout_chunks = []
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

            # 读取stdout（音频数据）并计算哈希
            sha256_obj = hashlib.sha256()
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                stdout_chunks.append(chunk)
                sha256_obj.update(chunk)

            # 等待进程结束
            returncode = process.wait(timeout=30)

            # 等待stderr线程结束
            stderr_thread.join(timeout=5)

            if returncode != 0:
                stderr_data = "".join(stderr_lines)
                raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr_data)
            else:
                # 计算哈希值
                hexdigest = sha256_obj.hexdigest()
                oid = f"sha256:{hexdigest}"

        except subprocess.TimeoutExpired:
            if process:
                process.terminate()
                process.wait(timeout=5)
            raise RuntimeError("FFmpeg计算哈希超时（30秒）")
        except subprocess.CalledProcessError as e:
            stderr_data = e.stderr if isinstance(e.stderr, str) else "Unknown error"
            raise RuntimeError(f"FFmpeg failed to calculate hash: {stderr_data}")

        if record_tooling:
            EventEmitter.item_event(oid, "hashed", f"audio {path.name}")

        return oid

    @classmethod
    def hash_file(cls, path: Path, hash_type: str = "sha256") -> str:
        """
        计算文件哈希

        Args:
            path: 文件路径
            hash_type: 哈希类型 (sha256, md5等)

        Returns:
            文件哈希 (hash_type:hexdigest)
        """
        if hash_type == "sha256":
            hasher = hashlib.sha256()
        elif hash_type == "md5":
            hasher = hashlib.md5()
        else:
            raise ValueError(f"Unsupported hash type: {hash_type}")

        with open(path, "rb") as f:
            while chunk := f.read(4096):
                hasher.update(chunk)

        hexdigest = hasher.hexdigest()
        return f"{hash_type}:{hexdigest}"

    @classmethod
    def hash_bytes(cls, data: bytes, hash_type: str = "sha256") -> str:
        """
        计算字节数据哈希

        Args:
            data: 字节数据
            hash_type: 哈希类型

        Returns:
            数据哈希 (hash_type:hexdigest)
        """
        if hash_type == "sha256":
            hasher = hashlib.sha256()
        elif hash_type == "md5":
            hasher = hashlib.md5()
        else:
            raise ValueError(f"Unsupported hash type: {hash_type}")

        hasher.update(data)
        hexdigest = hasher.hexdigest()
        return f"{hash_type}:{hexdigest}"

    @classmethod
    def verify_hash(cls, path: Path, expected_oid: str) -> bool:
        """
        验证文件哈希

        Args:
            path: 文件路径
            expected_oid: 期望的对象ID (sha256:hexdigest)

        Returns:
            是否匹配
        """
        if not expected_oid.startswith("sha256:"):
            EventEmitter.log("warn", f"Unexpected hash format: {expected_oid}")
            return False

        # 提取哈希类型和期望的hexdigest
        hash_type, expected_hex = expected_oid.split(":", 1)

        # 计算实际哈希
        actual_oid = cls.hash_file(path, hash_type)
        _, actual_hex = actual_oid.split(":", 1)

        if actual_hex == expected_hex:
            EventEmitter.item_event(path.name, "hash_verified", "")
            return True
        else:
            EventEmitter.error(
                f"Hash mismatch for {path.name}",
                {"expected": expected_hex, "actual": actual_hex},
            )
            return False
