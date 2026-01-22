"""
结构化结果类定义
为各种操作提供统一的结果对象
"""

from typing import Optional, Dict, Any, List
from .exceptions import GitMusicError


class Result:
    """基础结果类"""
    
    def __init__(self, success: bool, message: str, data: Optional[Dict] = None, 
                 error: Optional[GitMusicError] = None):
        self.success = success
        self.message = message
        self.data = data or {}
        self.error = error
        
    def __repr__(self):
        return f"Result(success={self.success}, message='{self.message}')"


class StoreResult(Result):
    """存储操作结果类"""
    
    def __init__(self, success: bool, message: str, data: Optional[Dict] = None, 
                 error: Optional[GitMusicError] = None, oid: Optional[str] = None):
        super().__init__(success, message, data, error)
        self.oid = oid


class RemoteResult(Result):
    """远程操作结果类"""
    
    def __init__(self, success: bool, message: str, data: Optional[Dict] = None, 
                 error: Optional[GitMusicError] = None, remote_path: Optional[str] = None):
        super().__init__(success, message, data, error)
        self.remote_path = remote_path


class VerifyResult(Result):
    """验证操作结果类"""
    
    def __init__(self, success: bool, message: str, data: Optional[Dict] = None, 
                 error: Optional[GitMusicError] = None, 
                 checked_count: int = 0, error_count: int = 0):
        super().__init__(success, message, data, error)
        self.checked_count = checked_count
        self.error_count = error_count


class CleanupResult(Result):
    """清理操作结果类"""
    
    def __init__(self, success: bool, message: str, data: Optional[Dict] = None, 
                 error: Optional[GitMusicError] = None,
                 deleted_count: int = 0, preserved_count: int = 0):
        super().__init__(success, message, data, error)
        self.deleted_count = deleted_count
        self.preserved_count = preserved_count


class ReleaseResult(Result):
    """发布操作结果类"""
    
    def __init__(self, success: bool, message: str, data: Optional[Dict] = None, 
                 error: Optional[GitMusicError] = None,
                 total_entries: int = 0, generated_count: int = 0):
        super().__init__(success, message, data, error)
        self.total_entries = total_entries
        self.generated_count = generated_count