基于您提供的 `all_python_files_merged.md` 代码文件，以及《plan_cli.md》、《脚本实现规范总结.md》、《命令实现规范总结.md》和《plan.md》四个文档，我进行了全面的审查。

**审查核心结论：**

项目当前处于“新旧架构混杂”的中间状态。**最关键的问题是：您上传代码中的 `cli_wrong.py` 才是真正符合《plan_cli.md》规划的现代架构，而当前的 `cli.py` 是一个过时的“进程调用器”实现。**

以下是详细的不规范处与不符点分析：

### 一、 核心架构与规划的严重偏离

#### 1. CLI 驱动模式完全相反

- **计划要求**：CLI 内部应定义命令逻辑，将步骤作为函数调用（Steps as functions），实现高效无中间文件联动。
- **代码现状 (`cli.py`)**：当前的 `cli.py` 是一个 **Subprocess Runner**。它通过 `subprocess.Popen` 调用 `repo/work/` 下的外部脚本，依靠解析 stdout 的 JSONL 来更新 UI。这违背了“命令定义在 cli.py 内”和“库调用”的原则。
- **代码现状 (`cli_wrong.py`)**：**这才是正确的实现。** 它定义了 `Command` 类、`StepContext`，并直接调用 `libgitmusic.commands` 中的逻辑函数（如 `publish_logic`），实现了进程内的数据流转。

#### 2. 逻辑“脑裂” (Split Brain)

- **计划要求**：迁移步骤为“一次性重构”，将现有脚本改写为步骤函数或库调用。
- **现状**：
  - **库层**：`libgitmusic/commands/` 下已经有了 `publish.py` 和 `checkout.py` 的逻辑封装。
  - **脚本层**：`repo/work/publish_meta.py` 和 `repo/work/checkout.py` 依然包含完整的业务逻辑。
  - **冲突**：`cli.py` 调用脚本层，`cli_wrong.py` 调用库层。两套逻辑同时存在，维护成本加倍且容易导致行为不一致。

#### 3. 环境变量依赖 vs 对象传递

- **计划要求**：虽然脚本层通过环境变量获取路径，但在 Python 内部调用（CLI -> Library）时，应通过 Context 传递配置。
- **现状**：`cli.py` 强行注入环境变量 (`_inject_env`) 供子进程使用。这使得单元测试变得非常困难，且不符合 Python 库设计的最佳实践。

------

### 二、 具体脚本实现的规范性问题

#### 1. `import` 路径 Hack

- **问题文件**：几乎所有脚本（`cleanup_orphaned.py`, `sync_cache.py`, `download_ytdlp.py` 等）。
- **代码**：`sys.path.append(str(Path(__file__).parent.parent))`。
- **规范问题**：这是一种极不规范的“胶水代码”。这导致脚本强依赖于文件目录结构，一旦移动脚本或打包，代码就会崩溃。
- **修正建议**：应将 `repo` 视为一个 Package，通过 `python -m repo.tools.script_name` 运行，或者在 `cli.py` 中直接 import 模块。

#### 2. `sync_cache.py` 未库化

- **计划要求**：`sync` 是一条核心命令，应作为步骤调用。
- **现状**：`repo/data/sync_cache.py` 是一个独立的巨型脚本，包含参数解析、多线程逻辑和 UI 输出。
- **问题**：`cli_wrong.py`（即正确的 CLI）试图实现 `sync` 步骤，但目前的 `libgitmusic` 中缺少对应的 `commands/sync.py` 封装，导致 CLI 难以复用其逻辑。

#### 3. `verify_hashes.py` 输出格式

- **规范要求**：必须输出 JSONL 事件流，不直接显示内容。
- **现状**：虽然使用了 `EventEmitter`，但在错误处理逻辑中（L133-L157），它在构建 `entries` 列表供 CLI 显示。这虽然符合“由 CLI 渲染”的要求，但该逻辑与 `libgitmusic` 耦合度不够，还是作为一个独立脚本存在的。在 `plan_cli.md` 架构下，校验逻辑应直接调用 `HashUtils.verify_hash` 并在 CLI 层面聚合结果。

#### 4. `download_ytdlp.py` 职责越界

- **规范要求**：`download` 后自动触发 `compress` -> `verify` -> `sync` -> `commit`（在 CLI 中实现）。
- **现状**：脚本内部实现了元数据嵌入、哈希计算、封面提取等一整套逻辑。
- **问题**：这导致 `download` 脚本变得过于臃肿且难以维护。在 CLI 架构下，下载脚本应该只负责 **下载 + 基础元数据获取**，后续的压缩、校验、入库应由 CLI 的 Pipeline（管道）调度其他步骤来完成。

------

### 三、 代码细节错误与隐患

1. **`cli.py` 的异常掩盖**：
   - L197: `except Exception as exc:` 捕获了所有异常并只打印 "Event parsing error"。这会掩盖掉 Python 代码本身的逻辑错误（如 `NameError`, `AttributeError`），导致调试极其困难。
2. **`publish_meta.py` 的重复逻辑**：
   - L63: `scan_progress_callback` 是为了适配 `publish_logic` 的回调，但 `publish_meta.py` 本身又在 L128 重新实现了一遍 `execute_publish` 的逻辑（为了支持 `--no-cleanup`）。这种代码复制是维护的噩梦。
3. **Config 加载不一致**：
   - `cli.py` 读取 `config.yaml`。
   - `sync_cache.py` 依然通过 `os.environ` 读取配置。如果用户只改了 `config.yaml` 但没通过 CLI 运行脚本，脚本将无法获取最新配置。

------

### 四、 修正行动建议

为了符合《plan_cli.md》和《plan.md》的愿景，建议执行以下操作：

1. **架构扶正**：
   - **删除** 当前的 `cli.py`。
   - **重命名** `cli_wrong.py` 为 `cli.py`。
2. **库逻辑补全 (`libgitmusic`)**：
   - 创建 `libgitmusic/commands/sync.py`，将 `repo/data/sync_cache.py` 的核心逻辑（比对、上传、下载）封装为函数。
   - 创建 `libgitmusic/commands/verify.py`，封装 `repo/data/verify_hashes.py` 的逻辑。
   - 创建 `libgitmusic/commands/cleanup.py`，封装 `repo/data/cleanup_orphaned.py` 的逻辑。
3. **脚本层清理**：
   - 一旦上述逻辑被移入 `libgitmusic`，**删除** `repo/work/` 和 `repo/data/` 下对应的独立脚本，或者将它们重写为仅包含几行代码的入口文件（Import lib -> Run function）。
4. **CLI 完善**：
   - 在新的 `cli.py`（原 `cli_wrong.py`）中，补全 `sync`, `verify`, `cleanup`, `release` 等命令的 `steps` 定义，直接调用上述封装好的库函数。

这个行动方案将消除“脑裂”状态，实现真正的模块化和“一站式最简命令”目标。