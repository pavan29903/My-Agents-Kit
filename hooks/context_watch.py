"""Context watch.

UserPromptSubmit: records the turn start time (for notify.py) and warns once
when the context crosses the warn and critical thresholds; re-arms after the
context shrinks (/clear or compaction).
PreCompact: saves a small checkpoint (git branch, changed files, last commit,
context size) that session_start.py restores after compaction. No prompt text
is stored.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from _common import STATE_DIR, git, load_state, run, save_state

WARN = int(os.environ.get("KIT_CTX_WARN_TOKENS", "200000"))
CRITICAL = int(os.environ.get("KIT_CTX_CRITICAL_TOKENS", "400000"))
TAIL_BYTES = 400_000


def context_tokens(transcript_path: str) -> Optional[int]:
    """Total prompt size from the newest assistant usage record in the transcript."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except (OSError, TypeError):
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            usage = json.loads(line).get("message", {}).get("usage")
        except ValueError:
            continue
        if isinstance(usage, dict):
            return sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
    return None


def on_prompt(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = data.get("session_id", "unknown")
    state = load_state(sid)
    state["turn_started"] = time.time()
    tokens = context_tokens(data.get("transcript_path", ""))
    message = None
    if tokens is not None:
        level = 2 if tokens >= CRITICAL else 1 if tokens >= WARN else 0
        if level < state.get("ctx_level", 0):
            state["ctx_level"] = level  # context shrank: re-arm
        if level > state.get("ctx_level", 0):
            state["ctx_level"] = level
            k = tokens // 1000
            message = (
                f"Context is {k}k tokens. Every turn re-reads all of it (slower, costlier). "
                + ("Strongly consider /compact or /clear after saving a checkpoint."
                   if level == 2 else "Consider /compact when this task reaches a natural break.")
            )
        state["ctx_tokens"] = tokens
    save_state(sid, state)
    return {"systemMessage": message} if message else None


def on_precompact(data: Dict[str, Any]) -> None:
    cwd = data.get("cwd") or "."
    changed = (git(["status", "--porcelain"], cwd) or "").splitlines()
    checkpoint = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cwd": cwd,
        "trigger": data.get("trigger"),
        "context_tokens": context_tokens(data.get("transcript_path", "")),
        "branch": git(["rev-parse", "--abbrev-ref", "HEAD"], cwd),
        "last_commit": git(["log", "-1", "--format=%h %s"], cwd),
        "changed_files": [line[3:] for line in changed[:40]],
        "changed_count": len(changed),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    key = "".join(c if c.isalnum() else "_" for c in cwd.lower())[-120:]
    (STATE_DIR / f"checkpoint-{key}.json").write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = data.get("hook_event_name")
    if event == "UserPromptSubmit":
        return on_prompt(data)
    if event == "PreCompact":
        on_precompact(data)
    return None


if __name__ == "__main__":
    run(handle)
