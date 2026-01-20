"""
Git操作库 - 封装Git命令操作
"""

import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from .events import EventEmitter


class GitOperations:
    """Git操作封装类"""

    def __init__(self, repo_root: Path):
        """
        初始化Git操作

        Args:
            repo_root: Git仓库根目录
        """
        self.repo_root = Path(repo_root).absolute()

    def _run_git(
        self, args: List[str], check: bool = True
    ) -> subprocess.CompletedProcess:
        """
        运行Git命令

        Args:
            args: Git命令参数列表
            check: 是否检查返回码

        Returns:
            subprocess.CompletedProcess对象
        """
        try:
            return subprocess.run(
                ["git"] + args,
                cwd=self.repo_root,
                check=check,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except subprocess.CalledProcessError as e:
            EventEmitter.error(f"Git命令失败: {' '.join(args)}")
            EventEmitter.error(f"错误输出: {e.stderr}")
            raise

    def add(self, paths: List[str]) -> bool:
        """
        添加文件到暂存区

        Args:
            paths: 文件路径列表（相对于仓库根目录）

        Returns:
            是否成功
        """
        try:
            result = self._run_git(["add"] + paths)
            for path in paths:
                EventEmitter.log("info", f"已添加 {path} 到暂存区")
            return True
        except subprocess.CalledProcessError:
            return False

    def commit(self, message: str, allow_empty: bool = False) -> bool:
        """
        提交更改

        Args:
            message: 提交消息
            allow_empty: 是否允许空提交

        Returns:
            是否成功
        """
        try:
            args = ["commit", "-m", message]
            if allow_empty:
                args.append("--allow-empty")

            result = self._run_git(args)
            EventEmitter.log("info", f"已提交更改: {message}")
            EventEmitter.log("debug", f"提交输出: {result.stdout.strip()}")
            return True
        except subprocess.CalledProcessError:
            return False

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        """
        推送到远程仓库

        Args:
            remote: 远程仓库名称
            branch: 分支名称

        Returns:
            是否成功
        """
        try:
            result = self._run_git(["push", remote, branch])
            EventEmitter.log("info", f"已推送到 {remote}/{branch}")
            return True
        except subprocess.CalledProcessError:
            return False

    def pull(self, remote: str = "origin", branch: str = "main") -> bool:
        """
        从远程仓库拉取更新

        Args:
            remote: 远程仓库名称
            branch: 分支名称

        Returns:
            是否成功
        """
        try:
            result = self._run_git(["pull", remote, branch])
            EventEmitter.log("info", f"已从 {remote}/{branch} 拉取更新")
            return True
        except subprocess.CalledProcessError:
            return False

    def status(self, short: bool = False) -> Optional[str]:
        """
        获取仓库状态

        Args:
            short: 是否使用简短格式

        Returns:
            状态输出字符串，失败返回None
        """
        try:
            args = ["status"]
            if short:
                args.append("--short")
            result = self._run_git(args)
            return result.stdout
        except subprocess.CalledProcessError:
            return None

    def has_changes(self) -> bool:
        """
        检查是否有未提交的更改

        Returns:
            是否有更改
        """
        status = self.status(short=True)
        return bool(status and status.strip())

    def get_current_branch(self) -> Optional[str]:
        """
        获取当前分支名称

        Returns:
            分支名称，失败返回None
        """
        try:
            result = self._run_git(["branch", "--show-current"])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None


# 简化函数接口
def git_add(repo_root: Path, paths: List[str]) -> bool:
    """添加文件到暂存区（简化函数）"""
    git = GitOperations(repo_root)
    return git.add(paths)


def git_commit(repo_root: Path, message: str, allow_empty: bool = False) -> bool:
    """提交更改（简化函数）"""
    git = GitOperations(repo_root)
    return git.commit(message, allow_empty)


def git_push(repo_root: Path, remote: str = "origin", branch: str = "main") -> bool:
    """推送到远程仓库（简化函数）"""
    git = GitOperations(repo_root)
    return git.push(remote, branch)


def git_pull(repo_root: Path, remote: str = "origin", branch: str = "main") -> bool:
    """从远程仓库拉取更新（简化函数）"""
    git = GitOperations(repo_root)
    return git.pull(remote, branch)


def git_commit_and_push(
    repo_root: Path,
    message: str,
    paths: Optional[List[str]] = None,
    remote: str = "origin",
    branch: str = "main",
) -> bool:
    """
    提交并推送更改

    Args:
        repo_root: 仓库根目录
        message: 提交消息
        paths: 要添加的文件路径（None表示自动添加所有更改）
        remote: 远程仓库名称
        branch: 分支名称

    Returns:
        是否成功
    """
    git = GitOperations(repo_root)

    # 添加文件
    if paths:
        if not git.add(paths):
            return False
    else:
        # 添加所有更改
        if not git.add(["."]):
            return False

    # 提交
    if not git.commit(message):
        return False

    # 推送
    return git.push(remote, branch)
