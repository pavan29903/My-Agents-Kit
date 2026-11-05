"""Shared helpers for my-agent-kit hooks.

Hooks read one JSON object on stdin and may print one JSON object on stdout.
Every hook must fail open: an exception inside a hook must never block the
user's work, so `run()` swallows errors and exits 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

KIT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("MY_AGENT_KIT_STATE", Path.home() / ".my-agent-kit" / "state"))


def read_input() -> Dict[str, Any]:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def emit(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def run(handler: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> None:
    try:
        result = handler(read_input())
        if result:
            emit(result)
    except Exception:  # fail open, but leave a trace for debugging
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATE_DIR / "hook-errors.log", "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\n")
        except OSError:
            pass
    sys.exit(0)


def load_config(name: str) -> Dict[str, Any]:
    """Load config/<name>.json, then the private ~/.my-agent-kit/<name>.json (never in git).

    Lists are concatenated (so private identity rules add to the shared ones);
    other keys from the private file win.
    """
    data: Dict[str, Any] = {}
    for path in (KIT_ROOT / "config" / f"{name}.json", Path.home() / ".my-agent-kit" / f"{name}.json"):
        if path.is_file():
            for key, value in json.loads(path.read_text(encoding="utf-8")).items():
                if isinstance(value, list) and isinstance(data.get(key), list):
                    data[key] = data[key] + value
                else:
                    data[key] = value
    return data


def git(args: List[str], cwd: Optional[str], timeout: float = 5) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd or None, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def pre_tool_decision(decision: str, reason: str) -> Dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def context(event: str, text: str) -> Dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def norm_path(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower()


def session_state_file(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return STATE_DIR / f"session-{safe}.json"


def load_state(session_id: str) -> Dict[str, Any]:
    path = session_state_file(session_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(session_id: str, state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = session_state_file(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)
