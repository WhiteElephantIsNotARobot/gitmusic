import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Iterator
from .events import EventEmitter


class ValidationError(ValueError):
    """元数据校验失败异常"""

    pass


class MetadataManager:
    """元数据管理模块，负责 metadata.jsonl 的读写、校验及锁机制"""

    def __init__(self, context: "Context"):
        """
        初始化元数据管理器

        Args:
            context: 上下文对象，包含所有路径和配置
        """
        from .context import Context

        if not isinstance(context, Context):
            raise TypeError("context must be an instance of Context")

        self.context = context
        self.file_path = context.metadata_file
        self.lock_path = context.metadata_file.with_suffix(".lock")
        self._has_lock = False

    def acquire_lock(self, timeout=10):
        """获取文件锁（兼容 Windows/Linux 的简单实现）"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 使用 x 模式打开，如果文件已存在则报错（原子操作）
                with open(self.lock_path, "x") as f:
                    f.write(str(os.getpid()))
                self._has_lock = True
                return
            except FileExistsError:
                time.sleep(0.1)

        raise RuntimeError(
            f"Could not acquire metadata lock after {timeout}s. Another process might be running."
        )

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
        with open(self.file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
        return entries

    def save_all(self, entries: List[Dict]):
        """保存所有元数据条目（原子写入）"""
        # 检查重复 audio_oid
        try:
            self._check_duplicate_oids(entries)
        except ValidationError as e:
            EventEmitter.error(
                f"元数据条目重复检查失败: {str(e)}",
                context={"total_entries": len(entries)},
            )
            raise

        # 校验所有条目
        for i, entry in enumerate(entries):
            try:
                self.validate_entry(entry)
            except ValidationError as e:
                EventEmitter.error(
                    f"元数据条目校验失败 (索引 {i}): {str(e)}",
                    context={"entry": entry, "index": i},
                )
                raise

        temp_path = self.file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            for entry in entries:
                # 保持统一的字段顺序
                ordered_entry = self._order_fields(entry)
                f.write(json.dumps(ordered_entry, ensure_ascii=False) + "\n")
        os.replace(temp_path, self.file_path)

    def _order_fields(self, entry: Dict) -> Dict:
        """统一元数据字段顺序"""
        order = [
            "audio_oid",
            "cover_oid",
            "title",
            "artists",
            "album",
            "date",
            "uslt",
            "created_at",
        ]
        return {k: entry[k] for k in order if k in entry}

    def _normalize_date(self, date_str: str) -> str:
        """标准化日期格式：YYYY -> YYYY-01-01, YYYY-MM -> YYYY-MM-01

        Args:
            date_str: 原始日期字符串

        Returns:
            标准化后的 YYYY-MM-DD 格式字符串
        """
        if not date_str:
            return date_str

        # 匹配 YYYY 格式
        if re.match(r"^\d{4}$", date_str):
            return f"{date_str}-01-01"
        # 匹配 YYYY-MM 格式
        elif re.match(r"^\d{4}-\d{2}$", date_str):
            return f"{date_str}-01"

        return date_str

    def validate_entry(self, entry: Dict) -> Dict:
        """校验元数据条目是否符合规范，自动标准化日期格式

        Args:
            entry: 元数据条目字典（可能被修改）

        Returns:
            标准化后的元数据条目

        Raises:
            ValidationError: 校验失败时抛出
        """
        # audio_oid: 匹配 ^sha256:[0-9a-f]{64}$
        audio_oid = entry.get("audio_oid")
        if not audio_oid:
            raise ValidationError("audio_oid 字段缺失")
        if not re.match(r"^sha256:[0-9a-f]{64}$", audio_oid):
            raise ValidationError(f"audio_oid 格式无效: {audio_oid}")

        # title: 非空字符串（清理非法字符后长度 > 0）
        title = entry.get("title")
        if title is None:
            raise ValidationError("title 字段缺失")
        if not isinstance(title, str):
            raise ValidationError("title 必须是字符串")
        cleaned_title = title.strip()
        if len(cleaned_title) == 0:
            raise ValidationError("title 不能为空字符串")

        # artists: 非空数组，元素非空字符串
        artists = entry.get("artists")
        if artists is None:
            raise ValidationError("artists 字段缺失")
        if not isinstance(artists, list):
            raise ValidationError("artists 必须是数组")
        if len(artists) == 0:
            raise ValidationError("artists 不能为空数组")
        for i, artist in enumerate(artists):
            if not isinstance(artist, str):
                raise ValidationError(f"artists[{i}] 必须是字符串")
            if not artist.strip():
                raise ValidationError(f"artists[{i}] 不能为空字符串")

        # date: 若存在则标准化为 YYYY-MM-DD
        date = entry.get("date")
        if date is not None:
            if not isinstance(date, str):
                raise ValidationError("date 必须是字符串")

            # 标准化日期格式
            normalized_date = self._normalize_date(date)

            # 验证标准化后的日期格式
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", normalized_date):
                raise ValidationError(
                    f"date 格式无效，应为 YYYY、YYYY-MM 或 YYYY-MM-DD: {date}"
                )

            # 验证日期有效性
            try:
                datetime.strptime(normalized_date, "%Y-%m-%d")
            except ValueError:
                raise ValidationError(f"date 不是有效日期: {date}")

            # 更新条目中的日期为标准化格式
            entry["date"] = normalized_date

        # created_at: 必须为 UTC ISO8601 时间戳
        created_at = entry.get("created_at")
        if not created_at:
            raise ValidationError("created_at 字段缺失")
        if not isinstance(created_at, str):
            raise ValidationError("created_at 必须是字符串")
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if not dt.tzinfo:
                raise ValidationError("created_at 必须包含时区信息")
        except ValueError:
            raise ValidationError(f"created_at 不是有效的 ISO8601 时间戳: {created_at}")

        # cover_oid: 若存在必须符合 sha256 格式
        cover_oid = entry.get("cover_oid")
        if cover_oid is not None:
            if not isinstance(cover_oid, str):
                raise ValidationError("cover_oid 必须是字符串")
            if not re.match(r"^sha256:[0-9a-f]{64}$", cover_oid):
                raise ValidationError(f"cover_oid 格式无效: {cover_oid}")

        # album: 若存在必须是非空字符串
        album = entry.get("album")
        if album is not None:
            if not isinstance(album, str):
                raise ValidationError("album 必须是字符串")
            if not album.strip():
                raise ValidationError("album 不能为空字符串")

        # uslt: 若存在必须是字符串
        uslt = entry.get("uslt")
        if uslt is not None and not isinstance(uslt, str):
            raise ValidationError("uslt 必须是字符串")

        # 空值策略：禁止写入空字符串；缺失字段显式为 null 或省略
        # 已经在校验中处理了字符串非空，但还需要确保没有空字符串值
        for key, value in entry.items():
            if isinstance(value, str) and value == "":
                raise ValidationError(f"{key} 不能为空字符串，应省略或设置为 null")

        return entry

    def _check_duplicate_oids(self, entries: List[Dict]) -> None:
        """检查条目列表中是否有重复的 audio_oid

        Args:
            entries: 元数据条目列表

        Raises:
            ValidationError: 发现重复 audio_oid 时抛出
        """
        seen = set()
        duplicates = []
        for i, entry in enumerate(entries):
            audio_oid = entry.get("audio_oid")
            if audio_oid:
                if audio_oid in seen:
                    duplicates.append((i, audio_oid))
                else:
                    seen.add(audio_oid)

        if duplicates:
            dup_info = ", ".join(
                f"索引 {i}: {oid[:20]}..." for i, oid in duplicates[:5]
            )
            if len(duplicates) > 5:
                dup_info += f" 等 {len(duplicates)} 个重复"
            raise ValidationError(f"发现重复的 audio_oid: {dup_info}")

    def update_entry(self, audio_oid: str, updates: Dict):
        """更新特定条目"""
        # 确保updates中的audio_oid与参数一致（如果提供了）
        if "audio_oid" in updates and updates["audio_oid"] != audio_oid:
            raise ValidationError(
                f"audio_oid 不匹配: 参数为 {audio_oid}, updates 中为 {updates['audio_oid']}"
            )

        entries = self.load_all()
        found = False
        for i, entry in enumerate(entries):
            if entry.get("audio_oid") == audio_oid:
                # 合并更新并校验
                merged_entry = entry.copy()
                merged_entry.update(updates)
                try:
                    self.validate_entry(merged_entry)
                except ValidationError as e:
                    EventEmitter.error(
                        f"更新条目校验失败 (audio_oid: {audio_oid}): {str(e)}",
                        context={"audio_oid": audio_oid, "updates": updates},
                    )
                    raise
                entries[i] = merged_entry
                found = True
                break

        if not found:
            # 新条目，确保包含audio_oid
            new_entry = updates.copy()
            if "audio_oid" not in new_entry:
                new_entry["audio_oid"] = audio_oid
            try:
                self.validate_entry(new_entry)
            except ValidationError as e:
                EventEmitter.error(
                    f"新条目校验失败 (audio_oid: {audio_oid}): {str(e)}",
                    context={"audio_oid": audio_oid, "updates": updates},
                )
                raise
            entries.append(new_entry)

        self.save_all(entries)
