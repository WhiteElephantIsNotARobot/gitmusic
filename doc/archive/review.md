通过仔细审查提供的代码文件（`all_python_files_merged.md`）与规划文档（`plan_cli.md`, `脚本实现规范总结.md`），我发现了以下代码规范性问题以及与计划严重不符的架构冲突。

特别值得注意的是，你上传的文件中包含一个 `cli_wrong.py`，但实际上 **`cli_wrong.py` 的架构设计（基于内部 Steps 函数调用）反而更符合 `plan_cli.md` 的最新要求**，而当前的 `cli.py`（基于 `subprocess` 调用外部脚本）属于旧架构或过渡方案。

以下是详细审查报告：

### 一、 代码实现规范性问题 (Python & 工程规范)

1. **不规范的模块导入方式 (sys.path hack)**
* **问题**：几乎所有脚本（如 `cleanup_orphaned.py`, `sync_cache.py`, `cli.py`）开头都使用了 `sys.path.append(str(Path(__file__).parent.parent))` 来解决导入 `libgitmusic` 的问题。
* **影响**：这是一种极其脆弱的“胶水代码”写法，依赖于文件系统的目录结构，一旦目录移动或打包分发，代码就会崩溃。
* **建议**：应将 `repo` 视为一个标准的 Python Package，通过 `setup.py` 或 `pyproject.toml` 以 Editable 模式安装 (`pip install -e .`)，或者在根目录运行模块 (`python -m repo.tools.cli`)。


2. **严重的“脑裂”架构 (Split Brain)**
* **问题**：项目中同时存在两套业务逻辑实现。
* **一套在 `libgitmusic/commands/**` (例如 `publish.py`, `checkout.py`)，这是符合库封装原则的。
* **另一套在 `repo/work/` 和 `repo/data/**` (例如 `work/publish_meta.py`, `work/checkout.py`)，这些是独立的可执行脚本。


* **现状**：当前的 `cli.py` **完全忽略了** `libgitmusic/commands/` 下封装好的逻辑，而是通过 `subprocess` 去调用 `repo/work/` 下的脚本。这导致了代码重复和维护困难。


3. **脆弱的参数传递 (Environment Variables Reliance)**
* **问题**：`cli.py` 通过 `_inject_env` 将配置注入环境变量，子脚本再从环境变量读取路径。
* **规范**：虽然规划中提到了使用环境变量，但在 Python 内部调用（特别是如果按照 `plan_cli.md` 走库调用模式）时，应直接传递对象或配置字典，而不是依赖隐式的全局环境变量，这使得单元测试变得非常困难。


4. **异常处理过于宽泛**
* **问题**：`cli.py` 中的 `_process_event_stream` 使用了裸 `except Exception as exc:`。
* **影响**：这会掩盖潜在的逻辑错误（如 `KeyError`, `AttributeError`），导致调试困难，只能看到 "Event parsing error"。



---

### 二、 与《plan_cli.md》计划不符的地方

`plan_cli.md` 是你提供的最新、最详细的架构文档，目前的 `cli.py` 实现与其存在根本性的冲突。

#### 1. 核心架构模式冲突 (最严重)

* **计划要求**：
> "Command logic inside cli.py... steps as functions."
> "把其他命令作为步骤调用... 实现高效无中间文件联动。"


* **当前代码 (`cli.py`)**：
* 完全是一个 **Process Runner**。它使用 `subprocess.Popen` 调用外部 Python 脚本。
* 数据交换依赖于解析 stdout 的 JSONL 文本，效率低且容易出错。


* **反而是 `cli_wrong.py**`：
* 实现了 `StepContext`。
* 实现了 `steps=[publish_scan, publish_process]` 这样的函数链式调用。
* **结论**：目前的 `cli.py` 是旧架构的产物，`cli_wrong.py` 才是符合 `plan_cli.md` 的代码结构。



#### 2. 音频 I/O 库的使用偏差

* **计划要求**：
> "Audio I/O Library... 负责音频流分离、音频帧哈希... 脚本通过该库调用"
> "hash_audio_frames(path, params) -> 'sha256:...'" (强调计算纯音频帧哈希)


* **当前代码 (`download_ytdlp.py`)**：
* 它在下载完成后，调用 `AudioIO.get_audio_hash(downloaded_file)`。
* 虽然调用了库，但逻辑是先下载完整文件再计算。如果为了严格的一致性（防止 ID3 标签差异影响哈希），应该确保 `get_audio_hash` 内部确实执行了 ffmpeg 流分离哈希（代码中 `hash_utils.py` 确实实现了流哈希，这点是符合的，但调用方式仍是基于文件的）。


* **当前代码 (`compress_images.py`)**：
* 它自己实现了部分逻辑，而不是完全委托给 `libgitmusic.audio`。虽然调用了 `AudioIO.compress_cover`，但文件读写和哈希计算逻辑散落在脚本中。



#### 3. 工作流逻辑的位置错误

* **计划要求**：
> "一条命令触发整条工作流... 命令内部按步骤顺序执行"


* **当前代码 (`cli.py`)**：
* `publish` 命令的逻辑被硬编码在 `run_command` 方法的 `if name == "publish":` 巨大分支块中。
* 它手动编排了 `run_script("publish_meta.py")` -> `run_script("compress_images.py")` -> `run_script("verify_hashes.py")` 等。
* **不符点**：根据计划，这些应该是在 `Command` 对象定义时的 `steps` 列表中注册的，而不是在 `run_command` 里写死 `if/else` 逻辑。



#### 4. 配置管理不符

* **计划要求**：
> "Remote args from config.yaml"


* **当前代码 (`sync_cache.py`)**：
* 脚本内部还是大量依赖 `os.environ.get`。虽然 CLI 读取了 yaml 并注入了 env，但底层脚本没有直接支持读取 yaml 的能力，导致它们无法独立运行（必须依赖 CLI 注入环境）。



#### 5. `checkout` 命令的实现

* **计划要求**：
> "Conflict protection... if conflict... must call publish --preview and exit"


* **当前代码 (`checkout.py`)**：
* 脚本检测到冲突后，只是 `EventEmitter.result("conflict", ...)` 然后退出。
* `cli.py` 捕获到 conflict 状态后，再发起 `publish --preview`。
* **判定**：这虽然实现了功能，但逻辑割裂。`cli_wrong.py` 的设计（共享 Context）能更好地解决这个问题。



---

### 三、 总结与建议

**目前的 `cli.py` 是一个过渡期的“胶水脚本”，它虽然能跑通流程，但完全没有利用 `libgitmusic` 进行重构，这与 `plan_cli.md` 的核心重构目标（"一次性重构所有脚本"）背道而驰。**

**修正建议：**

1. **废弃当前的 `cli.py`，启用并完善 `cli_wrong.py**`：
* `cli_wrong.py` 的架构（Command/Steps/Context）才是正确的。
* 需要将 `cli_wrong.py` 重命名为 `cli.py`。


2. **迁移逻辑到 `libgitmusic**`：
* 不要在 CLI 里调用 `repo/work/*.py`。
* 将 `repo/work/publish_meta.py` 的核心逻辑（扫描、比对）移动到 `libgitmusic/commands/publish.py` 或作为 `cli.py` 内部的 Step 函数。
* 将 `repo/data/sync_cache.py` 改写为 `libgitmusic/commands/sync.py`。


3. **标准化入口**：
* 确保所有操作都通过 `libgitmusic` 的 API 进行，CLI 层只负责参数解析、UI 渲染和流程编排。



**如果你希望按照 `plan_cli.md` 继续开发，请明确指示我基于 `cli_wrong.py` 的架构进行后续的代码生成和修复，而不是修补当前的 `cli.py`。**
