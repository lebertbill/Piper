import os
import threading
import json
import time
from pathlib import Path
from typing import Any, List, Dict, Optional

class PromptLogger:
    """
    Thread-safe singleton for logging all prompts sent to LLMs.
    Appends to a single file formatted for easy reading.
    """
    def __init__(self, log_file: str = "llm_prompts_log.txt"):
        self._lock = threading.Lock()
        self.log_path = Path(log_file)
        
    def set_log_path(self, log_file: str):
        """Set the log file path dynamically."""
        with self._lock:
            self.log_path = Path(log_file)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # Optional: initialize file with a header if new
            if not self.log_path.exists():
                with open(self.log_path, "w", encoding="utf-8") as f:
                    f.write(f"LLM Prompt Log initialized for paper at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
    def log(self, agent_name: str, model: str, messages: List[Dict[str, str]], response: Optional[str] = None):
        """Append a prompt and its (optional) response to the log file."""
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write("=" * 80 + "\n")
                    f.write(f"TIMESTAMP: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"AGENT:     {agent_name}\n")
                    f.write(f"MODEL:     {model}\n")
                    f.write("-" * 40 + " MESSAGES " + "-" * 30 + "\n")
                    for msg in messages:
                        if hasattr(msg, "role") and hasattr(msg, "content"):
                            role = (msg.role or "unknown").upper()
                            content = msg.content or ""
                        elif isinstance(msg, dict):
                            role = msg.get("role", "unknown").upper()
                            content = msg.get("content", "")
                        else:
                            role = "UNKNOWN"
                            content = str(msg)
                        f.write(f"[{role}]:\n{content}\n\n")
                    
                    if response:
                        f.write("-" * 40 + " RESPONSE " + "-" * 30 + "\n")
                        f.write(f"{response}\n")
                    
                    f.write("=" * 80 + "\n\n")
            except Exception as e:
                print(f"Failed to write to prompt log: {e}")

# Module-level singleton
prompt_logger = PromptLogger()
