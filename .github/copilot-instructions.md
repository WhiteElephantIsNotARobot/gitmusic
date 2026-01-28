# GitMusic 项目开发指南

## 项目概述

这是一个音乐管理仓库，用于管理音乐文件的元数据和生成成品。项目采用**内容寻址存储**（SHA256哈希）来管理音频和封面文件，Git仓库仅保存文本文件（`metadata.jsonl` 和脚本）。

### 核心架构

```
~/my-music/
├── repo/                          # Git工作副本（clone自服务器裸仓库）
│   ├── metadata.jsonl             # 核心数据库（JSONL格式）
│   ├── work/                      # 工作目录脚本
│   │   ├── checkout.py            # 检出脚本（按oid或标题）
│   │   └── publish_meta.py        # 发布脚本（扫描work/并更新metadata）
│   ├── data/                      # 数据管理脚本
│   │   ├── sync_cache.py          # 增量同步本地cache与远端data
│   │   ├── verify_hashes.py       # 校验cache中文件哈希
│   │   ├── compress_images.py     # 压缩封面图片并更新oid
│   │   └── cleanup_orphaned.py    # 清理孤立对象
│   ├── release/                   # 成品生成脚本
│   │   └── create_release.py      # 统一版成品生成（local/server模式）
│   └── tools/                     # 分析工具
│       ├── analyze_metadata.py    # 元数据分析与搜索
│       ├── analyze_duplicates.py  # 重复项分析
│       └── download_ytdlp.py      # yt-dlp下载脚本（从网易云音乐等平台下载）
├── work/                          # 临时编辑区（艺术家 - 标题.mp3）
├── cache/                         # 本地缓存（objects & covers）
│   ├── objects/sha256/<aa>/<oid>.mp3
│   └── covers/sha256/<aa>/<oid>.jpg
└── release/                       # 本地成品库（艺术家 - 标题.mp3）
```

### 服务器目录结构

```
/srv/music/
├── music.git                      # 裸仓库（仅metadata.jsonl和脚本）
├── repo/                          # 服务器工作副本（用于hook调用）
└── data/
    ├── objects/sha256/<aa>/<oid>.mp3
    ├── covers/sha256/<aa>/<oid>.jpg
    └── releases/                  # 服务器成品目录
```

## 关键概念

### 1. 内容寻址存储

- **audio_oid**: `sha256:<hex>`，音频文件的哈希值（移除ID3标签后的纯净音频流）
- **cover_oid**: `sha256:<hex>`，封面图片的哈希值
- 文件按哈希值存储在 `cache/objects/sha256/<aa>/` 和 `cache/covers/sha256/<aa>/` 目录下

### 2. metadata.jsonl 格式

每行是一个JSON对象，包含：
```json
{
  "audio_oid": "sha256:...",
  "cover_oid": "sha256:...",
  "title": "歌曲标题",
  "artists": ["艺术家1", "艺术家2"],
  "album": "专辑名（可选）",
  "date": "2024-01-01（可选）",
  "uslt": "歌词文本（可选）",
  "created_at": "2024-01-01T00:00:00Z"
}
```

**字段说明**：
- `audio_oid`: 音频文件的SHA256哈希（必填）
- `cover_oid`: 封面图片的SHA256哈希（可选）
- `title`: 歌曲标题（必填）
- `artists`: 艺术家列表（必填）
- `album`: 专辑名（可选）
- `date`: 发布日期（可选，格式：YYYY-MM-DD）
- `uslt`: 歌词文本（可选，带时间戳的LRC格式）
- `created_at`: 创建时间（UTC格式，如：2026-01-17T00:30:03Z）

**注意**：`updated_at` 和 `duration` 字段已弃用，不再使用。

### 3. 文件命名约定

- **工作目录**: `艺术家 - 标题.mp3`（无oid标记）
- **成品文件**: `艺术家 - 标题.mp3`（无oid标记）
- **缓存文件**: `<oid>.mp3` 或 `<oid>.jpg`（按哈希值命名）

## 核心工作流

### 导入新曲目

1. 将MP3文件放入 `work/` 目录（文件名格式：`艺术家 - 标题.mp3`）
2. 运行：`python repo/work/publish_meta.py`
3. 脚本会：
   - 计算音频流哈希（移除ID3标签）
   - 提取封面并计算哈希
   - 解析元数据（标题、艺术家、专辑、日期、歌词）
   - 在 `metadata.jsonl` 中查找匹配（按audio_oid）
   - 更新或新增条目
   - 保存音频和封面到 `cache/`
   - 删除 `work/` 中的文件

### 检出供编辑

1. 运行：`python repo/work/checkout.py <audio_oid 或 标题>`
2. 脚本会：
   - 从 `cache/` 复制音频文件
   - 嵌入标签和封面
   - 保存到 `work/` 目录（文件名：`艺术家 - 标题.mp3`）

### 生成成品

**本地模式**（客户端）：
```bash
python repo/release/create_release.py --mode local
```
- 从 `cache/objects` 读取音频
- 嵌入元数据和封面
- 保存到 `release/` 目录

**服务器模式**（服务器端）：
```bash
python repo/release/create_release.py --mode server --data-root /srv/music/data --output /srv/music/data/releases
```
- 从 `/srv/music/data/objects` 读取音频
- 嵌入元数据和封面
- 保存到 `/srv/music/data/releases/` 目录

### 缓存管理

**同步缓存**：
```bash
python repo/data/sync_cache.py -u <user> -H <host>
```
- 增量同步：远端有本地无 → 下载；本地有远端无 → 上传

**校验哈希**：
```bash
python repo/data/verify_hashes.py
```
- 验证 `cache/` 中文件哈希是否符合文件名

**压缩图片**：
```bash
python repo/data/compress_images.py
```
- 将封面转为JPG并重算哈希，更新 `metadata.jsonl`

**清理孤立对象**：
```bash
python repo/data/cleanup_orphaned.py --confirm
```
- 删除不在 `metadata.jsonl` 中引用的对象

### 分析工具

**搜索元数据**：
```bash
python repo/tools/analyze_metadata.py "关键词"
python repo/tools/analyze_metadata.py --search "关键词" --field title
python repo/tools/analyze_metadata.py --missing cover uslt
```

**分析重复项**：
```bash
python repo/tools/analyze_duplicates.py
```

### 下载工具

**从网易云音乐下载**：
```bash
python repo/tools/download_ytdlp.py "https://music.163.com/song?id=2124731026"
```
- 自动下载音频（最高质量320kbps）
- 下载封面图片
- 提取带时间戳的歌词
- 保存到 `metadata.jsonl` 和 `cache/`
- 支持重试机制（默认5次）

**指定重试次数**：
```bash
python repo/tools/download_ytdlp.py "https://music.163.com/song?id=2124731026" --retries 3
```

## 脚本使用规范

### 路径约定

所有脚本使用相对路径访问父目录：
- `repo_root = Path(__file__).parent.parent`
- `work_dir = repo_root.parent / "work"`
- `cache_root = repo_root.parent / "cache"`
- `release_dir = repo_root.parent / "release"`

### 进度条管理

使用 `BottomProgressBar` 类管理进度条，支持日志平滑滚动：
```python
progress_mgr = BottomProgressBar()
progress_mgr.set_progress(current, total, desc)
progress_mgr.write_log(message)
progress_mgr.close()
```

### 原子写入

写入文件时使用临时文件 + 原子替换：
```python
temp_file = target_path.with_suffix('.tmp')
with open(temp_file, 'w') as f:
    f.write(data)
shutil.move(str(temp_file), str(target_path))
```

### 错误处理

- 使用 `logger.error()` 记录错误
- 使用 `logger.warning()` 记录警告
- 使用 `logger.info()` 记录信息

## 服务器部署

### 服务器登录信息

**服务器地址**: `white_elephant@debian-server`

### 服务器当前状态

**已部署完成**：
- ✅ 裸仓库：`/srv/music/music.git`（已初始化）
- ✅ 工作仓库：`/srv/music/repo`（已克隆，包含最新代码）
- ✅ post-receive hook：已配置，推送时将请求写入队列
- ✅ 队列处理器：`music-queue-handler.service`（已启用并启动）
- ✅ 数据目录：`/srv/music/data/`（包含 objects、covers、releases）
- ✅ 元数据：`/srv/music/repo/metadata.jsonl`（508 条记录）
- ✅ 音频对象：`/srv/music/data/objects/sha256/`（216 个子目录）
- ✅ 封面对象：`/srv/music/data/covers/sha256/`（229 个子目录）
- ✅ 成品文件：`/srv/music/data/releases/`（已生成成品）

**服务器目录结构**：
```
/srv/music/
├── music.git                      # 裸仓库（仅metadata.jsonl和脚本）
├── repo/                          # 服务器工作副本（用于hook调用）
│   ├── metadata.jsonl             # 508条记录
│   ├── data/                      # 数据管理脚本
│   ├── release/                   # 成品生成脚本
│   ├── server/                    # 服务器相关脚本
│   │   ├── post-receive           # hook脚本
│   │   ├── queue_handler.py       # 队列处理器
│   │   └── music-queue-handler.service  # 队列处理器服务
│   ├── tools/                     # 分析工具
│   └── work/                      # 工作目录脚本
└── data/
    ├── objects/sha256/<aa>/<oid>.mp3  # 216个子目录
    ├── covers/sha256/<aa>/<oid>.jpg   # 229个子目录
    └── releases/                      # 服务器成品目录（已生成）
```

**post-receive hook 内容**：
```bash
#!/bin/bash
# 强制使用 Linux 换行符
LOG_FILE=/srv/music/music.git/hooks/post-receive.log
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[$(date)] --- Post-receive hook triggered ---"

export HOME=/home/white_elephant
export PATH=/usr/local/bin:/usr/bin:/bin

unset GIT_DIR
unset GIT_QUARANTINE_PATH

cd /srv/music/repo || exit 1

# 拉取最新代码
echo "--- Pulling latest changes ---"
git fetch origin master
git reset --hard origin/master

# 将请求写入队列
QUEUE_FILE=/srv/music/repo/server/queue.jsonl
echo "{\"timestamp\": \"$(date -Iseconds)\", \"action\": \"generate_releases\"}" >> "$QUEUE_FILE"

echo "--- Request added to queue ---"
echo "--- Queue handler will process the request ---"
echo "--- Done ---"
```

**队列处理器服务**：
```bash
# 查看队列处理器状态
sudo systemctl status music-queue-handler.service

# 查看队列处理器日志
sudo journalctl -u music-queue-handler.service -f

# 查看队列处理器自定义日志
cat /srv/music/repo/server/queue_handler.log

# 重启队列处理器
sudo systemctl restart music-queue-handler.service

# 停止队列处理器
sudo systemctl stop music-queue-handler.service
```

**队列处理器工作流程**：
1. post-receive hook 将请求写入队列文件 (`/srv/music/repo/server/queue.jsonl`)
2. 队列处理器 (`music-queue-handler.service`) 定期检查队列
3. 处理器处理请求，调用生成脚本
4. 生成脚本处理元数据变化并更新成品目录
5. 处理器只在有请求时打印日志

### 裸仓库设置

```bash
cd /srv/music
git init --bare music.git
```

### 权限设置

确保运行脚本的用户对以下目录有读写权限：
- `/srv/music/data/objects`
- `/srv/music/data/covers`
- `/srv/music/data/releases`
- `/srv/music/repo`

## 常见问题

### 1. 音频哈希计算

音频哈希基于**纯净音频流**（移除ID3标签）：
```bash
ffmpeg -i file.mp3 -map 0:a:0 -c copy -f mp3 -map_metadata -1 -id3v2_version 0 -write_id3v1 0 pipe:1 | sha256sum
```

### 2. 封面提取

优先使用 `ffmpeg` 提取封面，备选 `mutagen`：
```python
# ffmpeg方案
cmd = ['ffmpeg', '-i', str(audio_path), '-map', '0:v', '-map', '-0:V', '-c', 'copy', '-f', 'image2', 'pipe:1']

# mutagen方案
audio = File(audio_path)
if hasattr(audio, 'tags') and audio.tags:
    if isinstance(audio.tags, ID3):
        for frame in audio.tags.values():
            if frame.FrameID == 'APIC':
                cover_data = frame.data
```

### 3. 文件名冲突

脚本会自动清理非法字符：
```python
for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
    filename = filename.replace(char, '_')
```

### 4. Git操作

`publish_meta.py` 不再自动执行 `git commit/push`，需要手动执行：
```bash
cd repo
git add metadata.jsonl
git commit -m "update: <Artist - Title>"
git push
```

**使用 GitKraken MCP 工具**：
```bash
# 检查状态
git status

# 添加到暂存区
git add .

# 提交
git commit -m "统一元数据字段顺序，移除 updated_at 和 duration 字段"

# 推送
git push
```

## 开发注意事项

### 1. 避免自动填充专辑

`checkout.py` 不会自动用标题填充专辑字段。如果 `metadata.jsonl` 中没有 `album` 字段，音频文件将不包含专辑标签。

### 2. 并行处理

使用 `ThreadPoolExecutor` 进行并行处理，但注意：
- `sync_cache.py` 默认单线程（`--workers 1`）
- `publish_meta.py` 使用多线程（默认 `min(os.cpu_count() or 4, 8)`）
- `create_release.py` 服务器模式使用多线程（默认4）

### 3. 增量更新

`create_release.py` 服务器模式支持增量更新：
- 通过 `TXXX:METADATA_HASH` 标签检测元数据变更
- 自动清理过时文件
- 只生成变更的条目

### 4. 缓存策略

- 音频文件：永久保存（除非被清理）
- 封面文件：可压缩（`compress_images.py`）
- 成品文件：本地模式全量生成，服务器模式增量更新

### 5. 元数据字段顺序

所有脚本使用统一的字段顺序：
1. `audio_oid`
2. `cover_oid`
3. `title`
4. `artists`
5. `album`
6. `date`
7. `uslt`
8. `created_at`

**注意**：`updated_at` 和 `duration` 字段已弃用，不再使用。

### 6. yt-dlp 下载

`download_ytdlp.py` 脚本使用 yt-dlp 下载音乐：
- 显示原生下载进度条
- 支持重试机制（默认5次）
- 自动提取带时间戳的歌词
- 保存到 `metadata.jsonl` 和 `cache/`

### 7. 队列处理器

`queue_handler.py` 脚本监听队列并触发成品生成：
- 定期检查队列文件 (`/srv/music/repo/server/queue.jsonl`)
- 处理队列中的请求，调用生成脚本
- 只在有请求时打印日志
- 作为 systemd 服务运行 (`music-queue-handler.service`)

## 测试建议

### 1. 测试导入流程

```bash
# 准备测试文件
cp test.mp3 work/艺术家\ -\ 标题.mp3

# 运行导入
python repo/work/publish_meta.py

# 验证结果
python repo/tools/analyze_metadata.py --search "标题"
```

### 2. 测试检出流程

```bash
# 获取audio_oid
python repo/tools/analyze_metadata.py --search "标题" --read audio_oid

# 检出
python repo/work/checkout.py sha256:...

# 验证文件
ls work/
```

### 3. 测试成品生成

```bash
# 本地模式
python repo/release/create_release.py --mode local

# 验证
ls release/
```

### 4. 测试缓存同步

```bash
# 先校验哈希
python repo/data/verify_hashes.py

# 同步
python repo/data/sync_cache.py -u <user> -H <host>

# 清理孤立对象（干运行）
python repo/data/cleanup_orphaned.py -u <user> -H <host>

# 确认后执行清理
python repo/data/cleanup_orphaned.py -u <user> -H <host> --confirm
```

### 5. 测试下载功能

```bash
# 从网易云音乐下载
python repo/tools/download_ytdlp.py "https://music.163.com/song?id=2124731026"

# 指定重试次数
python repo/tools/download_ytdlp.py "https://music.163.com/song?id=2124731026" --retries 3

# 验证下载结果
python repo/tools/analyze_metadata.py --search "5:20AM"
```

## 依赖安装

```bash
pip install mutagen tqdm
```

确保系统安装 `ffmpeg` 和 `yt-dlp`：
```bash
# Debian/Ubuntu
sudo apt-get install ffmpeg
pip install yt-dlp

# macOS
brew install ffmpeg
pip install yt-dlp
```

## 版本控制

- Git仓库仅保存 `metadata.jsonl` 和脚本
- 二进制文件（音频、封面）通过内容寻址存储在 `cache/` 目录
- 使用 `sync_cache.py` 同步本地和远端的二进制文件

## 性能优化

### 1. 并行处理

- `publish_meta.py`: 多线程处理文件
- `create_release.py`: 服务器模式多线程生成
- `sync_cache.py`: 可配置线程数（默认1）

### 2. 增量更新

- `create_release.py` 服务器模式只生成变更的条目
- 通过 `TXXX:METADATA_HASH` 标签检测变更

### 3. 缓存策略

- 音频文件永久保存，避免重复下载/上传
- 封面文件可压缩以节省空间

### 4. 下载优化

- `download_ytdlp.py` 使用 yt-dlp 下载，支持断点续传
- 支持重试机制（默认5次）
- 自动选择最高质量音频格式（exhigh: 320kbps）

## 故障排查

### 1. 哈希不匹配

运行 `verify_hashes.py` 检查：
```bash
python repo/data/verify_hashes.py
```

### 2. 封面缺失

运行 `sync_cache.py` 下载缺失的封面：
```bash
python repo/data/sync_cache.py -u <user> -H <host> --direction download
```

### 3. 重复条目

运行 `analyze_duplicates.py` 检查：
```bash
python repo/tools/analyze_duplicates.py
```

### 4. 孤立对象

运行 `cleanup_orphaned.py` 清理：
```bash
python repo/data/cleanup_orphaned.py --confirm
```

### 5. 下载失败

下载失败时会自动重试（默认5次），可通过 `--retries` 参数调整：
```bash
python repo/tools/download_ytdlp.py "https://music.163.com/song?id=2124731026" --retries 10
```

### 6. 队列处理器问题

如果队列处理器未正常工作：
```bash
# 检查服务状态
sudo systemctl status music-queue-handler.service

# 查看队列处理器日志
sudo journalctl -u music-queue-handler.service -f

# 查看队列处理器自定义日志
cat /srv/music/repo/server/queue_handler.log

# 重启队列处理器
sudo systemctl restart music-queue-handler.service
```

## 最佳实践

1. **定期同步**: 使用 `sync_cache.py` 保持本地和远端数据一致
2. **定期清理**: 使用 `cleanup_orphaned.py` 清理孤立对象
3. **定期校验**: 使用 `verify_hashes.py` 验证文件完整性
4. **备份metadata**: Git提交前确保 `metadata.jsonl` 格式正确
5. **测试导入**: 新曲目先测试导入流程，确认无误后再批量处理
6. **使用yt-dlp下载**: 从网易云音乐等平台下载音乐时，使用 `download_ytdlp.py` 脚本
