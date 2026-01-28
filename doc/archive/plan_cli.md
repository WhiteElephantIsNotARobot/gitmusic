### Summary

本报告在此前讨论基础上，修正并补充了事件协议的 Markdown 表格格式，新增 **音频 I/O 库** 设计（负责音频流分离、封面读写、音频/封面原子写入与校验），并把这些要点融入 CLI 驱动的一站式实现方案。目标保持不变：**一条最简命令触发完整工作流、所有脚本只输出事件流、cli.py 负责交互与可视化、一次性重构所有脚本**。以下内容按主题详细记录设计细节、规范与实施要点，便于直接落地。

---

### Event types and schema

**说明**

- 所有脚本通过 stdout 输出 JSONL 事件流，cli.py 实时解析并渲染。
- 事件必须包含通用字段 `ts` 和 `cmd`；单行 JSON 一致性强，便于链式调用与日志持久化。

**事件类型与关键字段**

| **type** | **purpose** | **required fields** | **notes** |
|---|---|---|---|
| `phase_start` | 阶段开始 | `phase`; `total_items` | 标记阶段名与总量 |
| `batch_progress` | 批量进度 | `phase`; `processed`; `total_items`; `rate_per_sec` | 用于聚合进度显示 |
| `item_event` | 单项瞬时事件 | `id` or `file`; `status`; `message` | 不用于持续进度条，仅日志与审计 |
| `log` | 普通日志 | `level`; `message` | level: info|warn|debug |
| `result` | 阶段或命令结果 | `status`; `code`; `message`; `artifacts` | status: ok|warn|error |
| `error` | 严重错误 | `message`; `context` | cli.py 高亮并持久化到错误日志 |

**示例事件行**

- `{"type":"batch_progress","ts":"2026-01-17T15:00:00Z","cmd":"release","phase":"generate","processed":120,"total_items":508,"rate_per_sec":28}`

---

### Audio I O library

**目标**
提供一个独立、可复用的库负责所有音频相关 I/O：**音频流分离、音频帧哈希、封面提取与写入、封面压缩、原子写入与校验**。脚本通过该库调用，不直接操作文件系统细节。

**模块与接口概览**

| **模块** | **职责** | **关键方法** |
|---|---|---|
| `audio.extract` | 从容器文件分离纯音频流 | `extract_audio_stream(src_path, out_path)` |
| `audio.hash` | 计算音频帧哈希并记录 tooling | `hash_audio_frames(path, params) -> "sha256:..."` |
| `audio.cover` | 提取/写入/压缩封面 | `extract_cover(src_path) -> bytes`; `write_cover(target_path, bytes)` |
| `audio.write` | 嵌入标签与封面并原子写入 | `embed_tags_and_write(src_audio, metadata, cover_bytes, out_path)` |
| `audio.verify` | 本地与远端校验 | `verify_local(path, expected_oid)`; `verify_remote(remote_path, expected_oid)` |

**实现要点**

- **ffmpeg 参数统一**：库内部使用统一 ffmpeg 参数（由 config 注入），并在每次哈希时记录 `ffmpeg_version` 与 `params` 到 tooling 日志。
- **原子写入**：写入 release 或 cache 时先写 `.tmp`，完成后 `mv` 覆盖。
- **封面处理**：支持多格式输入，统一输出 JPG（可选质量），并返回新的 `cover_oid`。
- **错误与事件**：库函数在关键步骤通过 `EventEmitter` 输出 `log` / `error` / `item_event`，便于 cli 聚合。

**短示例调用（伪代码）**

- `oid = audio.hash.hash_audio_frames("/tmp/tmp.mp3", ffmpeg_params)`
- `cover = audio.cover.extract_cover("/tmp/tmp.mp3")`
- `audio.write.embed_tags_and_write("/tmp/tmp.mp3", metadata, cover, "/release/Artist - Title.mp3")`

---

### CLI command registration and execution

**核心原则**

- **命令定义在 cli.py 内**，保证最高自由度与可控性。
- 每个命令由 `Command` 对象注册，包含 `name`、`desc`、`steps`、`requires_lock`、`timeout_seconds`、`on_error` 等。
- **一条命令触发整条工作流**：命令内部按步骤顺序执行，步骤间可流式传递条目或在边界聚合。

**Command 结构要点**

- `steps` 为步骤函数列表，步骤函数签名统一：`step(ctx, input_iter) -> iterable of items/events`。
- `on_error` 策略：`stop` / `continue` / `notify`。
- `requires_lock`：若为真，cli 在执行前获取 metadata 写锁。

**链式调用模式**

- **流式管道**：上游步骤的 stdout 事件可直接作为下游步骤的 stdin（cli 内部实现），实现高效无中间文件联动。
- **序列触发**：在需要 commit/push 或人工确认时，cli 在步骤边界等待并决定是否继续。

**示例命令（概念）**

- `publish`：`[scan_work, hash_and_store, compress_cover, verify, sync_cache, commit]`
- `release`：`[prepare_list, generate_releases(batch), write_summary]`
- `full-update`：`[publish, sync, release]`（把其他命令作为步骤调用）

---

### API design and script I O standards

**libgitmusic API 概览**

- **MetadataManager**：`load_all()`, `get_by_oid()`, `validate_entry()`, `add_or_update()`, `commit(message)`. 包含字段校验，如音轨 oid 是否重复，必填字段是否缺失。
- **ObjectStore**：`store_audio(temp_path) -> audio_oid`, `get_audio_path(oid)`, `exists(oid)`, `store_cover(temp_path)`.
- **TransportAdapter**：抽象 `upload(local, remote)`, `download(remote, local)`, `remote_sha256(remote_path)`. 默认实现 `ScpAdapter`。
- **HashUtils**：`hash_audio_frames(path, params)`，并写 tooling 日志。
- **EventEmitter**：`emit(event_dict)`，统一输出 JSONL 并由 cli 写入 logs。
- **LockManager**：文件锁或进程锁，保护 metadata 写入。

**脚本输入输出标准**

- **输入**：严格 stdin JSONL（每行一项），不使用 CLI 参数。
- **输出**：stdout 输出 JSONL 事件流；stderr 仅短提示。
- **退出码**：0 成功；1 警告；2 错误。
- **日志**：cli 将事件追加到 `logs/<command>-TIMESTAMP.jsonl`，并在服务器模式把关键 `error` 追加到 `server_release_log`。

**事件驱动联动示例**

- `publish` 输出每个处理项的 `item_event`（含 `audio_oid`），cli 过滤出需要 release 的项并把这些 JSONL 行写入 `release` 步骤 stdin。

---

### CLI interaction and visual details

**REPL 与最简命令**

- 启动 `python repo/tools/cli.py` 进入 `gitmusic>`。
- 常用命令一词触发完整工作流：`publish`、`release`、`full-update`、`sync`、`verify`、`tail-errors`。
- 支持一次性命令：`python repo/tools/cli.py publish`。

**可视化原则**

- **聚合进度**：以 `phase` 与 `batch_progress` 为粒度显示总体进度，避免为每首歌创建短寿命进度条。
- **最近事件滚动区**：显示最近若干 `item_event`（成功/警告/错误），便于快速定位问题。
- **summary 面板**：命令结束时显示 `items_processed`、`errors`、`items_per_sec`、`elapsed_seconds`。
- **中断与取消**：`Ctrl-C` 触发取消，cli 输出 `error` 事件 `cancelled by user` 并清理锁与临时文件。

**视觉实现建议**

- 使用 `rich` 渲染表格、聚合进度与高亮错误；若不可用，回退为简洁文本输出。
- 对于真正耗时的单项（例如大文件转码），可临时显示单项进度条，但默认不为短任务创建。

---

### Transport security and verification

**默认适配器**

- 使用 `ScpAdapter`：`scp` 上传到远端临时路径 → 远端 `sha256sum` 校验 → `mv` 到目标目录 → 设置权限。
- 上传/下载均采用临时文件 `.part` / `.tmp`，完成并校验后原子替换。

**安全实践**

- 使用 SSH key 登录，私钥由管理员管理，不放入 config。
- scp/ssh 调用实现重试与指数退避，记录失败到 `logs/`。
- 权限最小化：运行脚本的用户仅对必要目录有写权限。

---

### Migration testing and deliverables

**迁移步骤（一次性重构）**

1. 实现 `libgitmusic` 基础库（metadata、object_store、transport_scp、hash、events、locking）。
2. 在 `cli.py` 内注册所有命令并实现步骤函数骨架。
3. 把现有脚本改写为步骤函数或库调用，遵守 I/O 与事件规范。
4. 小样本测试（10 条）验证功能与事件流。
5. 全量测试（500+）验证性能、批量进度、scp 传输完整性。
6. 部署到服务器，替换 post-receive hook 为触发 cli.py 的 systemd 服务或队列消费者。

**测试矩阵**

- 单元测试：metadata 校验、hash 计算、transport 上传/校验。
- 集成测试：publish→sync→release 流程，验证事件、日志、summary。
- 故障注入：网络中断、缺封面、哈希不匹配、文件名冲突。
- 性能测试：测量 items/sec、总耗时、并发批次行为。

**交付物**

- `repo/tools/cli.py`（命令注册、运行引擎、REPL、事件解析）
- `libgitmusic` 核心模块骨架（audio I/O、metadata、object_store、transport_scp、hash、events、locking）
- `config.example.yaml`（全局选项模板）
- `command-spec.md`（命令与步骤开发规范）
- 集成测试脚本与小样本测试数据
- 操作手册与故障排查清单

---

### Final notes

- 我已修正事件表格为合法 Markdown 表格并补充了 **音频 I/O 库** 设计，确保音频分离、封面处理、原子写入与校验都由库负责。
- 你要求的“一站式、最简命令、命令在 cli.py 内定义”已被采纳并详细化为命令注册与步骤引擎设计。
- 若你同意，我可以按此文档生成三份可直接使用的骨架文件：`cli.py`（命令注册与运行引擎）、`libgitmusic/audio`（音频 I/O 接口骨架）、`config.example.yaml`，便于你或 agent 立即开始一次性重构。
