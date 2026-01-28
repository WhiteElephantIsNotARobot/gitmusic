import os
import time
import atexit
from pathlib import Path
from typing import Optional
from .events import EventEmitter


class LockManager:
    """锁管理器，提供文件锁和进程锁功能"""

    def __init__(self, context: "Context"):
        """
        初始化锁管理器

        Args:
            context: 上下文对象，包含所有路径和配置
        """
        from .context import Context

        if not isinstance(context, Context):
            raise TypeError("context must be an instance of Context")

        self.context = context
        self.lock_dir = context.lock_dir
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.active_locks = set()

        # 注册退出时清理锁
        atexit.register(self.cleanup_all)

    def acquire_file_lock(
        self, lock_name: str, timeout: int = 30, poll_interval: float = 0.1
    ) -> bool:
        """
        获取文件锁

        Args:
            lock_name: 锁名称（将用于创建锁文件）
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）

        Returns:
            是否成功获取锁
        """
        lock_path = self.lock_dir / f"{lock_name}.lock"
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # 使用x模式创建文件，原子操作
                with open(lock_path, "x") as f:
                    f.write(str(os.getpid()))

                self.active_locks.add(lock_path)
                EventEmitter.log("debug", f"Acquired lock: {lock_name}")
                return True

            except FileExistsError:
                # 检查锁是否属于已终止的进程
                try:
                    with open(lock_path, "r") as f:
                        pid_str = f.read().strip()

                    if pid_str and pid_str.isdigit():
                        pid = int(pid_str)
                        # 检查进程是否存在（跨平台方法）
                        if not self._process_exists(pid):
                            EventEmitter.log(
                                "warn", f"Removing stale lock from dead process {pid}"
                            )
                            os.remove(lock_path)
                            continue

                except (IOError, ValueError):
                    # 锁文件损坏，删除并重试
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
                    continue

                # 锁被其他活跃进程持有，等待
                time.sleep(poll_interval)

        EventEmitter.log(
            "error", f"Failed to acquire lock {lock_name} after {timeout}s"
        )
        return False

    def release_file_lock(self, lock_name: str) -> bool:
        """
        释放文件锁

        Args:
            lock_name: 锁名称

        Returns:
            是否成功释放锁
        """
        lock_path = self.lock_dir / f"{lock_name}.lock"

        if lock_path in self.active_locks:
            try:
                if lock_path.exists():
                    os.remove(lock_path)
                self.active_locks.remove(lock_path)
                EventEmitter.log("debug", f"Released lock: {lock_name}")
                return True
            except OSError as e:
                EventEmitter.log(
                    "error", f"Failed to release lock {lock_name}: {str(e)}"
                )
                return False
        else:
            EventEmitter.log("warn", f"Lock {lock_name} not acquired by this process")
            return False

    def with_file_lock(self, lock_name: str, timeout: int = 30):
        """
        上下文管理器，用于自动获取和释放文件锁

        Usage:
            with lock_manager.with_file_lock("metadata"):
                # 执行需要锁的操作
        """

        class LockContext:
            def __init__(self, manager, name, timeout):
                self.manager = manager
                self.name = name
                self.timeout = timeout
                self.acquired = False

            def __enter__(self):
                self.acquired = self.manager.acquire_file_lock(self.name, self.timeout)
                if not self.acquired:
                    raise RuntimeError(f"Failed to acquire lock {self.name}")
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.acquired:
                    self.manager.release_file_lock(self.name)

        return LockContext(self, lock_name, timeout)

    def acquire_metadata_lock(self, timeout: int = 30) -> bool:
        """获取metadata锁（专用方法）"""
        return self.acquire_file_lock("metadata", timeout)

    def release_metadata_lock(self) -> bool:
        """释放metadata锁（专用方法）"""
        return self.release_file_lock("metadata")

    def _process_exists(self, pid: int) -> bool:
        """
        检查进程是否存在（跨平台实现）

        Args:
            pid: 进程ID

        Returns:
            进程是否存在
        """
        try:
            if os.name == "nt":  # Windows
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:  # Unix/Linux/Mac
                os.kill(pid, 0)
                return True
        except (OSError, ImportError, AttributeError):
            return False

    def cleanup_all(self):
        """清理所有由当前进程持有的锁"""
        locks_to_remove = list(self.active_locks)
        for lock_path in locks_to_remove:
            try:
                if lock_path.exists():
                    os.remove(lock_path)
                self.active_locks.remove(lock_path)
                EventEmitter.log("debug", f"Cleaned up lock: {lock_path.name}")
            except OSError:
                pass

    def get_active_locks(self) -> list:
        """获取当前进程持有的所有锁"""
        return [p.name for p in self.active_locks]
