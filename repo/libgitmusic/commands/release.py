import os
import json
import hashlib
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from ..events import EventEmitter
from ..audio import AudioIO
from ..metadata import MetadataManager
from ..object_store import ObjectStore


def calculate_metadata_hash(metadata: Dict) -> str:
    """
    计算元数据条目的哈希值，用于增量对比

    Args:
        metadata: 元数据字典

    Returns:
        哈希字符串 (sha256:hexdigest)
    """
    # 排除动态字段
    exclude_fields = {"updated_at", "created_at"}
    content = {k: v for k, v in metadata.items() if k not in exclude_fields}

    # 标准化JSON表示
    sorted_json = json.dumps(content, sort_keys=True, ensure_ascii=False)

    # 计算哈希
    hash_obj = hashlib.sha256(sorted_json.encode("utf-8"))
    return f"sha256:{hash_obj.hexdigest()}"


def extract_existing_metadata_hash(file_path: Path) -> Optional[str]:
    """
    从现有文件中提取嵌入的元数据哈希

    Args:
        file_path: 音频文件路径

    Returns:
        元数据哈希 (sha256:hexdigest) 或 None
    """
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3

        audio = MP3(file_path, ID3=ID3)
        if audio.tags:
            # 尝试使用 getall 方法获取 TXXX 标签
            txxx_tags = audio.tags.getall("TXXX")
            for tag in txxx_tags:
                if hasattr(tag, "desc") and tag.desc == "METADATA_HASH":
                    return tag.text[0]
    except Exception as e:
        EventEmitter.log(
            "debug", f"Failed to extract metadata hash from {file_path}: {str(e)}"
        )

    return None


def generate_release_filename(metadata: Dict) -> str:
    """
    生成发布文件名：艺术家 - 标题.mp3

    Args:
        metadata: 元数据字典

    Returns:
        安全的文件名
    """
    artists = metadata.get("artists", ["Unknown"])
    if isinstance(artists, list):
        artist_str = ", ".join(artists)
    else:
        artist_str = str(artists)

    title = metadata.get("title", "Unknown")

    if artist_str and artist_str != "Unknown":
        filename = f"{artist_str} - {title}.mp3"
    else:
        filename = f"{title}.mp3"

    return AudioIO.sanitize_filename(filename)


def handle_filename_conflict(
    target_path: Path, strategy: str = "suffix"
) -> Optional[Path]:
    """
    处理文件名冲突

    Args:
        target_path: 目标文件路径
        strategy: 冲突处理策略 ('overwrite', 'suffix', 'skip')

    Returns:
        处理后的文件路径，如果策略为 'skip' 且文件存在则返回 None
    """
    if not target_path.exists():
        return target_path

    if strategy == "overwrite":
        EventEmitter.log("warn", f"Overwriting existing file: {target_path}")
        return target_path

    elif strategy == "suffix":
        counter = 1
        while True:
            new_path = target_path.with_name(
                f"{target_path.stem}_{counter}{target_path.suffix}"
            )
            if not new_path.exists():
                EventEmitter.log("info", f"Using alternative filename: {new_path}")
                return new_path
            counter += 1

    elif strategy == "skip":
        EventEmitter.log("info", f"Skipping existing file: {target_path}")
        return None  # 表示跳过

    else:
        raise ValueError(f"Unknown conflict strategy: {strategy}")


def process_single_entry(
    entry: Dict,
    object_store: ObjectStore,
    release_dir: Path,
    conflict_strategy: str = "suffix",
    incremental: bool = False,
) -> bool:
    """
    处理单个元数据条目，生成发布文件

    Args:
        entry: 元数据条目
        object_store: 对象存储实例
        release_dir: 发布目录
        conflict_strategy: 文件名冲突处理策略
        incremental: 是否增量模式

    Returns:
        是否成功
    """
    try:
        audio_oid = entry.get("audio_oid")
        if not audio_oid:
            EventEmitter.error(
                f"Entry missing audio_oid: {entry.get('title', 'Unknown')}"
            )
            return False

        # 生成文件名
        filename = generate_release_filename(entry)
        target_path = release_dir / filename

        # 检查是否需要生成（增量模式）
        if incremental and target_path.exists():
            existing_hash = extract_existing_metadata_hash(target_path)
            current_hash = calculate_metadata_hash(entry)

            if existing_hash and existing_hash == current_hash:
                EventEmitter.item_event(filename, "skipped", "Metadata unchanged")
                return True

        # 处理文件名冲突
        if target_path.exists():
            resolved_path = handle_filename_conflict(target_path, conflict_strategy)
            if resolved_path is None:  # skip
                return True
            target_path = resolved_path

        # 获取音频路径
        audio_path = object_store.get_audio_path(audio_oid)
        if not audio_path or not audio_path.exists():
            EventEmitter.error(f"Audio object not found: {audio_oid}")
            return False

        # 获取封面数据
        cover_data = None
        cover_oid = entry.get("cover_oid")
        if cover_oid:
            cover_path = object_store.get_cover_path(cover_oid)
            if cover_path and cover_path.exists():
                with open(cover_path, "rb") as f:
                    cover_data = f.read()
            else:
                EventEmitter.log("warn", f"Cover object not found: {cover_oid}")

        # 嵌入元数据并生成文件
        EventEmitter.item_event(filename, "generating")
        AudioIO.embed_metadata(audio_path, entry, cover_data, target_path)

        # 设置文件时间戳（如果元数据中有创建时间）
        created_at = entry.get("created_at")
        if created_at:
            try:
                # 解析ISO格式时间戳
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                timestamp = dt.timestamp()
                os.utime(target_path, (timestamp, timestamp))
            except Exception as e:
                EventEmitter.log(
                    "debug", f"Failed to set timestamp for {filename}: {str(e)}"
                )

        EventEmitter.item_event(filename, "success", f"OID: {audio_oid[:16]}...")
        return True

    except Exception as e:
        EventEmitter.error(f"Failed to process entry: {str(e)}", {"entry": entry})
        return False


def scan_existing_releases(release_dir: Path) -> Dict[str, str]:
    """
    扫描现有发布文件，提取元数据哈希

    Args:
        release_dir: 发布目录

    Returns:
        字典：文件名 -> 元数据哈希
    """
    existing_hashes = {}

    for mp3_file in release_dir.glob("*.mp3"):
        try:
            metadata_hash = extract_existing_metadata_hash(mp3_file)
            if metadata_hash:
                existing_hashes[mp3_file.name] = metadata_hash
        except Exception as e:
            EventEmitter.log("debug", f"Failed to scan {mp3_file}: {str(e)}")

    return existing_hashes


def release_logic(
    metadata_mgr: MetadataManager,
    object_store: ObjectStore,
    release_dir: Path,
    mode: str = "local",
    conflict_strategy: str = "suffix",
    limit: Optional[int] = None,
    line_filter: Optional[str] = None,
    hash_filter: Optional[str] = None,
    search_filter: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
    workers: int = 1,
) -> Tuple[List[Dict], Optional[str]]:
    """
    Release命令的核心业务逻辑

    Args:
        metadata_mgr: 元数据管理器
        object_store: 对象存储
        release_dir: 发布目录
        mode: 生成模式 ('local', 'incremental')
        conflict_strategy: 文件名冲突处理策略
        limit: 最大处理数量
        line_filter: 行号过滤器
        hash_filter: 哈希过滤器
        search_filter: 搜索过滤器
        dry_run: 是否干跑模式

    Returns:
        (要处理的条目列表, 错误消息)
    """
    # 加载所有元数据
    all_entries = metadata_mgr.load_all()
    EventEmitter.log("info", f"Loaded {len(all_entries)} metadata entries")

    # 限制数量
    if limit and limit > 0:
        all_entries = all_entries[:limit]
        EventEmitter.log("info", f"Limited to {len(all_entries)} entries")

    # 按行号筛选
    if line_filter:
        line_nums = set()
        for part in line_filter.split(","):
            part = part.strip()
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                try:
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    line_nums.update(range(start, end + 1))
                except ValueError:
                    return [], f"Invalid line range: {part}"
            else:
                try:
                    line_nums.add(int(part.strip()))
                except ValueError:
                    return [], f"Invalid line number: {part}"

        filtered_entries = []
        for idx, entry in enumerate(all_entries, 1):
            if idx in line_nums:
                filtered_entries.append(entry)

        all_entries = filtered_entries
        EventEmitter.log("info", f"Selected {len(all_entries)} entries by line numbers")

    # 按哈希筛选
    if hash_filter:
        target_hash = hash_filter.lower()
        filtered_entries = []
        for entry in all_entries:
            audio_oid = entry.get("audio_oid", "").lower()
            if target_hash in audio_oid:
                filtered_entries.append(entry)

        all_entries = filtered_entries
        EventEmitter.log("info", f"Selected {len(all_entries)} entries by hash")

    # 按搜索关键词筛选
    if search_filter:
        search_term = search_filter.lower()
        filtered_entries = []
        for entry in all_entries:
            # 搜索标题和艺术家
            title = entry.get("title", "").lower()
            artists = entry.get("artists", [])
            artist_str = " ".join(artists).lower()

            if search_term in title or search_term in artist_str:
                filtered_entries.append(entry)

        all_entries = filtered_entries
        EventEmitter.log("info", f"Selected {len(all_entries)} entries by search")

    # 增量模式：计算需要生成的条目
    if mode == "incremental":
        EventEmitter.phase_start("scan")
        EventEmitter.log("info", "扫描现有发布文件")
        existing_hashes = scan_existing_releases(release_dir)
        EventEmitter.log("info", f"Found {len(existing_hashes)} existing release files")

        entries_to_process = []
        for entry in all_entries:
            filename = generate_release_filename(entry)
            current_hash = calculate_metadata_hash(entry)

            if (
                filename in existing_hashes
                and existing_hashes[filename] == current_hash
            ):
                # 文件存在且哈希匹配，跳过
                continue

            entries_to_process.append(entry)

        EventEmitter.log(
            "info",
            f"Incremental mode: {len(entries_to_process)}/{len(all_entries)} need generation",
        )
    else:
        entries_to_process = all_entries

    return entries_to_process, None


def execute_release(
    entries: List[Dict],
    object_store: ObjectStore,
    release_dir: Path,
    conflict_strategy: str = "suffix",
    incremental: bool = False,
    progress_callback=None,
    workers: int = 1,
) -> Tuple[int, int]:
    """
    执行真正的发布动作

    Args:
        entries: 要处理的条目列表
        object_store: 对象存储实例
        release_dir: 发布目录
        conflict_strategy: 文件名冲突处理策略
        incremental: 是否增量模式
        progress_callback: 进度回调函数

    Returns:
        (成功数, 总数)
    """
    if not entries:
        EventEmitter.result("ok", message="All releases are up to date")
        return 0, 0

    EventEmitter.phase_start("generate", total_items=len(entries))

    success_count = 0
    total_entries = len(entries)

    if workers <= 1:
        # 串行处理
        for i, entry in enumerate(entries):
            success = process_single_entry(
                entry,
                object_store,
                release_dir,
                conflict_strategy,
                incremental=incremental,
            )

            if success:
                success_count += 1

            EventEmitter.batch_progress("generate", i + 1, total_entries)

            if progress_callback:
                progress_callback(i + 1, total_entries)
    else:
        # 并行处理
        def process_entry_wrapper(entry):
            return process_single_entry(
                entry,
                object_store,
                release_dir,
                conflict_strategy,
                incremental=incremental,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            for entry in entries:
                future = executor.submit(process_entry_wrapper, entry)
                futures.append(future)

            # 等待所有任务完成，并更新进度
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                try:
                    success = future.result()
                    if success:
                        success_count += 1
                except Exception as e:
                    EventEmitter.error(f"Error processing entry: {str(e)}")
                completed += 1
                EventEmitter.batch_progress("generate", completed, total_entries)
                if progress_callback:
                    progress_callback(completed, total_entries)

    return success_count, total_entries
