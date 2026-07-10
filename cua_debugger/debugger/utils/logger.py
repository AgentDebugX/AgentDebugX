import re
import time
import weakref
from pathlib import Path
import threading
from queue import Queue
from typing import Literal


class ABlockLogger:
    COLOR_RE = re.compile(r'\x1b\[[0-9;]*m')

    # ---- class-level shared state ---------------------------------------
    # Cross-instance stdout atomicity: all loggers share this lock around print().
    _stdout_lock = threading.Lock()
    # Shared file handle + write lock + refcount, keyed by resolved absolute path.
    # key = str(Path(path).resolve()), value = {'fp','lock','refs','closed'}
    _file_registry: dict = {}
    _registry_lock = threading.Lock()
    # ---------------------------------------------------------------------

    def __init__(
        self,
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
        width: None | int = None
    ) -> None:
        self.level_map = {
            "DEBUG": 0,
            "INFO": 1,
            "WARNING": 2,
            "ERROR": 3,
        }
        self.level = self.level_map[level.strip().upper()]
        self.width = width

        self.role = None
        self.last_time_stamp = None
        self.queue = Queue()
        self.stop_event = threading.Event()

        # Space count for newline indentation alignment.
        self.static_indent = None

        # stdout toggle (default on); switched via bind_stdout / unbind_stdout.
        self._use_stdout = True

        # Files bound by this instance; each item is (key, registry_entry_dict).
        # The entry dict is the same object stored in the class-level registry,
        # so multiple loggers binding the same path share it.
        self._files = []
        self._files_lock = threading.Lock()

        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

        # Lifecycle cleanup.
        self._finalizer = weakref.finalize(
            self,
            self._close,
            self.queue, self.stop_event, self.worker,
            self._files, self._files_lock,
        )

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                text = self.queue.get(timeout=0.1)
            except Exception:
                # Idle tick: flush pending file buffers.
                with self._files_lock:
                    files_snapshot = list(self._files)
                for _, entry in files_snapshot:
                    with entry['lock']:
                        if not entry['closed']:
                            try:
                                entry['fp'].flush()
                            except Exception:
                                pass
                continue

            if text is None:
                break

            # ---- stdout: atomic across all logger instances --------------
            if self._use_stdout:
                with ABlockLogger._stdout_lock:
                    print(text)

            # ---- files: per-path shared lock -----------------------------
            with self._files_lock:
                files_snapshot = list(self._files)

            if files_snapshot:
                plain = self._de_color(text)
                for _, entry in files_snapshot:
                    with entry['lock']:
                        if not entry['closed']:
                            try:
                                entry['fp'].write(plain + '\n')
                            except Exception:
                                pass

            self.queue.task_done()

    @property
    def _time_stamp(self) -> str:
        curr_time_stamp = time.strftime("[%m/%d/%y %H:%M:%S]", time.localtime())
        if self.last_time_stamp is None or self.last_time_stamp != curr_time_stamp:
            self.last_time_stamp = curr_time_stamp
            return curr_time_stamp
        else:
            return ' ' * len(curr_time_stamp)

    @staticmethod
    def _close(queue, stop_event, worker, files, files_lock) -> None:
        queue.join()
        stop_event.set()
        queue.put(None)
        worker.join()

        # Worker has stopped; safe to release this instance's file references.
        with files_lock:
            local_files = list(files)
            files.clear()

        with ABlockLogger._registry_lock:
            for key, entry in local_files:
                if key in ABlockLogger._file_registry:
                    entry['refs'] -= 1
                    if entry['refs'] <= 0:
                        del ABlockLogger._file_registry[key]
                        with entry['lock']:
                            entry['closed'] = True
                            try:
                                entry['fp'].close()
                            except Exception:
                                pass

    @staticmethod
    def _role(role: str) -> str:
        return f"[{role}]"

    @staticmethod
    def _level(level: str) -> str:
        return f"{level:9s}"

    def _do_print(self, level_digit: int) -> bool:
        return level_digit >= self.level

    @staticmethod
    def _red(text: str) -> str:
        return f"\033[91m{str(text)}\033[0m"

    @staticmethod
    def _green(text: str) -> str:
        return f"\033[92m{str(text)}\033[0m"

    @staticmethod
    def _yellow(text: str) -> str:
        return f"\033[93m{str(text)}\033[0m"

    @staticmethod
    def _blue(text: str) -> str:
        return f"\033[94m{str(text)}\033[0m"

    @staticmethod
    def _de_color(text: str) -> str:
        return ABlockLogger.COLOR_RE.sub('', text)

    def _format(self, role: str, message: str) -> str:
        if self.static_indent is None:
            self.static_indent = len(str(self._time_stamp)) + 1 + 9 + 2

        indent = self.static_indent + len(self._role(role))
        padding = '\n' + (' ' * indent)

        # Fast path.
        if (not self.width) or ((len(message) <= self.width) and ('\n' not in message)):
            return message if '\n' not in message else message.replace('\n', padding)

        # General path.
        out = []
        for line in message.split('\n'):
            line_len = len(line)
            if line_len <= self.width:
                out.append(line)
            else:
                out.extend(line[i:i + self.width] for i in range(0, line_len, self.width))

        return padding.join(out)

    def set_level(self, level: str) -> "ABlockLogger":
        """Set minimum log level."""
        self.level = self.level_map[level.strip().upper()]
        return self

    def set_role(self, role: str) -> "ABlockLogger":
        """Set default role label."""
        self.role = role
        return self

    def bind_stdout(self) -> "ABlockLogger":
        """Enable stdout output (on by default)."""
        self._use_stdout = True
        return self

    def unbind_stdout(self) -> "ABlockLogger":
        """Disable stdout output."""
        self._use_stdout = False
        return self

    def bind_file(self, path, mode: str = 'a') -> "ABlockLogger":
        """Mirror output to a file; shares handle and lock across instances on same path."""
        path = Path(path).resolve()
        key = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Skip if this instance already bound this path (avoid duplicate writes).
        with self._files_lock:
            for k, _ in self._files:
                if k == key:
                    return self

        with ABlockLogger._registry_lock:
            entry = ABlockLogger._file_registry.get(key)
            if entry is None:
                fp = open(key, mode, encoding='utf-8')
                entry = {
                    'fp': fp,
                    'lock': threading.Lock(),
                    'refs': 1,
                    'closed': False,
                }
                ABlockLogger._file_registry[key] = entry
            else:
                # Mode of the first bind wins; later binds reuse the existing handle.
                entry['refs'] += 1

        with self._files_lock:
            self._files.append((key, entry))

        return self

    def unbind_files(self) -> "ABlockLogger":
        """Unbind all files from this instance; closes handle when last ref drops."""
        with self._files_lock:
            local_files = list(self._files)
            self._files.clear()

        with ABlockLogger._registry_lock:
            for key, entry in local_files:
                if key in ABlockLogger._file_registry:
                    entry['refs'] -= 1
                    if entry['refs'] <= 0:
                        del ABlockLogger._file_registry[key]
                        with entry['lock']:
                            entry['closed'] = True
                            try:
                                entry['fp'].close()
                            except Exception:
                                pass

        return self

    # ---- logging methods ------------------------------------------------
    # When raw=True, the message is queued as-is: no timestamp, level, role,
    # indent, or newline reflow.

    def info(self, message: str, role: None | str = None, raw: bool = False) -> str:
        """Log at INFO level; raw=True bypasses all formatting."""
        if self._do_print(1):
            if raw:
                text = message
            else:
                role = role or self.role or "Logger"
                text = f"{self._time_stamp} {self._blue(self._level('INFO'))}{self._role(role)}: {self._format(role, message)}"
            self.queue.put(text)
            return text
        else:
            return ''

    def debug(self, message: str, role: None | str = None, raw: bool = False) -> str:
        """Log at DEBUG level; raw=True bypasses all formatting."""
        if self._do_print(0):
            if raw:
                text = message
            else:
                role = role or self.role or "Logger"
                text = f"{self._time_stamp} {self._green(self._level('DEBUG'))}{self._role(role)}: {self._format(role, message)}"
            self.queue.put(text)
            return text
        else:
            return ''

    def warning(self, message: str, role: None | str = None, raw: bool = False) -> str:
        """Log at WARNING level; raw=True bypasses all formatting."""
        if self._do_print(2):
            if raw:
                text = message
            else:
                role = role or self.role or "Logger"
                text = f"{self._time_stamp} {self._yellow(self._level('WARNING'))}{self._role(role)}: {self._format(role, message)}"
            self.queue.put(text)
            return text
        else:
            return ''

    def error(self, message: str, role: None | str = None, raw: bool = False) -> str:
        """Log at ERROR level; raw=True bypasses all formatting."""
        if self._do_print(3):
            if raw:
                text = message
            else:
                role = role or self.role or "Logger"
                text = f"{self._time_stamp} {self._red(self._level('ERROR'))}{self._role(role)}: {self._format(role, message)}"
            self.queue.put(text)
            return text
        else:
            return ''

    def close(self) -> None:
        """Flush queue, stop worker, and release bound files."""
        self._finalizer()
