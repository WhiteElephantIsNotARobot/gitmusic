"""
自定义异常类定义
定义了GitMusic系统中使用的各类具体异常
"""

class GitMusicError(Exception):
    """GitMusic系统基础异常类"""
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(GitMusicError):
    """数据验证失败异常"""
    pass


class TransportError(GitMusicError):
    """传输失败异常"""
    pass


class IOError(GitMusicError):
    """IO操作失败异常"""
    pass


class LockError(GitMusicError):
    """锁操作失败异常"""
    pass


class ConfigurationError(GitMusicError):
    """配置错误异常"""
    pass


class CommandError(GitMusicError):
    """命令执行失败异常"""
    pass