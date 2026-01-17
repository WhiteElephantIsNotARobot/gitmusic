import hashlib
import sys
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter

def main():
    cache_root = Path(__file__).parent.parent.parent / "cache"
    files = list(cache_root.rglob("*.mp3")) + list(cache_root.rglob("*.jpg"))

    EventEmitter.phase_start("verify", total_items=len(files))

    errors = []
    for i, f in enumerate(files):
        expected_hash = f.stem
        EventEmitter.item_event(f.name, "checking")

        sha256_obj = hashlib.sha256()
        with open(f, "rb") as rb:
            while chunk := rb.read(4096):
                sha256_obj.update(chunk)

        actual_hash = sha256_obj.hexdigest()
        if actual_hash != expected_hash:
            EventEmitter.error(f"Hash mismatch for {f.name}", {"expected": expected_hash, "actual": actual_hash})
            errors.append(f.name)
        else:
            EventEmitter.item_event(f.name, "success")

        EventEmitter.batch_progress("verify", i + 1, len(files))

    if errors:
        EventEmitter.result("error", message=f"Verification failed for {len(errors)} files", artifacts={"failed_files": errors})
    else:
        EventEmitter.result("ok", message="All files verified successfully")

if __name__ == "__main__":
    main()
