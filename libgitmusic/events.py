from pathlib import Path
import json
import datetime
import sys


class EventEmitter:
    """统一事件输出类，所有脚本通过此类输出 JSONL 事件流"""

    @staticmethod
    def emit(event_type, **kwargs):
        event = {
            "type": event_type,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "cmd": Path(sys.argv[0]).stem,
            **kwargs,
        }
        print(json.dumps(event, ensure_ascii=True), flush=True)

    @staticmethod
    def log(level, message):
        EventEmitter.emit("log", level=level, message=message)

    @staticmethod
    def phase_start(phase, total_items=0):
        EventEmitter.emit("phase_start", phase=phase, total_items=total_items)

    @staticmethod
    def batch_progress(phase, processed, total_items, rate_per_sec=0):
        EventEmitter.emit(
            "batch_progress",
            phase=phase,
            processed=processed,
            total_items=total_items,
            rate_per_sec=rate_per_sec,
        )

    @staticmethod
    def item_event(item_id, status, message=""):
        EventEmitter.emit("item_event", id=item_id, status=status, message=message)

    @staticmethod
    def result(status, message="", artifacts=None):
        EventEmitter.emit(
            "result", status=status, message=message, artifacts=artifacts or {}
        )

    @staticmethod
    def error(message, context=None):
        EventEmitter.emit("error", message=message, context=context or {})
