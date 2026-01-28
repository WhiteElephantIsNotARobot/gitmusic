### 概要
本规范把 CLI 命令、UI 渲染层与库实现的职责彻底分离，目标是：**命令定义唯一、UI 可替换、库负责所有确定性 I O 与一致性**。规范包含：命令清单与每个命令的参数与工作流、UI 模块拆分与样式注册、库层路径与文件系统访问标准、事件与日志约定、迁移与测试计划。遵循本规范可消除重复实现、提高可维护性与可测试性，并保证服务端兼容性。

---

### 架构总览
- **运行时模型**  
  - 单一进程主控：`cli.py` 构建运行时 `Context` 并分发命令。  
  - 事件驱动：库在关键点通过 `events.emit()` 写入 JSONL 事件；CLI 订阅事件并在 interactive 模式渲染；log-only 模式事件写 stdout。  
  - 模块分层：`libgitmusic` 负责业务逻辑和 I O；`commands` 目录每个命令为薄适配器；`ui` 提供渲染器实现；`cli.py` 负责注册、解析与分发。  
- **职责边界**  
  - **CLI**：解析参数、构建 `Context`、加载命令注册表、订阅事件、渲染 UI、管理生命周期。  
  - **Commands**：声明参数、把 args 映射为步骤输入、按步骤顺序调用库函数、处理命令级 on_error 策略。  
  - **UI 层**：统一渲染 API，决定 interactive 与 log-only 的输出行为，样式集中注册。  
  - **库**：实现 metadata、object_store、audio_io、hash、transport、locking、events，直接执行本地与远端 I O 并保证原子性与幂等性。

---

### CLI 命令规范
下表列出所有命令的**必需选项**、**核心行为**与**工作流步骤**，每个命令的实现必须严格遵守步骤顺序与事件输出约定。

| **命令** | **必需选项与别名** | **核心工作流步骤** |
|---|---|---|
| **publish** | `--changed-only` `--preview` | 1. scan_work 2. diff_with_metadata 3. preview_and_confirm 4. hash_and_store_audio 5. compress_and_store_cover 6. validate_and_verify 7. update_metadata_commit |
| **checkout** | `<query>` `--missing` `--force|-f` `--limit` `--search-field` `--line|-l` | 1. query_metadata 2. conflict_check 3. fetch_objects 4. embed_metadata |
| **release** | `--mode local|server` `--force|-f` `--line|-l` `--workers` | 1. git_pull 2. prepare_list 3. sync_cache 4. generate_release_files 5. verify_release 6. summary_and_publish |
| **sync** | `--direction upload|download|both` `--dry-run` `--timeout` `--workers` 别名 push pull | 1. scan_local_remote_index 2. diff_and_plan 3. execute_plan 4. summary |
| **verify** | `--mode data|release` `--delete` | 1. collect_targets 2. compute_and_compare 3. optional_delete 4. summary |
| **cleanup** | `--mode local|server|both` `--confirm` | 1. build_reference_table 2. scan_objects 3. compute_orphans 4. report_or_delete |
| **download** | `<URL>` `--batch-file` `--fetch` `--no-preview` 别名 fetch | 1. fetch_metadata 2. download_media 3. compress_cover 4. verify_and_sync |
| **analyze** | `<query>` `--search-field` `--line|-l` `--read` `--missing` `--filter` `--limit` | 1. query_index 2. compute_stats |
| **compress_images** | `--size` | 1. scan_covers 2. compress_each 3. update_metadata_if_changed |

#### 每个命令的实现约定
- **参数声明唯一**：命令参数只在 `commands/<name>.py` 中声明，CLI 通过 registry 自动加载并生成 help。  
- **步骤函数复用**：步骤实现应调用 `libgitmusic` 的领域函数，命令模块不直接实现业务逻辑。  
- **事件输出**：每个步骤在开始时 emit `phase_start`，批量更新时 emit `batch_progress`，单项完成时 emit `item_event`，结束时 emit `result` 或 `summary`。  
- **锁策略**：只有在写入 metadata 或共享索引时才获取 `metadata` 锁，锁在 finally 块释放。  
- **on_error 策略**：命令声明 `on_error`（stop|continue|notify），命令模块在捕获异常时按策略处理并 emit `error`。  
- **交互点**：仅在必要交互点（如 publish preview）检查 `ctx.mode`，避免在每处渲染前判断 log-only。

---

### UI 模块拆分与渲染标准
#### 目标
把所有可视化逻辑集中到 UI 层，命令只声明要显示的数据，UI 层负责如何显示并根据运行模式决定是否渲染。

#### 组件与接口
- **Context.ui** 提供统一渲染接口 `UIRenderer`，命令通过 `ctx.ui` 调用。  
- **UIRenderer 必要方法**  
  - `render_table(spec: TableSpec, rows: Iterable[dict])`  
  - `render_progress(task_id: str, completed: int, total: int, meta: dict)`  
  - `render_event(ev: dict)`  
  - `render_panel(title: str, lines: Iterable[str])`  
  - `flush()`  
- **实现类**  
  - `RichUIRenderer` 使用 `rich` 渲染表格、聚合进度、错误高亮；在支持覆盖行的终端使用 carriage-return 覆盖主进度行，其他终端追加行保持滚动。  
  - `NoopUIRenderer` 在 log-only 或测试模式下不渲染，但可把结构化行通过 `events.emit()` 写入日志。  
  - `CaptureUIRenderer` 用于单元测试，捕获渲染调用以便断言。

#### 表格样式注册
- **TableSpec** 定义表格样式：`id`、`columns`、`max_rows`、`truncate`、`sortable`。  
- **TableStyleRegistry** 集中注册所有表格样式，例如 `publish_preview`、`release_results`、`verify_report`。样式可由 `config.yaml` 覆盖列宽与颜色主题。  
- **命令调用**：命令只调用 `ctx.ui.render_table(TableStyleRegistry.get("publish_preview"), rows)`。

#### 进度条与事件渲染
- **聚合进度**：每个阶段对应一个 `Progress` task，命令通过 `ctx.ui.render_progress` 更新。短任务合并为 batch updates。  
- **单项长任务**：步骤发出 `progress` 事件时 UI 创建临时单项进度条并在完成后移除。  
- **最近事件流**：UI 保持最近若干 `item_event` 的滚动显示，颜色区分状态。  
- **错误高亮**：收到 `error` 事件时 UI 在输出流中插入红色横幅并写入日志。

#### 运行模式处理
- **Context.mode** 在 CLI 启动时设置为 `interactive` 或 `log-only`。  
- **UI 层决定行为**：命令不再在每处判断 `if log_only`；UI 内部根据 mode 决定渲染或写事件。  
- **交互跳过**：命令在需要用户确认时检查 `ctx.mode`，在 log-only 下自动按配置或失败安全策略处理。

---

### 库 I O 与路径访问标准
#### 路径传递与 Context 约定
- **路径由 CLI 解析并注入 Context**：`project_root`、`work_dir`、`cache_dir`、`release_dir`、`logs_dir`、`tmp_dir` 等由 CLI 解析为绝对路径并放入 `ctx`。库不得自行查找 config。  
- **库函数参数必须为绝对 Path**：库内部立即 `resolve()` 并验证路径合法性与权限。  
- **临时文件约定**：库写临时文件使用 `ctx.tmp_dir` 或 `tempfile.mkdtemp`，文件名包含命令名与 PID。

#### 文件系统写入与原子性
- **写入流程**：写入到 `target.tmp` → `fsync` 临时文件 → `os.replace(target.tmp, target)` → `fsync` 目标目录。  
- **锁保护**：写 `metadata.jsonl` 或共享索引前必须 `locking.acquire("metadata")`，写完释放。  
- **权限与安全**：写入后设置权限，拒绝不安全 symlink，写入前检查磁盘空间阈值。

#### 远端访问与 transport 约定
- **抽象层**：所有远端操作通过 `transport` 抽象（ScpAdapter 默认）。CLI 与命令不直接调用 scp。  
- **上传流程**：本地写 tmp → transport.upload(local_tmp, remote_tmp) → transport.remote_sha256(remote_tmp) → 本地比对 → transport.remote_move(remote_tmp, remote_final)。  
- **下载流程**：transport.download(remote, local_tmp) → 本地校验 → mv 到 final。  
- **重试与退避**：transport 实现支持配置化重试次数、指数退避与超时。  
- **幂等性**：若远端已存在且哈希匹配，上传短路返回成功。

#### 事件与返回契约
- **事件字段**：每条事件包含 `ts`、`cmd`、`type`，按类型补充 `phase`、`processed`、`total_items`、`id`、`status`、`message`、`artifacts`。  
- **返回对象**：库函数返回结构化结果对象（例如 `StoreResult`、`VerifyResult`），包含路径、oid、status、error 信息。  
- **异常类型**：定义 `ValidationError`、`TransportError`、`IOError`、`LockError` 等，库在抛出前 emit `error`。

---

### 迁移计划 测试与交付
#### 迁移分阶段
1. **接口冻结**：定义 `Context`、`UIRenderer`、`TableSpec`、核心库最小接口并冻结。  
2. **实现 UI 层**：实现 `RichUIRenderer`、`NoopUIRenderer`、`TableStyleRegistry`，并在 CLI 初始化时注入 `ctx.ui`。  
3. **实现 Command Registry**：实现 `Command` 基类与 `CommandRegistry`，让 CLI 通过 registry 自动加载命令模块。  
4. **逐命令迁移**：按优先级迁移命令到 `commands/`，每次迁移后运行回归测试。优先迁移 `publish`、`release`、`sync`。  
5. **实现库细节**：并行实现 `metadata`、`object_store`、`transport`、`hash`、`audio_io` 的原子写入与校验逻辑。  
6. **全量回归与性能测试**：在 500+ 条数据上运行并调优。  
7. **部署切换**：替换服务器 hook 为 systemd worker 调用 CLI 的 log-only 模式。

#### 测试矩阵
- **单元测试**：UI 层渲染调用、TableSpec 注册、命令模块参数映射、库函数边界条件。  
- **集成测试**：每个命令端到端小样本测试（10 条）与全量测试（500+）。  
- **故障注入**：网络中断、远端哈希不一致、缺封面、文件名冲突、磁盘空间不足。  
- **验收标准**：interactive 模式使用 rich 渲染且不打印 JSON；log-only 模式输出 JSONL；metadata 写入原子且远端校验通过；全量运行错误率在可接受范围。

#### 交付物
- `cli.py` 主入口骨架与 Context 构建逻辑  
- `commands/` 目录每个命令的薄适配器实现样例  
- `ui/` 目录包含 `UIRenderer` 抽象、`RichUIRenderer`、`NoopUIRenderer`、`TableStyleRegistry` 与默认样式集合  
- `libgitmusic` 最小接口骨架与关键模块实现（metadata、object_store、transport、hash、audio_io、events、locking）  
- `config.example.yaml` 包含 `logs_dir`、transport、ffmpeg 参数、ui 样式覆盖项  
- 测试用例模板与小样本数据集

---

### 最后说明
本规范把“如何显示”与“如何做”彻底分离，命令定义唯一且集中，UI 可替换且样式可配置，库承担所有 I O 与一致性保证。按此规范重构能显著降低重复实现、提高可测试性并保证服务端兼容性。