"""Project detection shared by the automation hooks: finds a file's project
root, loads per-project settings, and works out which test and format commands
are safe to run automatically.

Only well-known test runners are auto-detected. A custom `npm test` script such
as `node test-webhook.js` may call real services, so it runs only when the
project opts in with `.kit.json`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import KIT_ROOT

IS_WIN = sys.platform == "win32"
ROOT_MARKERS = ("package.json", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "Cargo.toml", ".git")
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", "dist", "build", ".next", ".tox"}
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt")
KNOWN_JS_RUNNERS = re.compile(r"^\s*(?:npx\s+)?(jest|vitest(?:\s+run)?|mocha|react-scripts test|node --test|ava|tap)\b")
DEFAULTS: Dict[str, Any] = {"gate": True, "auto_tests": True, "format": True, "test_timeout": 300, "docs_min_files": 3, "max_gate_blocks": 2}


def project_root(path: str) -> Optional[Path]:
    """Nearest ancestor holding a project marker (package.json, pyproject.toml, .git, ...)."""
    p = Path(path)
    for candidate in [p] + list(p.parents) if p.is_dir() else list(p.parents):
        if any((candidate / m).exists() for m in ROOT_MARKERS):
            return candidate
    return None


def settings(root: Path) -> Dict[str, Any]:
    """Defaults <- config/automation.json <- ~/.my-agent-kit/automation.json (by path prefix) <- <root>/.kit.json."""
    merged = dict(DEFAULTS)
    for path in (KIT_ROOT / "config" / "automation.json", Path.home() / ".my-agent-kit" / "automation.json"):
        data = _read_json(path)
        merged.update(data.get("gate", {}))
        for prefix, override in (data.get("projects") or {}).items():
            if _norm(str(root)).startswith(_norm(prefix)):
                merged.update(override)
    merged.update(_read_json(root / ".kit.json"))
    return merged


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower() + "/"


def python_for(root: Path) -> List[str]:
    for venv in (".venv", "venv", "env"):
        exe = root / venv / ("Scripts/python.exe" if IS_WIN else "bin/python")
        if exe.is_file():
            return [str(exe)]
    if (root / "uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run", "--frozen", "python"]
    return [sys.executable]


def has_python_tests(root: Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if any((f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py") for f in filenames):
            return True
    return False


def pytest_available(python: List[str], root: Path) -> bool:
    if python[0] == "uv":
        return "pytest" in (root / "uv.lock").read_text(encoding="utf-8", errors="replace")
    import subprocess
    try:
        return subprocess.run(python + ["-c", "import pytest"], cwd=str(root), capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def test_command(root: Path, cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return {"cmd": str | list, "env": {...}, "why": str} or None when nothing safe is detectable."""
    cfg = settings(root) if cfg is None else cfg
    if cfg.get("test"):
        return {"cmd": cfg["test"], "shell": True, "env": {}, "why": ".kit.json"}
    pkg = _read_json(root / "package.json")
    script = (pkg.get("scripts") or {}).get("test", "")
    if script and KNOWN_JS_RUNNERS.match(script) and (root / "node_modules").is_dir():
        manager = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        cmd = [manager, "test"] + (["--", "--watchAll=false"] if "react-scripts" in script else [])
        if manager == "npm":
            cmd.insert(1, "--silent")
        return {"cmd": cmd, "shell": IS_WIN, "env": {"CI": "true"}, "why": f"package.json test script ({script.split()[0]})"}
    if (root / "go.mod").is_file() and shutil.which("go"):
        return {"cmd": ["go", "test", "./..."], "shell": False, "env": {}, "why": "go.mod"}
    if (root / "Cargo.toml").is_file() and shutil.which("cargo"):
        return {"cmd": ["cargo", "test", "-q"], "shell": False, "env": {}, "why": "Cargo.toml"}
    if has_python_tests(root):
        python = python_for(root)
        if pytest_available(python, root):
            return {"cmd": python + ["-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], "shell": False,
                    "env": {"PYTHONDONTWRITEBYTECODE": "1"}, "why": "pytest"}
        if (root / "tests").is_dir():
            return {"cmd": python + ["-m", "unittest", "discover", "-s", "tests"], "shell": False,
                    "env": {"PYTHONDONTWRITEBYTECODE": "1"}, "why": "unittest"}
    return None


def formatter_for(file_path: Path, root: Path) -> Optional[List[str]]:
    """A formatter command only when the project already uses that formatter, so work repos
    never get surprise whole-file reformatting."""
    suffix = file_path.suffix.lower()
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace") \
        if (root / "pyproject.toml").is_file() else ""
    if suffix in (".py", ".pyi"):
        if "[tool.ruff" in pyproject or (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file():
            exe = _tool(root, "ruff")
            if exe:
                return exe + ["format", str(file_path)]
        if "[tool.black" in pyproject:
            exe = _tool(root, "black")
            if exe:
                return exe + ["-q", str(file_path)]
        return None
    if suffix in (".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".json", ".html", ".vue", ".yaml", ".yml", ".md"):
        pkg = _read_json(root / "package.json")
        uses_prettier = "prettier" in pkg or any((root / n).exists() for n in (
            ".prettierrc", ".prettierrc.json", ".prettierrc.js", ".prettierrc.cjs", ".prettierrc.yaml",
            ".prettierrc.yml", "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs"))
        bin_path = root / "node_modules" / ".bin" / ("prettier.cmd" if IS_WIN else "prettier")
        if uses_prettier and bin_path.is_file():
            return [str(bin_path), "--write", "--log-level", "warn", str(file_path)]
    return None


def _tool(root: Path, name: str) -> Optional[List[str]]:
    for venv in (".venv", "venv", "env"):
        exe = root / venv / ("Scripts" if IS_WIN else "bin") / (name + (".exe" if IS_WIN else ""))
        if exe.is_file():
            return [str(exe)]
    found = shutil.which(name)
    return [found] if found else None


def is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_SUFFIXES)
