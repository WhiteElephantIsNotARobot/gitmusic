import sys
import os
import json
import time
import datetime
import hashlib
import shutil
import subprocess
import yaml
from pathlib import Path
from typing import List, Callable, Dict, Any, Optional
from send2trash import send2trash

from rich.console import Console
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
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
from libgitmusic.audio import AudioIO
from libgitmusic.metadata import MetadataManager
from libgitmusic.transport import TransportAdapter

console = Console()

class Command:
    def __init__(self, name: str, desc: str, steps: List[Callable] = None, requires_lock: bool = False):
        self.name = name
        self.desc = desc
        self.steps = steps or []
        self.requires_lock = requires_lock

class GitMusicCLI:
    def __init__(self, config_path: Optional[str] = None):
        self.repo_root = Path(__file__).parent.parent
        self.project_root = self.repo_root.parent
        self.config = self._load_config(config_path)
        self.metadata_mgr = MetadataManager(self._get_path("metadata_file"))
        self.session = PromptSession(history=FileHistory(str(self.repo_root / ".cli_history")))
        self.commands: Dict[str, Command] = {}
        self._register_all_commands()

    def _load_config(self, path: Optional[str]) -> dict:
        config_file = Path(path) if path else self.project_root / "config.yaml"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        return {
            "transport": {"user": "white_elephant", "host": "debian-server", "remote_data_root": "/srv/music/data"},
            "paths": {
                "work_dir": str(self.project_root / "work"),
                "cache_root": str(self.project_root / "cache"),
                "metadata_file": str(self.repo_root / "metadata.jsonl")
            }
        }

    def _get_path(self, key: str) -> Path:
        path_str = self.config.get("paths", {}).get(key, "")
        return Path(path_str).resolve()

    def _inject_env(self):
        """注入环境变量供子进程脚本使用"""
        os.environ["GITMUSIC_WORK_DIR"] = str(self._get_path("work_dir"))
        os.environ["GITMUSIC_CACHE_ROOT"] = str(self._get_path("cache_root"))
        os.environ["GITMUSIC_METADATA_FILE"] = str(self._get_path("metadata_file"))
        os.environ["GITMUSIC_REMOTE_USER"] = self.config.get("transport", {}).get("user", "")
        os.environ["GITMUSIC_REMOTE_HOST"] = self.config.get("transport", {}).get("host", "")
        os.environ["GITMUSIC_REMOTE_DATA_ROOT"] = self.config.get("transport", {}).get("remote_data_root", "")

    def register_command(self, name: str, desc: str, steps: List[Callable] = None, requires_lock: bool = False):
        self.commands[name] = Command(name, desc, steps or [], requires_lock)

    def run_script(self, script_path: Path, args: List[str], phase_name: str):
        """通用脚本运行器，捕获 JSONL 事件流并驱动 UI"""
        self._inject_env()
        cmd = [sys.executable, str(script_path)] + args
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TimeElapsedColumn(), console=console) as progress:
            task = None

            for line in process.stdout:
                try:
                    event = json.loads(line)
                    etype = event.get("type")

                    if etype == "phase_start":
                        task = progress.add_task(event.get("phase", phase_name), total=event.get("total_items", 0))
                    elif etype == "batch_progress" and task is not None:
                        progress.update(task, completed=event.get("processed", 0))
                    elif etype == "item_event":
                        progress.console.print(f"[dim] {event.get('status').upper()}: {event.get('id')}[/dim]")
                    elif etype == "log":
                        level = event.get("level", "info")
                        msg = event.get("message", "")
                        if level == "error": progress.console.print(f"[red]ERROR: {msg}[/red]")
                        elif level == "warn": progress.console.print(f"[yellow]WARN: {msg}[/yellow]")
                        else: progress.console.print(f"[blue]LOG: {msg}[/blue]")
                    elif etype == "error":
                        progress.console.print(f"[bold red]CRITICAL ERROR: {event.get('message')}[/bold red]")
                except json.JSONDecodeError:
                    # 非 JSON 输出（如 yt-dlp 的原始输出）直接打印或忽略
                    if line.strip():
                        progress.console.print(f"[dim]{line.strip()}[/dim]")

            process.wait()
            return process.returncode

    def _register_all_commands(self):
        """注册所有命令及其复合工作流"""

        @self.register_command("publish", "发布本地改动 (分析->压缩->校验->同步->提交)", requires_lock=True)
        def publish_flow(ctx, input_iter):
            # 1. 分析并显示 Diff
            self.run_script(self.repo_root / "work" / "publish_meta.py", ["--preview"], "Analyze")
            if input("\n确认发布以上变更? [y/N]: ").lower() != 'y': return

            # 2. 链式执行
            self.run_script(self.repo_root / "work" / "publish_meta.py", [], "Publish")
            self.run_script(self.repo_root / "data" / "compress_images.py", [], "Compress")
            self.run_script(self.repo_root / "data" / "verify_hashes.py", [], "Verify")
            self.run_script(self.repo_root / "data" / "sync_cache.py", ["--direction=upload"], "Push")

            # 3. Git Commit/Push
            subprocess.run(["git", "add", "metadata.jsonl"], cwd=self.repo_root)
            subprocess.run(["git", "commit", "-m", "update: metadata and objects"], cwd=self.repo_root)
            subprocess.run(["git", "push"], cwd=self.repo_root)

        @self.register_command("push", "同步本地缓存到服务器 (sync --direction=upload)")
        def push_alias(ctx, input_iter):
            self.run_script(self.repo_root / "data" / "sync_cache.py", ["--direction=upload"], "Push")

        @self.register_command("pull", "从服务器同步缓存到本地 (sync --direction=download)")
        def pull_alias(ctx, input_iter):
            self.run_script(self.repo_root / "data" / "sync_cache.py", ["--direction=download"], "Pull")

        @self.register_command("fetch", "获取 URL 的元数据预览")
        def fetch_cmd(ctx, input_iter):
            url = ctx["args"][0] if ctx["args"] else ""
            self.run_script(self.repo_root / "tools" / "download_ytdlp.py", [url, "--fetch"], "Fetch")

        @self.register_command("checkout", "检出音乐到工作目录", requires_lock=True)
        def checkout_flow(ctx, input_iter):
            work_dir = self._get_path("work_dir")
            if any(work_dir.glob("*.mp3")) and "-f" not in ctx["args"]:
                console.print("[yellow]警告: 工作目录不为空，正在显示待发布预览...[/yellow]")
                self.run_script(self.repo_root / "work" / "publish_meta.py", ["--preview"], "Conflict Check")
                console.print("[red]请先处理工作目录中的文件，或使用 -f 强制检出。[/red]")
                return
            self.run_script(self.repo_root / "work" / "checkout.py", ctx["args"], "Checkout")

        @self.register_command("release", "生成成品库 (Pull->Sync->Verify->Generate)")
        def release_flow(ctx, input_iter):
            if "-f" not in ctx["args"]:
                subprocess.run(["git", "pull"], cwd=self.repo_root)
                self.run_script(self.repo_root / "data" / "sync_cache.py", ["--direction=download"], "Sync")
                self.run_script(self.repo_root / "data" / "verify_hashes.py", [], "Verify")
            self.run_script(self.repo_root / "release" / "create_release.py", ctx["args"], "Release")

        # 注册其他基础命令
        for cmd_name in ["sync", "verify", "cleanup", "download", "analyze"]:
            self.register_command(cmd_name, f"运行 {cmd_name} 脚本")

    def run_command(self, name: str, args: List[str]):
        if name not in self.commands:
            console.print(f"[red]未知命令: {name}[/red]")
            return

        cmd = self.commands[name]
        ctx = {"args": args, "repo_root": self.repo_root, "metadata_mgr": self.metadata_mgr}

        try:
            if cmd.requires_lock:
                self.metadata_mgr.acquire_lock()

            # 优先执行复合工作流 (steps)
            if cmd.steps:
                # 在我们的实现中，复合工作流被包装在单个 step 函数中
                cmd.steps[0](ctx, None)
            else:
                # 否则查找对应的标准化脚本
                script_map = {
                    "sync": "data/sync_cache.py",
                    "verify": "data/verify_hashes.py",
                    "cleanup": "data/cleanup_orphaned.py",
                    "download": "tools/download_ytdlp.py",
                    "analyze": "tools/analyze_metadata.py"
                }
                if name in script_map:
                    self.run_script(Path(script_map[name]), args, name)
                else:
                    console.print(f"[red]错误: 命令 {name} 未定义且找不到对应脚本[/red]")
        except Exception as e:
            console.print(f"[red]错误: {str(e)}[/red]")
        finally:
            if cmd.requires_lock:
                self.metadata_mgr.release_lock()

    def repl(self):
        console.print(Panel(Text("GitMusic CLI 管理系统", style="bold magenta", justify="center")))
        console.print("输入 'help' 查看命令列表, 'exit' 退出\n")

        while True:
            try:
                text = self.session.prompt(
                    "gitmusic > ",
                    lexer=PygmentsLexer(BashLexer)
                ).strip()

                if not text:
                    continue
                if text.lower() in ['exit', 'quit']:
                    break
                if text.lower() == 'help':
                    self.show_help()
                    continue

                parts = text.split()
                self.run_command(parts[0], parts[1:])
            except KeyboardInterrupt:
                continue
            except EOFError:
                break

    def show_help(self):
        table = Table(title="可用命令")
        table.add_column("命令", style="cyan")
        table.add_column("描述", style="green")
        for cmd in self.commands.values():
            table.add_row(cmd.name, cmd.desc)
        console.print(table)

cli = GitMusicCLI()

# --- 命令注册示例 ---

@cli.register_command("scan", "扫描工作目录并显示待导入文件")
def scan_work_wrapper(ctx, input_iter):
    args = ctx["args"]
    work_dir = ctx["repo_root"].parent / "work"
    files = list(work_dir.glob("*.mp3"))

    with Live(console=console, refresh_per_second=4) as live:
        for i, f in enumerate(files):
            status_text = Text.assemble(
                (" [Scanning] ", "bold yellow"),
                (f.name, "italic white")
            )
            live.update(status_text)
            time.sleep(0.1)

    console.print(f"[green]扫描完成，发现 {len(files)} 个文件[/green]")
    return files

@cli.register_command("publish", "扫描并发布工作目录中的音乐到元数据库", requires_lock=True)
def publish_cmd_wrapper(ctx, input_iter):
    args = ctx["args"]
    changed_only = "--changed-only" in args
    preview = "--preview" in args or "preview" in args

    work_dir = ctx["repo_root"].parent / "work"
    files = list(work_dir.glob("*.mp3"))

    if not files:
        console.print("[yellow]工作目录为空[/yellow]")
        return

    # 加载现有元数据用于对比
    existing_entries = {e['audio_oid']: e for e in ctx["metadata_mgr"].load_all()}

    to_process = []

    with Live(console=console, refresh_per_second=10) as live:
        for f in files:
            live.update(Text.assemble((" [Analyzing] ", "bold yellow"), (f.name, "italic white")))

            # 解析文件名：艺术家 - 标题.mp3
            name = f.stem
            if " - " in name:
                artists_str, title = name.split(" - ", 1)
                artists = [a.strip() for a in artists_str.split("/")]
            else:
                artists = ["Unknown"]
                title = name

            # 计算哈希
            audio_oid = AudioIO.get_audio_hash(f)

            # 检查改动
            existing = existing_entries.get(audio_oid)
            is_changed = False
            change_reason = ""

            if not existing:
                is_changed = True
                change_reason = "New File"
            else:
                # 检查元数据是否不一致
                if existing.get('title') != title or set(existing.get('artists', [])) != set(artists):
                    is_changed = True
                    change_reason = "Metadata Mismatch"

            if not changed_only or is_changed:
                to_process.append({
                    "path": f,
                    "audio_oid": audio_oid,
                    "title": title,
                    "artists": artists,
                    "is_changed": is_changed,
                    "reason": change_reason,
                    "existing": existing
                })

    if not to_process:
        console.print("[green]没有发现需要处理的改动[/green]")
        return

    # 显示预览表格
    table = Table(title="Publish 预览")
    table.add_column("状态", style="bold")
    table.add_column("文件", style="cyan")
    table.add_column("原因", style="magenta")

    for item in to_process:
        status = "[green]NEW[/green]" if item['reason'] == "New File" else "[yellow]MOD[/yellow]"
        if not item['is_changed']:
            status = "[blue]SAME[/blue]"
        table.add_row(status, item['path'].name, item['reason'])

    console.print(table)

    if preview:
        return

    # 确认处理
    if input("\n确认发布以上内容? [y/N]: ").lower() != 'y':
        console.print("[red]已取消[/red]")
        return

    # 执行发布逻辑
    console.print("[bold green]开始发布...[/bold green]")

    with Live(console=console, refresh_per_second=10) as live:
        for item in to_process:
            live.update(Text.assemble((" [Processing] ", "bold green"), (item['path'].name, "italic white")))

            # 1. 提取并存储封面
            cover_data = AudioIO.extract_cover(item['path'])
            cover_oid = None
            if cover_data:
                cover_hash = hashlib.sha256(cover_data).hexdigest()
                cover_oid = f"sha256:{cover_hash}"
                cover_path = ctx["repo_root"].parent / "cache" / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
                AudioIO.atomic_write(cover_data, cover_path)

            # 2. 存储音频对象
            audio_hash = item['audio_oid'].split(":")[1]
            obj_path = ctx["repo_root"].parent / "cache" / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"
            if not obj_path.exists():
                with open(item['path'], 'rb') as f:
                    AudioIO.atomic_write(f.read(), obj_path)

            # 3. 更新元数据
            entry = item['existing'] or {
                "audio_oid": item['audio_oid'],
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            }
            entry.update({
                "cover_oid": cover_oid,
                "title": item['title'],
                "artists": item['artists']
            })
            ctx["metadata_mgr"].update_entry(item['audio_oid'], entry)

            # 4. 移动到回收站
            send2trash(str(item['path']))

    console.print("[bold green]发布完成！[/bold green]")

@cli.register_command("checkout", "从缓存检出音乐到工作目录进行编辑")
def checkout_cmd(args):
    # 解析参数
    query = " ".join([a for a in args if not a.startswith("-")])
    missing_fields = []
    force = "-f" in args or "--force" in args
    limit = 0

    # 处理 -m / --missing
    for i, arg in enumerate(args):
        if arg in ["-m", "--missing"] and i + 1 < len(args):
            missing_fields = [f.strip() for f in args[i+1].split(",")]
        elif arg.startswith("--limit") and i + 1 < len(args):
            limit = int(args[i+1])

    all_entries = cli.metadata_mgr.load_all()
    to_checkout = []

    # 过滤逻辑
    for entry in all_entries:
        # 1. 关键词/OID 匹配
        if query:
            if query.lower() not in entry.get('title', '').lower() and \
               query not in entry.get('audio_oid', '') and \
               not any(query.lower() in a.lower() for a in entry.get('artists', [])):
                continue

        # 2. Missing 过滤
        if missing_fields:
            is_missing = False
            for field in missing_fields:
                val = entry.get(field)
                if not val or (isinstance(val, list) and not val):
                    is_missing = True
                    break
            if not is_missing:
                continue

        to_checkout.append(entry)

    if limit > 0:
        to_checkout = to_checkout[:limit]

    if not to_checkout:
        console.print("[yellow]未找到匹配的条目[/yellow]")
        return

    # 预览表格
    table = Table(title=f"Checkout 预览 (共 {len(to_checkout)} 项)")
    table.add_column("ID", style="dim")
    table.add_column("标题", style="cyan")
    table.add_column("艺术家", style="green")
    if missing_fields:
        table.add_column("缺失字段", style="magenta")

    for entry in to_checkout[:20]: # 最多预览20条
        artists = "/".join(entry.get('artists', []))
        row = [entry['audio_oid'][:12], entry.get('title', 'Unknown'), artists]
        if missing_fields:
            missing = [f for f in missing_fields if not entry.get(f)]
            row.append(", ".join(missing))
        table.add_row(*row)

    if len(to_checkout) > 20:
        table.add_row("...", f"还有 {len(to_checkout)-20} 项未列出", "...", "...")

    console.print(table)

    if input("\n确认检出以上内容到工作目录? [y/N]: ").lower() != 'y':
        console.print("[red]已取消[/red]")
        return

    # 执行检出
    work_dir = cli.repo_root.parent / "work"
    cache_root = cli.repo_root.parent / "cache"

    with Live(console=console, refresh_per_second=10) as live:
        for entry in to_checkout:
            raw_filename = f"{'/'.join(entry['artists'])} - {entry['title']}.mp3"
            filename = AudioIO.sanitize_filename(raw_filename)

            out_path = work_dir / filename
            if out_path.exists() and not force:
                live.console.print(f"[yellow]跳过已存在文件: {filename}[/yellow]")
                continue

            live.update(Text.assemble((" [Checking out] ", "bold blue"), (filename, "italic white")))

            # 查找音频和封面
            audio_hash = entry['audio_oid'].split(":")[1]
            src_audio = cache_root / "objects" / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"

            cover_data = None
            if entry.get('cover_oid'):
                cover_hash = entry['cover_oid'].split(":")[1]
                cover_path = cache_root / "covers" / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
                if cover_path.exists():
                    with open(cover_path, 'rb') as f:
                        cover_data = f.read()

            if src_audio.exists():
                AudioIO.embed_metadata(src_audio, entry, cover_data, out_path)
            else:
                live.console.print(f"[red]错误: 找不到音频对象 {entry['audio_oid']}[/red]")

    console.print("[bold green]检出完成！[/bold green]")

@cli.register_command("sync", "同步本地缓存与服务器数据")
def sync_cmd_wrapper(ctx, input_iter):
    args = ctx["args"]
    user = "white_elephant" # 默认值
    host = "debian-server"
    remote_root = "/srv/music/data"
    direction = "both"
    dry_run = "--dry-run" in args

    # 解析参数
    for i, arg in enumerate(args):
        if arg == "--user" and i + 1 < len(args): user = args[i+1]
        elif arg == "--host" and i + 1 < len(args): host = args[i+1]
        elif arg == "-d" and i + 1 < len(args): direction = args[i+1]

    transport = TransportAdapter(user, host, remote_root)
    cache_root = ctx["repo_root"].parent / "cache"
    console.print(f"[bold blue]正在连接 {host}...[/bold blue]")

    # 1. 获取本地和远端文件列表
    local_files = set()
    for p in cache_root.rglob("*"):
        if p.is_file() and p.suffix in ['.mp3', '.jpg']:
            local_files.add(p.stem)

    remote_files = set(transport.list_remote_files("objects") + transport.list_remote_files("covers"))

    to_upload = local_files - remote_files
    to_download = remote_files - local_files

    # 2. 预览
    table = Table(title="同步预览")
    table.add_column("方向", style="bold")
    table.add_column("数量", style="cyan")

    if direction in ["upload", "both"]:
        table.add_row("上传 (Local -> Remote)", str(len(to_upload)))
    if direction in ["download", "both"]:
        table.add_row("下载 (Remote -> Local)", str(len(to_download)))

    console.print(table)

    if dry_run:
        console.print("[yellow]Dry run 模式，不执行实际传输[/yellow]")
        return

    if not to_upload and not to_download:
        console.print("[green]数据已是最新，无需同步[/green]")
        return

    if input("\n确认开始同步? [y/N]: ").lower() != 'y':
        return

    # 3. 执行同步
    if direction in ["upload", "both"] and to_upload:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TimeElapsedColumn(), console=console) as progress:
            task = progress.add_task("正在上传...", total=len(to_upload))
            for oid in to_upload:
                # 查找本地路径
                local_path = next(cache_root.rglob(f"{oid}.*"))
                subpath = "objects" if local_path.suffix == ".mp3" else "covers"
                remote_subpath = f"{subpath}/sha256/{oid[:2]}/{oid}{local_path.suffix}"

                progress.update(task, description=f"上传: {oid[:10]}...")
                transport.upload(local_path, remote_subpath)
                progress.advance(task)

    if direction in ["download", "both"] and to_download:
        # 下载逻辑类似，此处省略具体实现，将在下一步完善
        console.print("[yellow]下载功能待完善...[/yellow]")

    console.print("[bold green]同步完成！[/bold green]")

@cli.register_command("release", "生成成品音乐库")
def release_cmd_wrapper(ctx, input_iter):
    args = ctx["args"]
    mode = "local"
    force = "-f" in args

    for i, arg in enumerate(args):
        if arg == "--mode" and i + 1 < len(args):
            mode = args[i+1]

    all_entries = ctx["metadata_mgr"].load_all()

    if mode == "local":
        release_dir = ctx["repo_root"].parent / "release"
        cache_root = ctx["repo_root"].parent / "cache"
    else:
        # 服务器模式路径
        release_dir = Path("/srv/music/data/release")
        cache_root = Path("/srv/music/data")

    if force and release_dir.exists():
        console.print(f"[yellow]正在清空 {mode} release 目录...[/yellow]")
        # 无论是 local 还是 server 模式，只要运行 cli.py，就直接操作其可见的 release_dir
        if release_dir.exists():
            for item in release_dir.glob("*"):
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

    # 1. 分析需要生成的条目 (增量逻辑)
    to_generate = []
    existing_files = {f.name: f for f in release_dir.glob("*.mp3")} if mode == "local" else {}

    # 注意：服务器模式下的增量逻辑通常通过 TXXX:METADATA_HASH 标签实现
    # 这里先实现基础的全量/增量逻辑
    for entry in all_entries:
        filename = AudioIO.sanitize_filename(f"{'/'.join(entry['artists'])} - {entry['title']}.mp3")
        to_generate.append((entry, filename))

    # 2. 预览
    table = Table(title=f"Release 预览 ({mode} 模式)")
    table.add_column("计划", style="bold")
    table.add_column("数量", style="cyan")
    table.add_row("待生成文件", str(len(to_generate)))
    console.print(table)

    if input("\n确认开始生成? [y/N]: ").lower() != 'y':
        return

    # 3. 执行生成
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TimeElapsedColumn(), console=console) as progress:
        task = progress.add_task("正在生成成品...", total=len(to_generate))

        for entry, filename in to_generate:
            out_path = release_dir / filename
            progress.update(task, description=f"生成: {filename[:30]}...")

            # 查找音频和封面
            audio_hash = entry['audio_oid'].split(":")[1]
            src_audio = cache_root / ("objects" if mode == "local" else "objects") / "sha256" / audio_hash[:2] / f"{audio_hash}.mp3"

            cover_data = None
            if entry.get('cover_oid'):
                cover_hash = entry['cover_oid'].split(":")[1]
                cover_path = cache_root / ("covers" if mode == "local" else "covers") / "sha256" / cover_hash[:2] / f"{cover_hash}.jpg"
                if cover_path.exists():
                    with open(cover_path, 'rb') as f:
                        cover_data = f.read()

            if src_audio.exists():
                AudioIO.embed_metadata(src_audio, entry, cover_data, out_path)
                progress.advance(task)
            else:
                progress.console.print(f"[red]跳过: 找不到源文件 {entry['audio_oid']}[/red]")

    console.print("[bold green]Release 生成完成！[/bold green]")

@cli.register_command("full-update", "一键执行完整工作流: publish -> sync -> release")
def full_update_cmd_wrapper(ctx, input_iter):
    console.print(Panel(Text("开始完整更新工作流", style="bold green")))

    # 1. Publish
    console.print("\n[bold]步骤 1/3: Publish (发布本地改动)[/bold]")
    publish_cmd_wrapper(ctx, None)

    # 2. Sync
    console.print("\n[bold]步骤 2/3: Sync (同步缓存到服务器)[/bold]")
    sync_cmd_wrapper(ctx, None)

    # 3. Release
    console.print("\n[bold]步骤 3/3: Release (生成本地成品库)[/bold]")
    release_cmd_wrapper(ctx, None)

    console.print("\n")
    console.print(Panel(Text("完整更新完成！", style="bold green")))
@cli.register_command("verify", "校验本地缓存文件的哈希完整性")
def verify_cmd_wrapper(ctx, input_iter):
    cache_root = ctx["repo_root"].parent / "cache"
    files = list(cache_root.rglob("*.mp3")) + list(cache_root.rglob("*.jpg"))

    if not files:
        console.print("[yellow]缓存目录为空[/yellow]")
        return

    errors = []
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), console=console) as progress:
        task = progress.add_task("正在校验哈希...", total=len(files))
        for f in files:
            expected_hash = f.stem
            progress.update(task, description=f"校验: {expected_hash[:10]}...")

            sha256_obj = hashlib.sha256()
            with open(f, "rb") as rb:
                while chunk := rb.read(4096):
                    sha256_obj.update(chunk)

            actual_hash = sha256_obj.hexdigest()
            if actual_hash != expected_hash:
                errors.append((f.name, expected_hash, actual_hash))
            progress.advance(task)

    if errors:
        table = Table(title="哈希校验失败列表", style="red")
        table.add_column("文件名")
        table.add_column("预期哈希")
        table.add_column("实际哈希")
        for name, exp, act in errors:
            table.add_row(name, exp, act)
        console.print(table)
    else:
        console.print("[bold green]所有文件校验通过！[/bold green]")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli.run_command(sys.argv[1], sys.argv[2:])
    else:
        cli.repl()
