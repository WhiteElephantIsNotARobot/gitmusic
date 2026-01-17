import os
import subprocess
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple, List
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, USLT, TXXX
from .events import EventEmitter

class AudioIO:
    """音频 I/O 库，负责音频流分离、哈希计算、封面处理及原子写入"""

    @staticmethod
    def get_audio_hash(path: Path) -> str:
        """计算纯净音频流的 SHA256 哈希（移除所有元数据）"""
        cmd = [
            'ffmpeg', '-i', str(path),
            '-map', '0:a:0', '-c', 'copy',
            '-f', 'mp3', '-map_metadata', '-1',
            '-id3v2_version', '0', '-write_id3v1', '0',
            'pipe:1'
        ]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sha256_obj = hashlib.sha256()

        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            sha256_obj.update(chunk)

        process.wait()
        if process.returncode != 0:
            stderr_data = process.stderr.read().decode() if process.stderr else "Unknown error"
            raise RuntimeError(f"FFmpeg failed to calculate hash: {stderr_data}")

        return f"sha256:{sha256_obj.hexdigest()}"

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
        except Exception:
            pass

        # 备选方案：使用 ffmpeg 提取
        cmd = [
            'ffmpeg', '-i', str(audio_path),
            '-map', '0:v', '-map', '-0:V',
            '-c', 'copy', '-f', 'image2', 'pipe:1'
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, check=True)
            if result.stdout:
                return result.stdout
        except Exception:
            pass

        return None

    @staticmethod
    def atomic_write(content: bytes, target_path: Path):
        """原子性写入文件"""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path_str = tempfile.mkstemp(dir=target_path.parent, suffix='.tmp')
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(temp_fd, 'wb') as f:
                f.write(content)
            os.replace(temp_path, target_path)
        except Exception as e:
            if temp_path.exists():
                temp_path.unlink()
            raise e

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """清理文件名中的非法字符，统一替换为下划线"""
        for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
            filename = filename.replace(char, '_')
        return filename

    @staticmethod
    def embed_metadata(src_audio: Path, metadata: dict, cover_data: Optional[bytes], out_path: Path):
        """嵌入元数据和封面并生成最终音频文件"""
        # 先复制原始音频到目标路径（原子写入）
        with open(src_audio, 'rb') as f:
            AudioIO.atomic_write(f.read(), out_path)

        # 使用 mutagen 写入标签
        try:
            audio = MP3(out_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()

            tags = audio.tags
            if tags is not None:
                tags.add(TIT2(encoding=3, text=metadata.get('title', '')))
                tags.add(TPE1(encoding=3, text='/'.join(metadata.get('artists', []))))
                if metadata.get('album'):
                    tags.add(TALB(encoding=3, text=metadata['album']))
                if metadata.get('date'):
                    tags.add(TDRC(encoding=3, text=metadata['date']))
                if metadata.get('uslt'):
                    tags.add(USLT(encoding=3, lang='eng', desc='', text=metadata['uslt']))

                # 写入元数据哈希，用于增量更新检测
                if 'metadata_hash' in metadata:
                    tags.add(TXXX(encoding=3, desc='METADATA_HASH', text=metadata['metadata_hash']))

                if cover_data:
                    tags.add(APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3, # 封面
                        desc='Front Cover',
                        data=cover_data
                    ))

            audio.save()
        except Exception as e:
            EventEmitter.error(f"Failed to embed metadata: {str(e)}", {"path": str(out_path)})
            raise e
