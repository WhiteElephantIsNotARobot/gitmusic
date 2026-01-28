### 报告摘要

本报告整合并落实你最新的需求与背景：服务器裸仓库位于 `/srv/music/music.git`，服务器保留对象与专用 `releases` 目录；本地以单一父目录组织（包含 `repo`、`work`、`cache`、`release`），所有脚本按功能分类放入仓库子目录（`repo/work`、`repo/data`、`repo/cache`、`repo/release`）；文件名采用 **“艺术家 - 标题.mp3”** 格式；工作目录仅用于临时编辑并由“存入脚本”扫描 `work` 中 MP3 直接写回 `metadata.jsonl`（匹配则更新，不匹配则新增）；成品生成支持两种模式（本地写入本地 `release`，或在服务器写入服务器 `releases`）；裸仓库 `post-receive` hook 可调用服务器工作副本 `/srv/music/repo` 中的脚本触发服务器端生成。以下为完整、可执行的设计与实施细节。

---

### 背景信息

- 单人私有环境，局域网 Debian 服务器，网速有限。
- 库中约数百首音乐，含 ID3 与行级 USLT 歌词。
- 不把二进制对象（音轨、封面）放入 Git；Git 仅保存 `metadata.jsonl` 与脚本。
- 所有自动化脚本用 Python 实现并版本化放入仓库。
- 客户端不需专门播放器，直接读取成品 `艺术家 - 标题.mp3`。
- 工作流程要求：工作目录可临时放新歌或取出编辑，存回时按音轨哈希匹配 `metadata.jsonl` 更新或新增条目；工作目录文件名为 `艺术家 - 标题.mp3`，无 oid 标记。

---

### 最终需求清单

1. **裸仓库**：`/srv/music/music.git`（仅文本：`metadata.jsonl` 与脚本目录）。
2. **服务器数据目录**：`/srv/music/data/objects`（按 SHA256 内容寻址）、`/srv/music/data/covers`、`/srv/music/data/releases`（服务器专用成品目录）。
3. **本地父目录**（示例 `~/my-music/`）：包含 `repo/`（Git 工作副本）、`work/`（临时编辑，文件名 `艺术家 - 标题.mp3`）、`cache/`（本地对象与封面缓存）、`release/`（本地生成或同步的成品库）。
4. **脚本分类放置**：仓库根下按功能放脚本文件夹 `repo/work/`、`repo/data/`、`repo/release/`，每个文件夹内放对应脚本（每脚本单一职责）。
   - `repo/data/`：包含缓存同步、哈希校验、图片压缩等脚本（已拆分为独立脚本）
   - `repo/work/`：包含检出和发布元数据脚本
   - `repo/release/`：包含本地和服务器成品生成脚本
5. **工作目录行为**：取出脚本在 `work/` 生成 `艺术家 - 标题.mp3`；存入脚本扫描 `work/` 中 MP3，按音频帧哈希匹配 `metadata.jsonl`，匹配则更新条目并 `git commit/push`，不匹配则新增条目并 `git commit/push`；存回后删除 `work` 中该文件。
6. **成品生成**：支持两种模式：本地模式（在客户端生成写入本地 `release/`）与服务器模式（在服务器上生成写入 `/srv/music/data/releases/`）。成品文件名为 `艺术家 - 标题.mp3`。
7. **裸仓库 hook**：`post-receive` 调用服务器工作副本 `/srv/music/repo` 中脚本（或触发队列/服务）以运行服务器端生成脚本。
8. **缓存策略**：客户端 `cache/` 保存对象与封面；提供同步、清理、图片压缩脚本。
   - 同步细节：增量同步，本地没有远端有则下载，本地有远端没有则上传
   - 哈希校验：验证 cache 中文件哈希值是否符合文件名（oid）
   - 图片压缩：支持转 JPG 并重算哈希，同时更新 `metadata.jsonl` 中的引用
   - 清理：删除不在 `metadata.jsonl` 中引用的对象
9. **原子性与一致性**：所有写入 `releases` 与 `metadata.jsonl` 的操作采用临时文件写入后原子替换；`git commit` 前格式化并按 `audio_oid` 排序。
10. **实现语言**：全部脚本用 Python，脚本内部通过相对路径访问父目录下的 `work`、`cache`、`release`。

---

### 服务器目录结构（最终）

```
/srv/music/
  music.git                      # 裸仓库（/srv/music/music.git）
  repo/                          # 可选服务器工作副本（/srv/music/repo），由管理员 clone
    metadata.jsonl
    work/                         # （可选）仅用于服务器端操作脚本，不必存在
    tools/
      work/...
      data/...
      cache/...
      release/...
  data/
    objects/sha256/<aa>/<oid>.<ext>
    covers/sha256/<aa>/<oid>.<ext>
    releases/                      # 服务器专用成品目录（艺术家 - 标题.mp3）
```

说明：裸仓库 `/srv/music/music.git` 仅保存文本与脚本历史；服务器工作副本 `/srv/music/repo` 用于 hook 调用与脚本执行（`post-receive` 可 `git pull` 并运行 `/srv/music/repo/release/create_release_server.py`）。

---

### 本地（客户端）目录结构（示例 `~/my-music/`）

```
~/my-music/
  repo/                          # Git 工作副本（clone 自 /srv/music/music.git）
    metadata.jsonl
    work/                         # 脚本分类目录：repo/work/
      checkout.py                 # （待实现）按 oid 检出文件到 work
      publish_meta.py             # （待实现）扫描 work 并更新 metadata.jsonl
    data/                         # repo/data/（缓存管理脚本，已拆分）
      verify_hashes.py            # 校验 cache 中文件哈希值是否符合文件名
      compress_images.py          # 压缩图片转 JPG 并重算哈希，更新 metadata.jsonl
      sync_cache.py               # 增量同步本地 cache 与远端 data
    release/                      # repo/release/
      create_release_local.py     # 本地模式生成成品到 release/
      create_release_server.py    # （待实现）服务器模式生成成品
  work/                           # 临时编辑区（艺术家 - 标题.mp3）
  cache/                          # 本地缓存（objects & covers，按 oid 存放）
    objects/sha256/...
    covers/sha256/...
  release/                        # 本地生成或同步的成品库（艺术家 - 标题.mp3）
```

说明：脚本在 `repo/` 内运行，通过相对路径访问 `../work`、`../cache`、`../release`。

---

### 脚本分类与职责（放在仓库 `repo/` 下）

- **repo/work/**
  - `checkout.py`（待实现）：按 `metadata.jsonl` 指定条目或指定 `audio_oid`，从 `cache`（或远端 objects）复制并命名为 `艺术家 - 标题.mp3` 到 `../work/`。
  - `publish_meta.py`（待实现）：扫描 `../work/` 中 MP3（或按文件名参数），对每个文件计算音频帧哈希并在 `metadata.jsonl` 中查找匹配：匹配则更新条目（`title`、`artists`、`uslt`、`updated_at`），不匹配则新增条目（计算 `audio_oid`、抽标签填字段）；`git add/commit/push`；删除 `../work/<file>`。
- **repo/data/**
  - `verify_hashes.py`：校验 cache 中文件哈希值是否符合文件名（oid），报告不匹配项。
  - `compress_images.py`：压缩 `cache/covers` 中的图片转 JPG 并重算哈希，更新 `metadata.jsonl` 中的 `cover_oid` 引用。
  - `sync_cache.py`：增量同步本地 cache 与远端 data。远端有本地无 → 下载；本地有远端无 → 上传。由于内容寻址，直接按 oid 比较即可。
- **repo/release/**
  - `create_release_local.py`：本地模式，遍历 `metadata.jsonl`（或指定列表），从 `cache/objects` 复制原始音轨到临时文件，嵌入 `metadata` 中标签/封面，写入 `../release/艺术家 - 标题.mp3`（覆盖或新建）。
  - `create_release_server.py`（待实现）：服务器模式，在 `/srv/music/repo` 或服务器工作副本运行，从 `/srv/music/data/objects` 生成并写入 `/srv/music/data/releases/`。

所有脚本均用 Python 编写并在仓库中版本化；脚本运行时使用相对路径访问父目录下的 `work`、`cache`、`release`。

---

### 关键工作流（逐步、可执行）

#### 导入新曲目（可在客户端或服务器运行）

1. 把新 MP3 放入 `~/my-music/work/`（文件名 `艺术家 - 标题.mp3`）。
2. 运行 `repo/work/publish_meta.py <file>`：脚本计算音频帧哈希并在 `metadata.jsonl` 中查找匹配；匹配则更新条目并 `git commit/push`；不匹配则新增条目并 `git commit/push`；脚本删除 `work/<file>`。

#### 取出供编辑（checkout）

- 运行 `repo/work/checkout.py <audio_oid 或 metadata 条目>`：脚本从 `cache/objects`（或远端 objects）复制并命名为 `艺术家 - 标题.mp3` 到 `../work/`，并生成 `metadata_for_work.json`（可选）供参考。

#### 生成成品（两种模式）

- **本地模式**（客户端全库或按需生成）
  - 运行 `repo/release/create_release_local.py`：脚本遍历 `metadata.jsonl`（或指定列表），从 `cache/objects` 复制原始音轨，嵌入标签/封面，写入 `../release/艺术家 - 标题.mp3`。
- **服务器模式**（服务器端写入专用目录）
  - 在服务器工作副本 `/srv/music/repo` 运行 `release/create_release_server.py`：脚本从 `/srv/music/data/objects` 复制并嵌入标签，写入 `/srv/music/data/releases/`。
  - 裸仓库 `post-receive` hook 可 `cd /srv/music/repo && git pull && python3 release/create_release_server.py --only-changed`。

#### 客户端获取成品

- 优先通过 SMB/同步软件把服务器 `/srv/music/data/releases/` 同步到本地 `~/my-music/release/`。
- 若某首未同步，运行 `repo/release/download_release.py <url> <dst>` 拉取单首成品到本地 `release/`。

#### 缓存管理

- 校验哈希：运行 `repo/data/verify_hashes.py` 检查 cache 中文件哈希值是否符合文件名（oid）
- 压缩图片：运行 `repo/data/compress_images.py` 将封面转为 JPG 并更新 `metadata.jsonl`
- 同步缓存：运行 `repo/data/sync_cache.py` 增量同步本地 cache 与远端 data（远端有本地无则下载，本地有远端无则上传）

---

### 实现细节与技术要点

- **音轨 ID（内部）**：在 `metadata.jsonl` 中以 `audio_oid: "sha256:<hex>"` 保存；用于匹配与去重。文件名对外仅为 `艺术家 - 标题.mp3`。
- **哈希计算**：建议用“音频帧哈希”判断是否为同一音轨（用 `ffmpeg -i file.mp3 -vn -acodec copy -f mp3 - | sha256sum`），以允许标签改动不影响匹配。存入新歌时同时记录完整文件哈希与音频帧哈希（可选）。
- **原子写入**：写入 `release` 或更新 `metadata.jsonl` 时先写临时文件（`.part` 或 `.tmp`），校验通过后 `mv` 覆盖目标，避免半成品或元数据不一致。
- **文件命名**：release 文件名严格为 `艺术家 - 标题.mp3`（必要时可在文件名后加空格或括号避免冲突，但你已指定不带 id）。若出现同名冲突，脚本可在写入前检测并按策略覆盖或提示。
- **Git 操作**：`publish_meta.py` 在更新 `metadata.jsonl` 后执行 `git add metadata.jsonl && git commit -m "update: <Artist - Title>" && git push`。批量改动建议在分支上完成再合并以减少冲突。
- **裸仓库 hook**：hook 不直接执行复杂任务，推荐在 hook 中 `git pull` 到 `/srv/music/repo` 并调用工作副本脚本，或将事件写入队列由守护进程处理。
- **权限**：确保运行脚本的用户对 `data/objects`、`data/releases`、`/srv/music/repo` 有适当读写权限；Samba 用户权限单独配置。

---

### 示例命令片段（可直接使用）

- 计算音频帧哈希：

```bash
ffmpeg -i "file.mp3" -vn -acodec copy -f mp3 - 2>/dev/null | sha256sum
```

- 裸仓库 post-receive 示例（`/srv/music/music.git/hooks/post-receive`）：

```bash
#!/bin/sh
cd /srv/music/repo || exit 1
git pull origin main
/usr/bin/python3 release/create_release_server.py --only-changed
```

- 本地生成单首 release（示例）：

```bash
cd ~/my-music/repo
python3 release/create_release_local.py --oid sha256:aa11...
```

- 存回工作目录文件（示例）：

```bash
cd ~/my-music/repo
python3 work/publish_meta.py "../work/艺术家 - 标题.mp3"
```

---

### 验收清单

- 裸仓库位于 `/srv/music/music.git`，仅包含 `metadata.jsonl` 与脚本目录。
- 服务器数据目录 `/srv/music/data/objects`、`/srv/music/data/covers`、`/srv/music/data/releases` 已就绪并可写。
- 本地父目录包含 `repo/`（脚本与 metadata）、`work/`（临时编辑）、`cache/`（对象缓存）、`release/`（本地成品）。
- `repo/data/verify_hashes.py` 能校验 cache 中文件哈希值是否符合文件名。
- `repo/data/compress_images.py` 能压缩图片转 JPG 并更新 `metadata.jsonl`。
- `repo/data/sync_cache.py` 能增量同步本地 cache 与远端 data。
- `repo/release/create_release_local.py` 能在本地生成成品到 `release/`。
- `repo/work/publish_meta.py`（待实现）能扫描 `work/` 并更新 `metadata.jsonl`。
- `repo/work/checkout.py`（待实现）能按 oid 检出文件到 `work/`。
- `repo/release/create_release_server.py`（待实现）能在服务器生成成品。
- 裸仓库 `post-receive` hook 能触发服务器工作副本 `/srv/music/repo` 中的脚本。
- 所有脚本均放在仓库 `repo/` 下并通过相对路径访问父目录的 `work`、`cache`、`release`。

---
