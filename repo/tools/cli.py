import sys
import os
import json
import time
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Iterator, Union
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers.shell import BashLexer

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.audio import AudioIO
from libgitmusic.hash_utils import HashUtils
from libgitmusic.locking import LockManager
from libgitmusic.transport import TransportAdapter
from libgitmusic.context import Context, create_context
from libgitmusic.commands import publish as publish_cmd
from libgitmusic.commands import checkout as checkout_cmd
from libgitmusic.commands import sync as sync_cmd
from libgitmusic.commands import verify as verify_cmd
from libgitmusic.commands import cleanup as cleanup_cmd
from libgitmusic.commands import release as release_cmd
from libgitmusic.commands import analyze as analyze_cmd
from libgitmusic.commands import download as download_cmd
from libgitmusic.commands import compress_images as compress_cmd
from libgitmusic.git import git_commit_and_push, git_pull

console = Console()


class Command:
    """命令定义类，支持步骤函数和流式管道"""

    def __init__(
        self,
        name: str,
        desc: str,
        steps: Optional[List[Callable]] = None,
        requires_lock: bool = False,
        timeout_seconds: int = 3600,
        on_error: str = "stop",
    ):  # stop, continue, notify
        self.name = name
        self.desc = desc
        self.steps = steps or []
        self.requires_lock = requires_lock
        self.timeout_seconds = timeout_seconds
        self.on_error = on_error


class StepContext:
    """步骤执行上下文，存储状态和数据流"""

    def __init__(
        self,
        command_name: str,
        args: List[str],
        config: Dict,
        metadata_mgr: MetadataManager,
        object_store: ObjectStore,
        lock_manager: LockManager,
        context: Optional["Context"] = None,
        log_only: bool = False,
    ):
        self.command_name = command_name
        self.args = args
        self.config = config
        self.metadata_mgr = metadata_mgr
        self.object_store = object_store
        self.lock_manager = lock_manager
        self.context = context
        self.log_only = log_only
        self.artifacts = {}
        self.start_time = time.time()

    def elapsed(self) -> float:
        """获取经过的时间（秒）"""
        return time.time() - self.start_time


class GitMusicCLI:
    """GitMusic CLI 主类，支持步骤函数和流式管道"""

    def __init__(self, config_path: Optional[str] = None, log_only: bool = False):
        self.repo_root = Path(__file__).parent.parent

        # 确定项目根目录（保持向后兼容）
        if config_path:
            config_file = Path(config_path)
            if config_file.is_absolute():
                project_root = config_file.parent
            else:
                project_root = (self.repo_root.parent / config_path).parent
        else:
            project_root = self.repo_root.parent

        # 构建配置文件路径（如果未提供则使用默认）
        if config_path is None:
            config_path = str(project_root / "config.yaml")

        # 创建上下文（包含所有路径和配置）
        self.context = create_context(config_path)
        self.project_root = self.context.project_root
        self.config = self.context.config

        # 修正 metadata_file 路径（保持向后兼容，默认使用 repo/metadata.jsonl）
        if self.context.metadata_file == self.project_root / "metadata.jsonl":
            self.context.metadata_file = self.repo_root / "metadata.jsonl"

        # 日志配置
        self.log_only = log_only
        from libgitmusic.events import EventEmitter

        EventEmitter.setup_logging(
            logs_dir=self.context.logs_dir, log_only=self.log_only
        )

        # 初始化核心组件（使用上下文）
        self.metadata_mgr = MetadataManager(self.context)
        self.object_store = ObjectStore(self.context)
        self.lock_manager = LockManager(self.context)

        # REPL会话（延迟初始化）
        self.session = None

        # 命令注册表
        self.commands: Dict[str, Command] = {}
        self._register_all_commands()

        # 事件日志
        self.event_log = []
        # 最近事件（用于滚动显示）
        self.recent_events = []
        # 统计信息
        self.summary_stats = {}

    def _inject_env(self):
        """注入环境变量供子进程使用"""
        os.environ["GITMUSIC_WORK_DIR"] = str(self.context.work_dir)
        os.environ["GITMUSIC_CACHE_ROOT"] = str(self.context.cache_root)
        os.environ["GITMUSIC_METADATA_FILE"] = str(self.context.metadata_file)
        transport = self.context.transport_config
        os.environ["GITMUSIC_REMOTE_USER"] = transport.get("user", "")
        os.environ["GITMUSIC_REMOTE_HOST"] = transport.get("host", "")
        os.environ["GITMUSIC_REMOTE_DATA_ROOT"] = transport.get("remote_data_root", "")

    def register_command(
        self,
        name: str,
        desc: str,
        steps: Optional[List[Callable]] = None,
        requires_lock: bool = False,
        timeout_seconds: int = 3600,
        on_error: str = "stop",
    ):
        """注册命令"""
        self.commands[name] = Command(
            name=name,
            desc=desc,
            steps=steps or [],
            requires_lock=requires_lock,
            timeout_seconds=timeout_seconds,
            on_error=on_error,
        )

    def run_script(self, script_path: Path, args: List[str], phase_name: str) -> int:
        """运行脚本并捕获事件流"""
        self._inject_env()

        if not script_path.is_absolute():
            script_path = self.repo_root / script_path

        cmd = [sys.executable, str(script_path)] + args
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        return self._process_event_stream(process, phase_name)

    def _process_event_stream(self, process: subprocess.Popen, phase_name: str) -> int:
        """处理事件流并显示进度"""
        # log-only模式：直接输出原始JSONL，不渲染进度
        if self.log_only:
            if process.stdout is None:
                process.wait()
                return process.returncode
            for line in process.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                # 直接输出到stdout
                try:
                    sys.stdout.buffer.write(line.encode("utf-8"))
                    sys.stdout.buffer.write(b"\n")
                    sys.stdout.buffer.flush()
                except:
                    print(line, flush=True)
                # 尝试解析JSON，过滤敏感数据后写入日志文件
                try:
                    event = json.loads(line)
                    from libgitmusic.events import EventEmitter

                    filtered_event = EventEmitter._filter_sensitive_data(event)
                    json_str = json.dumps(filtered_event, ensure_ascii=False)
                    if EventEmitter._log_file is not None:
                        EventEmitter._log_file.write(json_str + "\n")
                        EventEmitter._log_file.flush()
                except json.JSONDecodeError:
                    # 非JSON行，忽略或写入原始行？
                    pass
            process.wait()
            return process.returncode

        # 正常模式：使用rich进度渲染
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TextColumn("[progress.speed]{task.speed:>6.1f} it/s"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = None

            if process.stdout is None:
                process.wait()
                return process.returncode

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                    self.event_log.append(event)
                    etype = event.get("type")

                    if etype == "phase_start":
                        task = progress.add_task(
                            event.get("phase", phase_name),
                            total=event.get("total_items", 0),
                        )
                    elif etype == "batch_progress" and task is not None:
                        progress.update(task, completed=event.get("processed", 0))
                    elif etype == "item_event":
                        # 规范item_event输出格式：时间 [状态] 文件 - 操作
                        timestamp = event.get("ts", "")
                        if timestamp:
                            # 提取时间部分（HH:MM:SS）
                            try:
                                time_part = timestamp.split("T")[1].split(".")[0][:8]
                            except:
                                time_part = (
                                    timestamp[:8] if len(timestamp) >= 8 else timestamp
                                )
                        else:
                            time_part = datetime.now().strftime("%H:%M:%S")

                        status = event.get("status", "").upper()
                        item_id = event.get("id", "")
                        operation = event.get("operation", "")

                        # 根据状态设置颜色
                        if status == "OK":
                            status_color = "[green]"
                        elif status == "WARN":
                            status_color = "[yellow]"
                        elif status == "ERROR":
                            status_color = "[red]"
                        else:
                            status_color = "[white]"

                        # 格式化输出
                        if operation:
                            progress.console.print(
                                f"{time_part} {status_color}[{status}][/] {item_id} — {operation}"
                            )
                        else:
                            progress.console.print(
                                f"{time_part} {status_color}[{status}][/] {item_id}"
                            )
                    elif etype == "log":
                        level = event.get("level", "info")
                        msg = event.get("message", "")
                        if level == "error":
                            progress.console.print(f"[red]ERROR: {msg}[/red]")
                        elif level == "warn":
                            progress.console.print(f"[yellow]WARN: {msg}[/yellow]")
                        else:
                            progress.console.print(f"[blue]LOG: {msg}[/blue]")
                    elif etype == "error":
                        progress.console.print(
                            f"[bold red]CRITICAL ERROR: {event.get('message')}[/bold red]"
                        )
                    elif etype == "result":
                        status = event.get("status", "unknown")
                        msg = event.get("message", "")
                        if status == "error":
                            progress.console.print(f"[red]RESULT ERROR: {msg}[/red]")
                        elif status == "warn":
                            progress.console.print(
                                f"[yellow]RESULT WARN: {msg}[/yellow]"
                            )
                        else:
                            progress.console.print(f"[green]RESULT OK: {msg}[/green]")

                except json.JSONDecodeError:
                    if line:
                        progress.console.print(f"[dim]{line}[/dim]")
                except Exception as exc:
                    progress.console.print(
                        f"[red]Event parsing error: {str(exc)}[/red]"
                    )

            process.wait()
            return process.returncode

    def run_pipe(
        self, source_step: Callable, sink_step: Callable, ctx: StepContext
    ) -> Iterator[Dict]:
        """
        运行管道：source_step的输出作为sink_step的输入

        Args:
            source_step: 源步骤函数，返回迭代器
            sink_step: 目标步骤函数，接受迭代器输入
            ctx: 执行上下文

        Returns:
            输出迭代器
        """
        # 获取源步骤的输出
        source_output = source_step(ctx, None)

        # 如果source_output是迭代器，直接传递给sink_step
        if hasattr(source_output, "__iter__"):
            return sink_step(ctx, source_output)
        else:
            # 否则包装为单元素迭代器
            def single_item():
                if source_output is not None:
                    yield source_output

            return sink_step(ctx, single_item())

    def _register_all_commands(self):
        """注册所有命令"""

        # 1. publish命令 - 发布本地改动
        def publish_scan(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """扫描工作目录，分析变更"""
            # 解析参数
            changed_only = False
            preview = False
            i = 0
            args = ctx.args
            while i < len(args):
                if args[i] == "--changed-only":
                    changed_only = True
                    i += 1
                elif args[i] == "--preview":
                    preview = True
                    i += 1
                elif args[i] in ["-h", "--help"]:
                    # 帮助已在run_command中处理
                    i += 1
                else:
                    i += 1

            # 定义进度回调
            total_files = [0]  # 使用列表以便在回调中修改
            processed_files = [0]

            def progress_callback(current, total=None):
                if total is not None:
                    if total_files[0] == 0:
                        total_files[0] = total
                        EventEmitter.phase_start("scan", total_items=total)
                    processed_files[0] = current
                    EventEmitter.batch_progress("scan", current, total_files[0])
                else:
                    # 单项进度（用于execute_publish）
                    pass

            # 调用库函数
            items, error_msg = publish_cmd.publish_logic(
                ctx.metadata_mgr,
                changed_only=changed_only,
                progress_callback=progress_callback,
            )

            if error_msg:
                error_detail = {"error_type": "scan_failed", "message": error_msg}
                EventEmitter.error(f"扫描失败: {error_msg}", error_detail)
                # publish命令使用stop策略，抛出异常
                raise RuntimeError(f"扫描失败: {error_msg}")

            # 如果是预览模式，输出结果但不执行
            if preview:
                # 显示publish预览表格（仅在非log-only模式）
                if items and not ctx.log_only:
                    table = Table(title="Publish 预览", show_lines=True)
                    table.add_column("#", style="cyan", justify="right")
                    table.add_column("change", style="yellow")
                    table.add_column("title", style="white")
                    table.add_column("artists", style="magenta")
                    table.add_column("diff", style="dim")
                    table.add_column("summary", style="green")

                    for i, item in enumerate(items, 1):
                        change = item.get("change", "new")
                        title = item.get("title", "")[:30]
                        artists = ", ".join(item.get("artists", []))[:30]
                        diff = item.get("diff", "")[:20]
                        summary = item.get("summary", "")[:30]

                        table.add_row(str(i), change, title, artists, diff, summary)

                    console.print(table)

                # 转换items为可序列化格式
                def make_serializable(obj):
                    if isinstance(obj, Path):
                        return str(obj)
                    elif isinstance(obj, dict):
                        return {k: make_serializable(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [make_serializable(v) for v in obj]
                    else:
                        return obj

                serializable_items = make_serializable(items)
                EventEmitter.result(
                    "preview",
                    message=f"发现 {len(items)} 个待处理项（预览模式）",
                    artifacts={"items": serializable_items},
                )
                return iter([])

            # 将items转换为迭代器
            for item in items:
                yield item

            EventEmitter.result("ok", message=f"扫描完成，发现 {len(items)} 个待处理项")

        def publish_process(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """处理扫描结果，存储对象并更新元数据"""
            items = list(input_iter)
            if not items:
                EventEmitter.result("ok", message="没有需要处理的项")
                return iter([])

            # 定义进度回调
            processed = [0]
            total = len(items)
            EventEmitter.phase_start("process", total_items=total)

            def progress_callback(filename):
                processed[0] += 1
                EventEmitter.batch_progress("process", processed[0], total)
                EventEmitter.item_event(filename, "processing", "")

            # 调用库函数
            try:
                publish_cmd.execute_publish(
                    ctx.metadata_mgr,
                    items,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                error_msg = f"处理失败: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e), "items_count": len(items)})
                # publish命令使用stop策略，抛出异常
                raise RuntimeError(error_msg) from e

            # 存储处理过的audio_oid供后续步骤使用
            ctx.artifacts["processed_audio_oids"] = [
                item["audio_oid"] for item in items
            ]

            EventEmitter.result("ok", message=f"处理完成 {len(items)} 个项")
            return iter([])

        # 2. checkout命令 - 检出音乐到工作目录
        def checkout_filter(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """根据参数过滤元数据条目"""
            args = ctx.args
            query = ""
            missing_fields = []
            limit = 0
            force = False
            search_field = None
            line = None

            # 解析参数
            i = 0
            while i < len(args):
                if args[i] == "--query" and i + 1 < len(args):
                    query = args[i + 1]
                    i += 2
                elif args[i] == "--missing" and i + 1 < len(args):
                    missing_fields = args[i + 1].split(",")
                    i += 2
                elif args[i] in ["-f", "--force"]:
                    force = True
                    ctx.artifacts["force"] = True
                    i += 1
                elif args[i] == "--limit" and i + 1 < len(args):
                    limit = int(args[i + 1])
                    ctx.artifacts["limit"] = limit
                    i += 2
                elif args[i] in ["--search-field", "-s"] and i + 1 < len(args):
                    search_field = args[i + 1]
                    i += 2
                elif args[i] in ["--line", "-l"] and i + 1 < len(args):
                    line = args[i + 1]
                    i += 2
                else:
                    i += 1

            # 调用库函数进行过滤
            filtered = checkout_cmd.checkout_logic(
                ctx.metadata_mgr,
                query=query,
                missing_fields=missing_fields if missing_fields else None,
                limit=limit,
                search_field=search_field,
                line=line,
            )

            EventEmitter.phase_start("checkout_filter", total_items=len(filtered))

            for i, entry in enumerate(filtered):
                EventEmitter.batch_progress("checkout_filter", i + 1, len(filtered))
                yield entry

            EventEmitter.result("ok", message=f"过滤到 {len(filtered)} 个条目")

        def checkout_execute(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """执行检出操作"""
            items = list(input_iter)
            if not items:
                EventEmitter.result("ok", message="没有需要检出的条目")
                return iter([])

            force = ctx.artifacts.get("force", False)

            # 检查工作目录冲突
            work_dir = self.context.work_dir
            if not force and any(work_dir.glob("*.mp3")):
                EventEmitter.error(
                    "工作目录非空",
                    {"suggestion": "使用 -f 强制检出"},
                )
                return iter([])

            # 定义进度回调
            processed = [0]
            total = len(items)
            EventEmitter.phase_start("checkout_execute", total_items=total)

            def progress_callback(filename):
                processed[0] += 1
                EventEmitter.batch_progress("checkout_execute", processed[0], total)
                EventEmitter.item_event(filename, "checked_out", "")

            # 调用库函数
            results = checkout_cmd.execute_checkout(
                self.project_root,
                items,
                force=force,
                progress_callback=progress_callback,
            )

            # 处理结果
            for filename, status in results:
                if status == "success":
                    EventEmitter.item_event(filename, "success", "检出成功")
                elif status == "skipped":
                    EventEmitter.item_event(filename, "skipped", "文件已存在")
                else:
                    EventEmitter.error(f"检出失败: {filename} - {status}")

            EventEmitter.result("ok", message=f"检出完成 {len(items)} 个文件")
            return iter([])

        self.register_command(
            name="checkout",
            desc="检出音乐到工作目录",
            steps=[checkout_filter, checkout_execute],
            requires_lock=True,
            on_error="stop",
        )

        # 3. sync命令 - 同步缓存
        def sync_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """执行同步操作"""
            # 解析参数
            direction = "both"
            dry_run = False
            workers = 4
            timeout = 60
            retries = 3
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--direction" and i + 1 < len(args):
                    direction = args[i + 1]
                    i += 2
                elif args[i] == "--dry-run":
                    dry_run = True
                    i += 1
                elif args[i] == "--workers" and i + 1 < len(args):
                    workers = int(args[i + 1])
                    i += 2
                elif args[i] == "--timeout" and i + 1 < len(args):
                    timeout = int(args[i + 1])
                    i += 2
                elif args[i] == "--retries" and i + 1 < len(args):
                    retries = int(args[i + 1])
                    i += 2
                else:
                    i += 1

            # 获取配置
            cache_root = self.context.cache_root
            transport = TransportAdapter(self.context)

            # 调用库函数
            exit_code = sync_cmd.sync_logic(
                cache_root=cache_root,
                transport=transport,
                direction=direction,
                workers=workers,
                retries=retries,
                timeout=timeout,
                dry_run=dry_run,
            )

            # 返回空迭代器
            return iter([])

        self.register_command(
            name="sync",
            desc="同步本地缓存与服务器数据",
            steps=[sync_step],
            requires_lock=False,
            on_error="continue",
        )

        # 注册其他命令别名
        self.register_command(
            name="push",
            desc="同步本地缓存到服务器 (sync --direction=upload)",
            steps=[],  # 将重定向到sync命令
            requires_lock=False,
        )

        self.register_command(
            name="pull",
            desc="从服务器同步缓存到本地 (sync --direction=download)",
            steps=[],  # 将重定向到sync命令
            requires_lock=False,
        )

        # verify命令 - 验证本地和远程文件完整性
        def verify_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """验证文件完整性"""
            # 解析参数
            mode = "local"
            custom_path = None
            delete = False
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--mode" and i + 1 < len(args):
                    mode = args[i + 1]
                    i += 2
                elif args[i] == "--path" and i + 1 < len(args):
                    custom_path = Path(args[i + 1])
                    i += 2
                elif args[i] == "--delete":
                    delete = True
                    i += 1
                else:
                    i += 1

            # 获取配置
            cache_root = self.context.cache_root
            metadata_file = self.context.metadata_file
            release_dir = self.context.release_dir if mode == "release" else None

            # 检查是否有需要针对性校验的audio_oids
            audio_oids = ctx.artifacts.get("processed_audio_oids")

            # 调用库函数
            try:
                exit_code = verify_cmd.verify_logic(
                    cache_root=cache_root,
                    metadata_file=metadata_file,
                    mode=mode,
                    custom_path=custom_path,
                    release_dir=release_dir,
                    audio_oids=audio_oids,
                    delete=delete,
                )
            except Exception as e:
                error_msg = f"验证逻辑异常: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e), "mode": mode})
                # verify命令使用continue策略，记录错误但继续执行
                exit_code = 1  # 设置失败状态码但继续处理

            # 显示verify报告表格（这里需要从verify_cmd获取详细结果）
            # 暂时显示摘要信息（仅在非log-only模式）
            if not ctx.log_only:
                table = Table(title="Verify 报告", show_lines=True)
                table.add_column("file", style="white")
                table.add_column("expected_oid", style="dim")
                table.add_column("actual_oid", style="dim")
                table.add_column("status", style="green")

                # 这里应该从实际验证结果中获取详细信息
                # 暂时显示模式信息
                table.add_row(
                    f"{mode} verification",
                    "-",
                    "-",
                    "completed" if exit_code == 0 else "failed (continuing)",
                )

                console.print(table)

            return iter([])

        # git提交步骤 - 提交元数据更改
        def commit_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """提交元数据更改到Git"""
            # 获取处理的audio_oids数量
            processed_audio_oids = ctx.artifacts.get("processed_audio_oids", [])
            count = len(processed_audio_oids)

            if count == 0:
                EventEmitter.result("ok", message="没有需要提交的更改")
                return iter([])

            metadata_file = self.context.metadata_file
            repo_root = self.project_root

            # 生成提交消息
            if count == 1:
                commit_msg = f"update: 1 item"
            else:
                commit_msg = f"update: {count} items"

            # 使用git库提交并推送
            try:
                success = git_commit_and_push(
                    repo_root=repo_root,
                    message=commit_msg,
                    paths=[str(metadata_file.relative_to(repo_root))],
                    remote="origin",
                    branch="main",
                )

                if success:
                    EventEmitter.result("ok", message=f"成功提交并推送 {count} 个更改")
                else:
                    error_msg = "Git提交推送失败"
                    EventEmitter.error(error_msg, {"operation": "git_commit_and_push", "count": count})
                    # commit步骤在publish命令中使用，publish使用stop策略，抛出异常
                    raise RuntimeError(error_msg)
            except Exception as e:
                error_msg = f"Git提交推送异常: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e), "count": count})
                # commit步骤在publish命令中使用，publish使用stop策略，抛出异常
                raise RuntimeError(error_msg) from e

            return iter([])

        self.register_command(
            name="verify",
            desc="验证本地和远程文件完整性",
            steps=[verify_step],
            requires_lock=False,
            on_error="continue",
        )

        # cleanup命令 - 清理孤立对象
        def cleanup_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """清理孤立对象"""
            # 解析参数
            mode = "local"
            confirm = False
            dry_run = False
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--mode" and i + 1 < len(args):
                    mode = args[i + 1]
                    i += 2
                elif args[i] == "--confirm":
                    confirm = True
                    i += 1
                elif args[i] == "--dry-run":
                    dry_run = True
                    i += 1
                else:
                    i += 1

            # 获取配置
            cache_root = self.context.cache_root
            metadata_file = self.context.metadata_file
            transport_cfg = self.config.get("transport", {})
            remote_user = transport_cfg.get("user")
            remote_host = transport_cfg.get("host")
            remote_data_root = transport_cfg.get("remote_data_root", "/srv/music/data")

            # 调用库函数
            try:
                exit_code = cleanup_cmd.cleanup_logic(
                    metadata_file=metadata_file,
                    cache_root=cache_root,
                    mode=mode,
                    confirm=confirm,
                    dry_run=dry_run,
                    remote_user=remote_user,
                    remote_host=remote_host,
                    remote_data_root=remote_data_root,
                )
            except Exception as e:
                error_msg = f"清理逻辑异常: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e), "mode": mode})
                # cleanup命令使用stop策略，抛出异常
                raise RuntimeError(error_msg) from e

            return iter([])

        # release命令 - 生成发布文件
        def release_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """生成发布文件"""
            # 解析参数
            mode = "local"
            conflict_strategy = "suffix"
            dry_run = False
            limit = None
            line_filter = None
            hash_filter = None
            search_filter = None
            force = False
            workers = 1
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--mode" and i + 1 < len(args):
                    mode = args[i + 1]
                    i += 2
                elif args[i] == "--conflict-strategy" and i + 1 < len(args):
                    conflict_strategy = args[i + 1]
                    i += 2
                elif args[i] == "--dry-run":
                    dry_run = True
                    i += 1
                elif args[i] == "--limit" and i + 1 < len(args):
                    limit = int(args[i + 1])
                    i += 2
                elif args[i] == "--line" and i + 1 < len(args):
                    line_filter = args[i + 1]
                    i += 2
                elif args[i] == "--hash" and i + 1 < len(args):
                    hash_filter = args[i + 1]
                    i += 2
                elif args[i] == "--search" and i + 1 < len(args):
                    search_filter = args[i + 1]
                    i += 2
                elif args[i] in ["--force", "-f"]:
                    force = True
                    i += 1
                elif args[i] == "--workers" and i + 1 < len(args):
                    workers = int(args[i + 1])
                    i += 2
                else:
                    i += 1

            # 获取发布目录
            release_dir = self.context.release_dir
            if force and release_dir.exists():
                # 清空目录
                import shutil

                for item in release_dir.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                EventEmitter.log("info", f"Cleared release directory: {release_dir}")
            release_dir.mkdir(parents=True, exist_ok=True)

            # 执行git pull（规范要求）
            try:
                if not git_pull(self.repo_root, remote="origin", branch="main"):
                    error_msg = "Git pull失败"
                    EventEmitter.error(error_msg, {"operation": "git_pull", "remote": "origin", "branch": "main"})
                    # release命令使用continue策略，记录错误但继续执行
                    return iter([])
            except Exception as e:
                error_msg = f"Git pull异常: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e)})
                # release命令使用continue策略，记录错误但继续执行
                return iter([])

            # 调用库函数
            try:
                entries_to_process, error_msg = release_cmd.release_logic(
                    metadata_mgr=ctx.metadata_mgr,
                    object_store=self.object_store,
                    release_dir=release_dir,
                    mode=mode,
                    conflict_strategy=conflict_strategy,
                    limit=limit,
                    line_filter=line_filter,
                    hash_filter=hash_filter,
                    search_filter=search_filter,
                    dry_run=dry_run,
                )

                if error_msg:
                    EventEmitter.error(f"Release逻辑失败: {error_msg}", {"error_type": "release_logic", "error_msg": error_msg})
                    # release命令使用continue策略，记录错误但继续执行
                    entries_to_process = []  # 使用空列表继续处理
            except Exception as e:
                error_msg = f"Release逻辑异常: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e), "operation": "release_logic"})
                # release命令使用continue策略，记录错误但继续执行
                entries_to_process = []  # 使用空列表继续处理

            # 如果是干跑模式，输出结果但不执行
            if dry_run:
                artifacts = {
                    "total_entries": len(entries_to_process),
                    "release_dir": str(release_dir),
                    "mode": mode,
                    "sample_entries": [
                        {
                            "title": e.get("title"),
                            "artists": e.get("artists"),
                            "audio_oid": e.get("audio_oid"),
                            "filename": release_cmd.generate_release_filename(e),
                        }
                        for e in entries_to_process[:5]
                    ],
                }

                EventEmitter.result(
                    "ok",
                    message=f"Dry run: Would process {len(entries_to_process)} entries",
                    artifacts=artifacts,
                )
                return iter([])

            # 定义进度回调
            processed = [0]
            total = len(entries_to_process)

            def progress_callback(current, total_items=None):
                processed[0] = current
                if total_items:
                    EventEmitter.batch_progress("generate", current, total_items)
                else:
                    EventEmitter.batch_progress("generate", current, total)

            # 执行发布
            try:
                success_count, total_count = release_cmd.execute_release(
                    entries=entries_to_process,
                    object_store=self.object_store,
                    release_dir=release_dir,
                    conflict_strategy=conflict_strategy,
                    incremental=(mode == "incremental"),
                    progress_callback=progress_callback,
                )
            except Exception as e:
                # 对于release命令，使用continue策略，记录错误但不停止
                error_msg = f"Release执行失败: {str(e)}"
                EventEmitter.error(error_msg, {"exception": str(e)})
                
                # 返回部分结果
                artifacts = {
                    "total_entries": len(entries_to_process),
                    "successful": 0,
                    "failed": len(entries_to_process),
                    "release_dir": str(release_dir),
                    "mode": mode,
                    "error": error_msg,
                }
                
                EventEmitter.result(
                    "error",
                    message=error_msg,
                    artifacts=artifacts,
                )
                return iter([])

            # 返回结果
            artifacts = {
                "total_entries": total_count,
                "successful": success_count,
                "failed": total_count - success_count,
                "release_dir": str(release_dir),
                "mode": mode,
            }

            # 显示release结果表格（仅在非log-only模式）
            if success_count > 0 and not ctx.log_only:
                table = Table(title="Release 结果", show_lines=True)
                table.add_column("#", style="cyan", justify="right")
                table.add_column("file", style="white")
                table.add_column("status", style="green")
                table.add_column("duration", style="dim")
                table.add_column("artifact", style="magenta")

                # 这里应该从实际执行结果中获取详细信息
                # 暂时显示摘要信息
                table.add_row(
                    "1",
                    f"{success_count} files",
                    "success" if success_count == total_count else "partial",
                    f"{ctx.elapsed():.1f}s",
                    str(release_dir),
                )

                if total_count - success_count > 0:
                    table.add_row(
                        "2", f"{total_count - success_count} files", "failed", "-", "-"
                    )

                console.print(table)

            if success_count == total_count:
                EventEmitter.result(
                    "ok",
                    message=f"Successfully generated {success_count} release files",
                    artifacts=artifacts,
                )
            else:
                # 部分成功，根据策略这可能是可以接受的
                EventEmitter.result(
                    "warn",
                    message=f"Generated {success_count}/{total_count} release files",
                    artifacts=artifacts,
                )

            return iter([])

        self.register_command(
            name="release",
            desc="生成发布文件",
            steps=[sync_step, verify_step, release_step],
            requires_lock=True,
            on_error="continue",
        )

        self.register_command(
            name="cleanup",
            desc="清理孤立对象",
            steps=[cleanup_step],
            requires_lock=True,
            on_error="stop",
        )

        # compress-images命令 - 压缩封面图片
        def compress_images_step(
            ctx: StepContext, input_iter: Iterator
        ) -> Iterator[Dict]:
            """压缩封面图片"""
            # 解析参数
            quality = 85
            max_width = 800
            min_size_kb = 500
            size_param = None
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--size" and i + 1 < len(args):
                    size_param = args[i + 1]
                    i += 2
                elif args[i] == "--quality" and i + 1 < len(args):
                    quality = int(args[i + 1])
                    i += 2
                elif args[i] == "--max-width" and i + 1 < len(args):
                    max_width = int(args[i + 1])
                    i += 2
                elif args[i] == "--min-size-kb" and i + 1 < len(args):
                    min_size_kb = int(args[i + 1])
                    i += 2
                else:
                    i += 1

            # 解析--size参数（支持单位：kb, mb, gb，默认kb）
            if size_param:
                size_param = size_param.lower().strip()
                unit = "kb"
                if size_param.endswith("kb"):
                    size_val = size_param[:-2]
                    unit = "kb"
                elif size_param.endswith("mb"):
                    size_val = size_param[:-2]
                    unit = "mb"
                elif size_param.endswith("gb"):
                    size_val = size_param[:-2]
                    unit = "gb"
                else:
                    size_val = size_param
                    unit = "kb"

                try:
                    size_num = float(size_val)
                    if unit == "kb":
                        min_size_kb = int(size_num)
                    elif unit == "mb":
                        min_size_kb = int(size_num * 1024)
                    elif unit == "gb":
                        min_size_kb = int(size_num * 1024 * 1024)
                except ValueError:
                    EventEmitter.error(f"Invalid size value: {size_param}")
                    return iter([])

            # 调用库函数
            entries_to_compress, error_msg = compress_cmd.compress_images_logic(
                metadata_mgr=ctx.metadata_mgr,
                object_store=self.object_store,
                min_size_kb=min_size_kb,
            )

            if error_msg:
                EventEmitter.error(f"Compress images逻辑失败: {error_msg}")
                return iter([])

            # 如果存在处理过的audio_oid列表，则只压缩这些条目的封面
            processed_audio_oids = ctx.artifacts.get("processed_audio_oids")
            if processed_audio_oids:
                original_count = len(entries_to_compress)
                entries_to_compress = [
                    entry
                    for entry in entries_to_compress
                    if entry.get("audio_oid") in processed_audio_oids
                ]
                if entries_to_compress:
                    EventEmitter.log(
                        "info",
                        f"Filtered compression to {len(entries_to_compress)}/{original_count} entries based on processed audio OIDs",
                    )
                else:
                    EventEmitter.log(
                        "info", "No processed entries require cover compression"
                    )
                    EventEmitter.result(
                        "ok", message="No processed entries require cover compression"
                    )
                    return iter([])

            # 定义进度回调
            processed = [0]
            total = len(entries_to_compress)

            def progress_callback(current, total_items=None):
                processed[0] = current
                if total_items:
                    EventEmitter.batch_progress(
                        "compress_execute", current, total_items
                    )
                else:
                    EventEmitter.batch_progress("compress_execute", current, total)

            # 执行压缩动作
            updated_count, total_count = compress_cmd.execute_compress_images(
                entries_to_compress=entries_to_compress,
                metadata_mgr=ctx.metadata_mgr,
                object_store=self.object_store,
                progress_callback=progress_callback,
            )

            # 返回结果
            artifacts = {
                "total_entries": total_count,
                "updated": updated_count,
                "min_size_kb": min_size_kb,
            }

            if updated_count == 0:
                EventEmitter.result("ok", message="No images needed compression")
            else:
                EventEmitter.result(
                    "ok",
                    message=f"Compressed {updated_count} images",
                    artifacts=artifacts,
                )

            return iter([])

        self.register_command(
            name="publish",
            desc="发布本地改动 (分析->确认->压缩->校验->同步->提交)",
            steps=[
                publish_scan,
                publish_process,
                compress_images_step,
                verify_step,
                sync_step,
                commit_step,
            ],
            requires_lock=True,
            on_error="stop",
        )

        # analyze命令 - 分析元数据
        def analyze_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """分析元数据"""
            # 解析参数
            query = ""
            search_field = None
            missing_fields = None
            fields_to_extract = None
            filter_fields = None
            line_filter = None
            limit = 10  # 默认值，根据规范
            mode = "search"  # 'search', 'stats', 'duplicates'
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--query" and i + 1 < len(args):
                    query = args[i + 1]
                    i += 2
                elif args[i] == "--search-field" and i + 1 < len(args):
                    search_field = args[i + 1]
                    i += 2
                elif args[i] == "--missing" and i + 1 < len(args):
                    missing_fields = args[i + 1]
                    i += 2
                elif args[i] == "--fields" and i + 1 < len(args):
                    fields_to_extract = args[i + 1]
                    i += 2
                elif args[i] == "--filter" and i + 1 < len(args):
                    filter_fields = args[i + 1]
                    i += 2
                elif args[i] == "--line" and i + 1 < len(args):
                    line_filter = args[i + 1]
                    i += 2
                elif args[i] == "--limit" and i + 1 < len(args):
                    limit = int(args[i + 1])
                    i += 2
                elif args[i] == "--mode" and i + 1 < len(args):
                    mode = args[i + 1]
                    i += 2
                else:
                    i += 1

            # 调用库函数
            entries, analysis_results, error_msg = analyze_cmd.analyze_logic(
                metadata_mgr=ctx.metadata_mgr,
                query=query,
                search_field=search_field,
                missing_fields=missing_fields,
                fields_to_extract=fields_to_extract,
                filter_fields=filter_fields,
                line_filter=line_filter,
                limit=limit,
                mode=mode,
            )

            if error_msg:
                EventEmitter.error(f"Analyze逻辑失败: {error_msg}")
                return iter([])

            # 执行分析动作
            analyze_cmd.execute_analyze(
                entries=entries,
                analysis_results=analysis_results,
                mode=mode,
                limit=limit,
            )

            return iter([])

        self.register_command(
            name="analyze",
            desc="分析元数据（搜索、统计、重复项检测）",
            steps=[analyze_step],
            requires_lock=False,
            on_error="continue",
        )

        # download命令 - 下载音频文件
        def download_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """下载音频文件"""
            # 解析参数
            url = None
            batch_file = None
            no_cover = False
            metadata_only = False
            no_preview = False
            limit = None
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--batch-file" and i + 1 < len(args):
                    batch_file = args[i + 1]
                    i += 2
                elif args[i] == "--no-cover":
                    no_cover = True
                    i += 1
                elif args[i] == "--metadata-only" or args[i] == "--fetch":
                    metadata_only = True
                    i += 1
                elif args[i] == "--no-preview":
                    no_preview = True
                    i += 1
                elif args[i] == "--limit" and i + 1 < len(args):
                    limit = int(args[i + 1])
                    i += 2
                elif not url and not args[i].startswith("-"):  # 第一个非选项参数作为URL
                    url = args[i]
                    i += 1
                else:
                    i += 1

            # 收集URL
            urls = []
            if batch_file:
                batch_file_path = Path(batch_file)
                if not batch_file_path.exists():
                    EventEmitter.error(f"Batch file not found: {batch_file_path}")
                    return iter([])

                with open(batch_file_path, "r", encoding="utf-8") as f:
                    urls = [
                        line.strip() for line in f if line.strip().startswith("http")
                    ]

                if not urls:
                    EventEmitter.error(
                        f"No valid URLs found in batch file: {batch_file_path}"
                    )
                    return iter([])
            elif url:
                urls = [url]
            else:
                EventEmitter.error("No URL provided. Specify URL or --batch-file.")
                return iter([])

            # 获取工作目录
            work_dir = self.context.work_dir
            work_dir.mkdir(parents=True, exist_ok=True)

            # 调用库函数
            successful_downloads, failed_downloads, error_msg = (
                download_cmd.download_logic(
                    urls=urls,
                    output_dir=work_dir,
                    extract_cover=not no_cover,
                    metadata_only=metadata_only,
                    no_preview=no_preview,
                    limit=limit,
                )
            )

            if error_msg:
                EventEmitter.error(f"Download逻辑失败: {error_msg}")
                return iter([])

            # 执行下载动作
            download_cmd.execute_download(
                successful_downloads=successful_downloads,
                failed_downloads=failed_downloads,
                output_dir=work_dir,
                metadata_only=metadata_only,
            )

            return iter([])

        self.register_command(
            name="download",
            desc="下载音频文件（支持YouTube等平台）",
            steps=[download_step],
            requires_lock=False,
            on_error="continue",
        )

        self.register_command(
            name="compress-images",
            desc="压缩封面图片以节省空间",
            steps=[compress_images_step],
            requires_lock=True,
            on_error="stop",
        )

    def _handle_event(self, event):
        """处理单个事件，更新日志和统计"""
        # log-only模式：直接输出JSONL
        if self.log_only:
            from libgitmusic.events import EventEmitter

            filtered_event = EventEmitter._filter_sensitive_data(event)
            import json

            json_str = json.dumps(filtered_event, ensure_ascii=False)
            try:
                sys.stdout.buffer.write(json_str.encode("utf-8"))
                sys.stdout.buffer.write(b"\n")
                sys.stdout.buffer.flush()
            except:
                print(json_str, flush=True)
            # 注意：不执行后续的rich渲染和统计更新
            return

        # 添加到事件日志
        self.event_log.append(event)

        # 添加到最近事件列表（限制大小）
        self.recent_events.append(event)
        if len(self.recent_events) > 20:
            self.recent_events.pop(0)

        # 收集错误信息（用于错误汇总）
        etype = event.get("type")
        if etype == "error":
            error_info = {
                "type": "event_error",
                "message": event.get("message", ""),
                "context": event.get("context", {}),
                "timestamp": event.get("ts", "")
            }
            if hasattr(self, 'command_errors'):
                self.command_errors.append(error_info)
        elif etype == "item_event":
            status = event.get("status", "")
            if status == "error":
                error_info = {
                    "type": "item_error",
                    "item_id": event.get("id", ""),
                    "message": event.get("message", ""),
                    "timestamp": event.get("ts", "")
                }
                if hasattr(self, 'command_errors'):
                    self.command_errors.append(error_info)

        # 更新统计信息
        if etype == "item_event":
            self.summary_stats["items_processed"] = (
                self.summary_stats.get("items_processed", 0) + 1
            )
            status = event.get("status", "")
            if status == "error":
                self.summary_stats["errors"] = self.summary_stats.get("errors", 0) + 1
            elif status == "warn":
                self.summary_stats["warnings"] = (
                    self.summary_stats.get("warnings", 0) + 1
                )
        elif etype == "error":
            self.summary_stats["errors"] = self.summary_stats.get("errors", 0) + 1
        elif etype == "phase_start":
            # 记录阶段开始时间
            phase = event.get("phase")
            if "phase_start_times" not in self.summary_stats:
                self.summary_stats["phase_start_times"] = {}
            self.summary_stats["phase_start_times"][phase] = time.time()

            # 显示Header行（符合视觉规范）
            current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            total_items = event.get("total_items", 0)
            # 获取当前命令名（从事件或上下文）
            command_name = event.get(
                "command", self.summary_stats.get("current_command", "unknown")
            )
            # 仅在非log-only模式显示Header行
            if not self.log_only:
                console.print(
                    f"[bold cyan]{current_time} | {command_name} | phase={phase} | total={total_items} | status=running[/bold cyan]"
                )
        elif etype == "batch_progress":
            # 更新进度
            pass

    def _execute_steps_with_error_handling(self, cmd, ctx):
        """根据on_error策略执行步骤链"""
        if len(cmd.steps) > 1:
            # 链式执行：每个步骤的输出作为下一个步骤的输入
            current_iter = None
            for i, step in enumerate(cmd.steps):
                try:
                    if i == 0:
                        # 第一个步骤
                        result = step(ctx, None)
                        if hasattr(result, "__iter__"):
                            current_iter = result
                        else:
                            # 包装为单元素迭代器
                            def single_item():
                                if result is not None:
                                    yield result
                            current_iter = single_item()
                    else:
                        # 后续步骤使用前一个步骤的输出
                        if current_iter is not None:
                            current_iter = step(ctx, current_iter)
                except Exception as e:
                    # 步骤执行异常，根据on_error策略处理
                    if cmd.on_error == "stop":
                        # 立即停止并重新抛出异常
                        raise
                    else:
                        # continue或notify：记录错误并继续
                        error_info = {
                            "step": i,
                            "step_name": step.__name__ if hasattr(step, '__name__') else str(step),
                            "error": str(e),
                            "type": "step_exception"
                        }
                        self.command_errors.append(error_info)
                        EventEmitter.error(f"步骤 {i} 执行失败: {str(e)}", error_info)
                        
                        # 如果是continue或notify，尝试继续执行（返回空迭代器）
                        if i == 0:
                            current_iter = iter([])
                        else:
                            # 中断管道执行
                            break
        else:
            # 单个步骤
            try:
                cmd.steps[0](ctx, None)
            except Exception as e:
                # 根据on_error策略处理
                if cmd.on_error == "stop":
                    raise
                else:
                    # continue或notify：记录错误
                    error_info = {
                        "step": 0,
                        "step_name": cmd.steps[0].__name__ if hasattr(cmd.steps[0], '__name__') else str(cmd.steps[0]),
                        "error": str(e),
                        "type": "step_exception"
                    }
                    self.command_errors.append(error_info)
                    EventEmitter.error(f"步骤执行失败: {str(e)}", error_info)

    def _handle_command_exception(self, cmd, exception):
        """处理命令级别的异常"""
        error_info = {
            "type": "command_exception",
            "error": str(exception),
            "on_error_strategy": cmd.on_error
        }
        
        if cmd.on_error == "stop":
            # 立即停止并显示错误
            if not self.log_only:
                console.print(f"[red]命令执行错误: {str(exception)}[/red]")
            EventEmitter.error(f"命令执行失败: {str(exception)}", error_info)
            raise  # 重新抛出，中止执行
        else:
            # continue或notify：记录错误但不停止
            self.command_errors.append(error_info)
            EventEmitter.error(f"命令执行错误（继续执行）: {str(exception)}", error_info)
            if not self.log_only:
                console.print(f"[yellow]命令执行错误（按{cmd.on_error}策略继续）: {str(exception)}[/yellow]")

    def _display_error_summary(self):
        """显示错误汇总"""
        if not self.command_errors:
            return
            
        from rich.table import Table
        
        table = Table(title=f"错误汇总 ({len(self.command_errors)} 个错误)", show_lines=True)
        table.add_column("类型", style="red")
        table.add_column("步骤", style="yellow")
        table.add_column("错误信息", style="white")
        
        for error in self.command_errors:
            error_type = error.get("type", "unknown")
            step_info = str(error.get("step", "-"))
            if "step_name" in error:
                step_info = f"{error['step_name']} (步骤{error.get('step', '?')})"
            
            error_msg = error.get("error", "未知错误")
            table.add_row(error_type, step_info, error_msg)
        
        console.print(table)

    def _display_summary(self):
        """显示命令执行摘要"""
        if not self.summary_stats:
            return

        items_processed = self.summary_stats.get("items_processed", 0)
        errors = self.summary_stats.get("errors", 0)
        warnings = self.summary_stats.get("warnings", 0)

        # 计算耗时
        start_time = self.summary_stats.get("start_time")
        end_time = time.time()
        elapsed = end_time - start_time if start_time else 0

        # 计算速率
        rate = items_processed / elapsed if elapsed > 0 else 0

        # 获取日志文件信息
        logfile_info = "未记录"
        try:
            from libgitmusic.events import EventEmitter

            if EventEmitter._log_file is not None:
                logfile_path = EventEmitter._log_file.name
                logfile_info = str(Path(logfile_path).relative_to(self.project_root))
        except:
            pass

        # 构建摘要内容
        summary_content = f"""处理项数: {items_processed}
错误数: {errors}
警告数: {warnings}
耗时: {elapsed:.2f}秒
速率: {rate:.2f}项/秒
日志文件: {logfile_info}"""

        # 使用rich.Panel显示摘要
        summary_panel = Panel(
            summary_content, title="执行摘要", border_style="green", padding=(1, 2)
        )

        console.print(summary_panel)

        # 显示最近事件（如果有）
        if self.recent_events:
            from rich.table import Table as RichTable

            recent_table = RichTable(title="最近事件（最多20条）", show_lines=True)
            recent_table.add_column("时间", style="dim")
            recent_table.add_column("类型", style="cyan")
            recent_table.add_column("状态", style="yellow")
            recent_table.add_column("消息", style="white")

            for event in self.recent_events[-10:]:  # 只显示最后10条
                ts = event.get("ts", "")[:19]  # 截取日期时间部分
                etype = event.get("type", "")
                status = event.get("status", "")
                message = event.get("message", "")[:50]  # 截断消息
                recent_table.add_row(ts, etype, status, message)

            console.print(recent_table)

    def run_command(self, name: str, args: List[str]):
        """执行命令"""
        if name == "help":
            self.show_help()
            return

        if name not in self.commands:
            console.print(f"[red]未知命令: {name}[/red]")
            return

        cmd = self.commands[name]

        # 重置统计信息和事件日志
        self.event_log.clear()
        self.recent_events.clear()
        self.summary_stats.clear()
        
        # 重置错误收集
        self.command_errors = []

        # 注册事件监听器
        from libgitmusic.events import EventEmitter

        # 启动日志文件记录
        EventEmitter.start_log_file(command_name=name)

        EventEmitter.register_listener(self._handle_event)
        # 记录开始时间
        self.summary_stats["start_time"] = time.time()

        # 显示Header行（符合视觉规范，仅在非log-only模式）
        if not self.log_only:
            current_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            console.print(
                f"[bold cyan]{current_time} | {name} | phase=start | total=0 | status=running[/bold cyan]"
            )

        # 保存当前命令名到统计信息中，供事件处理使用
        self.summary_stats["current_command"] = name

        # 处理帮助请求
        if "--help" in args or "-h" in args:
            self.show_command_help(name)
            return

        # 处理命令别名重定向
        if name == "push":
            self.run_command("sync", ["--direction=upload"] + args)
            return
        elif name == "pull":
            self.run_command("sync", ["--direction=download"] + args)
            return

        # 创建执行上下文
        ctx = StepContext(
            command_name=name,
            args=args,
            config=self.config,
            metadata_mgr=self.metadata_mgr,
            object_store=self.object_store,
            lock_manager=self.lock_manager,
            context=self.context,
            log_only=self.log_only,
        )

        try:
            # 获取锁（如果需要）
            if cmd.requires_lock:
                if not self.lock_manager.acquire_metadata_lock(timeout=30):
                    console.print("[red]无法获取元数据锁，可能有其他进程正在运行[/red]")
                    return

            # 执行步骤链
            if cmd.steps:
                if not self.log_only:
                    console.print(f"[bold cyan]执行命令: {name}[/bold cyan]")

                # 根据on_error策略执行步骤
                self._execute_steps_with_error_handling(cmd, ctx)

                if not self.log_only:
                    console.print(f"[green]命令执行完成: {name}[/green]")
            else:
                if not self.log_only:
                    console.print(f"[yellow]命令 {name} 没有定义步骤[/yellow]")

        except KeyboardInterrupt:
            if not self.log_only:
                console.print("[yellow]命令被用户中断[/yellow]")
            EventEmitter.error("cancelled by user", {"command": name})
        except Exception as e:
            # 根据on_error策略处理异常
            self._handle_command_exception(cmd, e)
        finally:
            # 显示摘要（仅在非log-only模式）
            if not self.log_only:
                self._display_summary()
                # 显示错误汇总（如果有错误且on_error不是stop）
                if self.command_errors and cmd.on_error in ["continue", "notify"]:
                    self._display_error_summary()
            # 注销事件监听器
            from libgitmusic.events import EventEmitter

            EventEmitter.unregister_listener(self._handle_event)
            # 停止日志记录
            EventEmitter.stop_logging()
            # 释放锁
            if cmd.requires_lock:
                self.lock_manager.release_metadata_lock()

    def repl(self):
        """REPL交互模式"""
        # 延迟初始化PromptSession，避免Windows终端问题
        if self.session is None:
            self.session = PromptSession(
                history=FileHistory(str(self.project_root / ".cli_history"))
            )

        console.print(
            Panel(Text("GitMusic CLI 管理系统", style="bold magenta", justify="center"))
        )
        console.print("输入 'help' 查看命令列表, 'exit' 退出\n")

        while True:
            try:
                text = self.session.prompt(
                    "gitmusic > ", lexer=PygmentsLexer(BashLexer)
                ).strip()

                if not text:
                    continue
                if text.lower() in ["exit", "quit"]:
                    break
                if text.lower() == "help":
                    self.show_help()
                    continue

                parts = text.split()
                self.run_command(parts[0], parts[1:])

            except KeyboardInterrupt:
                continue
            except EOFError:
                break

    def show_command_help(self, name: str):
        """显示命令详细帮助"""
        if name not in self.commands:
            console.print(f"[red]未知命令: {name}[/red]")
            return

        cmd = self.commands[name]
        console.print(Panel(Text(f"命令帮助: {name}", style="bold cyan")))
        console.print(f"[bold]描述:[/bold] {cmd.desc}\n")

        # 命令特定帮助
        help_map = {
            "publish": """[bold]选项:[/bold]
  --changed-only  仅处理有变动的文件
  --preview       仅显示预览，不执行发布""",
            "checkout": """[bold]选项:[/bold]
  --query <str>   搜索关键词
  --missing <f>   按缺失字段过滤 (cover,uslt,album,date)
  --force, -f     强制覆盖已有文件
  --limit <n>     限制处理数量""",
            "sync": """[bold]选项:[/bold]
  --direction <d> 同步方向 (upload|download|both)
  --dry-run       仅显示差异
  --timeout <n>   单文件超时时间
  --workers <n>   并行线程数""",
            "push": "[bold]别名:[/bold] sync --direction=upload",
            "pull": "[bold]别名:[/bold] sync --direction=download",
        }

        if name in help_map:
            console.print(help_map[name])
        else:
            console.print("[dim]该命令暂无详细选项说明。[/dim]")

    def show_help(self):
        """显示所有命令帮助"""
        table = Table(title="可用命令")
        table.add_column("命令", style="cyan")
        table.add_column("描述", style="green")

        for cmd_name in sorted(self.commands.keys()):
            cmd = self.commands[cmd_name]
            table.add_row(cmd_name, cmd.desc)

        console.print(table)
        console.print("\n使用 'help <命令名>' 查看详细选项")


def main():
    """主函数"""
    import argparse
    import sys
    import os

    # 修复控制台编码问题，确保在bash等环境中中文正常显示
    # 设置环境变量作为第一层保障
    os.environ["PYTHONIOENCODING"] = "utf-8"

    try:
        # Python 3.7+ 支持reconfigure方法，直接设置stdout/stderr编码
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        # 对于不支持reconfigure的Python版本，使用TextIOWrapper包装
        try:
            import io

            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            # 如果所有方法都失败，至少环境变量应该能起作用
            pass

    parser = argparse.ArgumentParser(description="GitMusic CLI")
    parser.add_argument(
        "--log-only", action="store_true", help="仅输出JSONL日志，不渲染人类友好界面"
    )
    parser.add_argument("--logs-dir", help="日志目录路径")
    parser.add_argument("command", nargs="?", help="命令名称")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="命令参数")

    args = parser.parse_args()

    # 创建CLI实例，传递log_only参数
    cli = GitMusicCLI(log_only=args.log_only)

    # 覆盖日志目录配置（如果命令行指定）
    if args.logs_dir:
        cli.context.logs_dir = Path(args.logs_dir).resolve()
        from libgitmusic.events import EventEmitter

        EventEmitter.setup_logging(logs_dir=cli.context.logs_dir, log_only=cli.log_only)

    if args.command:
        cli.run_command(args.command, args.args)
    else:
        cli.repl()


if __name__ == "__main__":
    main()
