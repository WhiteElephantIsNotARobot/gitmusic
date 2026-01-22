"""
GitMusic核心库 - 音乐管理系统的底层组件
"""

from .events import EventEmitter
from .metadata import MetadataManager
from .transport import TransportAdapter
from .audio import AudioIO
from .object_store import ObjectStore
from .hash_utils import HashUtils
from .locking import LockManager
from .git import (
    GitOperations,
    git_add,
    git_commit,
    git_push,
    git_pull,
    git_commit_and_push,
)
from .context import Context, create_context
from .exceptions import (
    GitMusicError,
    ValidationError,
    TransportError,
    IOError,
    LockError,
    ConfigurationError,
    CommandError,
)
from .results import (
    Result,
    StoreResult,
    RemoteResult,
    VerifyResult,
    CleanupResult,
    ReleaseResult,
)

__all__ = [
    "EventEmitter",
    "MetadataManager",
    "TransportAdapter",
    "AudioIO",
    "ObjectStore",
    "HashUtils",
    "LockManager",
    "GitOperations",
    "git_add",
    "git_commit",
    "git_push",
    "git_pull",
    "git_commit_and_push",
    "Context",
    "create_context",
    "GitMusicError",
    "ValidationError",
    "TransportError",
    "IOError",
    "LockError",
    "ConfigurationError",
    "CommandError",
    "Result",
    "StoreResult",
    "RemoteResult",
    "VerifyResult",
    "CleanupResult",
    "ReleaseResult",
]
