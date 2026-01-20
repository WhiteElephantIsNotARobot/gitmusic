import hashlib
import shutil
from pathlib import Path
from typing import Optional, Tuple
from .events import EventEmitter


class ObjectStore:
    """对象存储管理器，负责音频和封面对象的存储、检索和校验"""

    def __init__(self, context: "Context"):
        """
        初始化对象存储

        Args:
            context: 上下文对象，包含所有路径和配置
        """
        from .context import Context

        if not isinstance(context, Context):
            raise TypeError("context must be an instance of Context")

        self.context = context
        self.cache_root = context.cache_root
        self.objects_dir = context.cache_root / "objects"
        self.covers_dir = context.cache_root / "covers"

        # 确保目录存在
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)

    def _get_object_path(self, oid: str) -> Path:
        """根据对象ID获取存储路径"""
        # oid格式: "sha256:hexdigest"，提取hexdigest部分
        if oid.startswith("sha256:"):
            hexdigest = oid[7:]
        else:
            hexdigest = oid

        # 根据文件类型确定目录
        if hexdigest.endswith(".jpg"):
            # 封面文件可能在covers目录，但oid不包含.jpg
            hexdigest = hexdigest[:-4]
            # 实际存储结构: covers/sha256/前两个字符/完整哈希.jpg
            return self.covers_dir / "sha256" / hexdigest[:2] / f"{hexdigest}.jpg"
        else:
            # 音频文件或其他对象
            # 实际存储结构: objects/sha256/前两个字符/完整哈希.mp3
            return self.objects_dir / "sha256" / hexdigest[:2] / f"{hexdigest}.mp3"

    def store_audio(self, temp_path: Path, compute_hash: bool = True) -> str:
        """
        存储音频文件并返回对象ID

        Args:
            temp_path: 临时音频文件路径
            compute_hash: 是否计算哈希（如果已知可传入False）

        Returns:
            音频对象ID (sha256:hexdigest)
        """
        if compute_hash:
            # 计算音频哈希（使用AudioIO或HashUtils）
            from .audio import AudioIO

            oid = AudioIO.get_audio_hash(temp_path)
        else:
            # 假设文件名已经是哈希值
            oid = f"sha256:{temp_path.stem}"

        target_path = self._get_object_path(oid)

        if target_path.exists():
            EventEmitter.log("debug", f"Audio object already exists: {oid}")
            return oid

        # 原子写入
        from .audio import AudioIO

        with open(temp_path, "rb") as f:
            AudioIO.atomic_write(f.read(), target_path)

        EventEmitter.item_event(oid, "stored", "audio")
        return oid

    def store_cover(self, cover_data: bytes, compute_hash: bool = True) -> str:
        """
        存储封面图片并返回对象ID

        Args:
            cover_data: 封面图片字节数据
            compute_hash: 是否计算哈希

        Returns:
            封面对象ID (sha256:hexdigest)
        """
        if compute_hash:
            hexdigest = hashlib.sha256(cover_data).hexdigest()
            oid = f"sha256:{hexdigest}"
        else:
            # 假设已知哈希
            raise ValueError("Must compute hash for cover data")

        target_path = self._get_object_path(oid + ".jpg")

        if target_path.exists():
            EventEmitter.log("debug", f"Cover object already exists: {oid}")
            return oid

        # 原子写入
        from .audio import AudioIO

        AudioIO.atomic_write(cover_data, target_path)

        EventEmitter.item_event(oid, "stored", "cover")
        return oid

    def get_audio_path(self, oid: str) -> Optional[Path]:
        """获取音频对象文件路径，如果不存在则返回None"""
        path = self._get_object_path(oid)
        return path if path.exists() else None

    def get_cover_path(self, oid: str) -> Optional[Path]:
        """获取封面对象文件路径，如果不存在则返回None"""
        # 使用_get_object_path，传入带.jpg后缀的oid以匹配存储结构
        path = self._get_object_path(oid + ".jpg")
        return path if path.exists() else None

    def exists(self, oid: str) -> bool:
        """检查对象是否存在"""
        path = self._get_object_path(oid)
        return path.exists()

    def copy_to_workdir(
        self,
        oid: str,
        target_path: Path,
        metadata: dict,
        cover_oid: Optional[str] = None,
    ):
        """
        将音频对象复制到工作目录，嵌入元数据和封面

        Args:
            oid: 音频对象ID
            target_path: 目标文件路径（工作目录）
            metadata: 元数据字典
            cover_oid: 封面对象ID（可选）
        """
        audio_path = self.get_audio_path(oid)
        if not audio_path:
            raise FileNotFoundError(f"Audio object not found: {oid}")

        cover_data = None
        if cover_oid:
            cover_path = self.get_cover_path(cover_oid)
            if cover_path:
                with open(cover_path, "rb") as f:
                    cover_data = f.read()
            else:
                EventEmitter.log("warn", f"Cover object not found: {cover_oid}")

        from .audio import AudioIO

        AudioIO.embed_metadata(audio_path, metadata, cover_data, target_path)

        EventEmitter.item_event(str(target_path), "checked_out", f"from {oid}")

    def verify_integrity(self) -> Tuple[int, int, list]:
        """
        验证对象存储完整性

        Returns:
            (total_checked, errors, error_details)
        """
        errors = []
        total = 0

        # 检查objects目录下的sha256子目录及其两级子目录
        for obj_file in self.objects_dir.glob("sha256/*/*.mp3"):
            if obj_file.is_file():
                total += 1
                expected_hash = obj_file.stem  # 去掉.mp3扩展名
                with open(obj_file, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()

                if actual_hash != expected_hash:
                    errors.append(f"Object hash mismatch: {obj_file.name}")
                    EventEmitter.error(
                        f"Hash mismatch for object {obj_file.name}",
                        {"expected": expected_hash, "actual": actual_hash},
                    )

        # 检查covers目录下的sha256子目录及其两级子目录
        for cover_file in self.covers_dir.glob("sha256/*/*.jpg"):
            if cover_file.is_file():
                total += 1
                expected_hash = cover_file.stem  # 去掉.jpg扩展名
                with open(cover_file, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()

                if actual_hash != expected_hash:
                    errors.append(f"Cover hash mismatch: {cover_file.name}")
                    EventEmitter.error(
                        f"Hash mismatch for cover {cover_file.name}",
                        {"expected": expected_hash, "actual": actual_hash},
                    )

        return total, len(errors), errors
