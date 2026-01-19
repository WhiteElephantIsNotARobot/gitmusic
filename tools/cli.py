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
from libgitmusic.commands import publish as publish_cmd
from libgitmusic.commands import checkout as checkout_cmd

console = Console()


class Command:
    """命令定义类，支持步骤函数和流式管道"""

    def __init__(
        self,
        name: str,
        desc: str,
        steps: List[Callable] = None,
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
    ):
        self.command_name = command_name
        self.args = args
        self.config = config
        self.metadata_mgr = metadata_mgr
        self.object_store = object_store
        self.lock_manager = lock_manager
        self.artifacts = {}
        self.start_time = time.time()

    def elapsed(self) -> float:
        """获取经过的时间（秒）"""
        return time.time() - self.start_time


class GitMusicCLI:
    """GitMusic CLI 主类，支持步骤函数和流式管道"""

    def __init__(self, config_path: Optional[str] = None):
        self.repo_root = Path(__file__).parent.parent
        self.project_root = self.repo_root.parent
        self.config = self._load_config(config_path)

        # 初始化核心组件
        metadata_file = self._get_path("metadata_file")
        self.metadata_mgr = MetadataManager(metadata_file)

        cache_root = self._get_path("cache_root")
        self.object_store = ObjectStore(cache_root)

        self.lock_manager = LockManager(self.project_root / ".locks")

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

    def _load_config(self, path: Optional[str]) -> dict:
        """加载配置文件"""
        config_file = Path(path) if path else self.project_root / "config.yaml"
        if config_file.exists():
            import yaml

            with open(config_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {
            "transport": {
                "user": "white_elephant",
                "host": "debian-server",
                "remote_data_root": "/srv/music/data",
                "retries": 5,
                "timeout": 60,
                "workers": 4,
            },
            "paths": {
                "work_dir": str(self.project_root / "work"),
                "cache_root": str(self.project_root / "cache"),
                "metadata_file": str(self.repo_root / "metadata.jsonl"),
                "release_dir": str(self.project_root / "release"),
            },
            "image": {"quality": 2},
        }

    def _get_path(self, key: str) -> Path:
        """从配置获取路径"""
        path_str = self.config.get("paths", {}).get(key, "")
        if not path_str:
            raise ValueError(f"Path key '{key}' not found in config")
        return Path(path_str).resolve()

    def _inject_env(self):
        """注入环境变量供子进程使用"""
        os.environ["GITMUSIC_WORK_DIR"] = str(self._get_path("work_dir"))
        os.environ["GITMUSIC_CACHE_ROOT"] = str(self._get_path("cache_root"))
        os.environ["GITMUSIC_METADATA_FILE"] = str(self._get_path("metadata_file"))
        transport = self.config.get("transport", {})
        os.environ["GITMUSIC_REMOTE_USER"] = transport.get("user", "")
        os.environ["GITMUSIC_REMOTE_HOST"] = transport.get("host", "")
        os.environ["GITMUSIC_REMOTE_DATA_ROOT"] = transport.get("remote_data_root", "")

    def register_command(
        self,
        name: str,
        desc: str,
        steps: List[Callable] = None,
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
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = None

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
                        if task is None:  # 仅在非进度条模式下显示
                            status = event.get("status", "").upper()
                            item_id = event.get("id", "")
                            progress.console.print(f"[dim] {status}: {item_id}[/dim]")
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
                self.project_root,
                changed_only=changed_only,
                progress_callback=progress_callback,
            )

            if error_msg:
                EventEmitter.error(f"扫描失败: {error_msg}")
                return iter([])

            # 如果是预览模式，输出结果但不执行
            if preview:
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
            publish_cmd.execute_publish(
                ctx.metadata_mgr,
                self.project_root,
                items,
                progress_callback=progress_callback,
            )

            EventEmitter.result("ok", message=f"处理完成 {len(items)} 个项")
            return iter([])

        self.register_command(
            name="publish",
            desc="发布本地改动 (分析->确认->压缩->校验->同步->提交)",
            steps=[publish_scan, publish_process],
            requires_lock=True,
            on_error="stop",
        )

        # 2. checkout命令 - 检出音乐到工作目录
        def checkout_filter(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """根据参数过滤元数据条目"""
            args = ctx.args
            query = ""
            missing_fields = []
            limit = 0
            force = False

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
                else:
                    i += 1

            # 调用库函数进行过滤
            filtered = checkout_cmd.checkout_logic(
                ctx.metadata_mgr,
                query=query,
                missing_fields=missing_fields if missing_fields else None,
                limit=limit,
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
            work_dir = self._get_path("work_dir")
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
        def sync_analyze(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """分析同步差异"""
            transport_cfg = self.config.get("transport", {})
            transport = TransportAdapter(
                user=transport_cfg.get("user"),
                host=transport_cfg.get("host"),
                remote_data_root=transport_cfg.get(
                    "remote_data_root", "/srv/music/data"
                ),
            )

            cache_root = self._get_path("cache_root")

            # 列出本地和远程文件
            local_files = {
                str(p.relative_to(cache_root)).replace("\\", "/")
                for p in cache_root.rglob("*")
                if p.is_file() and p.suffix in [".mp3", ".jpg"]
            }

            remote_files = set(
                transport.list_remote_files("objects")
                + transport.list_remote_files("covers")
            )

            # 解析方向参数
            direction = "both"
            dry_run = False
            args = ctx.args

            i = 0
            while i < len(args):
                if args[i] == "--direction" and i + 1 < len(args):
                    direction = args[i + 1]
                    i += 2
                elif args[i] == "--dry-run":
                    dry_run = True
                    i += 1
                else:
                    i += 1

            ctx.artifacts["direction"] = direction
            ctx.artifacts["dry_run"] = dry_run
            ctx.artifacts["transport"] = transport
            ctx.artifacts["to_upload"] = list(local_files - remote_files)
            ctx.artifacts["to_download"] = list(remote_files - local_files)

            # 显示预览
            table = Table(title="同步预览")
            table.add_column("方向", style="bold")
            table.add_column("数量", style="cyan")
            if direction in ["upload", "both"]:
                table.add_row(
                    "上传 (Local -> Remote)", str(len(ctx.artifacts["to_upload"]))
                )
            if direction in ["download", "both"]:
                table.add_row(
                    "下载 (Remote -> Local)", str(len(ctx.artifacts["to_download"]))
                )
            console.print(table)

            EventEmitter.result("ok", message="Sync analysis completed")
            return iter([ctx.artifacts])

        def sync_execute(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """执行同步操作"""
            artifacts = next(input_iter, {})
            direction = artifacts.get("direction", "both")
            dry_run = artifacts.get("dry_run", False)
            transport = artifacts.get("transport")
            to_upload = artifacts.get("to_upload", [])
            to_download = artifacts.get("to_download", [])

            cache_root = self._get_path("cache_root")

            if dry_run:
                EventEmitter.log("info", "Dry run mode - no files will be transferred")
                return iter([])

            # 上传文件
            if direction in ["upload", "both"] and to_upload:
                EventEmitter.phase_start("upload", total_items=len(to_upload))
                for i, rel_path in enumerate(to_upload):
                    local_path = cache_root / rel_path
                    try:
                        transport.upload(local_path, rel_path)
                        EventEmitter.item_event(rel_path, "uploaded", "")
                    except Exception as e:
                        EventEmitter.error(
                            f"Failed to upload {rel_path}", {"error": str(e)}
                        )
                    EventEmitter.batch_progress("upload", i + 1, len(to_upload))

            # 下载文件
            if direction in ["download", "both"] and to_download:
                EventEmitter.phase_start("download", total_items=len(to_download))
                for i, rel_path in enumerate(to_download):
                    local_path = cache_root / rel_path
                    try:
                        transport.download(rel_path, local_path)
                        EventEmitter.item_event(rel_path, "downloaded", "")
                    except Exception as e:
                        EventEmitter.error(
                            f"Failed to download {rel_path}", {"error": str(e)}
                        )
                    EventEmitter.batch_progress("download", i + 1, len(to_download))

            EventEmitter.result("ok", message="Sync completed")
            return iter([])

        self.register_command(
            name="sync",
            desc="同步本地缓存与服务器数据",
            steps=[sync_analyze, sync_execute],
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
            EventEmitter.phase_start("verify", total_items=0)
            EventEmitter.result("warn", message="verify命令尚未实现")
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
            EventEmitter.phase_start("cleanup", total_items=0)
            EventEmitter.result("warn", message="cleanup命令尚未实现")
            return iter([])

        self.register_command(
            name="cleanup",
            desc="清理孤立对象",
            steps=[cleanup_step],
            requires_lock=True,
            on_error="stop",
        )

        # release命令 - 生成发布文件
        def release_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """生成发布文件"""
            EventEmitter.phase_start("release", total_items=0)
            EventEmitter.result("warn", message="release命令尚未实现")
            return iter([])

        self.register_command(
            name="release",
            desc="生成发布文件",
            steps=[release_step],
            requires_lock=True,
            on_error="stop",
        )

        # analyze命令 - 分析元数据重复项
        def analyze_step(ctx: StepContext, input_iter: Iterator) -> Iterator[Dict]:
            """分析元数据重复项"""
            EventEmitter.phase_start("analyze", total_items=0)
            EventEmitter.result("warn", message="analyze命令尚未实现")
            return iter([])

        self.register_command(
            name="analyze",
            desc="分析元数据重复项",
            steps=[analyze_step],
            requires_lock=False,
            on_error="continue",
        )

    def _handle_event(self, event):
        """处理单个事件，更新日志和统计"""
        # 添加到事件日志
        self.event_log.append(event)

        # 添加到最近事件列表（限制大小）
        self.recent_events.append(event)
        if len(self.recent_events) > 20:
            self.recent_events.pop(0)

        # 更新统计信息
        etype = event.get("type")
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
        elif etype == "batch_progress":
            # 更新进度
            pass

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

        table = Table(title="执行摘要", show_header=False, show_lines=True)
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")

        table.add_row("处理项数", str(items_processed))
        table.add_row("错误数", str(errors))
        table.add_row("警告数", str(warnings))
        table.add_row("耗时（秒）", f"{elapsed:.2f}")
        table.add_row("速率（项/秒）", f"{rate:.2f}")

        console.print(table)

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

        # 注册事件监听器
        from libgitmusic.events import EventEmitter

        EventEmitter.register_listener(self._handle_event)
        # 记录开始时间
        self.summary_stats["start_time"] = time.time()

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
        )

        try:
            # 获取锁（如果需要）
            if cmd.requires_lock:
                if not self.lock_manager.acquire_metadata_lock(timeout=30):
                    console.print("[red]无法获取元数据锁，可能有其他进程正在运行[/red]")
                    return

            # 执行步骤链
            if cmd.steps:
                console.print(f"[bold cyan]执行命令: {name}[/bold cyan]")

                # 如果有多个步骤，按顺序执行管道
                if len(cmd.steps) > 1:
                    # 链式执行：每个步骤的输出作为下一个步骤的输入
                    current_iter = None
                    for i, step in enumerate(cmd.steps):
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
                else:
                    # 单个步骤
                    cmd.steps[0](ctx, None)

                console.print(f"[green]命令执行完成: {name}[/green]")
            else:
                console.print(f"[yellow]命令 {name} 没有定义步骤[/yellow]")

        except KeyboardInterrupt:
            console.print("[yellow]命令被用户中断[/yellow]")
            EventEmitter.error("cancelled by user", {"command": name})
        except Exception as e:
            console.print(f"[red]命令执行错误: {str(e)}[/red]")
        finally:
            # 显示摘要
            self._display_summary()
            # 注销事件监听器
            from libgitmusic.events import EventEmitter

            EventEmitter.unregister_listener(self._handle_event)
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

    parser = argparse.ArgumentParser(description="GitMusic CLI")
    parser.add_argument("command", nargs="?", help="命令名称")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="命令参数")

    args = parser.parse_args()

    cli = GitMusicCLI()

    if args.command:
        cli.run_command(args.command, args.args)
    else:
        cli.repl()


if __name__ == "__main__":
    main()
