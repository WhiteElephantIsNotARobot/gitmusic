import sys
import os
import subprocess
from pathlib import Path
from send2trash import send2trash

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter
from libgitmusic.metadata import MetadataManager

def main():
    confirm = "--confirm" in sys.argv
    mode = "local" # 默认 local
    for arg in sys.argv:
        if arg.startswith("--mode="):
            mode = arg.split("=")[1]

    # 从环境变量获取路径
    cache_root_path = os.environ.get("GITMUSIC_CACHE_ROOT")
    metadata_file_path = os.environ.get("GITMUSIC_METADATA_FILE")
    remote_user = os.environ.get("GITMUSIC_REMOTE_USER", "white_elephant")
    remote_host = os.environ.get("GITMUSIC_REMOTE_HOST", "debian-server")
    remote_data_root = os.environ.get("GITMUSIC_REMOTE_DATA_ROOT", "/srv/music/data")

    if not all([cache_root_path, metadata_file_path]):
        EventEmitter.error("Missing required environment variables (CACHE_ROOT, METADATA_FILE)")
        return

    metadata_mgr = MetadataManager(Path(metadata_file_path))
    cache_root = Path(cache_root_path)

    EventEmitter.phase_start("cleanup_analyze")

    # 1. 加载所有引用的 OID
    entries = metadata_mgr.load_all()
    referenced_oids = set()
    for e in entries:
        referenced_oids.add(e['audio_oid'].split(":")[1])
        if e.get('cover_oid'):
            referenced_oids.add(e['cover_oid'].split(":")[1])

    # 2. 扫描本地缓存目录
    orphaned_files = []
    if mode in ["local", "both"]:
        for p in cache_root.rglob("*"):
            if p.is_file() and p.suffix in ['.mp3', '.jpg']:
                if p.stem not in referenced_oids:
                    orphaned_files.append(p)

    EventEmitter.item_event("analysis", "done", message=f"Found {len(orphaned_files)} orphaned files locally")

    if not orphaned_files and mode != "both":
        EventEmitter.result("ok", message="No orphaned files found")
        return

    if not confirm:
        EventEmitter.result("warn", message=f"Found {len(orphaned_files)} orphaned files. Run with --confirm to delete.", artifacts={"orphaned_count": len(orphaned_files)})
        return

    # 3. 执行清理
    EventEmitter.phase_start("cleanup_delete", total_items=len(orphaned_files))
    for i, f in enumerate(orphaned_files):
        EventEmitter.item_event(f.name, "deleting")
        send2trash(str(f))

        # 如果是 both 模式，尝试同步删除远端
        if mode == "both":
            subpath = "objects" if f.suffix == ".mp3" else "covers"
            remote_path = f"{remote_data_root}/{subpath}/sha256/{f.stem[:2]}/{f.name}"
            try:
                subprocess.run(["ssh", f"{remote_user}@{remote_host}", f"rm -f {remote_path}"], check=True)
            except Exception:
                EventEmitter.log("warn", f"Failed to delete remote file: {remote_path}")

        EventEmitter.batch_progress("cleanup_delete", i + 1, len(orphaned_files))

    EventEmitter.result("ok", message=f"Cleaned up {len(orphaned_files)} orphaned files")

if __name__ == "__main__":
    main()
