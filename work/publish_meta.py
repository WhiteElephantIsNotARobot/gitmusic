#!/usr/bin/env python3
"""
工作目录发布脚本
扫描 work/ 目录中的 MP3 文件，计算音频哈希并更新 metadata.jsonl
"""

import os
import json
import hashlib
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
import logging
import sys
import threading
from io import StringIO

# 尝试导入 mutagen 和 tqdm
try:
    from mutagen import File
    from mutagen.id3 import ID3, APIC
except ImportError:
    print("错误: mutagen 库未安装", file=sys.stderr)
    exit(1)

try:
    from tqdm import tqdm
except ImportError:
    print("错误: tqdm 库未安装，请运行 pip install tqdm", file=sys.stderr)
    exit(1)


class BottomProgressBar:
    """底部固定进度条管理器（支持日志平滑滚动）"""
    def __init__(self):
        self.progress_bar = None
        self.lock = threading.Lock()

    def set_progress(self, current, total, desc=""):
        """设置进度"""
        with self.lock:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total, desc=desc, unit="file",
                                       bar_format='{desc} {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]',
                                       file=sys.stdout, dynamic_ncols=True)
            else:
                if self.progress_bar.desc != desc:
                    self.progress_bar.set_description(desc)
                self.progress_bar.total = total
                self.progress_bar.n = current
                self.progress_bar.refresh()

    def write_log(self, message):
        """通过tqdm安全地打印日志，不破坏进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.write(message)
            else:
                print(message)

    def close(self):
        """关闭进度条"""
        with self.lock:
            if self.progress_bar:
                self.progress_bar.close()
                self.progress_bar = None


# 创建全局进度管理器
progress_mgr = BottomProgressBar()


class TqdmLogHandler(logging.Handler):
    """将日志重定向到tqdm.write的处理器"""
    def emit(self, record):
        try:
            msg = self.format(record)
            progress_mgr.write_log(msg)
        except Exception:
            self.handleError(record)


# 配置日志
handler = TqdmLogHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)


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


def split_artists(artists_str):
    """拆分艺术家字符串"""
    if not artists_str:
        return []
    # 先按 ' & ' 拆分，再按 ', ' 拆分
    parts = artists_str.split(' & ')
    artist_list = []
    for part in parts:
        artist_list.extend([p.strip() for p in part.split(', ') if p.strip()])
    return artist_list


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
                        artist_list.extend(split_artists(str(a)))
                    metadata["artists"] = artist_list
                else:
                    metadata["artists"] = split_artists(str(artists))

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


def load_metadata(metadata_file):
    """加载现有 metadata.jsonl"""
    if not metadata_file.exists():
        return []

    metadata_list = []
    with open(metadata_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metadata_list.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return metadata_list


def save_metadata(metadata_file, metadata_list):
    """保存 metadata.jsonl（按 audio_oid 排序）"""
    # 按 audio_oid 排序
    metadata_list.sort(key=lambda x: x.get('audio_oid', ''))

    # 写入临时文件
    temp_file = metadata_file.with_suffix('.tmp')
    with open(temp_file, 'w', encoding='utf-8') as f:
        for item in metadata_list:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    # 原子替换
    shutil.move(str(temp_file), str(metadata_file))


def process_file(audio_file, metadata_list, cache_root):
    """处理单个文件：计算哈希，更新或新增条目"""
    try:
        # 提取音频流和哈希
        audio_data, audio_hash = extract_audio_stream(audio_file)
        audio_oid = f"sha256:{audio_hash}"

        # 提取封面和哈希
        cover_data, cover_hash = extract_cover(audio_file)
        new_cover_oid = f"sha256:{cover_hash}" if cover_hash else None

        # 解析元数据
        metadata = parse_metadata(audio_file)

        # 查找是否已存在
        existing = None
        for item in metadata_list:
            if item.get('audio_oid') == audio_oid:
                existing = item
                break

        now = datetime.utcnow().isoformat() + 'Z'

        result = {
            'filename': audio_file.name,
            'action': None,  # 'added' or 'updated'
            'title': metadata.get('title', '未知'),
            'artists': metadata.get('artists', []),
            'audio_oid': audio_oid
        }

        if existing:
            # 更新现有条目
            result['action'] = 'updated'
            logger.info(f"更新条目: {metadata.get('title', '未知')}")

            # 保留不变的字段
            existing['audio_oid'] = audio_oid
            existing['created_at'] = existing.get('created_at', now)

            # 更新所有可能变化的字段，空值则删除字段
            for field in ['title', 'artists', 'album', 'date', 'uslt']:
                value = metadata.get(field)
                if value:
                    existing[field] = value
                elif field in existing:
                    del existing[field]

            # 确保 title 和 artists 不为空（如果为空则保留原有值或设置默认值）
            if 'title' not in existing or not existing['title']:
                existing['title'] = metadata.get('title', '未知')
            if 'artists' not in existing or not existing['artists']:
                existing['artists'] = metadata.get('artists', [])

            existing['updated_at'] = now

            # 处理封面
            if new_cover_oid:
                existing['cover_oid'] = new_cover_oid
                # 保存新封面到 cache
                if cover_data:
                    save_to_cache(cover_data, new_cover_oid, cache_root, 'covers')
            else:
                # 如果新条目没有封面，删除旧的封面引用
                if 'cover_oid' in existing:
                    del existing['cover_oid']
        else:
            # 新增条目
            result['action'] = 'added'
            logger.info(f"新增条目: {metadata.get('title', '未知')}")
            new_item = {
                'audio_oid': audio_oid,
                'created_at': now,
                'updated_at': now
            }

            # 只添加非空字段
            if metadata.get('title'):
                new_item['title'] = metadata['title']
            else:
                new_item['title'] = '未知'

            if metadata.get('artists'):
                new_item['artists'] = metadata['artists']
            else:
                new_item['artists'] = []

            if metadata.get('album'):
                new_item['album'] = metadata['album']

            if metadata.get('date'):
                new_item['date'] = metadata['date']

            if metadata.get('uslt'):
                new_item['uslt'] = metadata['uslt']

            if new_cover_oid:
                new_item['cover_oid'] = new_cover_oid

            metadata_list.append(new_item)

            # 保存音频和封面到 cache
            save_to_cache(audio_data, audio_oid, cache_root, 'objects')
            if cover_data:
                save_to_cache(cover_data, new_cover_oid, cache_root, 'covers')

        return result
    except Exception as e:
        logger.error(f"处理失败 {audio_file.name}: {e}")
        return None


def save_to_cache(data, oid, cache_root, data_type):
    """保存数据到 cache"""
    if not data or not oid:
        return

    hash_hex = oid.replace('sha256:', '')
    subdir = hash_hex[:2]

    if data_type == 'objects':
        target_dir = cache_root / 'objects' / 'sha256' / subdir
        ext = '.mp3'
    else:
        target_dir = cache_root / 'covers' / 'sha256' / subdir
        ext = '.jpg'

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{hash_hex}{ext}"

    # 只在不存在时写入
    if not target_path.exists():
        with open(target_path, 'wb') as f:
            f.write(data)
        logger.info(f"保存到 cache: {data_type}/{subdir}/{hash_hex[:8]}...")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='扫描 work 目录并更新 metadata.jsonl')
    parser.add_argument('files', nargs='*', help='指定要处理的文件（可选）')
    parser.add_argument('--no-git', action='store_true', help='不自动执行 git 操作')
    args = parser.parse_args()

    # 确定工作目录
    repo_root = Path(__file__).parent.parent
    work_dir = repo_root.parent / "work"
    metadata_file = repo_root / "metadata.jsonl"
    cache_root = repo_root.parent / "cache"

    if not work_dir.exists():
        logger.error(f"work 目录不存在: {work_dir}")
        return

    # 获取要处理的文件
    if args.files:
        audio_files = [Path(f) for f in args.files if Path(f).exists()]
    else:
        # 扫描 work 目录（包括子文件夹）
        audio_files = []
        for ext in ['*.mp3', '*.flac', '*.m4a', '*.wav', '*.ogg']:
            audio_files.extend(work_dir.rglob(ext))

    if not audio_files:
        logger.error("未找到音频文件")
        return

    logger.info(f"找到 {len(audio_files)} 个音频文件")

    # 加载现有 metadata
    metadata_list = load_metadata(metadata_file)
    logger.info(f"现有 metadata 条目数: {len(metadata_list)}")

    # 处理每个文件（带进度条）
    processed_results = []
    total_files = len(audio_files)
    logger.info(f"开始处理 {total_files} 个文件...")

    # 创建进度条（固定描述，不随文件变化）
    progress_mgr.set_progress(0, total_files, "处理中")

    for idx, audio_file in enumerate(audio_files, 1):
        # 只更新进度，不更新描述
        progress_mgr.set_progress(idx, total_files, "处理中")

        result = process_file(audio_file, metadata_list, cache_root)
        if result:
            processed_results.append(result)
            logger.info(f"✓ 成功: {audio_file.name}")
        else:
            logger.error(f"✗ 失败: {audio_file.name}")

    if not processed_results:
        logger.error("没有文件被成功处理")
        return

    # 保存 metadata
    save_metadata(metadata_file, metadata_list)
    logger.info(f"Metadata 已保存到: {metadata_file}")

    # 统计结果
    added_count = sum(1 for r in processed_results if r['action'] == 'added')
    updated_count = sum(1 for r in processed_results if r['action'] == 'updated')
    logger.info(f"处理成功: {len(processed_results)}/{len(audio_files)} 个文件")
    logger.info(f"  新增: {added_count} 个")
    logger.info(f"  更新: {updated_count} 个")

    # 显示详细列表
    if added_count > 0:
        logger.info("\n【新增条目列表】")
        for r in processed_results:
            if r['action'] == 'added':
                artists = ', '.join(r['artists'])
                logger.info(f"  • {artists} - {r['title']}")

    if updated_count > 0:
        logger.info("\n【更新条目列表】")
        for r in processed_results:
            if r['action'] == 'updated':
                artists = ', '.join(r['artists'])
                logger.info(f"  • {artists} - {r['title']}")

    # 删除 work 中的文件
    logger.info("删除已处理的work文件...")
    total_del = len(processed_results)
    progress_mgr.set_progress(0, total_del, "清理中")

    for idx, result in enumerate(processed_results, 1):
        work_file = work_dir / result['filename']
        if work_file.exists():
            work_file.unlink()
            logger.info(f"已删除: {result['filename']}")
        progress_mgr.set_progress(idx, total_del, "清理中")

    # 删除空文件夹
    logger.info("清理空文件夹...")

    def remove_empty_dirs(path):
        """递归删除空文件夹"""
        empty_dirs = []
        for dir_path in sorted(path.rglob('*'), key=lambda x: len(x.parts), reverse=True):
            if dir_path.is_dir():
                try:
                    if not any(dir_path.iterdir()):
                        empty_dirs.append(dir_path.relative_to(path))
                        dir_path.rmdir()
                except OSError:
                    pass  # 目录非空或其他错误

        if empty_dirs:
            progress_mgr.set_progress(0, len(empty_dirs), "删空目录")
            for idx, empty_dir in enumerate(empty_dirs, 1):
                logger.info(f"删除空文件夹: {empty_dir}")
                progress_mgr.set_progress(idx, len(empty_dirs), "删空目录")

    remove_empty_dirs(work_dir)

    # Git 操作
    if not args.no_git:
        logger.info("执行 git 操作...")
        os.chdir(repo_root)

        # 添加
        subprocess.run(['git', 'add', 'metadata.jsonl'], check=False)

        # 提交
        if processed_results:
            commit_msg = f"Update metadata ({len(processed_results)} files)"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=False)

        # 推送
        subprocess.run(['git', 'push'], check=False)

        logger.info("Git 操作完成")

    # 关闭进度条
    progress_mgr.close()

    print("\n完成！")
    print("接下来可以:")
    print("1. 运行 repo/release/create_release_local.py 生成成品")
    print("2. 运行 repo/data/sync_cache.py 同步到远端")


if __name__ == "__main__":
    main()
