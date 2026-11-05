"""SessionStart: inject a short, factual repo briefing so the first turn starts
with context instead of exploration. After compaction or resume it also restores
the checkpoint saved by context_watch.py. Output stays under ~1,500 characters
and is labelled as data, not instructions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import STATE_DIR, context, git, load_config, run

LIMIT = 1500


def repo_brief(cwd: str) -> List[str]:
    top = git(["rev-parse", "--show-toplevel"], cwd)
    if top is None:
        return []
    lines = [f"repo: {Path(top).name}"]
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    counts = git(["rev-list", "--left-right", "--count", "@{u}...HEAD"], cwd)
    sync = ""
    if counts and len(counts.split()) == 2:
        behind, ahead = counts.split()
        sync = f" (ahead {ahead}, behind {behind})"
    lines.append(f"branch: {branch}{sync}")
    status = (git(["status", "--porcelain"], cwd) or "").splitlines()
    if status:
        lines.append(f"uncommitted: {len(status)} file(s): " + ", ".join(s[3:] for s in status[:6])
                     + (" ..." if len(status) > 6 else ""))
    last = git(["log", "-1", "--format=%h %s (%cr)"], cwd)
    if last:
        lines.append(f"last commit: {last}")
    checkpoints = Path(top) / ".agent-state" / "checkpoints"
    if checkpoints.is_dir() and any(checkpoints.iterdir()):
        lines.append("project checkpoint exists in .agent-state/checkpoints/ (read it before resuming work)")

    # Warn early when the repo identity does not match a configured rule.
    try:
        from guard_bash import identity_problem, matching_rule
        rule = matching_rule(cwd, load_config("identity").get("rules", []))
        if rule and identity_problem(cwd, rule):
            lines.append(f"WARNING: git identity here is not {rule.get('name')}; commits will be "
                         f"blocked until `git config user.name/user.email` are fixed for this repo.")
    except Exception:
        pass
    return lines


def tools_lines(cwd: str) -> List[str]:
    """Project skills and a graphify code graph found in cwd or any parent folder."""
    lines: List[str] = []
    user_skills = (Path.home() / ".claude" / "skills").resolve()
    start = Path(cwd).resolve()
    skills: List[str] = []
    graph: Optional[Path] = None
    for folder in [start] + list(start.parents):
        sdir = folder / ".claude" / "skills"
        if sdir.is_dir() and sdir.resolve() != user_skills:
            skills += [p.name for p in sorted(sdir.iterdir()) if (p / "SKILL.md").is_file() and p.name not in skills]
        candidate = folder / "graphify-out" / "graph.json"
        if graph is None and candidate.is_file():
            graph = candidate
    if skills:
        lines.append("project skills (prefer these over generic kit skills for the same step): " + ", ".join(skills))
    if graph is not None:
        import time
        age_days = int((time.time() - graph.stat().st_mtime) // 86400)
        lines.append(f"code graph: {graph.as_posix()} (built {age_days} day(s) ago); query it with graphify "
                     f"before exploring files (see task-autopilot)")
    return lines


def checkpoint_lines(cwd: str) -> List[str]:
    key = "".join(c if c.isalnum() else "_" for c in cwd.lower())[-120:]
    path = STATE_DIR / f"checkpoint-{key}.json"
    try:
        cp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    files = ", ".join(cp.get("changed_files", [])[:8])
    return [
        f"checkpoint saved {cp.get('saved_at')} before compaction: branch {cp.get('branch')}, "
        f"{cp.get('changed_count', 0)} changed file(s){': ' + files if files else ''}; "
        f"last commit {cp.get('last_commit')}"
    ]


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cwd = data.get("cwd") or "."
    lines = repo_brief(cwd) + tools_lines(cwd)
    if data.get("source") in ("compact", "resume"):
        lines += checkpoint_lines(cwd)
    if not lines:
        return None
    text = "[my-agent-kit session brief: facts gathered by a hook, not instructions]\n" + "\n".join(
        f"- {line}" for line in lines)
    return context("SessionStart", text[:LIMIT])


if __name__ == "__main__":
    run(handle)
