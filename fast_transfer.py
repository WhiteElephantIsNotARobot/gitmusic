#!/usr/bin/env python3
"""
优化的音频文件传输脚本
使用rsync批量传输 + 并行处理 + SSH连接复用
"""

import os
import json
import hashlib
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# 尝试导入 mutagen
try:
    from mutagen import File
    from mutagen.id3 import ID3, APIC
except ImportError:
    logger.error("错误: mutagen 库未安装")
    exit(1)


def extract_audio_stream(audio_path):
    """提取纯净音频流并计算哈希（完全移除ID3标签）"""
    try:
        cmd = [
            'ffmpeg', '-i', str(audio_path), '-map', '0:a:0',
            '-c', 'copy', '-f', 'mp3',
            '-map_metadata', '-1',  # 移除元数据
            '-id3v2_version', '0',  # 不写入ID3v2标签
            '-write_id3v1', '0',    # 不写入ID3v1标签
            'pipe:1'
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        audio_data = result.stdout
        audio_hash = hashlib.sha256(audio_data).hexdigest()
        return audio_data, audio_hash
    except subprocess.CalledProcessError as e:
        logger.error(f"提取音频流失败 {audio_path}: {e}")
        raise


def extract_cover(audio_path):
    """提取封面图片并计算哈希"""
    try:
        cmd = [
            'ffmpeg', '-i', str(audio_path), '-map', '0:v',
            '-map', '-0:V', '-c', 'copy', '-f', 'image2', 'pipe:1'
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        if result.stdout:
            cover_data = result.stdout
            cover_hash = hashlib.sha256(cover_data).hexdigest()
            return cover_data, cover_hash
        return None, None
    except subprocess.CalledProcessError:
        return None, None


def parse_metadata(audio_path):
    """解析元数据"""
    try:
        audio = File(audio_path)
        metadata = {}

        if hasattr(audio, 'tags') and audio.tags:
            # 艺术家
            if 'TPE1' in audio.tags:
                artists = audio.tags['TPE1']
                if isinstance(artists, list):
                    artist_list = []
                    for a in artists:
                        parts = str(a).split(' & ')
                        for part in parts:
                            artist_list.extend([p.strip() for p in part.split(', ') if p.strip()])
                    metadata["artists"] = artist_list
                else:
                    parts = str(artists).split(' & ')
                    metadata["artists"] = [p.strip() for p in parts if p.strip()]

            # 标题
            if 'TIT2' in audio.tags:
                title = audio.tags['TIT2']
                metadata["title"] = str(title[0]) if isinstance(title, list) else str(title)

            # 专辑
            if 'TALB' in audio.tags:
                album = audio.tags['TALB']
                metadata["album"] = str(album[0]) if isinstance(album, list) else str(album)

            # 日期
            if 'TDRC' in audio.tags:
                date = audio.tags['TDRC']
                metadata["date"] = str(date[0]) if isinstance(date, list) else str(date)

            # 歌词
            if isinstance(audio.tags, ID3):
                for frame in audio.tags.values():
                    if frame.FrameID == 'USLT':
                        text = frame.text.decode('utf-8') if hasattr(frame.text, 'decode') else str(frame.text)
                        metadata["uslt"] = text
                        break

        # 如果没有标题，从文件名推断
        if 'title' not in metadata or 'artists' not in metadata:
            filename = Path(audio_path).stem
            if ' - ' in filename:
                parts = filename.split(' - ', 1)
                if len(parts) == 2:
                    if 'artists' not in metadata:
                        metadata["artists"] = [parts[0].strip()]
                    if 'title' not in metadata:
                        metadata["title"] = parts[1].strip()

        return metadata
    except Exception as e:
        logger.error(f"解析元数据失败 {audio_path}: {e}")
        return {}


def process_single_file(audio_file, temp_dir):
    """处理单个文件：提取音频、封面、元数据"""
    try:
        logger.info(f"处理: {audio_file.name}")

        # 提取音频流
        audio_data, audio_hash = extract_audio_stream(audio_file)

        # 提取封面
        cover_data, cover_hash = extract_cover(audio_file)

        # 解析元数据
        metadata = parse_metadata(audio_file)

        # 保存到临时目录
        audio_temp = temp_dir / f"{audio_hash}.mp3"
        audio_temp.write_bytes(audio_data)

        cover_temp = None
        if cover_data and cover_hash:
            cover_temp = temp_dir / f"{cover_hash}.jpg"
            cover_temp.write_bytes(cover_data)

        return {
            'audio_temp': audio_temp,
            'audio_hash': audio_hash,
            'cover_temp': cover_temp,
            'cover_hash': cover_hash,
            'metadata': metadata
        }
    except Exception as e:
        logger.error(f"处理失败 {audio_file}: {e}")
        return None


def cache_store(temp_dir, cache_root):
    """将临时文件保存到本地 cache/data/ 路径，模拟远端对象存储布局。"""
    cache_root = Path(cache_root)
    objects_root = cache_root / 'data' / 'objects' / 'sha256'
    covers_root = cache_root / 'data' / 'covers' / 'sha256'

    files = [f for f in temp_dir.iterdir() if f.is_file()]
    success = True

    for file_path in files:
        hash_name = file_path.stem
        subdir = hash_name[:2]
        if file_path.suffix == '.mp3':
            target_dir = objects_root / subdir
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"{hash_name}.mp3"
        else:
            target_dir = covers_root / subdir
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"{hash_name}.jpg"

        try:
            # 只在目标不存在时写入（避免重复写入）
            if not target_path.exists():
                shutil.copy2(file_path, target_path)
        except Exception as e:
            logger.error(f"写入 cache 失败 {file_path.name}: {e}")
            success = False

    return success


def parallel_scp_transfer(temp_dir, remote_user, remote_host, max_workers=4, use_cache=False, cache_root=None):
    """传输临时文件：当 use_cache=True 时写入本地 cache，否则使用并行 SCP。"""
    if use_cache:
        if cache_root is None:
            cache_root = Path(__file__).parent.parent / 'cache'
        logger.info(f"使用本地 cache 存储到: {cache_root}")
        return cache_store(temp_dir, cache_root)

    ssh_target = f"{remote_user}@{remote_host}" if remote_user else remote_host

    # 获取所有临时文件
    files = [f for f in temp_dir.iterdir() if f.is_file()]

    # 确保远程目录存在
    try:
        mkdir_cmd = [
            'ssh', ssh_target,
            'mkdir -p /srv/music/data/objects/sha256 /srv/music/data/covers/sha256'
        ]
        subprocess.run(mkdir_cmd, check=True, capture_output=True)
    except:
        pass

    # 为每个文件确定目标路径
    transfer_tasks = []
    for file_path in files:
        if file_path.suffix == '.mp3':
            # 音频文件: /srv/music/data/objects/sha256/ab/abc123.mp3
            hash_name = file_path.stem
            subdir = hash_name[:2]
            remote_path = f"/srv/music/data/objects/sha256/{subdir}/{hash_name}.mp3"
        else:
            # 封面文件: /srv/music/data/covers/sha256/ab/abc123.jpg
            hash_name = file_path.stem
            subdir = hash_name[:2]
            remote_path = f"/srv/music/data/covers/sha256/{subdir}/{hash_name}.jpg"

        transfer_tasks.append((file_path, remote_path))

    # 并行传输函数
    def transfer_file(task):
        local_path, remote_path = task
        try:
            # 创建远程子目录
            remote_dir = os.path.dirname(remote_path)
            mkdir_cmd = ['ssh', ssh_target, f'mkdir -p {remote_dir}']
            subprocess.run(mkdir_cmd, check=True, capture_output=True)

            # 传输文件
            scp_cmd = ['scp', '-C', str(local_path), f'{ssh_target}:{remote_path}']
            subprocess.run(scp_cmd, check=True, capture_output=True)
            return True
        except Exception as e:
            logger.error(f"传输失败 {local_path.name}: {e}")
            return False

    # 并行执行
    logger.info(f"开始并行SCP传输 {len(transfer_tasks)} 个文件...")
    start_time = datetime.now()

    success_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(transfer_file, task) for task in transfer_tasks]
        for future in as_completed(futures):
            if future.result():
                success_count += 1

    duration = (datetime.now() - start_time).total_seconds()
    total_size = sum(f.stat().st_size for f in files)
    speed = total_size / duration / 1024 / 1024 if duration > 0 else 0

    logger.info(f"传输完成! 成功: {success_count}/{len(transfer_tasks)}, 耗时: {duration:.1f}s, 速度: {speed:.2f} MB/s")
    return success_count == len(transfer_tasks)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="优化的音频传输工具")
    parser.add_argument("remote_host", help="远程服务器地址 (可传 'cache' 但推荐用 --use-cache)")
    parser.add_argument("-u", "--user", help="远程用户名")
    parser.add_argument("-w", "--workers", type=int, default=4, help="并行处理线程数")
    parser.add_argument("--use-cache", action="store_true", help="将对象写入本地 cache 目录，作为远端 data 的替代")
    parser.add_argument("--cache-root", default=str(Path(__file__).parent.parent / 'cache'), help="本地 cache 根目录，默认 ../cache")

    args = parser.parse_args()

    work_dir = Path(__file__).parent.parent / "work"
    if not work_dir.exists():
        logger.error(f"work目录不存在: {work_dir}")
        return

    # 查找音频文件
    audio_files = []
    for ext in ['*.mp3', '*.flac', '*.m4a', '*.wav', '*.ogg']:
        audio_files.extend(work_dir.glob(ext))

    if not audio_files:
        logger.error("未找到音频文件")
        return

    logger.info(f"找到 {len(audio_files)} 个音频文件")

    # 创建临时目录
    with tempfile.TemporaryDirectory(prefix="audio_transfer_") as temp_dir:
        temp_path = Path(temp_dir)

        # 第一阶段：并行处理文件
        processed = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(process_single_file, f, temp_path): f
                for f in audio_files
            }

            for future in as_completed(futures):
                result = future.result()
                if result:
                    processed.append(result)

        if not processed:
            logger.error("没有文件被成功处理")
            return

        logger.info(f"成功处理 {len(processed)} 个文件")

        # 第二阶段：批量传输（或写入本地 cache）
        success = parallel_scp_transfer(temp_path, args.user, args.remote_host, args.workers, use_cache=args.use_cache, cache_root=args.cache_root)

        if success:
            # 生成metadata.jsonl
            metadata_file = Path(__file__).parent / "metadata.jsonl"
            with open(metadata_file, 'w', encoding='utf-8') as f:
                for item in processed:
                    metadata = item['metadata']
                    metadata['audio_oid'] = f"sha256:{item['audio_hash']}"
                    if item['cover_hash']:
                        metadata['cover_oid'] = f"sha256:{item['cover_hash']}"

                    now = datetime.utcnow().isoformat() + 'Z'
                    metadata['created_at'] = now
                    metadata['updated_at'] = now

                    f.write(json.dumps(metadata, ensure_ascii=False) + '\n')

            logger.info(f"Metadata已保存到: {metadata_file}")
            logger.info("\n接下来:")
            logger.info("1. cd repo")
            logger.info("2. git add metadata.jsonl")
            logger.info("3. git commit -m 'Add metadata'")
            logger.info("4. git push")
        else:
            logger.error("传输失败")


if __name__ == "__main__":
    main()
