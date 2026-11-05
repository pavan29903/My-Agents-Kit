"""Stop hook: Claude cannot finish while its changes are unverified.

For every project Claude edited code in this session, the gate:
1. runs the project's automatically detected (or .kit.json configured) tests;
   on failure it blocks the stop and hands Claude the failing output;
   when tests cannot run automatically (no safe command detected, or
   "auto_tests": false), it requires Claude to have run tests itself after
   its last code edit;
2. requires a change document (any .md under docs/, or README/CHANGELOG) when
   the session touched `docs_min_files` or more code files.

Safety valves: blocks at most `max_gate_blocks` times in a row (then lets
Claude stop and tells you why), skips doc-only edits, never reruns tests for
unchanged files, kills the whole process tree on timeout, and is disabled with
KIT_GATE=0 or {"gate": false} in a project's .kit.json.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _common import load_state, run, save_state
from _project import is_doc, settings, test_command

OUTPUT_TAIL = 60


def signature(root: Path, files: List[str]) -> List[List[Any]]:
    sig: List[List[Any]] = []
    for rel in sorted(files):
        p = root / rel
        sig.append([rel, p.stat().st_mtime if p.exists() else -1.0])  # lists survive the JSON round trip
    return sig


def has_change_doc(files: List[str]) -> bool:
    for f in files:
        low = f.replace("\\", "/").lower()
        name = low.rsplit("/", 1)[-1]
        if is_doc(low) and ("docs/" in low or low.startswith("doc/") or name.startswith(("readme", "changelog"))):
            return True
    return False


def run_tests(root: Path, tc: Dict[str, Any], timeout: int) -> Tuple[Optional[bool], str]:
    """(passed, output tail). passed is None on timeout or when the runner could not start."""
    env = dict(os.environ, **tc.get("env", {}))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(tc["cmd"], cwd=str(root), shell=tc.get("shell", False), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags, start_new_session=sys.platform != "win32")
    except OSError as exc:
        return None, f"could not start tests: {exc}"
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return None, f"tests timed out after {timeout}s"
    text = out.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    tail = "\n".join(text.splitlines()[-OUTPUT_TAIL:])
    return proc.returncode == 0, tail


def _kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, 9)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
    proc.wait(timeout=10)


def cmd_text(tc: Dict[str, Any]) -> str:
    cmd = tc["cmd"]
    return cmd if isinstance(cmd, str) else " ".join(Path(cmd[0]).name if i == 0 else c for i, c in enumerate(cmd))


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if os.environ.get("KIT_GATE", "1") == "0":
        return None
    sid = data.get("session_id", "unknown")
    state = load_state(sid)
    dirty: Dict[str, List[str]] = state.get("dirty") or {}
    if not dirty:
        return None

    failures: List[str] = []
    missing_docs: List[str] = []
    notes: List[str] = []
    passed_sigs = state.setdefault("passed", {})
    max_blocks = 2
    finished: List[str] = []

    for root_str, files in list(dirty.items()):
        root = Path(root_str)
        cfg = settings(root) if root.is_dir() else {"gate": False}
        max_blocks = int(cfg.get("max_gate_blocks", max_blocks))
        code = [f for f in files if not is_doc(f)]
        if not cfg.get("gate", True) or not code:
            finished.append(root_str)
            continue
        sig = signature(root, code)
        auto = cfg.get("auto_tests", True)
        tc = test_command(root, cfg) if auto else None
        tests_ok = True
        if tc is None:
            # Claude must run the tests itself after its last code edit.
            if float(state.get("tested_at", 0)) < float(state.get("last_code_edit", 0)):
                tests_ok = False
                why = ("automatic test runs are off for this project (auto_tests: false)" if not auto
                       else "no test command could be detected safely")
                failures.append(f"### {root.name}: no tests were run after your last code change ({why}).\n"
                                "Run the relevant tests yourself now. They must not touch real databases, "
                                "external services, or production data; mock them. If the project has no "
                                "tests for this code, add them (task-autopilot step 4), then run them.")
            else:
                notes.append(f"{root.name}: Claude ran tests after the last edit")
        elif passed_sigs.get(root_str) != sig:
            ok, tail = run_tests(root, tc, int(cfg.get("test_timeout", 300)))
            if ok:
                passed_sigs[root_str] = sig
                notes.append(f"{root.name}: tests passed ({cmd_text(tc)})")
            elif ok is None:
                notes.append(f"{root.name}: {tail} ({cmd_text(tc)}); not blocking")
            else:
                tests_ok = False
                failures.append(f"### {root.name}: `{cmd_text(tc)}` failed\n```\n{tail}\n```")
        needs_docs = tests_ok and len(code) >= int(cfg.get("docs_min_files", 3)) and not has_change_doc(files)
        if needs_docs:
            missing_docs.append(f"{root.name} ({len(code)} code files changed)")
        if tests_ok and not needs_docs:
            finished.append(root_str)

    if failures or missing_docs:
        blocks = int(state.get("gate_blocks", 0))
        if blocks >= max_blocks:
            state.update(dirty={}, gate_blocks=0, passed={})
            save_state(sid, state)
            what = "; ".join([f.splitlines()[0].lstrip("# ") for f in failures] + [f"docs missing: {m}" for m in missing_docs])
            return {"systemMessage": f"Verify gate gave up after {blocks} attempts and let Claude stop. "
                                     f"Still open: {what}. Check before trusting this result."}
        state["gate_blocks"] = blocks + 1
        save_state(sid, state)
        parts = ["my-agent-kit verify gate: the work is not finished yet."]
        if failures:
            parts.append("Verification is missing or failing. Fix the cause (not the test, unless the test is "
                         "wrong). If a failure is pre-existing or environmental (database, network, secrets), "
                         "prove it (for example `git stash` and rerun) and say so clearly to the user.\n"
                         + "\n\n".join(failures))
        if missing_docs:
            parts.append("Write the change document required by the task-autopilot skill "
                         "(docs/changes/<YYYY-MM-DD>-<topic>.md: what changed, how it works, how to use it, "
                         "tests and edge cases covered, verification evidence, limitations) for: "
                         + ", ".join(missing_docs))
        return {"decision": "block", "reason": "\n\n".join(parts)}

    for root_str in finished:
        dirty.pop(root_str, None)
        passed_sigs.pop(root_str, None)
    state.update(dirty=dirty, gate_blocks=0)
    save_state(sid, state)
    return {"systemMessage": "Verify gate: " + "; ".join(notes)} if notes else None


if __name__ == "__main__":
    run(handle)
