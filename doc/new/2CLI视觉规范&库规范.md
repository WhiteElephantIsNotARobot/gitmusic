概要
这份最终报告明确规定 输出行为与视觉规范（CLI 端）和 库层职责与接口契约（libgitmusic）。核心决策已固定：CLI 在交互模式下只渲染人类可读输出，不在 REPL 打印 JSON；所有结构化事件由库通过 EventEmitter 写入日志文件（JSONL），CLI 仅在 log-only 模式将 JSONL 输出到 stdout 以便系统服务捕获。报告覆盖终端输出格式、进度条与表格设计、日志策略、配置与启动、库模块职责、关键 API 签名、数据校验规则、原子写入与传输校验、以及迁移与测试要点。

---

CLI 输出设计 单列持续滚动且视觉丰富

输出原则
- 单列单块持续滚动：所有输出按时间顺序追加到终端，启动时不覆盖历史内容，滚动由终端（PowerShell、bash、Windows Terminal）管理。  
- 交互模式与 log-only 模式分离：  
  - interactive：使用 rich 渲染进度条、表格、彩色标签与 summary，但不打印 JSON。  
  - log-only：用于服务端，CLI 将事件以 JSONL 写入 stdout（每行一条事件），便于 systemd 或容器捕获。  
- 事件不可见化：库内部产生日志事件（JSON 对象）并写入日志文件；CLI 在 interactive 模式不把这些 JSON 直接打印到终端，只渲染人类友好的文本/表格/进度。

主输出元素与示例行
- Header 行（每阶段开始或命令启动时）  
  - 格式示例（人类可读）：  
    2026-01-20T15:10:00Z  |  publish  |  phase=scan_work  |  total=120  |  status=running  
  - 渲染：使用 rich.panel.Panel 或 Console.rule，状态用颜色标识（running=cyan, ok=green, warn=yellow, error=red）。

- 主进度行（聚合进度）  
  - 使用 rich.progress.Progress，列包含 Description, BarColumn, Percentage, TextColumn(items/sec), TimeRemainingColumn。  
  - 示例输出（覆盖同一行或追加新行，取决终端能力）：  
    PROGRESS  120/508  23%  |  28 it/s  |  ETA 00:00:30

- 最近事件流（逐行追加）  
  - 每个 item_event 输出一行，格式：15:10:05  [OK]  Artist - Title.mp3  —  wrote release。  
  - 颜色：OK=green, WARN=yellow, ERR=red。保留最近若干条在屏幕上，历史由终端滚动查看。

- 表格渲染（仅在需要时输出）  
  - 使用 rich.table.Table，列数 ≤ 6，单元仅一行文本，自动换行或截断。  
  - publish 预览表格列：# change title artists diff summary。  
  - release 结果表格列：# file status duration artifact。  
  - verify 报告表格列：file expectedoid actualoid status。  
  - 表格支持分页命令（例如 --line 或 d <line> 在 REPL 中触发），但默认输出为单页或截断以保持滚动体验。

- Summary 面板（命令结束）  
  - 使用 rich.panel.Panel 显示：processed、errors、warnings、elapsed、rate、logfile。  
  - 同时写入一行 JSON summary 到日志文件（便于机器消费），但不在 REPL 打印该 JSON。

进度策略与短任务处理
- 批量/阶段粒度：对大量短任务（例如 500+ 条），只显示阶段级 batch_progress（processed/total、items/sec、ETA），避免为每首歌创建短寿命进度条。  
- 短任务合并：若单项耗时 < 1s，不创建单项进度条；仅在 item_event 中记录完成。  
- 长任务单项进度：若步骤发出 progress 事件（0–100%），CLI 临时创建单项进度 task 并在完成后移除。  
- ETA 计算：基于滑动窗口速率（最近 N 秒 items/sec）估算剩余时间并显示。

---

日志策略 与配置

日志文件与 JSONL 事件
- 日志目录：默认 <projectroot>/logs/，可在 config.yaml 中配置 logsdir 或通过环境变量 GITMUSIC_LOGS 覆盖。  
- 事件写入：库通过 EventEmitter.emit() 将事件写入当前运行的日志文件（JSONL，每行一条事件）。事件包含 ts、cmd、type 及具体字段。  
- CLI 行为：  
  - interactive：CLI 订阅事件流并渲染人类友好输出；事件 JSON 仅写入日志文件，不打印到终端。  
  - log-only：CLI 将事件 JSONL 写入 stdout（每行一条），并可同时写入日志文件。适合 systemd、容器或集中化日志采集。  
- 日志命名：<logs_dir>/<command>-YYYYMMDD-HHMMSS.jsonl。CLI 在运行开始创建文件并追加事件。  
- 日志轮转：建议实现按大小或按天轮转策略并保留最近 N 次运行日志。

配置与启动
- config 路径：默认项目根（repo 同级）；可通过环境变量 GITMUSIC_CONFIG 指定自定义路径。  
- 日志路径覆盖：GITMUSICLOGS 或 --logs CLI 参数覆盖 config.yaml 中 logsdir。  
- 服务端运行：systemd unit 启动 CLI 时使用 --log-only，systemd 捕获 stdout；若需要文件持久化，CLI 同时写入 logs_dir。

---

libgitmusic 详细架构与接口契约

总体职责
- 库承担全部业务逻辑与数据一致性：metadata 管理、object store、audio I/O、hash、transport（scp）、locking、事件发射。  
- CLI 仅负责渲染与编排：不在库中实现终端渲染或交互提示。

模块与关键接口
| 模块        | 职责                                               | 关键接口（示例签名）                                         |
| ----------- | -------------------------------------------------- | ------------------------------------------------------------ |
| metadata    | 索引加载、字段校验、增量更新、原子写入             | loadindex() -> Index validateentry(entry: dict) -> ValidationResult addorupdate(entry: dict) -> None commit(message: str) -> CommitResult |
| objectstore | 本地 cache 管理、对象写入、路径解析、幂等写入      | storeaudio(temppath: Path) -> StoreResult 返回 {audiooid, localpath, uploaded} getpath(audiooid: str) -> Path exists(audiooid: str) -> bool |
| audioio     | 分离音频流、提取封面、压缩封面、嵌入标签、原子写入 | extractaudio(src: Path, dst: Path) -> None extractcover(src: Path) -> bytes compresscover(bytes, targetsize) -> bytes embedtagsandwrite(srcaudio, metadata, coverbytes, out_path) -> None |
| hash        | 统一音频帧哈希计算并记录 tooling                   | hashaudioframes(path: Path, params: dict) -> str 返回 sha256:... 并写 tooling 日志 |
| transport   | 传输抽象与 ScpAdapter 实现                         | upload(local: Path, remote: str) -> RemoteResult 包含远端 sha256 download(remote: str, local: Path) -> None |
| events      | 事件发射器                                         | emit(event: dict) -> None（写入日志文件）                    |
| locking     | 写锁管理                                           | acquire(name: str, timeout: int) -> LockHandle release(handle) |

返回结构与异常
- 返回对象：关键函数返回结构化对象（例如 StoreResult、RemoteResult），包含状态、路径、oid、错误信息等。  
- 异常类型：定义明确异常类（ValidationError, TransportError, IOError, ConflictError），库在抛出前 emit 对应 error 事件。CLI 捕获并按命令 on_error 策略处理。

---

数据一致性 校验与原子操作

Metadata 写入规则
- 字段校验（必须）：
  - audio_oid 匹配 ^sha256:[0-9a-f]{64}$。  
  - title 非空字符串（清理非法字符后长度 > 0）。  
  - artists 非空数组，元素非空字符串。  
  - date 若存在必须为 YYYY-MM-DD。  
  - created_at 必须为 UTC ISO8601 时间戳。  
- 空值策略：禁止写入空字符串；缺失字段显式为 null 或省略。  
- 字段顺序：写入 metadata.jsonl 时按约定顺序（便于 diff 与审计）。

OID 与对象一致性
- 本地校验：若 audio_oid 指向本地 cache，库重算哈希并比对；不匹配 emit error 并抛异常。  
- 远端校验：上传后 Transport.upload 在远端执行 sha256sum 并返回远端哈希；库比对本地与远端哈希，若不一致重试或报错。  
- 幂等性：若对象已存在且哈希匹配，store_audio 跳过实际写入并返回成功。  
- 引用完整性：MetadataManager 能生成引用表用于 cleanup，避免误删被引用对象。

原子写入与锁
- 写入流程：写入 metadata.jsonl 或 release 文件时先写入 .tmp，完成后 mv 覆盖目标文件。  
- 锁保护：写入前获取 LockManager 锁（文件锁或进程锁），写入后释放并 emit result。  
- 并发策略：CLI 在需要写入的命令前统一获取锁，避免并发写入冲突。

---

传输适配 ScpAdapter 细节
- 上传流程：本地写 .tmp → scp 上传到远端临时路径 → 远端执行 sha256sum → 比对成功后 mv 到目标目录 → 设置权限。  
- 重试策略：指数退避重试，重试次数与超时由 config 控制。  
- 幂等上传：若远端已存在且哈希匹配，跳过移动步骤并返回成功。  
- 安全：使用 SSH key，私钥由运维管理，不放入 config。

---

测试 验收与迁移要点

测试矩阵
- 单元测试：metadata 校验、hash、audio_io、transport、locking。  
- 集成测试：publish→sync→release 流程，小样本（10 条）与全量（500+）测试。  
- 故障注入：网络中断、哈希不匹配、缺封面、文件名冲突。  
- 性能测试：记录 items/sec、并发瓶颈、IO 延迟。

验收标准
- 视觉：interactive 模式使用 rich 渲染进度条、表格与错误高亮，输出为单列持续滚动且美观。  
- 日志：log-only 模式输出 JSONL，systemd 能直接捕获并转发。  
- 一致性：metadata 写入通过单元测试，object_store 上传后远端 sha256 校验通过。  
- 稳定性：全量 500+ 运行成功，错误率在可接受范围并记录性能指标。

迁移步骤（建议顺序）
1. 冻结 libgitmusic 公共 API。  
2. 实现 EventEmitter 与日志持久化（JSONL）。  
3. 实现 CLI 渲染骨架（rich）与 log-only 模式。  
4. 实现 MetadataManager 与 LockManager 并编写单元测试。  
5. 实现 Transport ScpAdapter 并做端到端上传校验测试。  
6. 实现 AudioIO 基本功能并完成 publish 端到端流程测试。  
7. 全量回归与性能调优，部署 systemd worker 使用 --log-only。

---

结语
这份报告把 单列持续滚动的终端输出 与 富视觉（rich 进度条、表格、高亮） 结合起来，同时把所有结构化事件限定为日志文件（JSONL）或 log-only stdout，确保服务端兼容性与运维可观测性。库层承担全部业务与校验责任，接口稳定且可测试。