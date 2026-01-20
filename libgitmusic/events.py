from pathlib import Path
import json
import datetime
import sys

# 全局事件监听器列表
_event_listeners = []


class EventEmitter:
    """统一事件输出类，所有脚本通过此类输出 JSONL 事件流"""

    # 日志配置
    _log_file = None
    _log_only_mode = False
    _logs_dir = Path.cwd() / "logs"

    @staticmethod
    def setup_logging(logs_dir=None, log_only=False):
        """配置日志输出
        Args:
            logs_dir: 日志目录路径，None表示使用默认logs目录
            log_only: 是否只输出JSONL（不渲染人类友好输出）
        """
        if logs_dir is not None:
            EventEmitter._logs_dir = Path(logs_dir)
        EventEmitter._log_only_mode = log_only
        # 确保日志目录存在
        EventEmitter._logs_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def register_listener(listener):
        """注册事件监听器，listener(event_dict)"""
        _event_listeners.append(listener)

    @staticmethod
    def unregister_listener(listener):
        """注销事件监听器"""
        if listener in _event_listeners:
            _event_listeners.remove(listener)

    @staticmethod
    def start_log_file(command_name=None):
        """开始记录日志到文件
        Args:
            command_name: 命令名称，用于生成日志文件名。如果为None，使用当前脚本名。
        """
        if command_name is None:
            command_name = Path(sys.argv[0]).stem
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        EventEmitter._logs_dir.mkdir(parents=True, exist_ok=True)
        log_filename = EventEmitter._logs_dir / f"{command_name}-{timestamp}.jsonl"
        try:
            EventEmitter._log_file = open(log_filename, "a", encoding="utf-8")
        except Exception as e:
            sys.stderr.write(f"无法打开日志文件 {log_filename}: {e}\n")
            EventEmitter._log_file = None

    @staticmethod
    def stop_logging():
        """停止日志记录，关闭文件"""
        if EventEmitter._log_file is not None:
            try:
                EventEmitter._log_file.close()
            except Exception:
                pass
            EventEmitter._log_file = None

    @staticmethod
    def _filter_sensitive_data(event_dict):
        """过滤敏感数据，如密码、密钥等"""
        sensitive_keys = ["password", "secret", "key", "token", "credential"]
        filtered = event_dict.copy()
        for key in list(filtered.keys()):
            key_lower = key.lower()
            for sensitive in sensitive_keys:
                if sensitive in key_lower:
                    filtered[key] = "[FILTERED]"
                    break
        return filtered

    @staticmethod
    def emit(event_type, **kwargs):
        event = {
            "type": event_type,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "cmd": Path(sys.argv[0]).stem,
            **kwargs,
        }
        # 调用所有监听器
        for listener in _event_listeners:
            try:
                listener(event)
            except Exception:
                pass

        # 写入日志文件（如果启用）
        if EventEmitter._log_file is not None:
            filtered_event = EventEmitter._filter_sensitive_data(event)
            json_str = json.dumps(filtered_event, ensure_ascii=False)
            EventEmitter._log_file.write(json_str + "\n")
            EventEmitter._log_file.flush()

        # 只有在没有监听器时才输出JSONL（例如脚本独立运行）
        if not _event_listeners:
            json_str = json.dumps(event, ensure_ascii=False)
            try:
                # 尝试使用UTF-8编码输出，避免控制台编码问题
                sys.stdout.buffer.write(json_str.encode("utf-8"))
                sys.stdout.buffer.write(b"\n")
                sys.stdout.buffer.flush()
            except:
                # 如果失败，回退到普通print（可能会在Windows控制台出错）
                print(json_str, flush=True)

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
