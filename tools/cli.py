import sys
import os
import sys
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Callable, Union, Any
import argparse
import shlex

from rich.console import Console
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
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Optional, Union, Any

# 导入核心库用于配置加载
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager

console = Console(color_system="auto")


@dataclass
class CommandOption:
    """命令选项定义"""

    name: str  # 选项名称，如 "--query"
    short: Optional[str] = None  # 短选项，如 "-q"
    help: str = ""  # 选项帮助文本
    metavar: Optional[str] = None  # 参数占位符
    required: bool = False  # 是否必需
    default: Any = None  # 默认值
    choices: Optional[List[str]] = None  # 可选值列表


@dataclass
class Command:
    """命令定义 - 统一管理命令的所有信息"""

    name: str  # 命令名称
    description: str  # 命令描述
    script: Path  # 对应的脚本路径
    default_args: List[str] = field(default_factory=list)  # 默认参数
    param_transformer: Optional[Callable[[List[str]], Optional[List[str]]]] = (
        None  # 参数转换器
    )
    options: List[CommandOption] = field(default_factory=list)  # 选项定义
    requires_lock: bool = False  # 是否需要元数据锁
    timeout_seconds: int = 3600  # 超时时间
    on_error: str = "stop"  # 错误处理策略：stop|continue|notify
    workflow: Optional[List] = None  # 工作流步骤（用于复合命令）
    examples: List[str] = field(default_factory=list)  # 使用示例
    aliases: List[str] = field(default_factory=list)  # 命令别名


class GitMusicCLI:
    """GitMusic CLI - 仅负责命令调度和事件流显示，不包含具体业务逻辑"""

    def __init__(self, config_path: Optional[str] = None):
        self.repo_root = Path(__file__).parent.parent
        self.project_root = self.repo_root.parent
        self.config = self._load_config(config_path)

        # 只初始化必要的组件（用于配置）
        metadata_file = self._get_path("metadata_file")
        self.metadata_mgr = MetadataManager(metadata_file)

        # 命令注册表
        self.commands = self._register_commands()

        # REPL会话（延迟初始化，避免Windows终端问题）
        self.session = None

        # 事件日志
        self.event_log = []

        # 冲突信息存储
        self.last_conflict = None

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
        """注入环境变量供子进程脚本使用"""
        os.environ["GITMUSIC_WORK_DIR"] = str(self._get_path("work_dir"))
        os.environ["GITMUSIC_CACHE_ROOT"] = str(self._get_path("cache_root"))
        os.environ["GITMUSIC_METADATA_FILE"] = str(self._get_path("metadata_file"))
        transport = self.config.get("transport", {})
        os.environ["GITMUSIC_REMOTE_USER"] = transport.get("user", "")
        os.environ["GITMUSIC_REMOTE_HOST"] = transport.get("host", "")
        os.environ["GITMUSIC_REMOTE_DATA_ROOT"] = transport.get("remote_data_root", "")
        # 调试：检查环境变量是否设置
        import sys

        if "--debug-env" in sys.argv:
            console.print(
                f"[yellow]DEBUG: GITMUSIC_METADATA_FILE = {os.environ.get('GITMUSIC_METADATA_FILE')}[/yellow]"
            )

    def run_script(self, script_path: Path, args: List[str], phase_name: str) -> int:
        """
        运行外部脚本并捕获其JSONL事件流

        Args:
            script_path: 脚本路径（相对于repo_root）
            args: 脚本参数
            phase_name: 阶段名称（用于进度显示）

        Returns:
            脚本退出码
        """
        self._inject_env()

        # 如果是相对路径，转换为相对于repo_root的绝对路径
        if not script_path.is_absolute():
            script_path = self.repo_root / script_path

        if not script_path.exists():
            console.print(f"[red]脚本不存在: {script_path}[/red]")
            return 1

        cmd = [sys.executable, str(script_path)] + args
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        return self._process_event_stream(process, phase_name)

    def _process_event_stream(self, process: subprocess.Popen, phase_name: str) -> int:
        """处理脚本输出的事件流并显示进度"""
        # 如果设置了简单输出模式，直接使用简单模式
        if os.environ.get("GITMUSIC_SIMPLE_OUTPUT"):
            return self._process_event_stream_simple(process, phase_name)

        # 尝试使用Rich进度条，失败时降级到简单输出
        try:
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

                if process.stdout is None:
                    process.wait()
                    return process.returncode or 1

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
                                progress.console.print(
                                    f"[dim] {status}: {item_id}[/dim]"
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
                                progress.console.print(
                                    f"[red]RESULT ERROR: {msg}[/red]"
                                )
                            elif status == "warn":
                                progress.console.print(
                                    f"[yellow]RESULT WARN: {msg}[/yellow]"
                                )
                            elif status == "conflict":
                                progress.console.print(
                                    f"[bold yellow]CONFLICT DETECTED: {msg}[/bold yellow]"
                                )
                                # 存储冲突信息供后续处理
                                artifacts = event.get("artifacts", {})
                                if "conflicts" in artifacts:
                                    self.last_conflict = artifacts
                            else:
                                progress.console.print(
                                    f"[green]RESULT OK: {msg}[/green]"
                                )

                            # 显示artifacts中的内容
                            artifacts = event.get("artifacts", {})
                            if "entries" in artifacts:
                                entries = artifacts["entries"]
                                if entries:
                                    # 获取命令名（从事件中或使用阶段名）
                                    cmd_name = event.get("cmd", phase_name.lower())
                                    self._format_entries_display(
                                        cmd_name, entries, progress.console
                                    )
                                if artifacts.get("truncated"):
                                    progress.console.print(
                                        f"[yellow]注意: 只显示前 {len(entries)} 个条目（共 {artifacts.get('count', 0)} 个）[/yellow]"
                                    )
                            elif artifacts:
                                # 显示其他artifacts数据（如sync的分析结果）
                                cmd_name = event.get("cmd", phase_name.lower())
                                # 调试：打印artifacts内容
                                # progress.console.print(f"[dim]DEBUG: cmd={cmd_name}, artifacts keys={list(artifacts.keys())}[/dim]")
                                if cmd_name in ["sync", "sync_cache"]:
                                    # 专门处理sync命令的分析结果
                                    self._format_sync_artifacts(
                                        artifacts, progress.console
                                    )
                                if cmd_name == "sync":
                                    # 专门处理sync命令的分析结果
                                    self._format_sync_artifacts(
                                        artifacts, progress.console
                                    )

                    except json.JSONDecodeError:
                        if line:
                            progress.console.print(f"[dim]{line}[/dim]")
                    except Exception as exc:
                        progress.console.print(
                            f"[red]Event parsing error: {str(exc)}[/red]"
                        )

                process.wait()
                return process.returncode

        except (UnicodeEncodeError, Exception) as exc:
            # 降级到简单输出模式
            console.print(
                f"[yellow]Warning: Falling back to simple output ({exc})[/yellow]"
            )
            return self._process_event_stream_simple(process, phase_name)

    def _process_event_stream_simple(
        self, process: subprocess.Popen, phase_name: str
    ) -> int:
        """简单的事件流处理 - 用于降级模式"""
        if process.stdout is None:
            process.wait()
            return process.returncode or 1

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
                self.event_log.append(event)
                etype = event.get("type")

                if etype == "log":
                    level = event.get("level", "info")
                    msg = event.get("message", "")
                    prefix = f"{level.upper()}: " if level != "info" else ""
                    print(f"{prefix}{msg}")
                elif etype == "error":
                    msg = event.get("message", "")
                    print(f"ERROR: {msg}")
                elif etype == "result":
                    status = event.get("status", "unknown")
                    msg = event.get("message", "")
                    print(f"RESULT {status.upper()}: {msg}")

                    # 显示artifacts中的内容
                    artifacts = event.get("artifacts", {})
                    if "entries" in artifacts:
                        entries = artifacts["entries"]
                        if entries:
                            # 获取命令名（从事件中或使用阶段名）
                            cmd_name = event.get("cmd", phase_name.lower())
                            self._format_entries_display(cmd_name, entries, None)

                    elif artifacts:
                        # 显示其他artifacts数据（如sync的分析结果）
                        cmd_name = event.get("cmd", phase_name.lower())
                        if cmd_name in ["sync", "sync_cache"]:
                            # 专门处理sync命令的分析结果
                            self._format_sync_artifacts(artifacts, None)
                # 忽略进度事件，在简单模式下不显示

            except json.JSONDecodeError:
                if line:
                    print(f"RAW: {line}")
            except Exception as exc:
                print(f"Event parsing error: {exc}")

        process.wait()
        return process.returncode

    def _transform_checkout_args(self, args: List[str]) -> Optional[List[str]]:
        """转换checkout命令参数到脚本参数"""
        if not args:
            console.print("[red]错误: checkout命令需要参数[/red]")
            console.print("[yellow]用法:[/yellow]")
            console.print("  checkout sha256:<oid>           # 按OID检出单个文件")
            console.print("  checkout --query <pattern>      # 搜索并检出")
            console.print("  checkout --missing <fields>     # 检出缺失指定字段的文件")
            console.print("  checkout --help                 # 显示帮助")
            return None

        transformed = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--query":
                if i + 1 < len(args):
                    transformed.extend(["--batch", "--pattern", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--query需要参数[/red]")
                    return None
            elif arg == "--missing":
                if i + 1 < len(args):
                    # 将逗号分隔的字段转换为多个--missing参数
                    fields = args[i + 1].split(",")
                    transformed.append("--batch")
                    for field in fields:
                        transformed.extend(["--missing", field.strip()])
                    i += 2
                else:
                    console.print("[red]--missing需要参数[/red]")
                    return None
            elif arg == "--limit":
                if i + 1 < len(args):
                    transformed.extend(["--max", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--limit需要参数[/red]")
                    return None
            elif arg in ["--force", "-f"]:
                transformed.append(arg)
                i += 1
            elif arg.startswith("sha256:"):
                # 直接传递OID作为位置参数
                transformed.append(arg)
                i += 1
            else:
                # 其他参数直接传递
                transformed.append(arg)
                i += 1

        # 检查是否有有效的参数（不只是--force）
        has_valid_param = any(
            arg.startswith("sha256:")
            or "--batch" in transformed
            or arg == "--batch"  # 可能直接传递了--batch
            for arg in transformed
        )

        if not has_valid_param:
            console.print("[red]错误: 需要指定检出方式[/red]")
            console.print("[yellow]用法:[/yellow]")
            console.print("  checkout sha256:<oid>           # 按OID检出单个文件")
            console.print("  checkout --query <pattern>      # 搜索并检出")
            console.print("  checkout --missing <fields>     # 检出缺失指定字段的文件")
            return None

        return transformed

    def _transform_analyze_args(self, args: List[str]) -> Optional[List[str]]:
        """转换analyze命令参数到脚本参数"""
        transformed = []
        i = 0

        # 处理查询词作为位置参数（不以--开头的第一个参数）
        if args and not args[0].startswith("--"):
            transformed.append(args[0])  # 查询词作为位置参数
            i = 1

        while i < len(args):
            arg = args[i]
            if arg == "--duplicates":
                # 调用analyze_duplicates.py脚本
                return ["--duplicates"]
            elif arg == "--search":
                if i + 1 < len(args):
                    # 搜索关键词作为位置参数（覆盖前面的查询词）
                    if transformed and not transformed[0].startswith("--"):
                        transformed[0] = args[i + 1]  # 替换之前的查询词
                    else:
                        transformed.append(args[i + 1])  # 添加查询词
                    i += 2
                else:
                    console.print("[red]--search需要参数[/red]")
                    return None
            elif arg == "--search-field":
                if i + 1 < len(args):
                    transformed.extend(["--search-field", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--search-field需要参数[/red]")
                    return None
            elif arg == "--line":
                if i + 1 < len(args):
                    transformed.extend(["--line", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--line需要参数[/red]")
                    return None
            elif arg in ["--read-fields", "--filter"]:
                if i + 1 < len(args):
                    transformed.extend(["--fields", args[i + 1]])
                    i += 2
                else:
                    console.print(f"[red]{arg}需要参数[/red]")
                    return None
            elif arg == "--missing":
                if i + 1 < len(args):
                    transformed.extend(["--missing", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--missing需要参数[/red]")
                    return None
            elif arg in ["--stats", "--count", "--case-sensitive"]:
                transformed.append(arg)
                i += 1
            elif arg == "--output":
                if i + 1 < len(args):
                    transformed.extend(["--output", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--output需要参数[/red]")
                    return None
            else:
                # 其他参数直接传递
                transformed.append(arg)
                i += 1
        return transformed

    def _transform_sync_args(self, args: List[str]) -> Optional[List[str]]:
        """转换sync命令参数到脚本参数，并验证参数有效性"""
        transformed = []
        i = 0

        while i < len(args):
            arg = args[i]
            if arg in ["--help", "-h"]:
                transformed.append(arg)
                i += 1
            elif arg == "--direction":
                if i + 1 < len(args):
                    direction = args[i + 1]
                    # 验证方向值
                    if direction not in ["upload", "download", "both"]:
                        console.print(
                            f"[red]错误: --direction 必须是 upload/download/both，得到 {direction}[/red]"
                        )
                        return None
                    transformed.extend(["--direction", direction])
                    i += 2
                else:
                    console.print("[red]--direction需要参数[/red]")
                    return None
            elif arg == "--workers":
                if i + 1 < len(args):
                    workers = args[i + 1]
                    # 验证是否为数字
                    if not workers.isdigit():
                        console.print("[red]错误: --workers 必须是数字[/red]")
                        return None
                    transformed.extend(["--workers", workers])
                    i += 2
                else:
                    console.print("[red]--workers需要参数[/red]")
                    return None
            elif arg == "--timeout":
                if i + 1 < len(args):
                    timeout = args[i + 1]
                    if not timeout.isdigit():
                        console.print("[red]错误: --timeout 必须是数字[/red]")
                        return None
                    transformed.extend(["--timeout", timeout])
                    i += 2
                else:
                    console.print("[red]--timeout需要参数[/red]")
                    return None
            elif arg == "--retries":
                if i + 1 < len(args):
                    retries = args[i + 1]
                    if not retries.isdigit():
                        console.print("[red]错误: --retries 必须是数字[/red]")
                        return None
                    transformed.extend(["--retries", retries])
                    i += 2
                else:
                    console.print("[red]--retries需要参数[/red]")
                    return None
            elif arg == "--dry-run":
                transformed.append("--dry-run")
                i += 1
            else:
                # 其他未知参数直接传递
                transformed.append(arg)
                i += 1

        return transformed

    def _transform_verify_args(self, args: List[str]) -> Optional[List[str]]:
        """转换verify命令参数到脚本参数，并验证参数有效性"""
        transformed = []
        i = 0

        while i < len(args):
            arg = args[i]
            if arg in ["--help", "-h"]:
                transformed.append(arg)
                i += 1
            elif arg == "--mode":
                if i + 1 < len(args):
                    mode = args[i + 1]
                    if mode not in ["local", "server", "release"]:
                        console.print(
                            f"[red]错误: --mode 必须是 local/server/release，得到 {mode}[/red]"
                        )
                        return None
                    transformed.extend(["--mode", mode])
                    i += 2
                else:
                    console.print("[red]--mode需要参数[/red]")
                    return None
            elif arg == "--path":
                if i + 1 < len(args):
                    # 注意：当前脚本可能不支持 --path 参数
                    transformed.extend(["--path", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--path需要参数[/red]")
                    return None
            else:
                # 其他参数直接传递
                transformed.append(arg)
                i += 1

        return transformed

    def _transform_cleanup_args(self, args: List[str]) -> Optional[List[str]]:
        """转换cleanup命令参数到脚本参数，并验证参数有效性"""
        transformed = []
        i = 0

        has_dry_run = False
        has_force = False
        has_confirm = False

        while i < len(args):
            arg = args[i]
            if arg in ["--help", "-h"]:
                transformed.append(arg)
                i += 1
            elif arg == "--dry-run":
                transformed.append("--dry-run")
                has_dry_run = True
                i += 1
            elif arg == "--force" or arg == "-f":
                transformed.append(arg)
                has_force = True
                i += 1
            elif arg == "--confirm":
                transformed.append("--confirm")
                has_confirm = True
                i += 1
            elif arg == "--mode":
                if i + 1 < len(args):
                    mode = args[i + 1]
                    if mode not in ["local", "server", "both"]:
                        console.print(
                            f"[red]错误: --mode 必须是 local/server/both，得到 {mode}[/red]"
                        )
                        return None
                    transformed.extend(["--mode", mode])
                    i += 2
                else:
                    console.print("[red]--mode需要参数[/red]")
                    return None
            else:
                # 其他未知参数直接传递
                transformed.append(arg)
                i += 1

        # cleanup默认是安全的dry-run模式，仅显示分析结果
        # 需要--confirm或--force才会执行删除
        # 如果指定了--force，将其转换为--confirm传递给脚本
        if has_force and not has_confirm:
            # --force 相当于 --confirm
            transformed.append("--confirm")

        return transformed

    def _transform_download_args(self, args: List[str]) -> Optional[List[str]]:
        """转换download命令参数到脚本参数，并验证参数有效性"""
        if not args:
            console.print("[red]错误: download命令需要参数[/red]")
            console.print("[yellow]用法:[/yellow]")
            console.print("  download <URL>                     # 下载单个视频")
            console.print("  download --batch-file <path>       # 批量下载")
            console.print("  download --fetch <URL>             # 仅预览元数据")
            console.print("  download --help                    # 显示帮助")
            return None

        transformed = []
        i = 0
        has_url = False
        has_batch_file = False

        while i < len(args):
            arg = args[i]
            if arg in ["--help", "-h"]:
                # 帮助请求由上层处理
                transformed.append(arg)
                i += 1
            elif arg == "--fetch":
                # 映射到脚本的 --metadata-only 参数
                transformed.append("--metadata-only")
                i += 1
            elif arg == "--no-preview":
                # 传递给脚本
                transformed.append("--no-preview")
                i += 1
            elif arg == "--batch-file":
                if i + 1 < len(args):
                    transformed.extend(["--batch-file", args[i + 1]])
                    has_batch_file = True
                    i += 2
                else:
                    console.print("[red]--batch-file需要参数[/red]")
                    return None
            elif arg == "--limit":
                if i + 1 < len(args):
                    transformed.extend(["--limit", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--limit需要参数[/red]")
                    return None
            elif arg == "--output-dir":
                if i + 1 < len(args):
                    transformed.extend(["--output-dir", args[i + 1]])
                    i += 2
                else:
                    console.print("[red]--output-dir需要参数[/red]")
                    return None
            elif arg == "--no-cover":
                transformed.append("--no-cover")
                i += 1
            elif arg.startswith("http://") or arg.startswith("https://"):
                # URL位置参数
                transformed.append(arg)
                has_url = True
                i += 1
            else:
                # 其他未知参数直接传递（可能脚本支持）
                transformed.append(arg)
                i += 1

        # 验证：必须有URL或--batch-file
        if not has_url and not has_batch_file:
            console.print("[red]错误: 需要指定URL或--batch-file[/red]")
            console.print("[yellow]用法示例:[/yellow]")
            console.print("  download https://youtube.com/watch?v=...")
            console.print("  download --batch-file urls.txt")
            console.print("  download --fetch https://youtube.com/watch?v=...")
            return None

        return transformed

    def _transform_release_args(self, args: List[str]) -> Optional[List[str]]:
        """转换release命令参数到脚本参数，并验证参数有效性"""
        transformed = []
        i = 0

        while i < len(args):
            arg = args[i]
            if arg in ["--help", "-h"]:
                transformed.append(arg)
                i += 1
            elif arg in ["--force", "-f"]:
                transformed.append(arg)
                i += 1
            elif arg == "--mode":
                if i + 1 < len(args):
                    mode = args[i + 1]
                    if mode not in ["local", "server"]:
                        console.print(
                            f"[red]错误: --mode 必须是 local/server，得到 {mode}[/red]"
                        )
                        return None
                    transformed.extend(["--mode", mode])
                    i += 2
                else:
                    console.print("[red]--mode需要参数[/red]")
                    return None
            elif arg in ["--line", "-l"]:
                if i + 1 < len(args):
                    line_arg = args[i + 1]
                    # 验证行号格式：可以是单个数字、逗号分隔的数字或范围
                    # 这里简单验证，具体解析由脚本处理
                    transformed.extend(["--line", line_arg])
                    i += 2
                else:
                    console.print("[red]--line需要参数[/red]")
                    return None
            elif arg == "--workers":
                if i + 1 < len(args):
                    workers = args[i + 1]
                    if not workers.isdigit():
                        console.print("[red]错误: --workers 必须是数字[/red]")
                        return None
                    transformed.extend(["--workers", workers])
                    i += 2
                else:
                    console.print("[red]--workers需要参数[/red]")
                    return None
            else:
                # 其他参数直接传递（可能脚本支持如--search、--hash等）
                transformed.append(arg)
                i += 1

        return transformed

    def _transform_fetch_args(self, args: List[str]) -> List[str]:
        """转换fetch命令参数到脚本参数"""
        # fetch已经设置了default_args ["--fetch"]
        return args

    def _format_entries_display(self, cmd: str, entries: List[Dict], console_obj=None):
        """格式化显示entries列表，根据命令类型使用不同格式"""
        if not entries:
            return

        # 限制显示数量
        max_display = 20
        display_entries = entries[:max_display]
        total_count = len(entries)

        # 根据命令类型选择显示格式
        if cmd == "cleanup":
            # cleanup命令：显示孤立文件列表
            if console_obj:
                # Rich模式
                from rich.table import Table

                table = Table(title=f"孤立文件 ({total_count} 个)", show_lines=True)
                table.add_column("类型", style="cyan")
                table.add_column("文件名", style="green")
                table.add_column("路径", style="dim")

                for entry in display_entries:
                    file_type = entry.get("type", "未知")
                    file_name = entry.get("name", "")
                    file_path = entry.get("path", entry.get("file", ""))
                    # 截断过长的路径
                    if len(file_path) > 60:
                        file_path = "..." + file_path[-57:]
                    table.add_row(file_type, file_name, file_path)

                console_obj.print(table)
            else:
                # 简单模式
                print(f"孤立文件 ({total_count} 个):")
                for i, entry in enumerate(display_entries, 1):
                    file_type = entry.get("type", "未知")
                    file_name = entry.get("name", "")
                    file_path = entry.get("path", entry.get("file", ""))
                    print(f"{i:3d}. [{file_type}] {file_name}")
                    print(f"    路径: {file_path}")
                if total_count > max_display:
                    print(f"... 还有 {total_count - max_display} 个文件未显示")

        elif cmd == "analyze":
            # analyze命令：显示元数据条目
            if not display_entries:
                return

            # 获取所有可能的字段
            all_fields = set()
            for entry in display_entries:
                all_fields.update(entry.keys())

            # 选择要显示的字段（排除内部字段）
            exclude_fields = {"audio_oid", "cover_oid", "uslt", "_line", "_raw"}
            display_fields = [f for f in sorted(all_fields) if f not in exclude_fields]

            # 限制字段数量
            if len(display_fields) > 6:
                display_fields = display_fields[:6]
                display_fields.append("...")

            if console_obj:
                # Rich模式
                from rich.table import Table

                table = Table(title=f"匹配的条目 ({total_count} 个)", show_lines=True)
                for field in display_fields:
                    if field == "...":
                        table.add_column("...", style="dim")
                    else:
                        table.add_column(field, style="cyan")

                for entry in display_entries:
                    row = []
                    for field in display_fields:
                        if field == "...":
                            row.append("...")
                        else:
                            value = entry.get(field)
                            if isinstance(value, list):
                                row.append(", ".join(str(v) for v in value))
                            elif value is None:
                                row.append("")
                            else:
                                row.append(str(value))
                    table.add_row(*row)

                console_obj.print(table)
            else:
                # 简单模式
                print(f"匹配的条目 ({total_count} 个):")
                for i, entry in enumerate(display_entries, 1):
                    print(f"{i:3d}. ", end="")
                    first = True
                    for field in display_fields:
                        if field == "...":
                            continue
                        value = entry.get(field)
                        if value is not None:
                            if not first:
                                print(" | ", end="")
                            if isinstance(value, list):
                                value_str = ", ".join(str(v) for v in value)
                            else:
                                value_str = str(value)
                            print(f"{field}: {value_str}", end="")
                            first = False
                    print()

        else:
            # 通用格式：表格显示
            if console_obj:
                # Rich模式 - 通用表格
                from rich.table import Table

                # 获取样本条目的字段
                sample = display_entries[0] if display_entries else {}
                fields = list(sample.keys())
                if len(fields) > 6:
                    fields = fields[:6]
                    fields.append("...")

                table = Table(title=f"条目 ({total_count} 个)", show_lines=True)
                for field in fields:
                    if field == "...":
                        table.add_column("...", style="dim")
                    else:
                        table.add_column(field, style="cyan")

                for entry in display_entries:
                    row = []
                    for field in fields:
                        if field == "...":
                            row.append("...")
                        else:
                            value = entry.get(field)
                            if isinstance(value, list):
                                row.append(", ".join(str(v) for v in value))
                            elif value is None:
                                row.append("")
                            else:
                                row.append(str(value))
                    table.add_row(*row)

                console_obj.print(table)
            else:
                # 简单模式 - 通用格式
                print(f"条目 ({total_count} 个):")
                for i, entry in enumerate(display_entries, 1):
                    print(f"{i:3d}. ", end="")
                    items = []
                    for key, value in entry.items():
                        if isinstance(value, list):
                            value_str = ", ".join(str(v) for v in value)
                        else:
                            value_str = str(value)
                        if len(value_str) > 50:
                            value_str = value_str[:47] + "..."
                        items.append(f"{key}: {value_str}")
                    print(" | ".join(items[:3]))
                    if len(items) > 3:
                        print("     " + " | ".join(items[3:6]))

        # 显示截断提示
        if total_count > max_display:
            if console_obj:
                console_obj.print(
                    f"[yellow]注意: 只显示前 {max_display} 个条目（共 {total_count} 个）[/yellow]"
                )
            else:
                print(f"注意: 只显示前 {max_display} 个条目（共 {total_count} 个）")

    def _format_sync_artifacts(self, artifacts: Dict, console_obj=None):
        """格式化显示sync命令的分析结果"""
        try:
            if console_obj:
                # Rich模式
                from rich.table import Table
                from rich import box

                # 创建概览表格
                overview_table = Table(
                    title="同步分析概览", box=box.SIMPLE, show_lines=False
                )
                overview_table.add_column("类别", style="cyan")
                overview_table.add_column("音频", style="green", justify="right")
                overview_table.add_column("封面", style="yellow", justify="right")
                overview_table.add_column("总计", style="bold", justify="right")

                # 添加本地数据
                local = artifacts.get("local", {})
                overview_table.add_row(
                    "本地",
                    str(local.get("audio", 0)),
                    str(local.get("covers", 0)),
                    str(local.get("total", 0)),
                )

                # 添加远程数据
                remote = artifacts.get("remote", {})
                overview_table.add_row(
                    "远程",
                    str(remote.get("audio", 0)),
                    str(remote.get("covers", 0)),
                    str(remote.get("total", 0)),
                )

                # 添加待上传数据
                to_upload = artifacts.get("to_upload", {})
                overview_table.add_row(
                    "待上传",
                    str(to_upload.get("audio", 0)),
                    str(to_upload.get("covers", 0)),
                    str(to_upload.get("total", 0)),
                )

                # 添加待下载数据
                to_download = artifacts.get("to_download", {})
                overview_table.add_row(
                    "待下载",
                    str(to_download.get("audio", 0)),
                    str(to_download.get("covers", 0)),
                    str(to_download.get("total", 0)),
                )

                console_obj.print(overview_table)
            else:
                # 简单模式
                print("\n同步分析结果:")
                print("=" * 40)

                local = artifacts.get("local", {})
                print(
                    f"本地:  音频={local.get('audio', 0)}  封面={local.get('covers', 0)}  总计={local.get('total', 0)}"
                )

                remote = artifacts.get("remote", {})
                print(
                    f"远程:  音频={remote.get('audio', 0)}  封面={remote.get('covers', 0)}  总计={remote.get('total', 0)}"
                )

                to_upload = artifacts.get("to_upload", {})
                print(
                    f"待上传: 音频={to_upload.get('audio', 0)}  封面={to_upload.get('covers', 0)}  总计={to_upload.get('total', 0)}"
                )

                to_download = artifacts.get("to_download", {})
                print(
                    f"待下载: 音频={to_download.get('audio', 0)}  封面={to_download.get('covers', 0)}  总计={to_download.get('total', 0)}"
                )
                print("=" * 40)

        except Exception as e:
            if console_obj:
                console_obj.print(f"[red]格式化sync结果失败: {str(e)}[/red]")
            else:
                print(f"格式化sync结果失败: {str(e)}")

    def _register_commands(self) -> Dict[str, Command]:
        """注册所有命令及其配置"""
        commands = {}

        # publish命令
        commands["publish"] = Command(
            name="publish",
            description="发布本地改动到库中",
            script=Path("work/publish_meta.py"),
            requires_lock=True,
            options=[
                CommandOption("--changed-only", help="仅处理有变动的文件"),
                CommandOption("--preview", help="仅显示预览，不执行发布"),
            ],
            examples=[
                "publish --preview           # 预览变更",
                "publish --changed-only      # 仅发布已变动的文件",
            ],
        )

        # checkout命令
        commands["checkout"] = Command(
            name="checkout",
            description="从缓存检出音频到工作目录",
            script=Path("work/checkout.py"),
            requires_lock=True,
            param_transformer=self._transform_checkout_args,
            options=[
                CommandOption("--query", metavar="<str>", help="搜索关键词"),
                CommandOption(
                    "--missing",
                    metavar="<fields>",
                    help="按缺失字段过滤 (cover,uslt,album,date)",
                ),
                CommandOption("--force", short="-f", help="强制覆盖已有文件"),
                CommandOption("--limit", metavar="<n>", help="限制处理数量"),
            ],
            examples=[
                "checkout --query 'try everything'      # 搜索并检出",
                "checkout --missing uslt --limit 1      # 检出缺失歌词的1个文件",
                "checkout sha256:abc123...             # 按OID检出",
            ],
        )

        # sync命令
        commands["sync"] = Command(
            name="sync",
            description="同步本地与远程缓存",
            script=Path("data/sync_cache.py"),
            requires_lock=False,
            param_transformer=self._transform_sync_args,
            options=[
                CommandOption(
                    "--direction",
                    metavar="<d>",
                    help="同步方向 (upload|download|both)",
                    choices=["upload", "download", "both"],
                ),
                CommandOption("--dry-run", help="仅显示差异，不执行同步"),
                CommandOption("--timeout", metavar="<n>", help="单文件超时时间"),
                CommandOption("--workers", metavar="<n>", help="并行线程数"),
                CommandOption("--retries", metavar="<n>", help="失败重试次数"),
            ],
            examples=[
                "sync --direction=upload --dry-run     # 预览上传内容",
                "sync --workers=8                      # 使用8个线程同步",
                "sync --retries=5 --timeout=30         # 设置重试和超时",
            ],
        )

        # verify命令
        commands["verify"] = Command(
            name="verify",
            description="校验文件哈希完整性",
            script=Path("data/verify_hashes.py"),
            requires_lock=False,
            param_transformer=self._transform_verify_args,
            options=[
                CommandOption("--path", metavar="<path>", help="指定校验路径"),
                CommandOption(
                    "--mode",
                    metavar="<mode>",
                    help="校验模式 (local|server|release)",
                    choices=["local", "server", "release"],
                ),
            ],
            examples=[
                "verify --mode release    # 校验release目录的文件哈希",
                "verify --path cache/objects  # 校验指定目录",
            ],
        )

        # cleanup命令
        commands["cleanup"] = Command(
            name="cleanup",
            description="清理孤立文件（默认仅显示分析结果，需要--confirm才会删除）",
            script=Path("data/cleanup_orphaned.py"),
            requires_lock=False,
            param_transformer=self._transform_cleanup_args,
            options=[
                CommandOption("--dry-run", help="显式指定仅显示分析结果（默认行为）"),
                CommandOption(
                    "--force", short="-f", help="强制删除不询问（相当于--confirm）"
                ),
                CommandOption(
                    "--mode",
                    metavar="<mode>",
                    help="清理模式 (local|server|both)",
                    choices=["local", "server", "both"],
                ),
                CommandOption("--confirm", help="确认执行删除操作"),
            ],
            examples=[
                "cleanup                              # 显示分析结果（默认安全模式）",
                "cleanup --mode both                 # 分析本地和远程孤立文件",
                "cleanup --mode local --confirm      # 确认删除本地孤立文件",
                "cleanup --mode both --force         # 强制删除本地和远程孤立文件",
            ],
        )

        # download命令
        commands["download"] = Command(
            name="download",
            description="下载音频并更新库",
            script=Path("tools/download_ytdlp.py"),
            requires_lock=True,
            param_transformer=self._transform_download_args,
            options=[
                # 位置参数通过示例说明
                CommandOption(
                    "--batch-file",
                    metavar="<path>",
                    help="批量下载文件路径（一行一个URL）",
                ),
                CommandOption("--fetch", help="仅获取元数据预览，不下载"),
                CommandOption("--limit", metavar="<n>", help="限制下载数量"),
                CommandOption("--output-dir", metavar="<path>", help="自定义输出目录"),
                CommandOption("--no-cover", help="不提取封面"),
            ],
            examples=[
                "download https://youtube.com/watch?v=...   # 下载单个视频",
                "download --batch-file urls.txt            # 批量下载",
                "download --fetch URL                     # 仅预览元数据",
                "download --limit 5 --batch-file urls.txt # 限制下载数量",
                "download --no-cover URL                  # 不提取封面下载",
            ],
        )

        # analyze命令
        commands["analyze"] = Command(
            name="analyze",
            description="分析元数据",
            script=Path("tools/analyze_metadata.py"),
            requires_lock=False,
            param_transformer=self._transform_analyze_args,
            options=[
                # 位置参数：query
                CommandOption("--search-field", metavar="<field>", help="指定搜索字段"),
                CommandOption(
                    "--line", metavar="<n>", help="按行号读取（逗号分隔或范围）"
                ),
                CommandOption(
                    "--read-fields", metavar="<fields>", help="提取指定字段（逗号分隔）"
                ),
                CommandOption(
                    "--missing",
                    metavar="<fields>",
                    help="过滤缺失指定字段的条目（逗号分隔）",
                ),
                CommandOption(
                    "--filter", metavar="<fields>", help="输出时过滤字段（逗号分隔）"
                ),
                CommandOption("--stats", help="显示统计信息"),
                CommandOption("--count", help="只显示匹配数量"),
                CommandOption("--duplicates", help="分析重复项"),
                CommandOption(
                    "--output", metavar="<path>", help="输出到文件（JSON格式）"
                ),
                CommandOption("--case-sensitive", help="区分大小写搜索"),
            ],
            examples=[
                "analyze 'try everything'                  # 搜索关键词",
                "analyze --line 1,5,10-15                # 读取指定行",
                "analyze --missing cover,uslt            # 查找缺失封面和歌词的文件",
                "analyze --duplicates                    # 分析重复项",
            ],
        )

        # release命令
        commands["release"] = Command(
            name="release",
            description="生成成品库",
            script=Path("release/create_release.py"),
            requires_lock=False,
            param_transformer=self._transform_release_args,
            options=[
                CommandOption(
                    "--mode",
                    metavar="<m>",
                    help="运行模式 (local|server)",
                    choices=["local", "server"],
                ),
                CommandOption("--force", short="-f", help="生成前清空目录"),
                CommandOption(
                    "--line", metavar="<n>", short="-l", help="按行号生成（短选项-l）"
                ),
                CommandOption("--workers", metavar="<n>", help="并行线程数"),
            ],
            examples=[
                "release --mode local       # 本地生成成品库",
                "release --force           # 强制重新生成",
                "release --line 5          # 只生成第5行对应的条目",
            ],
        )

        # fetch命令（download --fetch的别名）
        commands["fetch"] = Command(
            name="fetch",
            description="获取URL元数据预览（download --fetch的别名）",
            script=Path("tools/download_ytdlp.py"),
            default_args=["--fetch"],
            requires_lock=False,
            param_transformer=self._transform_fetch_args,
            options=[
                CommandOption(
                    "--batch-file",
                    metavar="<path>",
                    help="批量下载文件路径（一行一个URL）",
                ),
            ],
            examples=[
                "fetch https://youtube.com/watch?v=...   # 预览单个视频元数据",
                "fetch --batch-file urls.txt            # 批量预览",
            ],
        )

        # push和pull作为sync的别名，在run_command中特殊处理

        return commands

    def run_command(self, name: str, args: List[str]):
        """执行命令 - 调用相应的外部脚本"""

        # 特殊命令处理
        if name == "help":
            if args and args[0]:
                self.show_command_help(args[0])
            else:
                self.show_help()
            return

        if name == "push":
            self.run_command("sync", ["--direction=upload"] + args)
            return
        elif name == "pull":
            self.run_command("sync", ["--direction=download"] + args)
            return

        # 使用命令注册表
        if name not in self.commands:
            console.print(f"[red]未知命令: {name}[/red]")
            console.print("使用 'help' 查看可用命令")
            return

        cmd = self.commands[name]
        script = cmd.script

        # 处理帮助请求
        if "--help" in args or "-h" in args:
            self.show_command_help(name)
            return

        # 使用参数转换器转换参数
        if cmd.param_transformer:
            transformed_args = cmd.param_transformer(args)
            if transformed_args is None:
                return  # 转换器已显示错误信息
            script_args = cmd.default_args + transformed_args
        else:
            script_args = cmd.default_args + args

        # 特殊处理：analyze --duplicates 需要调用不同的脚本
        if name == "analyze" and "--duplicates" in script_args:
            script = Path("tools/analyze_duplicates.py")
            script_args = []  # analyze_duplicates.py 不需要参数

        # 对于publish命令的特殊处理
        if name == "publish":
            if "--preview" in args:
                # 仅预览模式（转换为脚本的--dry-run参数）
                console.print(f"[bold cyan]执行命令: {name} --preview[/bold cyan]")
                return_code = self.run_script(script, ["--dry-run"], "Analyze")
            else:
                # 完整工作流
                console.print(f"[bold cyan]执行完整发布工作流[/bold cyan]")

                # 1. 预览
                console.print("\n[bold]步骤 1/6: 分析变更[/bold]")
                return_code = self.run_script(script, ["--dry-run"], "Analyze")
                if return_code != 0:
                    console.print("[red]分析失败，中止发布[/red]")
                    return

                # 确认
                try:
                    confirm = input("\n确认发布以上变更? [y/N]: ").lower()
                    if confirm != "y":
                        console.print("[yellow]发布已取消[/yellow]")
                        return
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[yellow]发布已取消[/yellow]")
                    return

                # 2. 发布
                console.print("\n[bold]步骤 2/6: 发布元数据[/bold]")
                return_code = self.run_script(script, [], "Publish")
                if return_code != 0:
                    console.print("[red]发布失败[/red]")
                    return

                # 3. 压缩图片
                console.print("\n[bold]步骤 3/6: 压缩封面[/bold]")
                return_code = self.run_script(
                    Path("data/compress_images.py"), [], "Compress"
                )

                # 4. 校验哈希
                console.print("\n[bold]步骤 4/6: 校验哈希[/bold]")
                return_code = self.run_script(
                    Path("data/verify_hashes.py"), [], "Verify"
                )
                if return_code != 0:
                    console.print("[yellow]哈希校验有警告，但继续执行[/yellow]")

                # 5. 同步到服务器
                console.print("\n[bold]步骤 5/6: 同步到服务器[/bold]")
                return_code = self.run_script(
                    Path("data/sync_cache.py"), ["--direction=upload"], "Push"
                )
                if return_code != 0:
                    console.print("[red]同步失败[/red]")
                    return

                # 6. Git提交
                console.print("\n[bold]步骤 6/6: Git提交[/bold]")
                try:
                    subprocess.run(
                        ["git", "add", "metadata.jsonl"], cwd=self.repo_root, check=True
                    )
                    subprocess.run(
                        ["git", "commit", "-m", "update: metadata and objects"],
                        cwd=self.repo_root,
                        check=True,
                    )
                    subprocess.run(["git", "push"], cwd=self.repo_root, check=True)
                    console.print("[green]Git提交成功[/green]")
                except subprocess.CalledProcessError as e:
                    console.print(f"[red]Git操作失败: {e}[/red]")
                    return

                console.print("[green]发布工作流完成[/green]")
        else:
            # 其他命令直接运行
            console.print(f"[bold cyan]执行命令: {name}[/bold cyan]")
            return_code = self.run_script(script, script_args, name.capitalize())

            if return_code == 0:
                console.print(f"[green]命令执行完成: {name}[/green]")
            else:
                console.print(
                    f"[red]命令执行失败: {name} (退出码: {return_code})[/red]"
                )

    def repl(self):
        """REPL交互模式"""
        console.print(
            Panel(Text("GitMusic CLI 管理系统", style="bold magenta", justify="center"))
        )
        console.print("输入 'help' 查看命令列表, 'exit' 退出\n")

        # 初始化session（如果需要）
        if self.session is None:
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.history import FileHistory
                from prompt_toolkit.lexers import PygmentsLexer
                from pygments.lexers.shell import BashLexer

                self.session = PromptSession(
                    history=FileHistory(str(self.project_root / ".cli_history")),
                    lexer=PygmentsLexer(BashLexer),
                )
            except Exception:
                self.session = None  # 保持None，使用fallback

        while True:
            try:
                if self.session is not None:
                    text = self.session.prompt("gitmusic > ").strip()
                else:
                    text = input("gitmusic > ").strip()

                if not text:
                    continue
                if text.lower() in ["exit", "quit"]:
                    break
                if text.lower() == "help":
                    self.show_help()
                    continue

                try:
                    parts = shlex.split(text)
                except Exception:
                    # 如果shlex解析失败，回退到简单分割
                    parts = text.split()
                self.run_command(parts[0], parts[1:])

            except KeyboardInterrupt:
                continue
            except EOFError:
                break

    def show_command_help(self, name: str):
        """显示命令详细帮助"""
        # 处理别名
        if name == "push":
            console.print(
                Panel(Text(f"命令帮助: {name} (sync的别名)", style="bold cyan"))
            )
            console.print("[bold]描述:[/bold] 同步本地缓存到服务器")
            console.print("[bold]等效命令:[/bold] sync --direction=upload")
            console.print("[bold]选项:[/bold] 与 sync --direction=upload 相同")
            console.print("[bold]示例:[/bold]")
            console.print("  push                     # 上传所有变更")
            console.print("  push --dry-run          # 仅显示将上传的文件")
            return
        elif name == "pull":
            console.print(
                Panel(Text(f"命令帮助: {name} (sync的别名)", style="bold cyan"))
            )
            console.print("[bold]描述:[/bold] 从服务器同步缓存到本地")
            console.print("[bold]等效命令:[/bold] sync --direction=download")
            console.print("[bold]选项:[/bold] 与 sync --direction=download 相同")
            console.print("[bold]示例:[/bold]")
            console.print("  pull                     # 下载所有变更")
            console.print("  pull --dry-run          # 仅显示将下载的文件")
            return

        # 检查命令是否存在
        if name not in self.commands:
            console.print(f"[dim]命令 {name} 暂无详细帮助信息[/dim]")
            return

        cmd = self.commands[name]

        # 构建帮助文本
        help_text = f"[bold]描述:[/bold] {cmd.description}\n"

        # 选项部分
        if cmd.options:
            help_text += "[bold]选项:[/bold]\n"
            for opt in cmd.options:
                opt_display = opt.name
                if opt.short:
                    opt_display += f", {opt.short}"
                if opt.metavar:
                    opt_display += f" {opt.metavar}"
                help_text += f"  {opt_display:<25} {opt.help}\n"

        # 特殊处理：download命令有位置参数
        if name == "download":
            help_text += "\n[bold]位置参数:[/bold]\n"
            help_text += "  <URL>                    下载地址\n"

        # 特殊处理：analyze命令有位置参数
        if name == "analyze":
            help_text += "\n[bold]位置参数:[/bold]\n"
            help_text += "  <query>                  搜索关键词（在所有字段中搜索）\n"

        # 别名信息
        if name == "sync":
            help_text += "\n[bold]别名:[/bold]\n"
            help_text += "  push = sync --direction=upload\n"
            help_text += "  pull = sync --direction=download\n"
        elif name == "download":
            help_text += "\n[bold]别名:[/bold] fetch = download --fetch\n"
        elif name == "fetch":
            help_text += "\n[bold]注意:[/bold] 此命令是 download --fetch 的别名\n"

        # 工作流信息（特殊命令）
        if name == "publish":
            help_text += "\n[bold]工作流:[/bold] 分析 → 确认 → 压缩封面 → 校验哈希 → 同步上传 → Git提交\n"
        elif name == "download":
            help_text += "\n[bold]工作流:[/bold] 元数据预览 → 下载 → 压缩封面 → 校验哈希 → 同步缓存 → Git提交\n"
        elif name == "release":
            help_text += (
                "\n[bold]工作流:[/bold] Git pull → 同步缓存 → 校验哈希 → 生成成品\n"
            )

        # 示例部分
        if cmd.examples:
            help_text += "\n[bold]示例:[/bold]\n"
            for example in cmd.examples:
                help_text += f"  {example}\n"

        # 显示帮助
        console.print(Panel(Text(f"命令帮助: {name}", style="bold cyan")))
        console.print(help_text)

    def show_help(self):
        """显示所有命令帮助"""
        # 从命令注册表构建列表，包含别名
        command_list = []

        # 主要命令（在commands注册表中的）
        for name, cmd in self.commands.items():
            command_list.append((name, cmd.description))

        # 添加别名
        command_list.append(("push", "同步本地缓存到服务器 (sync --direction=upload)"))
        command_list.append(
            ("pull", "从服务器同步缓存到本地 (sync --direction=download)")
        )

        # 按命令名排序
        command_list.sort(key=lambda x: x[0])

        table = Table(title="可用命令")
        table.add_column("命令", style="cyan")
        table.add_column("描述", style="green")

        for cmd_name, desc in command_list:
            table.add_row(cmd_name, desc)

        console.print(table)
        console.print("\n使用 'help <命令名>' 查看详细选项")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="GitMusic CLI")
    parser.add_argument("command", nargs="?", help="命令名称")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="命令参数")
    parser.add_argument(
        "--simple", action="store_true", help="使用简单输出模式（不显示进度条）"
    )

    args = parser.parse_args()

    # 设置环境变量，供降级模式使用
    if args.simple:
        os.environ["GITMUSIC_SIMPLE_OUTPUT"] = "1"

    cli = GitMusicCLI()

    try:
        if args.command:
            cli.run_command(args.command, args.args)
        else:
            cli.repl()
    except (KeyboardInterrupt, SystemExit):
        print("\n程序已退出")
        sys.exit(0)
    except Exception as exc:
        import traceback

        print(f"程序错误: {exc}", file=sys.stderr)
        if os.environ.get("GITMUSIC_DEBUG"):
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
