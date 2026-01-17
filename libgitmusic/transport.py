import subprocess
import os
from pathlib import Path
from typing import List, Tuple
from .events import EventEmitter

class TransportAdapter:
    """传输适配器，负责本地与远端文件的同步"""

    def __init__(self, user: str, host: str, remote_data_root: str):
        self.user = user
        self.host = host
        self.remote_data_root = remote_data_root

    def list_remote_files(self, subpath: str) -> List[str]:
        """列出远端特定目录下的所有文件相对路径"""
        remote_path = f"{self.remote_data_root}/{subpath}"
        # 使用 find 命令获取所有文件路径，并提取相对于 remote_data_root 的路径
        cmd = [
            "ssh", f"{self.user}@{self.host}",
            f"find {remote_path} -type f \\( -name '*.mp3' -o -name '*.jpg' \\) | sed 's|{self.remote_data_root}/||'"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except subprocess.CalledProcessError:
            return []

    def upload(self, local_path: Path, remote_subpath: str):
        """上传文件到远端（原子操作）"""
        remote_final_path = f"{self.remote_data_root}/{remote_subpath}"
        remote_tmp_path = f"{remote_final_path}.tmp"

        # 确保远端目录存在
        remote_dir = os.path.dirname(remote_final_path).replace('\\', '/')
        subprocess.run(["ssh", f"{self.user}@{self.host}", f"mkdir -p {remote_dir}"], check=True)

        # SCP 上传到临时文件
        subprocess.run(["scp", str(local_path), f"{self.user}@{self.host}:{remote_tmp_path}"], check=True)

        # 远端原子替换
        subprocess.run(["ssh", f"{self.user}@{self.host}", f"mv {remote_tmp_path} {remote_final_path}"], check=True)

    def download(self, remote_subpath: str, local_path: Path):
        """从远端下载文件（原子操作）"""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_tmp_path = local_path.with_suffix('.tmp')

        remote_path = f"{self.remote_data_root}/{remote_subpath}"

        # SCP 下载到临时文件
        subprocess.run(["scp", f"{self.user}@{self.host}:{remote_path}", str(local_tmp_path)], check=True)

        # 本地原子替换
        os.replace(local_tmp_path, local_path)
