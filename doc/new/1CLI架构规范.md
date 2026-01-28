Summary and Key Decisions
本报告给出一套明确、不可二义的实现规范，覆盖 CLI、核心库、事件与日志、模块职责、接口契约、执行模型、迁移与测试。核心决策已固定：采用单一运行时架构，所有业务逻辑与数据一致性由库实现（libgitmusic）；CLI 作为唯一入口负责调度、配置注入、事件解析与可视化；原有脚本一次性重构为库内步骤函数并由 CLI 直接调用。此方案保证长期可维护性、可测试性与严格标准化。

---

Architecture Overview
- Single Runtime Model  
  - CLI process 主控：命令注册、运行时配置、步骤编排、事件监听、日志持久化、REPL。  
  - libgitmusic library：实现全部核心业务逻辑与数据一致性（metadata、object store、audio I/O、hash、transport、locking、events）。  
  - Steps：库内函数，按统一签名暴露，CLI 直接调用并在同一进程/线程池内并发执行。  
- Why this model  
  - 统一错误处理与事件流；便于单元测试与集成测试；避免多种进程间通信导致的不一致与复杂性。  
- High level diagram  
  - CLI → Context → Step1 → Step2 → … → CLI (events → logs → UI)

---

CLI Specification
职责
- 读取全局配置 config.yaml 并构建运行时 Context。  
- 注册命令并定义工作流（命令在 cli.py 内定义）。  
- 调度步骤函数，管理锁、超时、并发与取消。  
- 订阅 EventEmitter，实时渲染聚合进度与最近事件，持久化事件到 logs/。  
- 提供 REPL 与一次性命令模式，最简命令触发完整链式工作流。

命令注册模型
- 每个命令由 Command 对象定义字段：  
  - name、desc、steps（函数列表）、requireslock、timeoutseconds、on_error（stop|continue|notify）、interactive。  
- 示例（概念）  
  - publish steps = [scanwork, hashandstore, compresscover, verify, sync_cache, commit]  
  - full-update steps = [publish, sync, release]（命令可嵌套调用）

运行与可视化
- 事件驱动 UI：CLI 仅渲染由步骤发出的事件（phasestart、batchprogress、item_event、result、error）。  
- 聚合进度：以阶段与批次为粒度显示总体进度；显示最近若干 item_event；结束显示 summary。  
- 中断与取消：Ctrl-C 触发取消，CLI 发出取消事件并清理锁与临时文件。  
- 日志位置：<project_root>/logs/，不纳入 Git。

---

Library Specification and Module Responsibilities
Overall rule  
库实现完整业务逻辑与校验，暴露清晰、稳定的 API。所有文件系统与远端交互通过库完成，脚本仅为库调用的薄包装（最终将移除独立脚本）。

主要模块与职责

| 模块         | 职责                                                         |
| ------------ | ------------------------------------------------------------ |
| metadata     | 管理 metadata.jsonl：加载、索引、验证、增量更新、原子写入、commit 辅助。 |
| object_store | 管理本地 cache 与远端对象：写入、路径解析、存在性检查、删除、引用计数辅助。 |
| audio_io     | 音频 I O：分离音频流、提取封面、压缩封面、嵌入标签、原子写入、本地校验。 |
| hash         | 统一音频帧哈希计算，记录 ffmpeg 版本与参数到 tooling 日志。  |
| transport    | 传输适配器抽象与实现（默认 ScpAdapter）：上传/下载、远端 sha256 校验、重试策略。 |
| events       | 事件发射器：emit(event_dict)，库在关键点调用，CLI 订阅。     |
| locking      | 文件级或进程级锁，保护 metadata 写入与关键并发操作。         |

API Contracts Examples
- MetadataManager.addorupdate(entry: dict) -> ValidationResult  
- ObjectStore.storeaudio(temppath: Path) -> str returns audio_oid  
- AudioIO.extract_cover(src: Path) -> bytes  
- HashUtils.hashaudioframes(path: Path, params: dict) -> str returns sha256:...  
- Transport.upload(local: Path, remote: str) -> RemoteResult with remote sha256 verification  
- EventEmitter.emit(event: dict) -> None

Implementation rules
- 所有写操作使用临时文件并原子替换（.tmp → mv）。  
- 上传后在远端执行 sha256sum 并比对，确保传输完整性。  
- 库内部在每个关键步骤 emit 事件（包括 tooling 信息如 ffmpeg_version）。

---

Events Logging and I O Standards
事件协议（必须遵守）
- 每个事件为 JSON-serializable 字典并包含 ts、cmd、type。  
- 主要类型：phasestart、batchprogress、item_event、log、result、error、summary。  
- batchprogress 字段示例： { "type":"batchprogress", "cmd":"publish", "phase":"synccache", "processed":120, "totalitems":508, "ratepersec":28 }  
- item_event 用于单项完成或警告，不用于持续进度条。  
- result 包含 status（ok|warn|error）、code、artifacts 列表。

I O 规则
- CLI ↔ 库：通过函数调用与返回值传递数据；事件通过 EventEmitter 输出。禁止使用 stdin/stdout 作为主通信手段在进程边界。  
- 外部工具：若调用 yt-dlp 等外部工具，库应捕获其输出并映射为事件；不允许外部工具直接写终端。  
- 日志持久化：CLI 将事件流写入 logs/<command>-TIMESTAMP.jsonl。服务器模式同时追加关键 error 到 serverreleaselog（由 config 指定）。

---

Execution Model Concurrency and Error Handling
执行模型
- CLI 在主进程内按步骤顺序执行，步骤可在线程池或协程中并发处理批次项。  
- 并发策略由步骤声明（例如 batch_size、workers），CLI 提供线程池与限流。  
- 对短任务采用批量并发并以 batch_progress 更新；对长任务允许单项进度回调。

错误策略
- 每个命令定义 on_error：stop（遇 error 停止）、continue（记录并继续）、notify（继续并在 summary 报告）。  
- 步骤遇不可恢复错误应 emit error 并抛异常；CLI 捕获并按 on_error 决策。  
- 所有错误写入日志并在服务器模式追加到 serverreleaselog。

---

Migration Plan Testing and Deliverables
迁移里程碑
1. 接口冻结 定义并审查 libgitmusic 公共 API。  
2. 库骨架实现 包含模块接口与单元测试桩。  
3. CLI 骨架实现 命令注册、步骤引擎、事件监听、日志写入。  
4. 逐步替换脚本 按优先级实现步骤函数：publish → release → sync → download → verify → cleanup。每替换一项做集成测试。  
5. 实现 transport ScpAdapter 并做端到端上传校验测试。  
6. 全量回归 在 500+ 条数据上运行并调优。  
7. 部署切换 替换服务器 hook 为 systemd worker 调用 CLI。  
8. 归档旧脚本与文档交付。

测试矩阵
- 单元测试覆盖库模块（metadata、hash、audio_io、transport）。  
- 集成测试覆盖命令链（publish→sync→release），包含小样本与全量。  
- 故障注入测试网络中断、哈希不匹配、缺封面、文件名冲突。  
- 性能测试记录 items/sec、并发瓶颈与 IO 延迟。

交付物
- repo/tools/cli.py（命令注册、运行引擎、REPL、事件解析）  
- libgitmusic 模块（audioio、metadata、objectstore、transport_scp、hash、events、locking）  
- config.example.yaml 与 command-spec.md 文档  
- 集成测试脚本与样本数据  
- 操作手册与故障排查清单

---

Operational Considerations
- 安全：SSH key 管理、最小权限运行、日志权限控制。  
- 监控：定期运行 verify_hashes 并把 summary 导出到监控系统；记录关键指标（失败率、items/sec）。  
- 日志轮转：实现日志归档策略，避免 logs/ 无限制增长。  
- 团队流程：变更 libgitmusic API 需审查；CLI 与库由同一维护组管理以避免接口漂移。