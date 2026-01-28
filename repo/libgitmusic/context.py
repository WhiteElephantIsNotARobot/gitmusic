"""
Context 类 - 统一路径管理和配置注入

遵循规范：单一入口解析，所有路径必须是绝对路径，通过 Context 参数传递给库函数。
"""

import os
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class Context:
    """GitMusic 上下文，包含所有路径和配置"""

    # 根目录和配置
    project_root: Path  # 项目根目录（包含 config.yaml 的目录）
    config: Dict[str, Any]  # 完整的配置字典

    # 核心路径（从配置解析）
    work_dir: Path  # 工作目录，存放待发布的 MP3 文件
    cache_root: Path  # 缓存根目录，存储音频和封面对象
    metadata_file: Path  # 元数据数据库文件
    release_dir: Path  # 发布目录，生成最终音乐库
    logs_dir: Path  # 日志目录，存储 JSONL 事件日志

    # 衍生路径（自动生成）
    tmp_dir: Path = field(init=False)  # 临时文件目录
    lock_dir: Path = field(init=False)  # 锁文件目录

    # 传输配置（方便访问）
    transport_config: Dict[str, Any] = field(init=False)

    # 临时文件跟踪（用于清理）
    _temp_files: list = field(default_factory=list, init=False, repr=False)
    _temp_dirs: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        """初始化后处理，计算衍生路径和验证配置"""
        # 确保所有路径都是绝对路径
        self.project_root = self.project_root.resolve()
        self.work_dir = self.work_dir.resolve()
        self.cache_root = self.cache_root.resolve()
        self.metadata_file = self.metadata_file.resolve()
        self.release_dir = self.release_dir.resolve()
        self.logs_dir = self.logs_dir.resolve()

        # 衍生路径
        self.tmp_dir = self.cache_root / "tmp"
        self.lock_dir = self.project_root / ".locks"

        # 提取传输配置
        self.transport_config = self.config.get("transport", {})

        # 确保目录存在
        self._ensure_dirs()

        # 验证路径安全性和权限
        self._validate_paths()

    def _ensure_dirs(self) -> None:
        """确保所有必要的目录存在"""
        dirs = [
            self.work_dir,
            self.cache_root,
            self.release_dir,
            self.logs_dir,
            self.tmp_dir,
            self.lock_dir,
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

    def _validate_paths(self) -> None:
        """验证路径安全性（存在性、权限、符号链接检查）"""
        # 检查关键目录是否可写
        for dir_path, name in [
            (self.work_dir, "work_dir"),
            (self.cache_root, "cache_root"),
            (self.release_dir, "release_dir"),
            (self.logs_dir, "logs_dir"),
        ]:
            if not dir_path.exists():
                raise ValueError(f"Directory {name} does not exist: {dir_path}")
            if not os.access(dir_path, os.W_OK):
                raise PermissionError(f"No write permission for {name}: {dir_path}")

            # 检查不安全符号链接
            self._check_symlink_safety(dir_path)

    def _check_symlink_safety(self, path: Path) -> None:
        """检查路径是否存在不安全的符号链接（TOCTOU 防护）"""
        try:
            real_path = path.resolve(strict=True)
            if str(real_path) != str(path):
                # 如果解析后的路径不同，可能是符号链接
                # 记录警告但不阻止，因为某些环境可能使用符号链接
                import warnings

                warnings.warn(
                    f"Path {path} is a symlink to {real_path}. "
                    "This may pose security risks in multi-user environments."
                )
        except Exception:
            pass  # 路径不存在或其他错误，忽略

    def get_temp_file(
        self, prefix: str = "", suffix: str = "", command_name: str = ""
    ) -> Path:
        """创建临时文件，自动跟踪用于清理

        Args:
            prefix: 文件名前缀
            suffix: 文件名后缀（如 .tmp）
            command_name: 命令名称，用于文件名标识

        Returns:
            临时文件路径
        """
        if not command_name:
            command_name = "unknown"

        # 包含 PID 和命令名，避免冲突
        pid = os.getpid()
        name = f"{prefix}{command_name}_{pid}_{len(self._temp_files):04d}{suffix}"
        temp_file = self.tmp_dir / name
        temp_file.touch()
        self._temp_files.append(temp_file)
        return temp_file

    def get_temp_dir(self, prefix: str = "", command_name: str = "") -> Path:
        """创建临时目录，自动跟踪用于清理

        Args:
            prefix: 目录名前缀
            command_name: 命令名称，用于目录名标识

        Returns:
            临时目录路径
        """
        if not command_name:
            command_name = "unknown"

        pid = os.getpid()
        name = f"{prefix}{command_name}_{pid}_{len(self._temp_dirs):04d}"
        temp_dir = self.tmp_dir / name
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dirs.append(temp_dir)
        return temp_dir

    def cleanup(self) -> None:
        """清理所有临时文件和目录"""
        for temp_file in self._temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception:
                pass  # 忽略清理错误

        for temp_dir in self._temp_dirs:
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        self._temp_files.clear()
        self._temp_dirs.clear()

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，自动清理"""
        self.cleanup()


def create_context(config_path: Optional[str] = None) -> Context:
    """从配置文件创建 Context 对象

    Args:
        config_path: 配置文件路径，如果为 None 则自动查找

    Returns:
        初始化的 Context 对象
    """
    import yaml

    # 确定项目根目录
    if config_path:
        config_file = Path(config_path).resolve()
        project_root = config_file.parent
    else:
        # 从环境变量或默认位置查找
        config_env = os.environ.get("GITMUSICCONFIG")
        if config_env:
            config_file = Path(config_env).resolve()
        else:
            # 假设当前工作目录为项目根目录
            project_root = Path.cwd()
            config_file = project_root / "config.yaml"

    # 加载配置
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    else:
        config = {}

    # 合并默认配置
    default_config = {
        "transport": {
            "user": "your_username",
            "host": "your.server.com",
            "remote_data_root": "/srv/music/data",
            "retries": 5,
            "timeout": 60,
            "workers": 4,
        },
        "paths": {
            "work_dir": str(project_root / "work"),
            "cache_root": str(project_root / "cache"),
            "metadata_file": str(project_root / "metadata.jsonl"),
            "release_dir": str(project_root / "release"),
            "logs_dir": str(project_root / "logs"),
        },
        "image": {"quality": 2},
    }

    # 深度合并
    def deep_merge(target, source):
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                deep_merge(target[key], value)
            else:
                target[key] = value
        return target

    config = deep_merge(default_config, config)

    # 提取路径
    paths = config.get("paths", {})

    # 创建 Context
    ctx = Context(
        project_root=project_root,
        config=config,
        work_dir=Path(paths["work_dir"]),
        cache_root=Path(paths["cache_root"]),
        metadata_file=Path(paths["metadata_file"]),
        release_dir=Path(paths["release_dir"]),
        logs_dir=Path(paths["logs_dir"]),
    )

    return ctx


__all__ = ["Context", "create_context"]
