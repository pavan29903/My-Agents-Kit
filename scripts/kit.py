#!/usr/bin/env python3
"""my-agent-kit command line.

  kit doctor                         check tools, identity, install state
  kit install [--only X] [--dry-run] link skills, merge hooks, sync global CLAUDE.md
  kit audit                          show drift between the kit and ~/.claude (no changes)
  kit uninstall [--dry-run]          remove everything the kit installed
  kit update                         git pull the kit, reinstall, fetch upstreams, show status
  kit validate [skill ...]           lint skills against the Agent Skills spec
  kit new-skill NAME -d DESCRIPTION  scaffold a skill
  kit upstream fetch|status|new|diff|update|accept|add|forget
  kit jobs morning-brief|nightly-audit|weekly-kit [--open]   run a scheduled job now
  kit schedule install|remove|list [--dry-run]               Windows Task Scheduler
  kit brief                                                  print the latest morning brief

Exit codes: 0 ok, 1 drift/findings/conflicts, 2 usage or environment error.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KIT = Path(__file__).resolve().parent.parent
SKILLS = KIT / "skills"
CONFIG = KIT / "config"
LOCK_PATH = CONFIG / "upstream.lock.json"
CACHE = KIT / ".upstream"
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
KIT_HOME = Path(os.environ.get("MY_AGENT_KIT_HOME", Path.home() / ".my-agent-kit"))
HOOK_MARKER = "my-agent-kit/hooks/"
BLOCK_BEGIN = "<!-- my-agent-kit:begin (managed by `kit install`; edit global/CLAUDE.md in the kit instead) -->"
BLOCK_END = "<!-- my-agent-kit:end -->"
IS_WIN = sys.platform == "win32"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class KitError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def say(msg: str = "") -> None:
    print(msg.encode("ascii", "replace").decode("ascii") if IS_WIN else msg, flush=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise KitError(f"{path} is not valid JSON: {exc}") from None


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def backup(path: Path) -> Optional[Path]:
    if not path.exists() and not path.is_symlink():
        return None
    dest = KIT_HOME / "backups" / time.strftime("%Y%m%d-%H%M%S") / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir() and not is_link(path):
        shutil.move(str(path), str(dest))
    else:
        shutil.copy2(str(path), str(dest))
    return dest


def norm_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(norm_bytes(data)).hexdigest()


def is_link(path: Path) -> bool:
    """True for symlinks and Windows junctions."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(IS_WIN and getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def link_target(path: Path) -> Optional[Path]:
    try:
        return Path(os.path.realpath(path))
    except OSError:
        return None


def make_link(dest: Path, src: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if IS_WIN:  # junctions need no admin rights or developer mode
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(src)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise KitError(f"mklink failed for {dest}: {proc.stdout.strip()} {proc.stderr.strip()}")
    else:
        os.symlink(src, dest, target_is_directory=True)


def remove_link(path: Path) -> None:
    # Never rmtree a link: older Pythons follow junctions and delete the target.
    if IS_WIN and path.is_dir():
        os.rmdir(path)
    else:
        os.unlink(path)


def python_cmd() -> str:
    return os.environ.get("KIT_PYTHON") or ("python" if IS_WIN else "python3")


def run_git(args: List[str], git_dir: Optional[Path] = None, cwd: Optional[Path] = None,
            binary: bool = False, check: bool = True) -> Any:
    cmd = ["git"] + (["--git-dir", str(git_dir)] if git_dir else []) + args
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True)
    if check and proc.returncode != 0:
        raise KitError(f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace").strip()


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: Dict[str, str] = {}
    key = None
    for line in text[4:end].split("\n"):
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            meta[key] = "" if value in (">", "|", ">-", "|-") else value.strip("\"'")
        elif key and line.startswith((" ", "\t")):
            meta[key] = (meta[key] + " " + line.strip()).strip()
    return meta, text[end + 4:].lstrip("\n")


def local_skills() -> List[Path]:
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()) if SKILLS.is_dir() else []


# ---------------------------------------------------------------- install

class Plan:
    def __init__(self) -> None:
        self.actions: List[Tuple[str, Any]] = []

    def add(self, description: str, fn: Any = None) -> None:
        self.actions.append((description, fn))

    def apply(self, dry_run: bool) -> int:
        for description, fn in self.actions:
            say(("would " if dry_run else "") + description)
            if not dry_run and fn:
                fn()
        return len(self.actions)


def plan_skills(plan: Plan, remove_all: bool = False) -> None:
    target_dir = CLAUDE_HOME / "skills"
    wanted = {} if remove_all else {p.name: p for p in local_skills()}
    for name, src in wanted.items():
        dest = target_dir / name
        if is_link(dest) and link_target(dest) == src.resolve():
            continue
        if dest.exists() or is_link(dest):
            plan.add(f"back up existing {dest} and link it to the kit",
                     lambda d=dest, s=src: (backup(d) if not is_link(d) else remove_link(d), make_link(d, s)))
        else:
            plan.add(f"link skill {name}", lambda d=dest, s=src: make_link(d, s))
    if target_dir.is_dir():
        for dest in sorted(target_dir.iterdir()):
            target = link_target(dest) if is_link(dest) else None
            if target and str(target).lower().startswith(str(SKILLS.resolve()).lower()) and dest.name not in wanted:
                plan.add(f"remove stale skill link {dest.name}", lambda d=dest: remove_link(d))


def desired_hooks() -> Dict[str, List[Dict[str, Any]]]:
    manifest = load_json(CONFIG / "hooks.json", {"events": {}})
    hooks_dir = (KIT / "hooks").resolve().as_posix()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event, entries in manifest.get("events", {}).items():
        for entry in entries:
            script = KIT / "hooks" / entry["script"]
            if not script.is_file():
                raise KitError(f"hook script missing: {script}")
            hook = {"type": "command", "command": f'{python_cmd()} "{hooks_dir}/{entry["script"]}"'}
            if entry.get("timeout"):
                hook["timeout"] = entry["timeout"]
            group: Dict[str, Any] = {"hooks": [hook]}
            if entry.get("matcher"):
                group["matcher"] = entry["matcher"]
            out.setdefault(event, []).append(group)
    return out


def merged_settings(settings: Dict[str, Any], remove_all: bool = False) -> Dict[str, Any]:
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks") or {}
    for event in list(hooks):
        kept = [g for g in hooks[event]
                if not any(HOOK_MARKER in str(h.get("command", "")).replace("\\", "/") for h in g.get("hooks", []))]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not remove_all:
        for event, groups in desired_hooks().items():
            hooks.setdefault(event, []).extend(groups)
    if hooks:
        result["hooks"] = hooks
    else:
        result.pop("hooks", None)
    return result


def plan_hooks(plan: Plan, remove_all: bool = False) -> None:
    path = CLAUDE_HOME / "settings.json"
    current = load_json(path, {})
    new = merged_settings(current, remove_all)
    if new != current:
        plan.add(f"{'remove kit hooks from' if remove_all else 'merge kit hooks into'} {path} (backup first)",
                 lambda: (backup(path), write_atomic(path, json.dumps(new, indent=2) + "\n")))


def claude_md_text(existing: str, remove_all: bool = False) -> str:
    pattern = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL)
    stripped = pattern.sub("", existing).rstrip("\n")
    if remove_all:
        return stripped + "\n" if stripped else ""
    source = (KIT / "global" / "CLAUDE.md").read_text(encoding="utf-8").strip("\n")
    private = KIT_HOME / "CLAUDE.private.md"  # employer- or machine-specific rules, never committed
    if private.is_file():
        source += "\n\n" + private.read_text(encoding="utf-8").strip("\n")
    block =f"{BLOCK_BEGIN}\n{source}\n{BLOCK_END}\n"
    return (stripped + "\n\n" + block) if stripped else block


def plan_claude_md(plan: Plan, remove_all: bool = False) -> None:
    path = CLAUDE_HOME / "CLAUDE.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = claude_md_text(existing, remove_all)
    if new.replace("\r\n", "\n") != existing.replace("\r\n", "\n"):
        plan.add(f"update the managed block in {path}",
                 lambda: (backup(path), write_atomic(path, new)) if path.is_file() else write_atomic(path, new))


PARTS = {"skills": plan_skills, "hooks": plan_hooks, "claude-md": plan_claude_md}


def build_plan(only: Optional[List[str]], remove_all: bool = False) -> Plan:
    plan = Plan()
    for name in only or list(PARTS):
        PARTS[name](plan, remove_all)
    return plan


def cmd_install(args: argparse.Namespace) -> int:
    plan = build_plan(args.only)
    if not plan.actions:
        say("Everything is already installed and current.")
        return 0
    plan.apply(args.dry_run)
    if not args.dry_run:
        say("Done. Restart Claude Code (or run /hooks) to load hook changes.")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    plan = build_plan(args.only)
    if not plan.actions:
        say("No drift: ~/.claude matches the kit.")
        return 0
    say("Drift found (run `kit install` to fix):")
    for description, _ in plan.actions:
        say(f"  - {description}")
    return 1


def cmd_uninstall(args: argparse.Namespace) -> int:
    plan = build_plan(args.only, remove_all=True)
    if not plan.actions:
        say("Nothing from the kit is installed.")
    plan.apply(args.dry_run)
    return 0


# ---------------------------------------------------------------- validate

REF_RE = re.compile(r"(?<![\w/.-])((?:scripts|references|assets|examples|templates)/[\w./-]+\w)")


def validate_skill(path: Path) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        return [f"{path.name}: missing SKILL.md"], []
    text = skill_md.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if not meta:
        errors.append(f"{path.name}: SKILL.md has no YAML frontmatter")
        return errors, warnings
    name, desc = meta.get("name", ""), meta.get("description", "")
    if name != path.name:
        errors.append(f"{path.name}: frontmatter name '{name}' must match the folder name")
    if not NAME_RE.match(name) or len(name) > 64:
        errors.append(f"{path.name}: name must be lowercase words joined by hyphens, max 64 chars")
    if not desc:
        errors.append(f"{path.name}: description is required")
    elif len(desc) > 1024:
        errors.append(f"{path.name}: description is {len(desc)} chars (max 1024)")
    elif not re.search(r"\b(use|when|invoke)\b", desc, re.IGNORECASE):
        warnings.append(f"{path.name}: description should say when to use the skill")
    lines = body.count("\n") + 1
    if lines > 500:
        warnings.append(f"{path.name}: body is {lines} lines; move detail into references/")
    referenced = set()
    for ref in REF_RE.findall(body):
        referenced.add(ref)
        if not (path / ref).exists():
            errors.append(f"{path.name}: references missing file {ref}")
    for f in path.rglob("*"):
        if f.is_file() and f.name != "SKILL.md" and "__pycache__" not in f.parts:
            rel = f.relative_to(path).as_posix()
            if rel.split("/")[0] in ("scripts", "references", "assets", "examples", "templates") and \
                    not any(rel == r or rel.startswith(r.rstrip("/") + "/") for r in referenced) and \
                    rel not in text:
                warnings.append(f"{path.name}: bundled file {rel} is never referenced from SKILL.md")
    return errors, warnings


def cmd_validate(args: argparse.Namespace) -> int:
    targets = [SKILLS / n for n in args.skills] if args.skills else local_skills()
    errors: List[str] = []
    warnings: List[str] = []
    for path in targets:
        e, w = validate_skill(path)
        errors += e
        warnings += w
    for line in errors:
        say(f"ERROR   {line}")
    for line in warnings:
        say(f"warning {line}")
    say(f"{len(targets)} skill(s) checked: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


def cmd_new_skill(args: argparse.Namespace) -> int:
    name = args.name
    if not NAME_RE.match(name):
        raise KitError("skill name must be lowercase words joined by hyphens, e.g. api-scaffold")
    path = SKILLS / name
    if path.exists():
        raise KitError(f"{path} already exists")
    path.mkdir(parents=True)
    title = name.replace("-", " ").capitalize()
    (path / "SKILL.md").write_bytes((
        f"---\nname: {name}\ndescription: {args.description}\n---\n\n# {title}\n\n"
        "## When to use\n\n- \n\n## Steps\n\n1. \n\n## Verify\n\n- \n").encode("utf-8"))
    say(f"Created {path / 'SKILL.md'}. Fill it in, then run `kit validate {name}` and `kit install --only skills`.")
    return 0


# ---------------------------------------------------------------- upstream

def sources() -> Dict[str, Dict[str, Any]]:
    return load_json(CONFIG / "upstreams.json", {"sources": {}}).get("sources", {})


def source(name: str) -> Dict[str, Any]:
    src = sources().get(name)
    if not src:
        raise KitError(f"unknown upstream source '{name}' (see config/upstreams.json)")
    return src


def cache_dir(name: str) -> Path:
    return CACHE / f"{name}.git"


def load_lock() -> Dict[str, Any]:
    return load_json(LOCK_PATH, {"version": 1, "skills": {}})


def save_lock(lock: Dict[str, Any]) -> None:
    lock["skills"] = dict(sorted(lock.get("skills", {}).items()))
    write_atomic(LOCK_PATH, json.dumps(lock, indent=2) + "\n")


def fetch(name: str) -> str:
    src, gd = source(name), cache_dir(name)
    if not gd.exists():
        gd.parent.mkdir(parents=True, exist_ok=True)
        say(f"cloning {src['url']} (bare; nothing is checked out, so long paths are harmless)")
        run_git(["clone", "--bare", "--filter=blob:none", "--quiet", src["url"], str(gd)])
    else:
        run_git(["fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*"], git_dir=gd)
    return head(name)


def head(name: str) -> str:
    gd = cache_dir(name)
    if not gd.exists():
        raise KitError(f"upstream '{name}' not fetched yet; run `kit upstream fetch`")
    return run_git(["rev-parse", f"refs/heads/{source(name).get('branch', 'main')}"], git_dir=gd)


def upstream_files(name: str, rev: str, path: str) -> Dict[str, bytes]:
    gd = cache_dir(name)
    listing = run_git(["ls-tree", "-r", "-z", "--name-only", rev, "--", path.rstrip("/") + "/"],
                      git_dir=gd, binary=True)
    out: Dict[str, bytes] = {}
    for full in filter(None, listing.decode("utf-8").split("\0")):
        rel = full[len(path.rstrip("/")) + 1:]
        out[rel] = run_git(["show", f"{rev}:{full}"], git_dir=gd, binary=True)
    return out


def local_files(skill: str) -> Dict[str, bytes]:
    root = SKILLS / skill
    if not root.is_dir():
        return {}
    return {f.relative_to(root).as_posix(): f.read_bytes() for f in sorted(root.rglob("*"))
            if f.is_file() and "__pycache__" not in f.parts}


def hashes(files: Dict[str, bytes]) -> Dict[str, str]:
    return {k: sha(v) for k, v in sorted(files.items())}


def skill_state(skill: str, entry: Dict[str, Any], latest: Optional[str]) -> Dict[str, Any]:
    base = entry.get("files", {})
    local = hashes(local_files(skill))
    info: Dict[str, Any] = {"skill": skill, "source": entry["source"], "pinned": entry["commit"][:8],
                            "local_changed": sorted(k for k in set(base) | set(local) if base.get(k) != local.get(k))}
    if latest is None:
        info.update(state="not-fetched", upstream_changed=[])
        return info
    now = hashes(upstream_files(entry["source"], latest, entry["path"]))
    info["upstream_changed"] = sorted(k for k in set(base) | set(now) if base.get(k) != now.get(k))
    info["latest"] = latest[:8]
    if not now:
        info["state"] = "removed-upstream"
    elif info["upstream_changed"] and info["local_changed"]:
        info["state"] = "conflict"
    elif info["upstream_changed"]:
        info["state"] = "update-available"
    elif info["local_changed"]:
        info["state"] = "customized"
    else:
        info["state"] = "current"
    return info


def all_states(fetch_first: bool) -> List[Dict[str, Any]]:
    lock = load_lock()
    latest: Dict[str, Optional[str]] = {}
    for name in {e["source"] for e in lock["skills"].values()}:
        try:
            latest[name] = fetch(name) if fetch_first else head(name)
        except KitError as exc:
            say(f"warning: {exc}")
            latest[name] = None
    return [skill_state(s, e, latest[e["source"]]) for s, e in lock["skills"].items()]


def new_upstream_skills(name: str, rev: str) -> List[Tuple[str, str]]:
    src, gd = source(name), cache_dir(name)
    skills_dir = src.get("skills_dir", "skills")
    names = run_git(["ls-tree", "-d", "--name-only", rev, skills_dir + "/"], git_dir=gd).splitlines()
    adopted = {e["path"] for e in load_lock()["skills"].values() if e["source"] == name}
    ignored = set(src.get("ignore", [])) | set(load_json(CONFIG / "upstreams.json", {}).get("declined", {}).get(name, []))
    out = []
    for full in names:
        short = full.split("/")[-1]
        if full in adopted or short in ignored:
            continue
        text = run_git(["show", f"{rev}:{full}/SKILL.md"], git_dir=gd, check=False)
        desc = parse_frontmatter(text)[0].get("description", "") if text else ""
        out.append((short, desc))
    return out


def write_skill(skill: str, files: Dict[str, bytes]) -> None:
    root = SKILLS / skill
    for rel in local_files(skill):
        if rel not in files:
            (root / rel).unlink()
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(norm_bytes(data) if b"\0" not in data else data)


def merge3(local: bytes, base: bytes, theirs: bytes) -> Tuple[bytes, int]:
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for label, data in (("local", local), ("base", base), ("upstream", theirs)):
            p = Path(tmp) / label
            p.write_bytes(norm_bytes(data))
            paths.append(str(p))
        proc = subprocess.run(["git", "merge-file", "-p", "-L", "yours", "-L", "base", "-L", "upstream"] + paths,
                              capture_output=True)
        if proc.returncode < 0:
            raise KitError("git merge-file failed")
        return proc.stdout, proc.returncode


def cmd_upstream(args: argparse.Namespace) -> int:
    op = args.op
    lock = load_lock()

    if op == "fetch":
        for name in sources():
            say(f"{name}: {fetch(name)[:8]}")
        return 0

    if op in ("status", "new"):
        rc = 0
        if op == "status":
            states = all_states(fetch_first=args.fetch)
            labels = {"current": "ok", "customized": "ok (your edits kept)", "update-available": "UPDATE",
                      "conflict": "MERGE", "removed-upstream": "GONE", "not-fetched": "?"}
            for s in states:
                extra = ""
                if s.get("upstream_changed"):
                    extra = f"  upstream changed: {', '.join(s['upstream_changed'][:4])}"
                say(f"{labels[s['state']]:<22} {s['skill']:<28} pinned {s['pinned']}{extra}")
                if s["state"] in ("update-available", "conflict"):
                    rc = 1
            if args.json:
                say(json.dumps(states, indent=2))
        for name in sources():
            try:
                rev = head(name)
            except KitError:
                continue
            fresh = new_upstream_skills(name, rev)
            if fresh:
                say(f"\nNot adopted from {name} (add with `kit upstream add {name} <skill>`):")
                for short, desc in fresh:
                    say(f"  {short:<30} {desc[:90]}")
        return rc

    if op == "add":
        if len(args.names) < 2:
            raise KitError("usage: kit upstream add SOURCE SKILL [--as NAME]")
        src_name, up_skill = args.names[0], args.names[1]
        local_name = args.as_name or up_skill
        if local_name in lock["skills"] or (SKILLS / local_name).exists():
            raise KitError(f"skill '{local_name}' already exists in the kit")
        rev = head(src_name)
        path = f"{source(src_name).get('skills_dir', 'skills')}/{up_skill}"
        files = upstream_files(src_name, rev, path)
        if not files:
            raise KitError(f"{path} not found in {src_name}@{rev[:8]}")
        write_skill(local_name, files)
        lock["skills"][local_name] = {"source": src_name, "path": path, "commit": rev, "files": hashes(files)}
        save_lock(lock)
        say(f"Added {local_name} from {src_name}@{rev[:8]} ({len(files)} file(s)). Run `kit install --only skills`.")
        return 0

    names = args.names or [s for s in lock["skills"]]
    unknown = [n for n in names if n not in lock["skills"]]
    if unknown:
        raise KitError(f"not tracked upstream: {', '.join(unknown)}")

    if op == "forget":
        for n in names:
            del lock["skills"][n]
        save_lock(lock)
        say(f"Stopped tracking {', '.join(names)}; the files stay as your own skills.")
        return 0

    rc = 0
    fetched: Dict[str, str] = {}
    for skill in names:
        entry = lock["skills"][skill]
        if entry["source"] not in fetched:
            fetched[entry["source"]] = fetch(entry["source"]) if op in ("update", "diff") else head(entry["source"])
        rev = fetched[entry["source"]]
        base_files = upstream_files(entry["source"], entry["commit"], entry["path"])
        theirs = upstream_files(entry["source"], rev, entry["path"])
        mine = local_files(skill)
        state = skill_state(skill, entry, rev)["state"]

        if op == "diff":
            for rel in sorted(set(base_files) | set(theirs)):
                a = norm_bytes(base_files.get(rel, b"")).decode("utf-8", "replace").splitlines(True)
                b = norm_bytes(theirs.get(rel, b"")).decode("utf-8", "replace").splitlines(True)
                for line in difflib.unified_diff(a, b, f"pinned/{rel}", f"upstream/{rel}"):
                    sys.stdout.write(line if line.endswith("\n") else line + "\n")
            continue

        if op == "accept":
            entry.update(commit=rev, files=hashes(theirs))
            say(f"{skill}: pinned to {rev[:8]} keeping your current files")
            continue

        # op == "update"
        if state in ("current", "customized"):
            say(f"{skill}: already up to date")
            continue
        if state == "removed-upstream":
            say(f"{skill}: removed upstream; keeping yours. `kit upstream forget {skill}` to stop tracking.")
            continue
        if state == "update-available" or args.theirs:
            write_skill(skill, theirs)
            entry.update(commit=rev, files=hashes(theirs))
            say(f"{skill}: updated to {rev[:8]}")
            continue
        # conflict: three-way merge each file
        merged: Dict[str, bytes] = {}
        conflicts: List[str] = []
        for rel in sorted(set(base_files) | set(theirs) | set(mine)):
            b, t, m = base_files.get(rel), theirs.get(rel), mine.get(rel)
            if t is not None and b is not None and sha(t) == sha(b):
                if m is not None:
                    merged[rel] = m  # only you changed it
            elif m is not None and b is not None and sha(m) == sha(b):
                if t is not None:
                    merged[rel] = t  # only upstream changed it
            elif m is None and t is None:
                continue
            elif m is None or t is None:
                conflicts.append(f"{rel} (deleted on one side)")
                merged[rel] = m if m is not None else t  # type: ignore[assignment]
            else:
                out, n = merge3(m, b or b"", t)
                merged[rel] = out
                if n:
                    conflicts.append(f"{rel} ({n} conflict(s))")
        if conflicts and not args.write_conflicts:
            review = CACHE / "review" / skill
            if review.exists():
                shutil.rmtree(review)
            for rel, data in merged.items():
                (review / rel).parent.mkdir(parents=True, exist_ok=True)
                (review / rel).write_bytes(data)
            say(f"{skill}: CONFLICTS in {', '.join(conflicts)}.\n"
                f"  Merged result with conflict markers: {review}\n"
                f"  Resolve there and copy into skills/{skill}, then `kit upstream accept {skill}`,\n"
                f"  or rerun with --write-conflicts to put the markers straight into skills/{skill}.")
            rc = 1
            continue
        write_skill(skill, merged)
        if not conflicts:
            entry.update(commit=rev, files=hashes(theirs))
            say(f"{skill}: merged upstream {rev[:8]} with your edits")
        else:
            say(f"{skill}: wrote conflict markers into skills/{skill}; fix them, then `kit upstream accept {skill}`")
            rc = 1
    if op in ("accept", "update"):
        save_lock(lock)
    return rc


# ---------------------------------------------------------------- doctor / update

def tool_version(cmd: List[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.returncode == 0 else "error"
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "missing"


def cmd_doctor(args: argparse.Namespace) -> int:
    problems = 0
    say(f"kit:         {KIT}")
    say(f"claude home: {CLAUDE_HOME}")
    for label, cmd in (("python", [python_cmd(), "--version"]), ("git", ["git", "--version"]),
                       ("gh", ["gh", "--version"]), ("uv", ["uv", "--version"]), ("node", ["node", "--version"])):
        version = tool_version(cmd)
        say(f"{label + ':':<12} {version}")
        if label in ("python", "git") and version in ("missing", "error"):
            problems += 1
    account = tool_version(["gh", "api", "user", "--jq", ".login"])
    say(f"{'gh account:':<12} {account}")
    for rule in load_json(CONFIG / "identity.json", {}).get("rules", []):
        if "@" not in rule.get("email", ""):
            say(f"PROBLEM: identity rule for {rule.get('name')} has no email; edit config/identity.json")
            problems += 1
    name = run_git(["config", "user.name"], cwd=KIT, check=False)
    email = run_git(["config", "user.email"], cwd=KIT, check=False)
    say(f"kit repo git identity: {name} <{email}>")
    drift = build_plan(None).actions
    say(f"install drift: {len(drift)} item(s)" + (" (run `kit install`)" if drift else ""))
    problems += bool(drift)
    log = KIT_HOME / "state" / "hook-errors.log"
    if log.is_file() and log.stat().st_size:
        say(f"hook errors logged in {log} (last lines):")
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]:
            say(f"  {line}")
    return 1 if problems else 0


def cmd_update(args: argparse.Namespace) -> int:
    if run_git(["remote"], cwd=KIT, check=False):
        say("pulling the kit ...")
        say(run_git(["pull", "--ff-only"], cwd=KIT) or "up to date")
    install_rc = cmd_install(argparse.Namespace(only=None, dry_run=False))
    say("\nchecking upstreams ...")
    ns = argparse.Namespace(op="status", names=[], fetch=True, json=False)
    return cmd_upstream(ns) or install_rc


# ---------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("jobs", "schedule", "brief"):
        sys.path.insert(0, str(KIT / "scripts"))
        import jobs
        rest = argv[1:] if argv[0] == "jobs" else ["schedule"] + argv[1:] if argv[0] == "schedule"             else ["latest", "morning-brief"]
        return jobs.main(rest)
    parser = argparse.ArgumentParser(prog="kit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn, help_text in (("install", cmd_install, "install the kit into ~/.claude"),
                                ("audit", cmd_audit, "report drift without changing anything"),
                                ("uninstall", cmd_uninstall, "remove what the kit installed")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--only", action="append", choices=list(PARTS))
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(fn=fn)

    p = sub.add_parser("validate", help="lint skills")
    p.add_argument("skills", nargs="*")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("new-skill", help="scaffold a skill")
    p.add_argument("name")
    p.add_argument("-d", "--description", required=True)
    p.set_defaults(fn=cmd_new_skill)

    p = sub.add_parser("upstream", help="track skills copied from other repos")
    p.add_argument("op", choices=["fetch", "status", "new", "diff", "update", "accept", "add", "forget"])
    p.add_argument("names", nargs="*")
    p.add_argument("--as", dest="as_name")
    p.add_argument("--fetch", action="store_true", help="fetch before status")
    p.add_argument("--theirs", action="store_true", help="update: discard your edits and take upstream")
    p.add_argument("--write-conflicts", action="store_true", help="update: write conflict markers into the skill")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_upstream)

    sub.add_parser("doctor", help="check the environment").set_defaults(fn=cmd_doctor)
    sub.add_parser("update", help="pull, install, check upstreams").set_defaults(fn=cmd_update)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except KitError as exc:
        say(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
