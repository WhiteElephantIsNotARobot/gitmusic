import os
import json
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.audio import AudioIO
from libgitmusic.metadata import MetadataManager

def main():
    # 解析参数
    mode = "local"
    for arg in sys.argv:
        if arg.startswith("--mode="):
            mode = arg.split("=")[1]

    repo_root = Path(__file__).parent.parent
    metadata_mgr = MetadataManager(repo_root / "metadata.jsonl")

    if mode == "local":
        release_dir = repo_root.parent / "release"
        cache_root = repo_root.parent / "cache"
    else:
        release_dir = Path("/srv/music/data/release")
        cache_root = Path("/srv/music/data")

    all_entries = metadata_mgr.load_all()
    EventEmitter.phase_start("release", total_items=len(all_entries))

    for i, entry in enumerate(all_entries):
        filename = AudioIO.sanitize_filename(f"{'/'.join(entry['artists'])} - {entry['title']}.mp3")
        out_path = release_dir / filename

        EventEmitter.item_event(filename, "generating")

        audio_hash = entry['audio_oid'].split(":")[1]
        src_audio = cache_root / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"

        cover_data = None
        if entry.get('cover_oid'):
            cover_hash = entry['cover_oid'].split(":")[1]
            cover_path = cache_root / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
            if cover_path.exists():
                with open(cover_path, 'rb') as f:
                    cover_data = f.read()

        if src_audio.exists():
            AudioIO.embed_metadata(src_audio, entry, cover_data, out_path)
            EventEmitter.item_event(filename, "success")
        else:
            EventEmitter.error(f"Source missing for {entry['audio_oid']}")

        EventEmitter.batch_progress("release", i + 1, len(all_entries))

    EventEmitter.result("ok", message="Release generation completed")

if __name__ == "__main__":
    main()


def get_metadata_hash(item):
    """计算元数据条目的哈希值，用于增量对比"""
    # 排除掉 updated_at 字段（如果存在），created_at 必须包含在内以检测数据库重建
    content = {k: v for k, v in item.items() if k not in ['updated_at']}
    s = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def sanitize_filename(filename):
    """清理文件名中的非法字符及空字符"""
    filename = filename.replace('\x00', '')
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(char, '_')
    return filename.strip()


def handle_filename_conflict(dest_path, conflict_strategy='suffix'):
    """
    处理文件名冲突

    Args:
        dest_path: 目标文件路径
        conflict_strategy: 冲突处理策略 ('overwrite', 'suffix', 'prompt')

    Returns:
        处理后的文件路径
    """
    if not dest_path.exists():
        return dest_path

    if conflict_strategy == 'overwrite':
        logger.warning(f"文件名冲突，将覆盖现有文件: {dest_path}")
        return dest_path

    elif conflict_strategy == 'suffix':
        # 添加后缀避免冲突
        counter = 1
        while True:
            new_path = dest_path.with_suffix(f".{counter}{dest_path.suffix}")
            if not new_path.exists():
                logger.warning(f"文件名冲突，使用新文件名: {new_path}")
                return new_path
            counter += 1

    elif conflict_strategy == 'prompt':
        # 提示用户选择
        logger.error(f"文件名冲突: {dest_path}")
        logger.error("请选择操作: [O]覆盖, [S]添加后缀, [C]取消")
        while True:
            choice = input().strip().lower()
            if choice == 'o':
                return dest_path
            elif choice == 's':
                counter = 1
                while True:
                    new_path = dest_path.with_suffix(f".{counter}{dest_path.suffix}")
                    if not new_path.exists():
                        logger.warning(f"使用新文件名: {new_path}")
                        return new_path
                    counter += 1
            elif choice == 'c':
                raise ValueError(f"用户取消了文件名冲突处理: {dest_path}")
            else:
                logger.error("无效选择，请重新输入 [O/S/C]")

    else:
        raise ValueError(f"未知的冲突策略: {conflict_strategy}")


def get_work_filename(metadata):
    """生成文件名：艺术家 - 标题.mp3"""
    artists = metadata.get('artists', [])
    title = metadata.get('title', '未知')
    artist_str = ', '.join(artists) if isinstance(artists, list) else str(artists)
    return f"{artist_str} - {title}.mp3" if artist_str else f"{title}.mp3"


def embed_metadata(audio_path, metadata, cover_path=None):
    """嵌入元数据到音频文件（稳健临时文件模式）"""
    tmp_file = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp3')
        os.close(fd)
        tmp_file = Path(tmp_path)
        shutil.copy2(audio_path, tmp_file)

        audio = MP3(tmp_file)
        if audio.tags is None:
            audio.add_tags()
        audio.delete()

        # 基础标签
        artists = metadata.get('artists', [])
        if artists:
            audio.tags.add(TPE1(encoding=3, text=artists if isinstance(artists, list) else [artists]))

        title = metadata.get('title', '未知')
        audio.tags.add(TIT2(encoding=3, text=title))

        album = metadata.get('album') or title
        audio.tags.add(TALB(encoding=3, text=album))

        date = metadata.get('date')
        if date:
            audio.tags.add(TDRC(encoding=3, text=date))

        uslt = metadata.get('uslt')
        if uslt:
            audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt))

        # 封面嵌入 (关键修复：确保 server 模式也能正确找到封面)
        if cover_path and cover_path.exists():
            with open(cover_path, 'rb') as f:
                cover_data = f.read()
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))

        # 嵌入元数据哈希用于增量对比
        meta_hash = get_metadata_hash(metadata)
        audio.tags.add(TXXX(encoding=3, desc='METADATA_HASH', text=[meta_hash]))

        audio.save()
        with open(tmp_file, 'rb') as f:
            return f.read()
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()


def process_single_item(item, data_root, releases_root, conflict_strategy='suffix'):
    """处理单个条目"""
    try:
        audio_oid = item.get('audio_oid')
        if not audio_oid: return False

        title = item.get('title', '未知')
        filename = sanitize_filename(get_work_filename(item))
        dest_path = releases_root / filename

        # 查找音频
        hash_hex = audio_oid[7:]
        audio_path = data_root / 'objects' / 'sha256' / hash_hex[:2] / f"{hash_hex}.mp3"
        if not audio_path.exists():
            logger.error(f"跳过生成 (音频缺失): {title} [{audio_oid[:16]}]")
            # 写入临时日志
            log_file = releases_root / 'temp_error.log'
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] 跳过生成 (音频缺失): {title} [{audio_oid[:16]}]\n")
            return False

        # 查找封面
        cover_oid = item.get('cover_oid')
        cover_path = None
        if cover_oid:
            c_hash = cover_oid[7:]
            cover_path = data_root / 'covers' / 'sha256' / c_hash[:2] / f"{c_hash}.jpg"
            if not cover_path.exists():
                logger.error(f"跳过生成 (封面缺失): {title} [{cover_oid[:16]}]")
                # 写入临时日志
                log_file = releases_root / 'temp_error.log'
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] 跳过生成 (封面缺失): {title} [{cover_oid[:16]}]\n")
                return False

        # 处理文件名冲突
        dest_path = handle_filename_conflict(dest_path, conflict_strategy)

        data = embed_metadata(audio_path, item, cover_path)

        # 原子写入
        tmp_dest = dest_path.with_suffix(f".{audio_oid[7:15]}.tmp")
        with open(tmp_dest, 'wb') as f:
            f.write(data)
        shutil.move(str(tmp_dest), str(dest_path))

        # 同步时间戳
        dt_str = item.get('created_at')
        if dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            ts = dt.timestamp()
            os.utime(dest_path, (ts, ts))

        return True
    except Exception as e:
        logger.error(f"处理失败 {item.get('title', '未知')}: {e}")
        # 写入临时日志
        log_file = releases_root / 'temp_error.log'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now()}] 处理失败 {item.get('title', '未知')}: {e}\n")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="成品生成工具 (统一版)")
    parser.add_argument('--mode', choices=['local', 'server'], required=True, help="运行模式")
    parser.add_argument('--data-root', help="数据根目录 (objects/covers 所在)")
    parser.add_argument('--output', help="成品输出目录")
    parser.add_argument('--workers', type=int, help="并行线程数")
    parser.add_argument('--conflict-strategy', choices=['overwrite', 'suffix', 'prompt'], default='suffix', help="文件名冲突处理策略")
    args = parser.parse_args()

    # 自动推断路径
    repo_root = Path(__file__).parent.parent
    if args.mode == 'server':
        data_root = Path(args.data_root or '/srv/music/data')
        output_dir = Path(args.output or '/srv/music/data/releases')
        workers = args.workers or 4
        incremental = True
    else:
        data_root = repo_root.parent / 'cache'
        output_dir = Path(args.output or repo_root.parent / 'release')
        workers = args.workers or 1
        incremental = False

    metadata_file = repo_root / 'metadata.jsonl'
    if not metadata_file.exists():
        logger.error("metadata.jsonl 不存在")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    with open(metadata_file, 'r', encoding='utf-8') as f:
        metadata_list = [json.loads(line) for line in f if line.strip()]

    target_filenames = {sanitize_filename(get_work_filename(item)): get_metadata_hash(item) for item in metadata_list}

    if incremental:
        # Server 模式：增量更新 + 自动清理
        logger.info("扫描现有成品文件...")
        existing_files = list(output_dir.glob('*.mp3'))
        files_to_delete = []
        valid_hashes = set()

        for f in existing_files:
            try:
                audio = MP3(f)
                meta_hash = str(audio.tags['TXXX:METADATA_HASH'].text[0]) if audio.tags and 'TXXX:METADATA_HASH' in audio.tags else ""
                if f.name not in target_filenames or target_filenames[f.name] != meta_hash:
                    files_to_delete.append(f)
                else:
                    valid_hashes.add(meta_hash)
            except:
                files_to_delete.append(f)

        if files_to_delete:
            logger.info(f"清理 {len(files_to_delete)} 个过时文件...")
            for f in files_to_delete: f.unlink()

        to_generate = [item for item in metadata_list if get_metadata_hash(item) not in valid_hashes]
    else:
        # Local 模式：全量生成 (先清空)
        logger.info(f"正在清空输出目录: {output_dir}")
        for f in output_dir.glob('*.mp3'): f.unlink()
        to_generate = metadata_list

    if not to_generate:
        logger.info("所有成品已是最新。")
        return

    logger.info(f"开始生成 {len(to_generate)} 个条目 (模式: {args.mode})...")
    success = 0

    if args.mode == 'local' and tqdm:
        pbar = tqdm(total=len(to_generate), desc="生成中")
        for item in to_generate:
            if process_single_item(item, data_root, output_dir, args.conflict_strategy): success += 1
            pbar.update(1)
        pbar.close()
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_single_item, item, data_root, output_dir, args.conflict_strategy) for item in to_generate]
            for future in as_completed(futures):
                if future.result(): success += 1

    logger.info(f"完成！成功: {success}/{len(to_generate)}")


if __name__ == "__main__":
    main()
