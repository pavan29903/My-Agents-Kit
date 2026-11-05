"""PreToolUse guard for Bash and PowerShell commands.

- Denies commits, pushes and PRs that carry AI attribution (Co-Authored-By:
  Claude, "Generated with Claude Code", anthropic noreply addresses).
- Denies commits/pushes in repos covered by config/identity.json when the repo's
  git identity is not the configured one, and prints the exact fix.
- Asks before commands that destroy work: force push, reset --hard, clean -fd,
  recursive deletes of a drive root or home directory, disk formatting.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _common import git, load_config, norm_path, pre_tool_decision, run

ATTRIBUTION = re.compile(
    r"co-authored-by:\s*claude|generated (with|by) \[?claude|noreply@anthropic\.com"
    r"|claude\.com/claude-code|\U0001F916 generated",
    re.IGNORECASE,
)
GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+commit\b")
GIT_PUSH = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+push\b")
GH_PR = re.compile(r"\bgh\s+pr\s+(create|edit|comment)\b")
LEADING_CD = re.compile(r"^\s*(?:cd|Set-Location|pushd)\s+(\"[^\"]+\"|'[^']+'|\S+)\s*(?:&&|;)")
GIT_C_DIR = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)")
MSG_FILE = re.compile(r"\bgit\b[^\n]*\bcommit\b[^\n]*?(?:-F|--file)[ =](\"[^\"]+\"|'[^']+'|\S+)")

DANGEROUS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bgit\b[^\n;&|]*\bpush\b[^\n;&|]*(?:\s--force(?!-with-lease)\b|\s-f\b|\s\+\S)"),
     "force push rewrites remote history; prefer --force-with-lease"),
    (re.compile(r"\bgit\b[^\n;&|]*\breset\s+--hard\b"), "git reset --hard discards uncommitted work"),
    (re.compile(r"\bgit\b[^\n;&|]*\bclean\s+-[a-zA-Z]*f"), "git clean -f deletes untracked files"),
    (re.compile(r"\bgit\b[^\n;&|]*\bcheckout\s+--\s+\.(\s|$)|\bgit\b[^\n;&|]*\brestore\s+\.(\s|$)"),
     "discards all local changes"),
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*\s+(?:-[a-zA-Z]+\s+)*"
                r"(?:/|~|\$HOME|/\*|[a-zA-Z]:[\\/]?|\*|\.)(?:\s|$|;|&)"),
     "recursive delete of a root, home, or current directory"),
    (re.compile(r"Remove-Item\b(?=[^\n]*-Recurse)[^\n]*?\s(?:[a-zA-Z]:\\?|~|\$env:USERPROFILE|\$HOME)(?:\s|$|;)",
                re.IGNORECASE),
     "recursive delete of a drive root or home directory"),
    (re.compile(r"\b(?:Format-Volume|diskpart|mkfs(?:\.\w+)?)\b|\bformat\s+[a-zA-Z]:", re.IGNORECASE),
     "disk formatting"),
    (re.compile(r"\bdel\s+(?:/[a-zA-Z]\s+)*/s\b[^\n]*\s[a-zA-Z]:\\?\s*$", re.IGNORECASE),
     "recursive delete of a drive root"),
]


def _unquote(token: str) -> str:
    return token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token


def resolve_dir(command: str, cwd: str) -> str:
    """Best effort: honour a leading `cd X &&` or `git -C X` when locating the repo."""
    for pattern in (GIT_C_DIR, LEADING_CD):
        m = pattern.search(command)
        if m:
            target = Path(_unquote(m.group(1)).replace("~", str(Path.home()), 1))
            return str(target if target.is_absolute() else Path(cwd or ".") / target)
    return cwd


def matching_rule(repo_dir: str, rules: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    top = git(["rev-parse", "--show-toplevel"], repo_dir)
    if top is None:
        return None
    remotes = git(["remote", "-v"], repo_dir) or ""
    for rule in rules:
        prefix = rule.get("path_prefix")
        if prefix and norm_path(top).startswith(norm_path(prefix)):
            return rule
        pattern = rule.get("remote_pattern")
        if pattern and re.search(pattern, remotes, re.IGNORECASE):
            return rule
    return None


def identity_problem(repo_dir: str, rule: Dict[str, Any]) -> Optional[str]:
    name = git(["config", "user.name"], repo_dir) or ""
    email = git(["config", "user.email"], repo_dir) or ""
    want_name, want_email = rule.get("name", ""), rule.get("email", "")
    if (want_name and name != want_name) or (want_email and email.lower() != want_email.lower()):
        return (
            f"This repo must commit as {want_name} <{want_email}> but git is configured as "
            f"{name or '?'} <{email or '?'}>. Fix it for this repo only, then retry:\n"
            f'  git config user.name "{want_name}"\n  git config user.email "{want_email}"'
        )
    return None


def unpushed_problem(repo_dir: str, rule: Optional[Dict[str, Any]]) -> Optional[str]:
    log = git(["log", "@{u}..HEAD", "--format=%H%x1f%an%x1f%ae%x1f%B%x1e"], repo_dir)
    if log is None:  # no upstream yet: everything not on any remote
        log = git(["log", "HEAD", "--not", "--remotes", "-n", "200",
                   "--format=%H%x1f%an%x1f%ae%x1f%B%x1e"], repo_dir)
    if not log:
        return None
    for record in log.split("\x1e"):
        parts = record.strip().split("\x1f")
        if len(parts) < 4:
            continue
        sha, name, email, body = parts[0][:8], parts[1], parts[2], parts[3]
        if ATTRIBUTION.search(body) or ATTRIBUTION.search(email):
            return (f"Commit {sha} contains AI attribution. Remove it before pushing "
                    f"(e.g. git commit --amend, or git rebase to reword).")
        if rule and rule.get("email") and email.lower() != rule["email"].lower():
            return (f"Commit {sha} is authored by {name} <{email}>, not {rule.get('name')} "
                    f"<{rule['email']}>. Re-author it before pushing: "
                    f'git commit --amend --reset-author --no-edit (for the latest commit).')
    return None


def message_file_text(command: str, cwd: str) -> str:
    m = MSG_FILE.search(command)
    if not m:
        return ""
    path = Path(_unquote(m.group(1)))
    path = path if path.is_absolute() else Path(cwd or ".") / path
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return ""


def decide(command: str, cwd: str, config: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    rules = config.get("rules", [])
    is_commit, is_push, is_pr = (bool(p.search(command)) for p in (GIT_COMMIT, GIT_PUSH, GH_PR))

    if config.get("block_ai_attribution", True) and (is_commit or is_pr or is_push):
        if ATTRIBUTION.search(command) or ATTRIBUTION.search(message_file_text(command, cwd)):
            return ("deny", "Commit/PR text contains AI attribution (Co-Authored-By: Claude or "
                            "'Generated with Claude Code'). Remove those lines; commits must be "
                            "attributed only to the repository owner.")

    if is_commit or is_push:
        repo_dir = resolve_dir(command, cwd)
        rule = matching_rule(repo_dir, rules)
        if rule:
            problem = identity_problem(repo_dir, rule)
            if problem:
                return ("deny", problem)
        if is_push and config.get("block_ai_attribution", True):
            problem = unpushed_problem(repo_dir, rule)
            if problem:
                return ("deny", problem)

    for pattern, why in DANGEROUS:
        if pattern.search(command):
            return ("ask", f"my-agent-kit guard: {why}. Confirm this is intended.")
    return None


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return None
    command = str((data.get("tool_input") or {}).get("command", ""))
    result = decide(command, data.get("cwd", ""), load_config("identity"))
    return pre_tool_decision(*result) if result else None


if __name__ == "__main__":
    run(handle)
