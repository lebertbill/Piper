"""
Session persistence for the RAG Chat.

Each conversation is stored as a JSON file under chat_sessions/:
  {id, title, created_at, updated_at, messages: [{role, content, images?}]}

File names are <session_id>.json where session_id encodes the creation timestamp.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

_DEFAULT_DIR = Path(__file__).parent / "chat_sessions"


def _ensure_dir(sessions_dir: Path | str | None = None) -> Path:
    d = Path(sessions_dir) if sessions_dir else _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:8]


def list_sessions(sessions_dir=None) -> list[dict]:
    """Return session summaries sorted newest-first."""
    d = _ensure_dir(sessions_dir)
    sessions = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sessions.append({
                "id": data["id"],
                "title": data.get("title", "Untitled"),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        except Exception:
            continue
    return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)


def load_session(session_id: str, sessions_dir=None) -> dict | None:
    d = _ensure_dir(sessions_dir)
    f = d / f"{session_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def save_session(session_id: str, title: str, messages: list, sessions_dir=None) -> None:
    """Atomic write: temp file → replace."""
    d = _ensure_dir(sessions_dir)
    f = d / f"{session_id}.json"
    now = datetime.now().isoformat(timespec="seconds")
    created_at = now
    if f.exists():
        try:
            created_at = json.loads(f.read_text()).get("created_at", now)
        except Exception:
            pass
    data = {
        "id": session_id,
        "title": title,
        "created_at": created_at,
        "updated_at": now,
        "messages": messages,
    }
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(f)


def delete_session(session_id: str, sessions_dir=None) -> None:
    d = _ensure_dir(sessions_dir)
    f = d / f"{session_id}.json"
    if f.exists():
        f.unlink()
