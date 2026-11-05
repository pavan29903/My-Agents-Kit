"""PostToolUse for Write/Edit/MultiEdit/NotebookEdit (and Bash/PowerShell).

1. Records the edited file against its project so verify_gate.py knows which
   projects need their tests run before Claude may finish.
2. Formats the file with the formatter the project already uses (ruff, black,
   prettier). Projects without a formatter config are never reformatted.
3. For Bash/PowerShell, notes when Claude itself ran a test command.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from _common import load_state, run, save_state
from _project import formatter_for, is_doc, project_root, settings

FORMAT_TIMEOUT = 20


def record(session_id: str, root: Path, file_path: Path) -> None:
    state = load_state(session_id)
    dirty = state.setdefault("dirty", {})
    files = dirty.setdefault(str(root), [])
    rel = file_path.resolve().relative_to(root.resolve()).as_posix() if _inside(file_path, root) else str(file_path)
    if rel not in files:
        files.append(rel)
    if not is_doc(rel):
        state["last_code_edit"] = time.time()
    save_state(session_id, state)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


TEST_COMMAND = re.compile(
    r"\b(pytest|py\.test|unittest|jest|vitest|mocha|playwright test|cypress run|go test|cargo test|dotnet test"
    r"|(npm|pnpm|yarn)( run)? test)\b"
    # standalone test scripts, e.g. isolated test harnesses: test_x.py, x_test.py, x.test.js
    r"|(python\S*|node|tsx|ts-node|py|\$\w+)[^\n|;&]*?\s[\"']?(\S*[\\/])?"
    r"(test_\w+\.py|\w+_test\.py|[\w.-]+\.(test|spec)\.[cm]?[jt]sx?)\b",
    re.IGNORECASE)


def record_test_run(data: Dict[str, Any]) -> None:
    """Remember that Claude ran tests itself (used by verify_gate when auto_tests is off)."""
    command = str((data.get("tool_input") or {}).get("command", ""))
    if TEST_COMMAND.search(command):
        sid = data.get("session_id", "unknown")
        state = load_state(sid)
        state["tested_at"] = time.time()
        save_state(sid, state)


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if data.get("tool_name") in ("Bash", "PowerShell"):
        record_test_run(data)
        return None
    if data.get("tool_name") not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None
    tool_input = data.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    file_path = Path(raw)
    if not file_path.is_absolute():
        file_path = Path(data.get("cwd") or ".") / file_path
    root = project_root(str(file_path))
    if root is None or ".claude" in file_path.parts:
        return None  # scratch files, memory, settings: not project work
    record(data.get("session_id", "unknown"), root, file_path)

    cfg = settings(root)
    if not cfg.get("format", True) or not file_path.is_file():
        return None
    cmd = formatter_for(file_path, root)
    if not cmd:
        return None
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=FORMAT_TIMEOUT,
                              shell=cmd[0].endswith(".cmd"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 and not is_doc(str(file_path)):
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                f"Formatter {Path(cmd[0]).name} could not format {file_path.name}: " + " | ".join(detail)}}
    return None


if __name__ == "__main__":
    run(handle)
