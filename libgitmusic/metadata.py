import json
import os
import time
from pathlib import Path
from typing import List, Dict, Optional, Iterator
from .events import EventEmitter

class MetadataManager:
    """元数据管理模块，负责 metadata.jsonl 的读写、校验及锁机制"""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.lock_path = file_path.with_suffix('.lock')
        self._has_lock = False

    def acquire_lock(self, timeout=10):
        """获取文件锁（兼容 Windows/Linux 的简单实现）"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 使用 x 模式打开，如果文件已存在则报错（原子操作）
                with open(self.lock_path, 'x') as f:
                    f.write(str(os.getpid()))
                self._has_lock = True
                return
            except FileExistsError:
                time.sleep(0.1)

        raise RuntimeError(f"Could not acquire metadata lock after {timeout}s. Another process might be running.")

    def release_lock(self):
        """释放文件锁"""
        if self._has_lock:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
            self._has_lock = False

    def load_all(self) -> List[Dict]:
        """加载所有元数据条目"""
        if not self.file_path.exists():
            return []

        entries = []
        with open(self.file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
        return entries

    def save_all(self, entries: List[Dict]):
        """保存所有元数据条目（原子写入）"""
        temp_path = self.file_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            for entry in entries:
                # 保持统一的字段顺序
                ordered_entry = self._order_fields(entry)
                f.write(json.dumps(ordered_entry, ensure_ascii=False) + '\n')
        os.replace(temp_path, self.file_path)

    def _order_fields(self, entry: Dict) -> Dict:
        """统一元数据字段顺序"""
        order = [
            "audio_oid", "cover_oid", "title", "artists",
            "album", "date", "uslt", "created_at"
        ]
        return {k: entry[k] for k in order if k in entry}

    def update_entry(self, audio_oid: str, updates: Dict):
        """更新特定条目"""
        entries = self.load_all()
        found = False
        for i, entry in enumerate(entries):
            if entry.get('audio_oid') == audio_oid:
                entries[i].update(updates)
                found = True
                break

        if not found:
            entries.append(updates)

        self.save_all(entries)
