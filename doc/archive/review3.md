基于你提供的源代码、架构设计文档（`plan_cli.md`）以及规范文档（`命令实现规范总结.md`、`脚本实现规范总结.md`），我对当前代码进行了全面的审查。

以下是审查报告：

### 总体评价

**当前状态：B+ (架构成型，核心逻辑完备，但在I/O接口标准和工作流集成上存在偏差)**

代码结构已经高度模块化，`libgitmusic` 核心库的封装非常扎实，成功实现了业务逻辑与底层实现的解耦。`tools/cli.py` 成功实现了作为命令驱动器的角色。

然而，**脚本层（Wrapper Scripts）与 CLI 之间的交互方式**与《脚本实现规范总结》中定义的“零选项、全Stdin输入”标准存在显著偏差。此外，部分复合工作流（如 `release` 前的自动同步）尚未在 CLI 中串联。

---

### 一、 架构与规范符合度审查

| 审查维度 | 规范要求 | 当前实现 | 状态 |
| --- | --- | --- | --- |
| **库封装** | 业务逻辑封装在 `libgitmusic` | ✅ 完美。`libgitmusic` 包含了所有核心逻辑。 | <font color="green">**通过**</font> |
| **零硬编码** | 不使用硬编码路径 | ✅ `cli.py` 注入环境变量，脚本通过 `os.environ` 获取。 | <font color="green">**通过**</font> |
| **输出标准** | JSONL 事件流 | ✅ `EventEmitter` 类被广泛使用，输出符合规范。 | <font color="green">**通过**</font> |
| **输入标准** | **严格 Stdin JSONL，零选项** | ❌ **偏差**。wrapper 脚本（如 `repo/data/sync_cache.py`）仍大量使用 `argparse` 定义命令行参数。 | <font color="red">**偏差**</font> |
| **原子操作** | 使用 `.tmp` 中转 | ✅ `AudioIO.atomic_write` 和 `MetadataManager.save_all` 均实现了原子写。 | <font color="green">**通过**</font> |
| **错误处理** | CLI 决定停止/继续 | ✅ `Command` 类定义了 `on_error` 策略，符合设计。 | <font color="green">**通过**</font> |

---

### 二、 核心命令实现审查

#### 1. `publish` (发布)

* **规范**：分析变更 -> 预览 -> 压缩 -> 校验 -> 同步 -> 提交。
* **实现**：
* `libgitmusic/commands/publish.py` 实现了扫描和执行逻辑。
* `work/publish_meta.py` 实现了根据参数进行预览或执行。


* **问题**：
* **工作流缺失**：`cli.py` 中的 `publish` 命令步骤仅包含 `scan` 和 `process`。**缺少**自动触发的 `compress_images`、`verify`、`sync` 步骤。
* **回收站机制**：代码使用了 `send2trash`，符合“移动到回收站”的隐含要求（优于直接删除）。



#### 2. `checkout` (检出)

* **规范**：冲突保护（无 `-f` 退出并提示）；非法字符替换。
* **实现**：
* `checkout_logic` 实现了过滤。
* `cli.py` 的 `checkout_execute` 步骤中有 `if not force and any(...)` 的冲突检查。
* `AudioIO.sanitize_filename` 实现了字符替换。


* **通过**：逻辑基本符合规范。

#### 3. `release` (生成成品)

* **规范**：增量生成；前置 `git pull`；自动执行 `sync` -> `verify` -> `release`。
* **实现**：
* `release_logic` 实现了基于 `METADATA_HASH` 的增量检测。
* **严重缺失**：`cli.py` 的 `release` 命令步骤列表仅有 `[release_step]`。**缺少** `sync` 和 `verify` 的前置步骤调用。
* **缺失**：代码中未见 `git pull` 的调用逻辑（规范要求脚本或CLI层执行）。



#### 4. `sync` (同步)

* **规范**：双向增量；重试与超时；从配置读取参数。
* **实现**：
* `TransportAdapter` 实现了 `scp` 上传下载。
* `execute_sync` 实现了线程池并发和重试。
* `cli.py` 正确从 `config` 读取了 user/host。


* **通过**：实现非常扎实。

#### 5. `download` (下载)

* **规范**：捕获 `yt-dlp` 原始输出并转发；批处理；自动工作流。
* **实现**：
* **严重偏差**：`download_audio` 使用 `subprocess.run(..., capture_output=True)`。这会**阻塞**直到下载完成，导致无法实时显示 `yt-dlp` 的下载进度。必须改为 `subprocess.Popen` 并逐行读取 stdout。
* **工作流缺失**：下载后未自动触发压缩、校验和同步。



---

### 三、 关键问题与修正建议

#### 1. 输入接口标准化偏差（Critical）

**问题**：`脚本实现规范总结.md` 明确要求“所有脚本全部使用标准输入接收配置，零选项”。但当前的 wrapper 脚本（如 `repo/work/publish_meta.py`）使用 `argparse` 接收命令行参数，`cli.py` 也是通过构建参数列表来调用它们。
**建议**：

* **方案 B（严格遵守规范）**：重写所有 `repo/tools/*.py` 和 `repo/work/*.py`，移除 `argparse`，改为 `cfg = json.load(sys.stdin)`，并修改 `cli.py` 将参数打包为 JSON 写入子进程的 stdin。

#### 2. 复合工作流未串联（High）

**问题**：`cli.py` 中 `publish` 和 `release` 命令目前只执行了核心动作，没有按规范串联其他步骤。
**修正**：在 `cli.py` 的 `Command` 注册中，将步骤列表串联起来。

* **Publish**: `[publish_scan, publish_process, compress_step, verify_step, sync_step]` (需注意步骤间的参数传递上下文)。
* **Release**: `[sync_step, verify_step, release_step]`。

#### 3. 实时进度丢失（Medium）

**问题**：`tools/download_ytdlp.py` 和 `libgitmusic/audio.py` (Hash计算) 使用了阻塞式的 `subprocess.run`。
**修正**：

* 对于耗时操作（下载、长音频哈希），使用 `subprocess.Popen`。
* 读取 stdout，解析 `yt-dlp` 的进度输出或 ffmpeg 的输出，并转换为 `item_event` 发送给 CLI。

#### 4. 缺少行号输出

**问题**：`analyze` 命令规范要求输出包含“条目行号”，但在 `MetadataManager` 和 `analyze_logic` 中，读取 JSONL 时未保留行号信息。
**修正**：`MetadataManager.load_all()` 应该返回带元信息的对象（例如 `_line_no` 字段），或者在加载时由 enumerate 生成并注入。

---

### 四、 总结与下一步

代码质量很高，核心库设计优秀，通过了大部分功能性审查。主要差距在于**CLI对复合命令的编排**以及**部分脚本的I/O细节**。

**建议执行的下一步：**

1. **修正 `cli.py` 中的命令步骤链**：将 `sync`, `verify`, `compress` 等步骤真正加入到 `publish` 和 `release` 的 `steps` 列表中。
2. **重构 `download_audio**`：改用 `Popen` 实现实时进度转发。
3. **决定 I/O 策略**：修改脚本以强制使用 Stdin JSON。

**你想让我先帮你修正 `cli.py` 中的工作流串联，还是先处理 `download` 模块的实时进度问题？**