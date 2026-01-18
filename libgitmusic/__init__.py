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

__all__ = [
    "EventEmitter",
    "MetadataManager",
    "TransportAdapter",
    "AudioIO",
    "ObjectStore",
    "HashUtils",
    "LockManager",
]
