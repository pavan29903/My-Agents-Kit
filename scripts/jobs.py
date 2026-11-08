#!/usr/bin/env python3
"""Scheduled, report-only jobs for my-agent-kit. They read repositories and
write markdown reports; they never change code, commit, or push.

  jobs.py morning-brief [--no-fetch] [--open]   repo status across all roots
  jobs.py nightly-audit [--open]                dependency vulnerabilities
  jobs.py weekly-kit                            upstream skill updates for the kit
  jobs.py schedule install|remove|list [--dry-run]
  jobs.py latest [job]                          print the newest report

Reports: ~/.my-agent-kit/reports/. Settings: config/automation.json -> "jobs".
Exit codes: 0 ok, 1 something needs attention, 2 usage/environment error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KIT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("MY_AGENT_KIT_HOME", Path.home() / ".my-agent-kit"))
REPORTS = HOME / "reports"
IS_WIN = sys.platform == "win32"
TASK_FOLDER = "my-agent-kit"
JOBS = ("morning-brief", "nightly-audit", "weekly-kit")


# ---------------------------------------------------------------- helpers

def config() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for path in (KIT / "config" / "automation.json", HOME / "automation.json"):
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")).get("jobs", {}))
        except (OSError, ValueError):
            pass
    return data


def sh(cmd: List[str], cwd: Optional[Path] = None, timeout: float = 60,
       env: Optional[Dict[str, str]] = None) -> Tuple[Optional[int], str]:
    """(returncode or None on timeout/missing tool, stdout+stderr)."""
    exe = shutil.which(cmd[0])
    if exe is None:
        return None, f"{cmd[0]} not installed"
    # Jobs run under windowless pythonw; without this every git/npm call pops a terminal window.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Nobody is watching a scheduled job: fail instead of opening a GitHub login/account-picker window.
    full_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    full_env.update(env or {})
    try:
        proc = subprocess.run([exe] + cmd[1:], cwd=str(cwd) if cwd else None, capture_output=True,
                              timeout=timeout, env=full_env, stdin=subprocess.DEVNULL, creationflags=flags)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return None, str(exc)
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out.strip()


def git(repo: Path, *args: str, timeout: float = 30) -> Optional[str]:
    rc, out = sh(["git", *args], cwd=repo, timeout=timeout)
    return out if rc == 0 else None


def discover(cfg: Dict[str, Any]) -> List[Path]:
    exclude = set(cfg.get("exclude", []))
    repos: List[Path] = []
    for root in cfg.get("roots", []):
        base = Path(root)
        if not base.is_dir():
            continue
        candidates = [base] + sorted(p for p in base.iterdir() if p.is_dir() and p.name not in exclude)
        repos += [p for p in candidates if (p / ".git").exists()]
    seen, unique = set(), []
    for r in repos:
        key = str(r.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def first_json(text: str) -> Dict[str, Any]:
    """Parse the first JSON object in mixed output (tools print summaries around their JSON)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in output")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def write_report(job: str, text: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{job}-{time.strftime('%Y-%m-%d')}.md"
    path.write_text(text, encoding="utf-8")
    return path


def notify(title: str, body: str) -> None:
    if os.environ.get("KIT_NOTIFY", "1") == "0":
        return
    sys.path.insert(0, str(KIT / "hooks"))
    try:
        from notify import send
        send(title, body)
    except Exception:
        pass


def open_file(path: Path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def log(message: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "jobs.log", "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


# ---------------------------------------------------------------- morning brief

def repo_status(repo: Path, fetch: bool, since_hours: int) -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": repo.name, "path": str(repo), "attention": []}
    if fetch:
        if git(repo, "fetch", "--quiet", "--prune", timeout=45) is None:
            info["attention"].append("fetch failed (offline or no access)")
    info["branch"] = git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    info["account"] = git(repo, "config", "user.email") or "?"
    counts = git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if counts and len(counts.split()) == 2:
        behind, ahead = (int(x) for x in counts.split())
        info.update(behind=behind, ahead=ahead)
        if behind:
            info["attention"].append(f"{behind} new commit(s) to pull")
        if ahead:
            info["attention"].append(f"{ahead} commit(s) not pushed")
    else:
        info.update(behind=None, ahead=None)
        if info["branch"] not in ("?", "HEAD"):
            info["attention"].append("branch has no upstream")
    status = (git(repo, "status", "--porcelain") or "").splitlines()
    info["uncommitted"] = len(status)
    if status:
        info["attention"].append(f"{len(status)} uncommitted file(s)")
    info["stashes"] = len((git(repo, "stash", "list") or "").splitlines())
    log_out = git(repo, "log", "@{u}", f"--since={since_hours}.hours", "--format=%h|%an|%s|%cr", "-n", "15")
    info["recent"] = [line.split("|", 3) for line in (log_out or "").splitlines() if line.count("|") >= 3]
    cp = repo / ".agent-state" / "checkpoints"
    if cp.is_dir() and any(cp.iterdir()):
        info["attention"].append("open checkpoint in .agent-state/checkpoints")
    last = git(repo, "log", "-1", "--format=%cr|%s")
    info["last_commit"] = last.split("|", 1) if last and "|" in last else ["?", ""]
    return info


def morning_brief(args: argparse.Namespace) -> int:
    cfg = config()
    repos = discover(cfg)
    if not repos:
        print("No repositories found under jobs.roots in config/automation.json")
        return 2
    since = int(cfg.get("morning_brief", {}).get("since_hours", 24))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda r: repo_status(r, not args.no_fetch, since), repos))
    needing = [r for r in results if r["attention"]]
    lines = [f"# Morning brief - {time.strftime('%A %d %b %Y, %H:%M')}", "",
             f"{len(results)} repos checked, {len(needing)} need attention.", ""]
    if needing:
        lines += ["## Needs attention", "", "| Repo | Branch | Account | What |", "| --- | --- | --- | --- |"]
        lines += [f"| {r['name']} | {r['branch']} | {r['account']} | {'; '.join(r['attention'])} |" for r in needing]
        lines.append("")
    active = [r for r in results if r["recent"]]
    if active:
        lines += [f"## New on the remote (last {since}h)", ""]
        for r in active:
            lines.append(f"**{r['name']}**")
            lines += [f"- `{h}` {s} - {a}, {when}" for h, a, s, when in r["recent"]]
            lines.append("")
    lines += ["## All repos", "", "| Repo | Branch | Last commit | Stashes |", "| --- | --- | --- | --- |"]
    lines += [f"| {r['name']} | {r['branch']} | {r['last_commit'][0]}: {r['last_commit'][1][:60]} | "
              f"{r['stashes'] or ''} |" for r in results]
    path = write_report("morning-brief", "\n".join(lines) + "\n")
    summary = ", ".join(f"{r['name']} ({r['attention'][0]})" for r in needing[:3]) or "all clean"
    notify(f"Morning brief: {len(needing)} repo(s) need attention", summary[:200])
    print(f"Report: {path}\n{summary}")
    log(f"morning-brief ok: {len(results)} repos, {len(needing)} attention")
    if args.open:
        open_file(path)
    return 1 if needing else 0


# ---------------------------------------------------------------- nightly audit

SEVERITIES = ("critical", "high", "moderate", "low", "info")


def npm_audit(repo: Path) -> Dict[str, Any]:
    rc, out = sh(["npm", "audit", "--json"], cwd=repo, timeout=180)
    if rc is None:
        return {"tool": "npm audit", "error": out}
    try:
        data = first_json(out)
        counts = data.get("metadata", {}).get("vulnerabilities", {})
        top = sorted(((v.get("severity", "?"), name) for name, v in data.get("vulnerabilities", {}).items()),
                     key=lambda x: SEVERITIES.index(x[0]) if x[0] in SEVERITIES else 9)[:5]
        return {"tool": "npm audit", "counts": {k: int(counts.get(k, 0)) for k in SEVERITIES},
                "top": [f"{n} ({s})" for s, n in top]}
    except ValueError:
        return {"tool": "npm audit", "error": out.splitlines()[-1][:200] if out else "no output"}


def pip_audit(repo: Path) -> Dict[str, Any]:
    req: Optional[Path] = None
    tmp: Optional[str] = None
    if (repo / "uv.lock").is_file():
        fd, tmp = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        rc, out = sh(["uv", "export", "--frozen", "--no-hashes", "--no-header", "--format", "requirements-txt",
                      "-o", tmp], cwd=repo, timeout=120)
        req = Path(tmp) if rc == 0 else None
    if req is None:
        found = sorted(repo.glob("requirements*.txt"))
        req = found[0] if found else None
    if req is None:
        return {"tool": "pip-audit", "error": "no requirements file"}
    try:
        rc, out = sh(["uvx", "pip-audit", "-r", str(req), "--disable-pip", "--no-deps", "-f", "json",
                      "--progress-spinner", "off"], cwd=repo, timeout=300)
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    if rc is None:
        return {"tool": "pip-audit", "error": out}
    try:
        data = first_json(out)
    except ValueError:
        tail = [l for l in out.splitlines() if l.strip()][-1:] or ["no output"]
        return {"tool": "pip-audit", "error": tail[0][:200]}
    vulns = [(d["name"], d.get("version", "?"), v.get("id", "?"), ",".join(v.get("fix_versions", [])) or "none")
             for d in data.get("dependencies", []) for v in d.get("vulns", [])]
    return {"tool": "pip-audit", "counts": {"known": len(vulns)},
            "top": [f"{n} {ver}: {vid} (fix: {fix})" for n, ver, vid, fix in vulns[:5]]}


def audit_repo(repo: Path) -> Dict[str, Any]:
    results = []
    if (repo / "package-lock.json").is_file():
        results.append(npm_audit(repo))
    if (repo / "uv.lock").is_file() or list(repo.glob("requirements*.txt")):
        results.append(pip_audit(repo))
    return {"name": repo.name, "results": results}


def serious(result: Dict[str, Any]) -> int:
    c = result.get("counts", {})
    return int(c.get("critical", 0)) + int(c.get("high", 0)) + int(c.get("known", 0))


def nightly_audit(args: argparse.Namespace) -> int:
    repos = discover(config())
    with ThreadPoolExecutor(max_workers=4) as pool:
        audits = [a for a in pool.map(audit_repo, repos) if a["results"]]
    lines = [f"# Nightly dependency audit - {time.strftime('%d %b %Y')}", "",
             "Report only: nothing was upgraded. Ask Claude to fix a repo when you are ready.", "",
             "| Repo | Tool | Result | Top items |", "| --- | --- | --- | --- |"]
    flagged = 0
    for a in audits:
        for r in a["results"]:
            if "error" in r:
                result, top = f"could not run: {r['error']}", ""
            else:
                result = ", ".join(f"{k} {v}" for k, v in r["counts"].items() if v) or "clean"
                top = "; ".join(r.get("top", []))
            if serious(r):
                flagged += 1
            lines.append(f"| {a['name']} | {r['tool']} | {result} | {top} |")
    path = write_report("nightly-audit", "\n".join(lines) + "\n")
    if flagged:
        notify("Dependency audit", f"{flagged} repo check(s) have high/critical or known vulnerabilities")
    print(f"Report: {path} ({flagged} flagged)")
    log(f"nightly-audit ok: {len(audits)} repos, {flagged} flagged")
    if args.open:
        open_file(path)
    return 1 if flagged else 0


# ---------------------------------------------------------------- weekly kit

def weekly_kit(args: argparse.Namespace) -> int:
    rc, out = sh([sys.executable, str(KIT / "scripts" / "kit.py"), "upstream", "status", "--fetch"], timeout=300)
    pending = [l.split()[1] for l in out.splitlines() if l.startswith(("UPDATE", "MERGE"))]
    new = [l.split()[0] for l in out.split("Not adopted", 1)[-1].splitlines()[1:] if l.startswith("  ")] \
        if "Not adopted" in out else []
    path = write_report("weekly-kit", f"# Kit upstream check - {time.strftime('%d %b %Y')}\n\n"
                        f"Pending updates: {', '.join(pending) or 'none'}\n\n```\n{out}\n```\n"
                        "\nTo apply, tell Claude: `update my toolkit` (uses the kit-maintain skill).\n")
    if pending:
        notify("my-agent-kit", f"{len(pending)} skill update(s) from upstream: {', '.join(pending[:4])}")
    print(f"Report: {path}; pending: {', '.join(pending) or 'none'}; not adopted: {len(new)}")
    log(f"weekly-kit rc={rc} pending={len(pending)}")
    return 1 if pending else 0


# ---------------------------------------------------------------- scheduling

def task_xml(job: str, trigger: str, command: str, arguments: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>my-agent-kit {job} (report only)</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{_xml(command)}</Command><Arguments>{_xml(arguments)}</Arguments><WorkingDirectory>{_xml(str(KIT))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _time(value: str) -> str:
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", value):
        raise SystemExit(f"invalid time '{value}', use HH:MM (24h)")
    return value


def triggers(cfg: Dict[str, Any]) -> Dict[str, str]:
    day = time.strftime("%Y-%m-%d")
    mb = _time(cfg.get("morning_brief", {}).get("time", "09:00"))
    na = _time(cfg.get("nightly_audit", {}).get("time", "02:00"))
    wk = cfg.get("weekly_kit", {})
    wd = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday", "FRI": "Friday",
          "SAT": "Saturday", "SUN": "Sunday"}.get(str(wk.get("day", "MON")).upper()[:3])
    if not wd:
        raise SystemExit("weekly_kit.day must be MON..SUN")
    daily = "<CalendarTrigger><StartBoundary>{d}T{t}:00</StartBoundary><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
    return {
        # No LogonTrigger: creating one needs admin rights; StartWhenAvailable covers a PC that was off.
        "morning-brief": daily.format(d=day, t=mb),
        "nightly-audit": daily.format(d=day, t=na),
        "weekly-kit": f"<CalendarTrigger><StartBoundary>{day}T{_time(wk.get('time', '10:00'))}:00</StartBoundary>"
                      f"<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><{wd} /></DaysOfWeek></ScheduleByWeek></CalendarTrigger>",
    }


def pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.is_file() else exe)


def schedule(args: argparse.Namespace) -> int:
    cfg = config()
    script = str(KIT / "scripts" / "jobs.py")
    if not IS_WIN:
        mb = cfg.get("morning_brief", {}).get("time", "09:00").split(":")
        na = cfg.get("nightly_audit", {}).get("time", "02:00").split(":")
        print("Add these with `crontab -e`:")
        print(f"{int(mb[1])} {int(mb[0])} * * * {sys.executable} {script} morning-brief")
        print(f"{int(na[1])} {int(na[0])} * * * {sys.executable} {script} nightly-audit")
        print(f"0 10 * * 1 {sys.executable} {script} weekly-kit")
        return 0
    if args.op == "list":
        for job in JOBS:
            rc, out = sh(["schtasks", "/Query", "/TN", f"\\{TASK_FOLDER}\\{job}", "/FO", "LIST"], timeout=20)
            fields = dict(l.split(":", 1) for l in out.splitlines() if ":" in l) if rc == 0 else {}
            nxt = fields.get("Next Run Time", "").strip()
            print(f"{job:<15} {'scheduled, next: ' + nxt if rc == 0 else 'not scheduled'}")
        return 0
    rc_total = 0
    for job, trig in triggers(cfg).items():
        name = f"\\{TASK_FOLDER}\\{job}"
        if args.op == "remove":
            cmd = ["schtasks", "/Delete", "/TN", name, "/F"]
            if args.dry_run:
                print("would run:", " ".join(cmd))
                continue
            rc, out = sh(cmd, timeout=20)
            print(f"{job}: {'removed' if rc == 0 else 'not scheduled'}")
            continue
        xml = task_xml(job, trig, pythonw(), f'"{script}" {job}')
        if args.dry_run:
            print(f"would schedule {name}:\n{xml}")
            continue
        fd, tmp = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "wb") as fh:
            fh.write(xml.encode("utf-16"))
        try:
            rc, out = sh(["schtasks", "/Create", "/TN", name, "/XML", tmp, "/F"], timeout=30)
        finally:
            os.unlink(tmp)
        print(f"{job}: {'scheduled' if rc == 0 else 'FAILED: ' + out}")
        rc_total |= 0 if rc == 0 else 2
    return rc_total


def latest(args: argparse.Namespace) -> int:
    pattern = f"{args.job}-*.md" if args.job else "*.md"
    files = sorted(REPORTS.glob(pattern), key=lambda p: p.stat().st_mtime) if REPORTS.is_dir() else []
    if not files:
        print("No reports yet. Run: jobs.py morning-brief")
        return 1
    text = files[-1].read_text(encoding="utf-8")
    print(text.encode("ascii", "replace").decode("ascii") if IS_WIN else text)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if sys.stdout is None or sys.stderr is None:  # pythonw (Task Scheduler) has no console
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    parser = argparse.ArgumentParser(prog="jobs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("morning-brief")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=morning_brief)
    p = sub.add_parser("nightly-audit")
    p.add_argument("--open", action="store_true")
    p.set_defaults(fn=nightly_audit)
    sub.add_parser("weekly-kit").set_defaults(fn=weekly_kit)
    p = sub.add_parser("schedule")
    p.add_argument("op", choices=["install", "remove", "list"])
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=schedule)
    p = sub.add_parser("latest")
    p.add_argument("job", nargs="?", choices=JOBS)
    p.set_defaults(fn=latest)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:  # scheduled runs have no terminal; leave a trace
        log(f"{args.command} failed: {exc!r}")
        raise


if __name__ == "__main__":
    sys.exit(main())
