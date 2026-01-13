#!/usr/bin/env python3
"""
音频文件解析脚本
从音频文件中提取元数据并生成metadata.jsonl格式
"""

import os
import json
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

# 尝试导入 mutagen，如果不存在则提示安装
try:
    from mutagen import File
    from mutagen.id3 import ID3, USLT, TPE1, TIT2, TALB, TDRC, TCON, APIC
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("错误: mutagen 库未安装")
    print("请运行: pip install mutagen")
    exit(1)


def calculate_audio_hash(file_path):
    """
    计算音频帧的SHA256哈希（排除ID3标签）
    使用ffmpeg提取音频流并计算哈希
    """
    try:
        # 使用ffmpeg提取音频流（排除ID3标签）
        cmd = [
            'ffmpeg', '-i', file_path,
            '-vn', '-acodec', 'copy',
            '-f', 'mp3', '-'
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # 计算SHA256
        audio_data = result.stdout
        audio_hash = hashlib.sha256(audio_data).hexdigest()
        return f"sha256:{audio_hash}"

    except (subprocess.CalledProcessError, FileNotFoundError):
        # 如果ffmpeg不可用，回退到计算整个文件的哈希
        # 这种方式不够理想，但可以工作
        print("警告: ffmpeg不可用，使用整个文件哈希（可能包含标签）")
        with open(file_path, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        return f"sha256:{file_hash}"


def calculate_cover_hash(cover_data):
    """计算封面图片的SHA256哈希"""
    return f"sha256:{hashlib.sha256(cover_data).hexdigest()}"


def parse_audio_file(file_path):
    """
    解析单个音频文件，返回元数据字典
    """
    try:
        audio = File(file_path)
        if audio is None:
            return {"error": "无法识别的音频文件", "file": file_path}

        result = {}

        # 计算音频指纹
        result["audio_oid"] = calculate_audio_hash(file_path)

        # 获取基本标签信息
        if hasattr(audio, 'tags') and audio.tags:
            # 艺术家
            def split_artists(artist_str):
                """分割艺术家字符串，支持', '和' & '分隔符"""
                if not artist_str:
                    return []
                # 先按' & '分割，再按', '分割
                parts = artist_str.split(' & ')
                result = []
                for part in parts:
                    subparts = part.split(', ')
                    result.extend([p.strip() for p in subparts if p.strip()])
                return result

            if 'TPE1' in audio.tags:
                artists = audio.tags['TPE1']
                if isinstance(artists, list):
                    artist_list = []
                    for a in artists:
                        artist_list.extend(split_artists(str(a)))
                    result["artists"] = artist_list
                else:
                    result["artists"] = split_artists(str(artists))
            elif 'artist' in audio.tags:
                artists = audio.tags['artist']
                if isinstance(artists, list):
                    artist_list = []
                    for a in artists:
                        artist_list.extend(split_artists(str(a)))
                    result["artists"] = artist_list
                else:
                    result["artists"] = split_artists(str(artists))

            # 标题
            if 'TIT2' in audio.tags:
                title = audio.tags['TIT2']
                if isinstance(title, list):
                    result["title"] = str(title[0])
                else:
                    result["title"] = str(title)
            elif 'title' in audio.tags:
                title = audio.tags['title']
                if isinstance(title, list):
                    result["title"] = str(title[0])
                else:
                    result["title"] = str(title)

            # 专辑
            if 'TALB' in audio.tags:
                album = audio.tags['TALB']
                if isinstance(album, list):
                    result["album"] = str(album[0])
                else:
                    result["album"] = str(album)
            elif 'album' in audio.tags:
                album = audio.tags['album']
                if isinstance(album, list):
                    result["album"] = str(album[0])
                else:
                    result["album"] = str(album)

            # 日期
            if 'TDRC' in audio.tags:
                date = audio.tags['TDRC']
                if isinstance(date, list):
                    result["date"] = str(date[0])
                else:
                    result["date"] = str(date)
            elif 'date' in audio.tags:
                date = audio.tags['date']
                if isinstance(date, list):
                    result["date"] = str(date[0])
                else:
                    result["date"] = str(date)

            # 流派（如果存在且不为空）
            if 'TCON' in audio.tags:
                genre = audio.tags['TCON']
                if isinstance(genre, list):
                    genre_list = [str(g) for g in genre if str(g).strip()]
                else:
                    genre_list = [str(genre)] if str(genre).strip() else []
                if genre_list:
                    result["genre"] = genre_list
            elif 'genre' in audio.tags:
                genre = audio.tags['genre']
                if isinstance(genre, list):
                    genre_list = [str(g) for g in genre if str(g).strip()]
                else:
                    genre_list = [str(genre)] if str(genre).strip() else []
                if genre_list:
                    result["genre"] = genre_list

            # 歌词（USLT）
            if isinstance(audio.tags, ID3):
                for frame in audio.tags.values():
                    if frame.FrameID == 'USLT':
                        text = frame.text.decode('utf-8') if hasattr(frame.text, 'decode') else str(frame.text)
                        result["uslt"] = text
                        break

            # 封面图片
            if isinstance(audio.tags, ID3):
                for frame in audio.tags.values():
                    if frame.FrameID == 'APIC':
                        cover_data = frame.data
                        result["cover_oid"] = calculate_cover_hash(cover_data)
                        break

        # 如果没有找到标题或艺术家，从文件名推断
        if 'title' not in result or 'artists' not in result:
            filename = Path(file_path).stem
            # 尝试从文件名解析 "艺术家 - 标题" 格式
            if ' - ' in filename:
                parts = filename.split(' - ', 1)
                if len(parts) == 2:
                    if 'artists' not in result:
                        result["artists"] = [parts[0].strip()]
                    if 'title' not in result:
                        result["title"] = parts[1].strip()

        # 添加时间戳
        now = datetime.utcnow().isoformat() + 'Z'
        result["created_at"] = now
        result["updated_at"] = now

        return result

    except Exception as e:
        return {"error": str(e), "file": file_path}


def main():
    """主函数"""
    work_dir = Path(__file__).parent.parent / "work"

    if not work_dir.exists():
        print(f"错误: work目录不存在: {work_dir}")
        return

    # 支持的音频扩展名
    audio_extensions = {'.mp3', '.flac', '.m4a', '.wav', '.ogg'}

    # 查找所有音频文件
    audio_files = []
    for file in work_dir.iterdir():
        if file.suffix.lower() in audio_extensions:
            audio_files.append(file)

    if not audio_files:
        print(f"在 {work_dir} 中未找到音频文件")
        return

    print(f"找到 {len(audio_files)} 个音频文件")
    print("=" * 60)

    results = []
    for i, file_path in enumerate(audio_files, 1):
        print(f"[{i}/{len(audio_files)}] 解析: {file_path.name}")

        result = parse_audio_file(str(file_path))

        # 检查错误
        if "error" in result:
            print(f"  错误: {result['error']}")
        else:
            # 显示关键信息
            artists = result.get('artists', ['未知'])
            title = result.get('title', '未知')
            album = result.get('album', '未知')
            print(f"  艺术家: {', '.join(artists)}")
            print(f"  标题: {title}")
            print(f"  专辑: {album}")
            if 'date' in result:
                print(f"  日期: {result['date']}")
            if 'genre' in result:
                print(f"  流派: {', '.join(result['genre'])}")
            if 'uslt' in result:
                preview = result['uslt'][:80] + "..." if len(result['uslt']) > 80 else result['uslt']
                print(f"  歌词: {preview}")
            if 'cover_oid' in result:
                print(f"  封面: {result['cover_oid']}")
            print(f"  音频指纹: {result['audio_oid']}")

        results.append(result)
        print()

    # 保存结果到 metadata.jsonl
    output_file = Path(__file__).parent / "metadata.jsonl"
    with open(output_file, 'w', encoding='utf-8') as f:
        for result in results:
            if "error" not in result:
                f.write(json.dumps(result, ensure_ascii=False) + '\n')

    print(f"解析完成，结果已保存到: {output_file}")
    print(f"成功解析: {sum(1 for r in results if 'error' not in r)}/{len(results)} 个文件")


if __name__ == "__main__":
    main()
