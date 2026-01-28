# GitMusic 命令规范文档

**版本**: 1.0  
**日期**: 2026-01-25  
**状态**: 生产就绪

本文档详细说明GitMusic CLI各命令的用法、参数、工作流和示例。

## 目录

1. [执行模型](#执行模型)
2. [命令总览](#命令总览)
3. [命令详细说明](#命令详细说明)
   - [publish](#publish)
   - [checkout](#checkout)
   - [release](#release)
   - [sync](#sync)
   - [verify](#verify)
   - [cleanup](#cleanup)
   - [download](#download)
   - [analyze](#analyze)
   - [compress_images](#compress_images)
4. [错误处理策略](#错误处理策略)
5. [配置参考](#配置参考)
6. [故障排查](#故障排查)

---

## 执行模型

### 事件驱动架构

所有命令基于事件驱动架构实现：
- **Interactive模式**: 渲染人类友好的终端输出（进度条、表格、彩色文本）
- **Log-only模式**: 输出JSONL到stdout，可重定向或供给systemd等服务

事件自动持久化到: `logs/<command>-YYYYMMDD-HHMMSS.jsonl`

### 路径管理

所有路径通过统一Context对象注入，支持：
- 工作目录 (`work_dir`)
- 缓存根目录 (`cache_root`)
- 元数据文件 (`metadata.json`)
- 发布目录 (`release_dir`)
- 日志目录 (`logs_dir`)

---

## 命令总览

| 命令 | 用途 | 主要参数 | 错误策略 | 锁需求 |
|------|------|---------|---------|--------|
| `publish` | 发布work目录到库 | `--changed-only`, `--preview` | stop | 写锁 |
| `checkout` | 从cache检出到work | `<query>`, `--search-field`, `--line`, `--limit` | stop | 读锁 |
| `release` | 生成成品库 | `--mode`, `--force`, `--workers`, `--line` | continue | 读锁 |
| `sync` | 双向同步 | `--direction`, `--dry-run`, `--workers` | continue | 无 |
| `verify` | 完整性校验 | `--mode`, `--delete` | notify | 无 |
| `cleanup` | 清理孤立对象 | `--mode`, `--confirm` | notify | 写锁 |
| `download` | 下载音频 | `<URL>`, `--batch-file`, `--fetch`, `--no-preview` | stop | 写锁 |
| `analyze` | 元数据分析 | `<query>`, `--search-field`, `--missing`, `--line` | stop | 读锁 |
| `compress_images` | 压缩封面 | `--size` | continue | 写锁 |

---

## 命令详细说明

### publish

**用途**: 发布work目录中的音频文件到GitMusic库，完成对象存储、封面压缩、哈希校验和元数据更新。

**语法**:
```bash
gitmusic publish [OPTIONS]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--changed-only` | 无 | flag | false | 仅处理有变动的文件（基于METADATA_HASH标签比对） |
| `--preview` | 无 | flag | false | 仅显示预览表格，不执行实际发布 |
| `--on-error` | 无 | string | "stop" | 错误处理策略: stop/continue/notify |

**工作流步骤**:

1. **扫描work目录**
   - 枚举work_dir中的所有MP3文件
   - 读取音频文件中的ID3标签和元数据
   - 事件: `phase_start` → `item_event(found)`

2. **差异比对**
   - 与metadata.jsonl中的现有条目对比
   - 计算每个文件的音频哈希（基于音频帧，忽略元数据）
   - 标记状态: NEW（全新）/ MOD（修改）/ SAME（无变化）
   - 生成字段级diff预览
   - 事件: `item_event(diffpreview)`

3. **预览确认**
   - Interactive模式: 渲染富文本表格显示所有变更
   - 等待用户输入 `y` 确认执行
   - `--preview`模式: 显示预览后直接退出

4. **提取音频并存储**
   - 提取纯音频流（移除所有标签）
   - 计算SHA256哈希
   - 写入objectstore（本地cache和远程）
   - 事件: `phase_start(hashstore)` → `batch_progress` → `item_event(audio_stored)`

5. **处理封面**
   - 提取嵌入封面（如果存在）
   - 使用ffmpeg压缩并限制尺寸（默认600x600）
   - 计算封面哈希并存储
   - 事件: `item_event(coverstored)`

6. **完整性校验**
   - 本地与远程SHA256哈希比对
   - 失败则终止命令（不可恢复错误）
   - 事件: `item_event(verifyok)` 或 `error`

7. **提交元数据**
   - 获取metadata写锁
   - 更新metadata.jsonl（原子写入）
   - 推送到远程Git仓库
   - 事件: `phase_start(commit)` → `result(summary)`

**示例**:

```bash
# 预览发布内容（推荐先执行）
gitmusic publish --preview

# 发布所有文件
gitmusic publish

# 仅发布变更文件
gitmusic publish --changed-only

# 自定义错误处理
gitmusic publish --on-error=continue
```

**输出示例**:

```
2026-01-25 22:15:30 | publish | preview | 50 | 0:00:02
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 发布预览 (NEW: 15, MOD: 8, SAME: 27)                ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 艺术家          │ 标题              │ 状态 │ 音频OID         │
│─────────────────│───────────────────│──────│─────────────────│
│ Adele           │ Hello             │ NEW  │ sha256:abc123...│
│ Ed Sheeran      │ Shape of You      │ MOD  │ sha256:def456...│
└──────────────────────────────────────────────────────┘
确认执行发布? (y/n): y

2026-01-25 22:15:35 | publish | hashstore | 23 | 0:00:15
  Processing: ████████████████████ 23/23 100% 1.5/s 预估 0:00:00
✓ audio_stored: Adele - Hello.mp3 (sha256:abc123...)
✓ cover_stored: Adele - Hello.mp3 (sha256:789abc...)
...

2026-01-25 22:15:50 | publish | commit | - | 0:00:05
✓ 已更新 metadata.jsonl 并推送到远程

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
发布完成
  总计: 23 文件
  成功: 23
  失败: 0
  日志: logs/publish-20260125-221530.jsonl
```

**注意事项**:
- 需要配置SSH免密登录（建议使用密钥认证）
- 确保有足够的磁盘空间存储对象
- 发布前建议先执行`--preview`确认变更

---

### checkout

**用途**: 从cache检出音频文件到work目录，嵌入最新元数据和封面。

**语法**:
```bash
gitmusic checkout [OPTIONS] [QUERY]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `QUERY` | 无 | string | 无 | 搜索关键词（在所有字段中搜索） |
| `--search-field` | 无 | string | 无 | 仅在指定字段搜索（如title, artists, album） |
| `--line` | `-l` | string | 无 | 按行号过滤（如"1,3,5"或"10-20"） |
| `--missing` | 无 | string | 无 | 查找缺失指定字段的条目（逗号分隔） |
| `--limit` | 无 | int | 无 | 限制检出数量 |
| `--force` | `-f` | flag | false | 强制覆盖work目录已存在文件 |
| `--on-error` | 无 | string | "stop" | 错误处理策略 |

**工作流步骤**:

1. **查询元数据**
   - 根据query/filters从metadata.jsonl筛选条目
   - 事件: `phase_start` → `item_event(candidate)`

2. **冲突检查**
   - 检查work目录是否存在同名文件
   - 发现冲突且无`-f`标志时:
     - 自动执行`gitmusic publish --preview`
     - 显示diff和冲突文件列表
     - 退出并提示先发布或强制覆盖

3. **下载对象**
   - 从objectstore下载音频和封面到临时位置
   - 事件: `batch_progress` → `item_event(fetched)`

4. **嵌入元数据**
   - 将最新元数据和封面嵌入音频文件
   - 写入`METADATA_HASH`标签（用于后续变更检测）
   - 原子写入work目录
   - 事件: `item_event(wrotefile)`

**示例**:

```bash
# 检出所有歌曲
gitmusic checkout

# 按艺术家搜索并检出
gitmusic checkout "Adele" --limit 10

# 按标题搜索
gitmusic checkout --search-field title "Hello"

# 检出缺失封面的歌曲
gitmusic checkout --missing cover --limit 20

# 按行号检出
gitmusic checkout --line 100-150

# 强制覆盖已存在文件
gitmusic checkout "Ed Sheeran" --force
```

**输出示例**:

```
2026-01-25 22:20:10 | checkout | query | 45 | 0:00:01
  候选条目: 45 条
✓ candidate: Adele - Hello (sha256:abc123...)
✓ candidate: Adele - Rolling in the Deep (sha256:def456...)
...

2026-01-25 22:20:15 | checkout | fetch | 45 | 0:00:08
  下载: ████████████████████ 45/45 100% 5.6/s 预估 0:00:00
✓ fetched: Adele - Hello.mp3 (12.3 MB)
...

2026-01-25 22:20:25 | checkout | write | 45 | 0:00:05
✓ wrotefile: work/Adele - Hello.mp3
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
检出完成
  总计: 45 文件
  成功: 45
  失败: 0
  输出目录: work/
```

**错误处理**:

```bash
# 冲突检测示例
$ gitmusic checkout "Adele"
⚠ work目录存在冲突文件（3个）:
  - work/Adele - Hello.mp3
  - work/Adele - Rolling in the Deep.mp3

建议操作:
  1. 发布更改: gitmusic publish
  2. 强制覆盖: gitmusic checkout "Adele" --force

预览差异:
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ work目录 vs metadata                            ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 文件                            │ 状态 │ 差异  │
│─────────────────────────────────│──────│───────│
│ Adele - Hello.mp3               │ MOD  │ +12KB │
│ Adele - Rolling in the Deep.mp3 │ MOD  │ +8KB  │
└─────────────────────────────────────────────────┘
```

---

### release

**用途**: 生成成品音乐库到release目录，支持local和server模式。

**语法**:
```bash
gitmusic release [OPTIONS]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--mode` | 无 | string | "local" | 运行模式: local或server |
| `--force` | `-f` | flag | false | 清空目标目录后重新生成 |
| `--workers` | 无 | int | 1 | 并行处理线程数 |
| `--line` | `-l` | string | 无 | 按行号生成（如"100-200"） |
| `--on-error` | 无 | string | "continue" | 错误处理策略（出错继续） |

**工作流步骤**:

1. **准备清单**
   - 根据`--line`或全部metadata生成处理清单
   - 增量模式: 仅生成METADATA_HASH变化的文件
   - 事件: `phase_start` → `item_event(listed)`

2. **同步缓存**
   - 确保所有对象在本地缓存可用
   - 缺失则从远程下载
   - 事件: `batch_progress` → `item_event(synced)`

3. **生成文件**
   - 嵌入完整元数据和封面
   - 必要时转码（保证MP3格式一致）
   - 原子写入release目录
   - 事件: `item_event(releasewritten)`

4. **完整性校验**
   - 验证生成的文件可播放且哈希匹配
   - 事件: `item_event(verifyok)` 或 `error`

5. **Server模式额外步骤**
   - 执行`git pull`获取最新metadata
   - 生成后可选`git push`更新索引
   - 事件: `log`

**示例**:

```bash
# 在本地生成完整库
gitmusic release

# 强制重新生成
gitmusic release --force

# Server模式（用于服务器）
gitmusic release --mode server --workers 4

# 仅生成指定范围
gitmusic release --line 1000-2000

# 并行生成提高效率
gitmusic release --workers 4
```

**输出示例**:

```
2026-01-25 22:30:10 | release | prepare | 1247 | 0:00:03
  生成清单: 1247 条目（增量: 45 新增, 12 更新）

2026-01-25 22:30:15 | release | sync | 57 | 0:00:12
  同步缓存: ████████████ 57/57 100% 4.8/s 预估 0:00:00
✓ synced: sha256:abc123... (Adele - Hello)
...

2026-01-25 22:30:30 | release | generate | 1247 | 0:05:23
  生成: ████████████████████ 1247/1247 100% 3.9/s 预估 0:00:00
✓ releasewritten: release/Adele/Hello.mp3
✓ releasewritten: release/Ed Sheeran/Shape of You.mp3
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
生成完成 (mode: local)
  总计: 1247 文件
  成功: 1247
  失败: 0
  耗时: 5分23秒
  输出目录: release/
  平均速率: 3.9 文件/秒
```

**Server模式**:

```bash
# 在服务器上定时执行
gitmusic release --mode server --force

# 输出包含Git操作
2026-01-25 22:35:10 | release | git_pull | - | 0:00:02
✓ 已更新到最新metadata

2026-01-25 22:35:15 | release | generate | 1500 | 0:06:15
...

2026-01-25 22:41:30 | release | git_push | - | 0:00:08
✓ 已推送release索引
```

---

### sync

**用途**: 双向同步本地cache与远程data目录，支持upload/download/both方向。

**语法**:
```bash
gitmusic sync [OPTIONS]
```

**别名**:
- `gitmusic push` = `gitmusic sync --direction=upload`
- `gitmusic pull` = `gitmusic sync --direction=download`

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--direction` | 无 | string | "both" | 同步方向: upload/download/both |
| `--dry-run` | 无 | flag | false | 仅显示差异，不执行同步 |
| `--workers` | 无 | int | 4 | 并行传输线程数 |
| `--timeout` | 无 | int | 60 | 单文件超时时间（秒） |
| `--on-error` | 无 | string | "continue" | 错误处理策略 |

**工作流步骤**:

1. **扫描索引**
   - 列出本地cache中所有对象ID
   - 列出远程data目录所有对象ID（SSH执行）
   - 事件: `phase_start`

2. **计算同步计划**
   - upload: 本地有但远程缺失的对象
   - download: 远程有但本地缺失的对象
   - both: 双向同步
   - 事件: `item_event(planitem)` → `log`

3. **执行传输**
   - 使用SCP/SFTP传输文件
   - 指数退避重试策略（可配置retries）
   - 传输后执行远程SHA256校验（完整性保证）
   - 事件: `batch_progress` → `item_event(uploaded/downloaded)`

4. **结果汇总**
   - 统计上传/下载数量和字节数
   - 列出失败项（如有）
   - 事件: `result(summary)`

**示例**:

```bash
# 双向同步（默认）
gitmusic sync

# 仅上传本地新增
gitmusic sync --direction upload

# 仅下载远程更新
gitmusic sync --direction download

# 预览同步内容
gitmusic sync --dry-run

# 使用别名
gitmusic push  # 上传
gitmusic pull  # 下载

# 调整并行度和超时
gitmusic sync --workers 8 --timeout 120
```

**输出示例**:

```
2026-01-25 22:40:10 | sync | scan | - | 0:00:05
  本地对象: 3412
  远程对象: 3405

2026-01-25 22:40:15 | sync | plan | 14 | 0:00:01
✓ planitem: [UPLOAD] sha256:abc123... (Adele - Hello.mp3, 4.5 MB)
✓ planitem: [DOWNLOAD] sha256:def456... (Ed Sheeran - Shape.mp3, 3.8 MB)
...

2026-01-25 22:40:20 | sync | transfer | 14 | 0:00:45
  上传: ████████████ 7/7 100% 0.2 MB/s
  下载: ████████████ 7/7 100% 0.3 MB/s
✓ uploaded: sha256:abc123... (4.5 MB, 12s)
✓ downloaded: sha256:def456... (3.8 MB, 10s)
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
同步完成 (direction: both)
  上传: 7 文件 (31.5 MB)
  下载: 7 文件 (26.8 MB)
  总计: 14 文件
  耗时: 45秒
```

**Dry-run模式**:

```bash
$ gitmusic sync --dry-run --direction upload
2026-01-25 22:45:10 | sync | plan | 5 | 0:00:02
  [DRY-RUN] 将上传 5 个对象:
    - sha256:abc123... (4.5 MB)
    - sha256:def456... (3.8 MB)
    ...
  总计: 22.3 MB

✓  dry-run 完成，未执行实际传输
```

---

### verify

**用途**: 校验cache或release中文件的SHA256完整性，可选自动清理损坏文件。

**语法**:
```bash
gitmusic verify [OPTIONS]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--mode` | 无 | string | 无 | 校验模式: data（cache）或release |
| `--delete` | 无 | flag | false | 自动删除不匹配的文件（移到回收站） |
| `--on-error` | 无 | string | "notify" | 错误处理策略 |

**回收站机制**:

- 删除文件移动到: `<cache_root>/.trash/` 或 `<release_dir>/.trash/`
- 目录结构保留，便于恢复
- 事件记录完整删除路径

**工作流步骤**:

1. **收集目标**
   - mode=data: 扫描cache_root所有对象
   - mode=release: 扫描release_dir所有文件
   - 事件: `phase_start`

2. **计算比对**
   - 重新计算每个文件的SHA256
   - 与metadata.jsonl中记录的期望值比较
   - 事件: `batch_progress` → `item_event(verifyok/verifyfail)`

3. **可选删除**
   - `--delete`时移动损坏文件到回收站
   - 事件: `item_event(deleted)`

4. **汇总结果**
   - 显示校验通过率
   - 列出所有损坏文件（如有）
   - 事件: `result(summary)`

**示例**:

```bash
# 校验cache
gitmusic verify --mode data

# 校验release
gitmusic verify --mode release

# 自动删除损坏文件
gitmusic verify --mode data --delete

# 仅通知错误（默认）
gitmusic verify --mode release --on-error=notify
```

**输出示例**:

```
2026-01-25 22:50:10 | verify | scan | 3412 | 0:00:08
  扫描: ████████████████████ 3412/3412 100%

2026-01-25 22:50:20 | verify | check | 3412 | 0:04:32
  校验: ████████████████████ 3412/3412 100% 12.5/s
✓ verifyok: sha256:abc123... (Adele - Hello.mp3)
✓ verifyok: sha256:def456... (Ed Sheeran - Shape.mp3)
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
校验完成 (mode: data)
  总计: 3412 文件
  通过: 3412 (100%)
  失败: 0
  耗时: 4分32秒
```

**发现损坏文件**:

```
2026-01-25 22:55:10 | verify | check | 1500 | 0:02:15
⚠ verifyfail: sha256:bad123... (cache/data/ab/bad123..., 
   期望: sha256:abc123..., 实际: sha256:abc124...)
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
校验完成 (mode: data) ⚠ 发现损坏
  总计: 1500 文件
  通过: 1498 (99.9%)
  失败: 2 (0.1%)
  损坏文件:
    - cache/data/ab/bad123... (期望: abc123..., 实际: abc124...)
    - cache/data/cd/bad456... (期望: def456..., 实际: def457...)

建议操作:
  1. 删除损坏文件: gitmusic verify --mode data --delete
  2. 从备份恢复或重新下载
  3. 重新执行 sync 同步正确文件
```

**自动删除**:

```bash
$ gitmusic verify --mode data --delete
⚠ verifyfail: sha256:bad123... (3.2 MB)
  deleted: 已移动到 cache/.trash/data/ab/bad123...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
校验完成 (mode: data)
  总计: 1500 文件
  通过: 1499 (99.9%)
  失败: 1 (已删除)
  回收站: cache/.trash/
```

---

### cleanup

**用途**: 识别并清理孤立对象（未被metadata引用的对象），支持本地/远程双端清理。

**语法**:
```bash
gitmusic cleanup [OPTIONS]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--mode` | 无 | string | 无 | 清理模式: local/server/both |
| `--confirm` | 无 | flag | false | **必须指定才执行删除**（安全机制） |
| `--on-error` | 无 | string | "notify" | 错误处理策略 |

**安全机制**:

- 默认仅报告孤立对象，不执行删除
- 必须显式指定`--confirm`才会删除
- 删除前再次确认数量
- 所有删除操作记录到日志

**工作流步骤**:

1. **构建引用表**
   - 扫描metadata.jsonl，收集所有audio_oid和cover_oid
   - 事件: `phase_start`

2. **扫描对象**
   - 列出本地cache中所有对象文件
   - 远程模式: SSH列出远程data目录
   - 事件: `log`

3. **计算孤立对象**
   - 对比得到未被引用的对象ID
   - 事件: `item_event(orphan)`

4. **报告或删除**
   - 无`--confirm`: 仅显示孤立对象列表
   - 有`--confirm`: 执行删除并显示summary
   - 事件: `result(summary)`

**示例**:

```bash
# 预览本地孤立对象
gitmusic cleanup --mode local

# 预览远程孤立对象
gitmusic cleanup --mode server

# 预览双端孤立对象
gitmusic cleanup --mode both

# 清理本地孤立对象（需确认）
gitmusic cleanup --mode local --confirm

# 清理双端
gitmusic cleanup --mode both --confirm
```

**输出示例**:

```bash
# 仅报告模式
$ gitmusic cleanup --mode local
2026-01-25 23:00:10 | cleanup | scan | - | 0:00:15
  metadata引用: 3412 对象
  本地对象: 3456

2026-01-25 23:00:25 | cleanup | analyze | 44 | 0:00:05
⚠ orphan: sha256:orph123... (cache/data/or/orph123..., 4.2 MB)
⚠ orphan: sha256:orph124... (cache/data/or/orph124..., 3.8 MB)
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
清理报告 (mode: local)
  metadata引用: 3412 对象
  本地总计: 3456 对象
  孤立对象: 44 个 (184 MB)

安全提示: 未指定 --confirm，未执行删除
执行删除命令: gitmusic cleanup --mode local --confirm

# 执行删除
$ gitmusic cleanup --mode local --confirm
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠⚠⚠ 确认删除孤立对象 ⚠⚠⚠

将删除 44 个孤立对象 (184 MB)

确认执行? (yes/no): yes

2026-01-25 23:05:10 | cleanup | delete | 44 | 0:00:12
  删除: ████████████████████ 44/44 100%
✓ deleted: sha256:orph123... (4.2 MB)
✓ deleted: sha256:orph124... (3.8 MB)
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
清理完成 (mode: local)
  已删除: 44 对象 (184 MB)
  剩余: 3412 对象
  释放空间: 184 MB
```

**远程清理**:

```bash
$ gitmusic cleanup --mode server --confirm
⚠⚠⚠ 确认删除远程孤立对象 ⚠⚠⚠

将删除 12 个远程孤立对象 (56 MB)

确认执行? (yes/no): yes

2026-01-25 23:10:10 | cleanup | delete_remote | 12 | 0:00:25
  远程删除: ████████████ 12/12 100%
✓ deleted: sha256:orph456... (remote/data/or/orph456..., 4.7 MB)
...
```

---

### download

**用途**: 使用yt-dlp从YouTube等平台下载音频，自动提取元数据和封面，并更新到GitMusic库。

**语法**:
```bash
gitmusic download [OPTIONS] [URL]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `URL` | 无 | string | 无 | 视频URL地址 |
| `--batch-file` | 无 | string | 无 | 批量下载文件（每行一个URL） |
| `--fetch` | 无 | flag | false | 仅获取元数据预览，不下载 |
| `--no-preview` | 无 | flag | false | 跳过元数据预览 |
| `--limit` | 无 | int | 无 | 批量模式下最大下载数量 |
| `--on-error` | 无 | string | "stop" | 错误处理策略 |

**支持的URL格式**:

通过yt-dlp支持的平台（YouTube、B站、SoundCloud等）

**yt-dlp输出处理**:

- 实时捕获yt-dlp输出并显示进度
- 解析下载进度、速度、剩余时间
- 失败时捕获详细错误信息

**工作流步骤**:

1. **获取元数据** (`--fetch`或预览阶段)
   - 调用yt-dlp获取视频信息
   - 提取标题、艺术家、时长、比特率等
   - 事件: `item_event(preview)`

2. **下载媒体**
   - 调用yt-dlp下载最佳质量音频
   - 格式转换为MP3（确保兼容性）
   - 实时显示进度（速度、剩余时间）
   - 事件: `progress` → `item_event(downloaded)`

3. **提取音频哈希**
   - 计算音频SHA256（用于去重）
   - 事件: `item_event(hash_calculated)`

4. **处理封面**
   - 提取视频缩略图或音频封面
   - 压缩并计算哈希
   - 事件: `item_event(coverstored)`

5. **存储和同步**
   - 上传音频和封面到objectstore
   - 更新metadata.jsonl并commit
   - 事件: `result(summary)`

**示例**:

```bash
# 下载单个URL
gitmusic download "https://youtube.com/watch?v=xxx"

# 批量下载
gitmusic download --batch-file urls.txt

# 限制下载数量
gitmusic download --batch-file urls.txt --limit 10

# 仅预览元数据
gitmusic download --fetch "https://youtube.com/watch?v=xxx"

# 跳过预览直接下载
gitmusic download "https://youtube.com/watch?v=xxx" --no-preview
```

**输出示例**:

```
2026-01-25 23:15:10 | download | preview | 1 | 0:00:02
  [youtube] Extracting URL: https://youtube.com/watch?v=xxx
[youtube] xxx: Downloading webpage
[youtube] xxx: Downloading android player API JSON

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
元数据预览
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 标题: Adele - Hello (Official Video)              ┃
┃ 艺术家: Adele                                      ┃
┃ 时长: 6:07                                         ┃
┃ 比特率: 192 kbps                                   ┃
┃ 平台: YouTube                                      ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

确认下载? (y/n): y

2026-01-25 23:15:15 | download | download | 1 | 0:00:45
[youtube] xxx: Downloading thumbnail ...
[youtube] xxx: Writing thumbnail to: ...
[download] Destination: /tmp/.../Adele - Hello.mp3
[download]   0.0% of 8.50MiB at ...
[download]  50.0% of 8.50MiB at 1.2MiB/s ETA 00:03
[download] 100.0% of 8.50MiB at 1.5MiB/s ETA 00:00
✓ downloaded: Adele - Hello.mp3 (8.5 MB, 45s)

2026-01-25 23:16:00 | download | process | 1 | 0:00:05
✓ hash_calculated: sha256:abc123...
✓ coverstored: sha256:def456... (thumbnail)

2026-01-25 23:16:05 | download | commit | - | 0:00:03
✓ 已添加到 metadata.jsonl

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
下载完成
  成功: 1 文件
  失败: 0
  新增到work目录: work/Adele - Hello.mp3
```

**批量下载**:

```bash
$ gitmusic download --batch-file music.txt --limit 5
cat music.txt
https://youtube.com/watch?v=1
https://youtube.com/watch?v=2
https://youtube.com/watch?v=3
...

2026-01-25 23:20:10 | download | download | 5 | 0:03:42
  进度: ████████████████████ 5/5 100% 0.02/s 预估 0:00:00
✓ downloaded: Song 1 (8.2 MB, 52s)
✓ downloaded: Song 2 (6.5 MB, 41s)
⚠ failed: Song 3 (网络错误，将重试)
✓ downloaded: Song 4 (9.1 MB, 58s)
✓ downloaded: Song 5 (5.8 MB, 37s)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
批量下载完成
  总计: 5 URL
  成功: 4
  失败: 1 (已重试3次)
  总计大小: 29.6 MB
  平均速率: 0.02 文件/秒
  失败项: https://youtube.com/watch?v=3 (网络超时)
```

**注意事项**:
- 需要yt-dlp在PATH中: `pip install yt-dlp` 或通过包管理器安装
- 首次使用建议加`--fetch`预览元数据
- 批量下载注意磁盘空间和网络流量
- 部分平台可能需要cookie配置（登录后内容）

---

### analyze

**用途**: 分析metadata.jsonl，支持搜索、过滤、字段提取、统计和按行号读取。

**语法**:
```bash
gitmusic analyze [OPTIONS] [QUERY]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `QUERY` | 无 | string | 无 | 搜索关键词（在所有字段搜索） |
| `--search-field` | 无 | string | 无 | 仅在指定字段搜索 |
| `--missing` | 无 | string | 无 | 查找缺失指定字段的条目 |
| `--line` | `-l` | string | 无 | 按行号读取（如"1,3,5-10"） |
| `--fields` | 无 | string | 无 | 提取指定字段（逗号分隔） |
| `--filter` | 无 | string | 无 | 输出时过滤字段 |
| `--limit` | 无 | int | 100 | 限制输出数量 |
| `--on-error` | 无 | string | "stop" | 错误处理策略 |

**搜索逻辑**:

- 无query: 返回所有条目
- 有query无`--search-field`: 在所有字段全文搜索
- 有query有`--search-field`: 仅在指定字段搜索
- 支持组合查询（query + missing + line等）

**字段说明**:

可用字段: `audio_oid`, `cover_oid`, `title`, `artists`, `album`, `date`, `uslt`, `created_at`

**工作流步骤**:

1. **加载元数据**
   - 读取metadata.jsonl所有条目
   - 事件: `phase_start`

2. **应用过滤器**
   - query搜索（全文或指定字段）
   - `--missing`过滤（如"cover,lyrics"）
   - `--line`按行号筛选
   - 事件: `item_event(match)`

3. **提取和处理**
   - `--fields`提取指定字段
   - 计算统计信息（总数、字段覆盖率、艺术家数量等）
   - 事件: `result(stats)`

**示例**:

```bash
# 显示所有条目（限制100条）
gitmusic analyze

# 搜索标题包含"Hello"的歌曲
gitmusic analyze "Hello" --search-field title

# 搜索艺术家包含"Adele"的歌曲
gitmusic analyze "Adele" --search-field artists

# 查找缺失封面的歌曲
gitmusic analyze --missing cover

# 查找缺失歌词和封面的歌曲
gitmusic analyze --missing "lyrics,cover"

# 按行号提取
gitmusic analyze --line 100-200

# 组合查询: 查找Adele的歌曲中缺失封面的
gitmusic analyze "Adele" --search-field artists --missing cover

# 提取特定字段
gitmusic analyze --fields "title,artists,album" --limit 20

# 查看第1000-1100行的数据
gitmusic analyze --line 1000-1100
```

**输出示例**:

```
2026-01-25 23:30:10 | analyze | query | 5247 | 0:00:02
  加载: 5247 条目

2026-01-25 23:30:12 | analyze | search | 12 | 0:00:01
✓ match: Adele - Hello (第142行)
✓ match: Adele - Rolling in the Deep (第143行)
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
分析结果
  总计: 5247 条目
  匹配: 12 条目

统计信息:
  字段覆盖率:
    - audio_oid: 100% (5247/5247)
    - cover_oid: 87.3% (4581/5247)
    - title: 100% (5247/5247)
    - artists: 100% (5247/5247)
    - album: 62.1% (3258/5247)
    - date: 45.8% (2403/5247)
    - uslt: 23.4% (1228/5247)
    - created_at: 100% (5247/5247)

  艺术家数量: 342

  匹配条目预览 (前10条):
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ 艺术家      ┃ 标题                 ┃ 状态            ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ Adele       │ Hello                │ cover ✓ uslt ✗  │
│ Adele       │ Rolling in the Deep  │ cover ✓ uslt ✗  │
└─────────────┴──────────────────────┴─────────────────┘
... (显示前10条，共12条)
```

**导出数据**:

```bash
# 结合jq处理JSON输出
gitmusic analyze "Adele" --search-field artists --log-only | \
  jq -r '.artifacts.entries[] | "\(.artists[0]) - \(.title)"'

# 结果
Adele - Hello
Adele - Rolling in the Deep
Adele - Someone Like You
...
```

**高级查询**:

```bash
# 查找2010年代的歌曲（假设date字段格式为YYYY-MM-DD）
gitmusic analyze --log-only | \
  jq '.artifacts.entries[] | select(.date | startswith("201"))'

# 统计每个艺术家的歌曲数量
gitmusic analyze --log-only | \
  jq '.artifacts.entries[] | .artists[0]' | \
  sort | uniq -c | sort -nr
```

---

### compress_images

**用途**: 压缩封面图片优化体积，哈希变化时自动更新metadata.jsonl。

**语法**:
```bash
gitmusic compress_images [OPTIONS]
```

**参数说明**:

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--size` | 无 | string | "100kb" | 压缩阈值，支持单位b/kb/mb/gb |
| `--on-error` | 无 | string | "continue" | 错误处理策略 |

**单位解析**:

- 支持: `b` (bytes), `kb` (kilobytes), `mb` (megabytes), `gb` (gigabytes)
- 不区分大小写: `100KB`, `10mb`, `1GB`
- 默认单位kb（不指定时）

**工作流步骤**:

1. **扫描封面**
   - 从metadata提取所有cover_oid
   - 列出本地cache中的封面文件
   - 筛选大于阈值的对象
   - 事件: `phase_start`

2. **压缩处理**
   - 使用ffmpeg压缩封面
   - 默认转换为JPEG，质量90%
   - 保持原始宽高比
   - 事件: `item_event(compressed)`

3. **哈希比对**
   - 计算新封面哈希
   - 与原哈希比较
   - 变化则更新metadata.jsonl
   - 无变化则跳过
   - 事件: `result(summary)`

**示例**:

```bash
# 压缩大于100KB的封面（默认）
gitmusic compress_images

# 压缩大于500KB的封面
gitmusic compress_images --size 500kb

# 压缩大于1MB的封面
gitmusic compress_images --size 1mb

# 压缩所有大于10KB的封面
gitmusic compress_images --size 10kb
```

**输出示例**:

```
2026-01-25 23:40:10 | compress_images | scan | 4581 | 0:00:05
  扫描封面: 4581 个对象
  大于阈值(100kb): 1243 个对象

2026-01-25 23:40:15 | compress_images | compress | 1243 | 0:02:15
  压缩: ████████████████████ 1243/1243 100% 9.2/s
✓ compressed: sha256:cover123... (256kb → 89kb, 65%)
✓ compressed: sha256:cover124... (512kb → 156kb, 70%)
⚠ skipped: sha256:cover125... (已小于阈值)
...

2026-01-25 23:42:30 | compress_images | update | 856 | 0:00:45
  更新metadata: ████████████ 856/856 100%
✓ 已更新 856 条目的cover_oid

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
压缩完成
  扫描: 4581 封面
  处理: 1243 封面 (大于100kb)
  压缩: 1243 封面
  哈希变化: 856 封面
  更新metadata: 856 条目
  
  空间节省:
    原始总计: 456 MB
    压缩后: 178 MB
    节省: 278 MB (61%)
  
  平均压缩率: 65%
```

**压缩效果参考**:

| 原始大小 | 压缩后 | 压缩率 | 质量 |
|---------|--------|--------|------|
| 200 KB | 60 KB | 70% | 良好 |
| 500 KB | 150 KB | 70% | 良好 |
| 1 MB | 300 KB | 70% | 可接受 |
| 2 MB | 600 KB | 70% | 可接受 |

**注意事项**:
- 压缩为**有损操作**，会改变封面质量
- 建议在压缩前备份重要封面
- 首次运行建议先用少量数据测试
- 压缩后无法自动恢复，需从原始文件重新生成

---

## 错误处理策略

所有命令支持`--on-error`参数，控制错误处理行为：

### 策略说明

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| `stop` | 遇到错误立即停止命令 | publish, checkout, download |
| `continue` | 记录错误但继续处理后续项 | release, sync, compress_images |
| `notify` | 继续执行，summary中报告错误 | verify, cleanup |

### 默认值

```yaml
publish: stop        # 数据一致，错误应停止
checkout: stop       # 用户操作，错误应停止
release: continue    # 大批量生成，单文件错误不影响整体
sync: continue       # 网络传输，单文件失败可后续重试
verify: notify       # 完整性检查，报告所有问题
cleanup: notify      # 清理操作，报告问题但不中断
download: stop       # 关键操作，错误应停止
analyze: stop        # 分析操作，参数错误应停止
compress_images: continue  # 批量处理，继续执行
```

### 错误类型

1. **致命错误**（总是停止）:
   - 配置错误（路径不存在、SSH连接失败）
   - 权限错误（无法读/写文件）
   - 校验失败（哈希不匹配，数据损坏）
   - 锁获取失败（并发冲突）

2. **可恢复错误**（根据策略处理）:
   - 网络超时（可重试）
   - 单个文件处理失败（不影响其他）
   - 元数据缺失（可跳过）
   - 封面提取失败（非关键）

### 错误报告

所有错误通过事件系统报告：

```json
{
  "ts": "2026-01-25T22:00:00.123456",
  "cmd": "publish",
  "type": "error",
  "id": "Adele - Hello.mp3",
  "message": "音频哈希校验失败",
  "artifacts": {
    "expected": "sha256:abc123...",
    "actual": "sha256:def456...",
    "error_type": "VerifyError"
  }
}
```

Interactive模式显示人类可读错误:
```
✗ error: Adele - Hello.mp3 - 音频哈希校验失败
  期望: sha256:abc123...
  实际: sha256:def456...
```

---

## 配置参考

### 配置文件位置

主配置文件: `config.yaml` (项目根目录)

### 配置项详解

#### transport（传输配置）

```yaml
transport:
  user: your_username               # SSH用户名
  host: your.server.com             # 服务器地址
  remote_data_root: /srv/music/data # 远程数据目录
  retries: 5                        # 失败重试次数
  timeout: 60                       # 超时时间（秒）
  workers: 4                        # 并行线程数
```

#### paths（路径配置）

```yaml
paths:
  work_dir: /path/to/work            # 待发布音频
  cache_root: /path/to/cache         # 对象存储
  metadata_file: /path/to/metadata   # 元数据文件
  release_dir: /path/to/release      # 成品库
  logs_dir: /path/to/logs            # 日志目录
```

#### command_defaults（命令默认行为）

```yaml
command_defaults:
  publish:
    on_error: "stop"
  release:
    on_error: "continue"
    workers: 1
  sync:
    on_error: "continue"
    direction: "both"
  # ...
```

完整配置见 `config.example.yaml`

---

## 故障排查

### 常见问题

#### 1. SSH连接失败

**症状**:
```
error: SSH connection failed
```

**解决**:
```bash
# 测试SSH连通性
ssh user@host

# 检查配置
config.yaml:
  transport:
    user: correct_username
    host: correct_host

# 确保SSH密钥已配置
ssh-copy-id user@host
```

#### 2. 权限错误

**症状**:
```
error: Permission denied
```

**解决**:
```bash
# 检查目录权限
ls -la work/ cache/ release/ logs/

# 修复权限
chmod 755 work/ cache/ release/ logs/
chmod 644 config.yaml
```

#### 3. 哈希校验失败

**症状**:
```
error: SHA256 mismatch
```

**解决**:
```bash
# 重新同步对象
gitmusic sync --direction=both

# 校验完整性
gitmusic verify --mode data

# 删除损坏对象
gitmusic verify --mode data --delete
```

#### 4. 锁冲突

**症状**:
```
error: Failed to acquire lock
```

**解决**:
```bash
# 检查是否有其他进程在运行
ps aux | grep gitmusic

# 手动删除锁文件（谨慎操作）
rm -rf .locks/

# 等待其他命令完成
```

#### 5. yt-dlp未找到

**症状**:
```
error: yt-dlp command not found
```

**解决**:
```bash
# 安装yt-dlp
pip install yt-dlp

# 或
apt install yt-dlp  # Debian/Ubuntu
brew install yt-dlp  # macOS
```

#### 6. 内存不足

**症状**:
```
error: Out of memory
```

**解决**:
```bash
# 减少并行数
gitmusic publish --workers 2
gitmusic sync --workers 2

# 分批处理
gitmusic release --line 1-1000
gitmusic release --line 1001-2000
```

### 性能优化

#### 提高传输速度

```yaml
# config.yaml
transport:
  workers: 8              # 增加并行线程
  timeout: 120            # 增加超时时间
```

#### 减少内存使用

```yaml
# 分批处理大目录
command_defaults:
  publish:
    # 手动分批或使用limit
  checkout:
    limit: 100            # 限制单次检出数量
```

### 日志分析

#### 查看事件日志

```bash
# 查看最新日志
ls -lt logs/ | head

# 查看特定命令日志
cat logs/publish-20260125-221530.jsonl

# 过滤错误事件
cat logs/publish-20260125-221530.jsonl | jq 'select(.type == "error")'

# 统计各类事件
cat logs/publish-20260125-221530.jsonl | jq '.type' | sort | uniq -c
```

#### 常见日志模式

**成功发布**:
```json
{"ts": "2026-01-25T22:00:00.123", "cmd": "publish", "type": "phasestart", "phase": "hashstore", "total_items": 23}
{"ts": "2026-01-25T22:00:01.456", "cmd": "publish", "type": "item_event", "id": "Adele - Hello.mp3", "status": "audio_stored", "artifacts": {"oid": "sha256:abc123...", "size": 4518291}}
...
{"ts": "2026-01-25T22:00:30.789", "cmd": "publish", "type": "result", "status": "ok", "message": "发布完成", "artifacts": {"total": 23, "success": 23, "failed": 0}}
```

**传输失败**:
```json
{"ts": "2026-01-25T22:00:15.234", "cmd": "sync", "type": "error", "id": "sha256:bad123...", "message": "上传失败: 网络超时", "artifacts": {"retry": 3, "timeout": true}}
```

### 获取帮助

#### 查看命令帮助

```bash
# 通用帮助
gitmusic --help

# 命令特定帮助
gitmusic publish --help
gitmusic download --help
```

#### 报告问题

提交issue时包含：
1. 命令和完整参数
2. 配置文件（脱敏后）
3. 相关日志文件
4. 错误信息截图
5. 环境信息（OS, Python版本）

---

## 附录

### A. 事件类型参考

| 事件类型 | 触发时机 | 必需字段 |
|---------|---------|---------|
| `phase_start` | 阶段开始时 | `phase`, `total_items` |
| `batch_progress` | 批量进度更新 | `phase`, `current`, `total` |
| `item_event` | 单项事件 | `id`, `status`, `message` |
| `progress` | 进度更新 | `percent`, `speed`, `eta` |
| `log` | 日志信息 | `level`, `message` |
| `error` | 错误发生 | `id`, `message`, `artifacts` |
| `result` | 命令完成 | `status`, `message`, `artifacts` |
| `summary` | 汇总信息 | 汇总统计信息 |

### B. 锁类型说明

| 锁类型 | 用途 | 获取时机 | 释放时机 |
|--------|------|---------|---------|
| metadata读锁 | 读取metadata | 命令开始时 | 命令结束时 |
| metadata写锁 | 写入metadata | 发布/commit前 | 写入完成后 |
| release读锁 | 读取release | release命令 | release命令结束 |

### C. 环境变量

可选环境变量（优先于配置文件）：

```bash
export GITMUSIC_WORK_DIR=/path/to/work
export GITMUSIC_CACHE_ROOT=/path/to/cache
export GITMUSIC_METADATA_FILE=/path/to/metadata.jsonl
export GITMUSIC_RELEASE_DIR=/path/to/release
export GITMUSIC_LOGS_DIR=/path/to/logs
export GITMUSIC_CONFIG=/path/to/config.yaml
```

### D. 迁移指南

从旧脚本迁移：

1. **analyze_metadata.py** → `gitmusic analyze`
   - 功能增强: 支持更多过滤器和统计
   - 输出改进: 富文本表格，更易读

2. **analyze_duplicates.py** → `gitmusic analyze --missing`
   - 整合到新analyze命令
   - 提供完整的重复检测

3. **download_ytdlp.py** → `gitmusic download`
   - 参数统一，支持--fetch, --no-preview
   - 更好的错误处理和事件流

---

**文档版本**: 1.0  
**最后更新**: 2026-01-25  
**维护者**: GitMusic Team
