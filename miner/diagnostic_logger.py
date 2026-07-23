import threading
import time
from pathlib import Path


class DiagnosticLogger:
    """
    Thread-safe singleton for logging label resolver and OCSR diagnostic output.
    Writes to label_resolver_diag.txt in the same folder as llm_prompts_log.txt.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.log_path: Path | None = None

    def set_log_path(self, log_file: str):
        with self._lock:
            self.log_path = Path(log_file)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write(f"Label Resolver Diagnostic Log — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")

    def write(self, message: str):
        """Write a diagnostic line. Also prints to stdout."""
        print(message)
        if self.log_path is None:
            return
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(message + "\n")
            except Exception as e:
                print(f"[DiagnosticLogger] Failed to write: {e}")

    def section(self, title: str):
        """Write a section header."""
        self.write(f"\n{'─' * 60}")
        self.write(f"  {title}")
        self.write(f"{'─' * 60}")


diagnostic_logger = DiagnosticLogger()
