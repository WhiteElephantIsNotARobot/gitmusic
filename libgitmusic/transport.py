import subprocess
import os
import hashlib
import time
from pathlib import Path
from typing import List, Tuple, Optional
from .events import EventEmitter
from .results import RemoteResult
from .exceptions import TransportError


class TransportAdapter:
    """传输适配器，负责本地与远端文件的同步"""

    def __init__(self, context: "Context"):
        """
        初始化传输适配器

        Args:
            context: 上下文对象，包含所有路径和配置
        """
        from .context import Context

        if not isinstance(context, Context):
            raise TypeError("context must be an instance of Context")

        transport_config = context.transport_config
        self.user = transport_config.get("user", "")
        self.host = transport_config.get("host", "")
        self.remote_data_root = transport_config.get("remote_data_root", "")
        self.retries = transport_config.get("retries", 3)
        self.timeout = transport_config.get("timeout", 60)
        self.workers = transport_config.get("workers", 4)
        self.context = context

    def list_remote_files(self, subpath: str) -> List[str]:
        """列出远端特定目录下的所有文件相对路径"""
        remote_path = f"{self.remote_data_root}/{subpath}"
        # 使用 find 命令获取所有文件路径，并提取相对于 remote_data_root 的路径
        cmd = [
            "ssh",
            f"{self.user}@{self.host}",
            f"find {remote_path} -type f \\( -name '*.mp3' -o -name '*.jpg' \\) | sed 's|{self.remote_data_root}/||'",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=True,
            )
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except subprocess.CalledProcessError:
            return []

    def _remote_exec(self, command: str) -> Tuple[str, str]:
        """执行远程命令，返回(stdout, stderr)"""
        cmd = ["ssh", f"{self.user}@{self.host}", command]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=True,
            timeout=self.timeout,
        )
        return result.stdout, result.stderr

    def _get_remote_hash(self, remote_path: str) -> Optional[str]:
        """获取远端文件的SHA256哈希值，如果文件不存在返回None"""
        try:
            stdout, _ = self._remote_exec(f"sha256sum {remote_path}")
            # 输出格式: "hash  filename"
            hash_part = stdout.split()[0]
            if len(hash_part) == 64 and all(
                c in "0123456789abcdef" for c in hash_part.lower()
            ):
                return hash_part.lower()
            else:
                EventEmitter.log("warn", f"Invalid hash output: {stdout}")
                return None
        except subprocess.CalledProcessError as e:
            # 文件可能不存在
            if e.returncode == 1:
                return None
            raise
        except subprocess.TimeoutExpired:
            EventEmitter.log("error", f"Timeout getting remote hash for {remote_path}")
            raise

    def upload(self, local_path: Path, remote_subpath: str) -> RemoteResult:
        """上传文件到远端（原子操作），包含远端SHA256校验和重试机制"""
        remote_final_path = f"{self.remote_data_root}/{remote_subpath}"
        remote_tmp_path = f"{remote_final_path}.tmp"

        # 计算本地哈希
        try:
            with open(local_path, "rb") as f:
                local_hash = hashlib.sha256(f.read()).hexdigest()
            EventEmitter.log("debug", f"Local SHA256: {local_hash}")
        except Exception as e:
            EventEmitter.error(
                f"Failed to compute local hash: {str(e)}",
                {"file": str(local_path)}
            )
            return RemoteResult(
                success=False,
                message=f"Failed to compute local hash: {str(e)}",
                error=TransportError(f"Failed to compute local hash: {str(e)}")
            )

        # 幂等性检查：远端文件是否存在且哈希匹配
        remote_hash = self._get_remote_hash(remote_final_path)
        if remote_hash is not None:
            if remote_hash == local_hash:
                EventEmitter.log(
                    "info",
                    f"Remote file already exists with matching hash, skipping upload: {remote_subpath}",
                )
                EventEmitter.item_event(
                    str(local_path), "skipped", f"remote hash matches"
                )
                return RemoteResult(
                    success=True,
                    message="Remote file already exists with matching hash",
                    remote_path=remote_subpath
                )
            else:
                EventEmitter.log(
                    "warn",
                    f"Remote file exists but hash mismatch, will overwrite: {remote_subpath}",
                )

        # 确保远端目录存在
        remote_dir = os.path.dirname(remote_final_path).replace("\\", "/")
        try:
            subprocess.run(
                ["ssh", f"{self.user}@{self.host}", f"mkdir -p {remote_dir}"],
                check=True,
                timeout=self.timeout
            )
        except Exception as e:
            EventEmitter.error(
                f"Failed to create remote directory: {str(e)}",
                {"directory": remote_dir}
            )
            return RemoteResult(
                success=False,
                message=f"Failed to create remote directory: {str(e)}",
                error=TransportError(f"Failed to create remote directory: {str(e)}")
            )

        # 带重试的上传循环
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                # SCP 上传到临时文件
                subprocess.run(
                    [
                        "scp",
                        str(local_path),
                        f"{self.user}@{self.host}:{remote_tmp_path}",
                    ],
                    check=True,
                    timeout=self.timeout,
                )

                # 获取远端临时文件哈希
                tmp_hash = self._get_remote_hash(remote_tmp_path)
                if tmp_hash is None:
                    raise RuntimeError(
                        f"Failed to compute remote hash for temporary file {remote_tmp_path}"
                    )

                # 比对哈希
                if tmp_hash != local_hash:
                    raise RuntimeError(
                        f"Hash mismatch after upload (attempt {attempt + 1}): local {local_hash[:8]} != remote {tmp_hash[:8]}"
                    )

                # 原子替换
                self._remote_exec(f"mv {remote_tmp_path} {remote_final_path}")

                # 验证最终文件哈希
                final_hash = self._get_remote_hash(remote_final_path)
                if final_hash != local_hash:
                    raise RuntimeError(
                        f"Hash mismatch after atomic move: {final_hash[:8]}"
                    )

                EventEmitter.item_event(
                    str(local_path), "uploaded", f"verified {local_hash[:8]}"
                )
                EventEmitter.log("info", f"Upload successful: {remote_subpath}")
                return RemoteResult(
                    success=True,
                    message="Upload successful",
                    remote_path=remote_subpath
                )

            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                RuntimeError,
            ) as e:
                last_error = e
                if attempt < self.retries:
                    wait_time = 2**attempt  # 指数退避
                    EventEmitter.log(
                        "warn",
                        f"Upload failed (attempt {attempt + 1}/{self.retries + 1}), retrying in {wait_time}s: {str(e)}",
                    )
                    time.sleep(wait_time)
                else:
                    EventEmitter.error(
                        f"Upload failed after {self.retries + 1} attempts: {str(e)}",
                        {"file": str(local_path)},
                    )
                    return RemoteResult(
                        success=False,
                        message=f"Upload failed after {self.retries + 1} attempts: {str(e)}",
                        error=TransportError(f"Upload failed after {self.retries + 1} attempts: {str(e)}"),
                        remote_path=remote_subpath
                    )

    def download(self, remote_subpath: str, local_path: Path):
        """从远端下载文件（原子操作）"""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_tmp_path = local_path.with_suffix(".tmp")

        remote_path = f"{self.remote_data_root}/{remote_subpath}"

        # SCP 下载到临时文件
        subprocess.run(
            ["scp", f"{self.user}@{self.host}:{remote_path}", str(local_tmp_path)],
            check=True,
        )

        # 本地原子替换
        os.replace(local_tmp_path, local_path)
