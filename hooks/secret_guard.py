"""PreToolUse guard for Write/Edit/MultiEdit: asks before writing text that looks
like a real credential into a file. Placeholders and env-var references pass."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from _common import pre_tool_decision, run

PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("connection string with password",
     re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:[^\s@/]{6,}@[^\s]+")),
]
PLACEHOLDER = re.compile(r"x{6,}|\.\.\.|<[^>]+>|your[_-]?|example|changeme|dummy|placeholder|\$\{|\$[A-Z_]+",
                         re.IGNORECASE)
SKIP_SUFFIXES = (".example", ".sample", ".template", ".md")


def find_secret(text: str) -> Optional[str]:
    for label, pattern in PATTERNS:
        for m in pattern.finditer(text):
            if not PLACEHOLDER.search(m.group(0)):
                return label
    return None


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if data.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        return None
    tool_input = data.get("tool_input") or {}
    path = str(tool_input.get("file_path", ""))
    if path.lower().endswith(SKIP_SUFFIXES):
        return None
    chunks = [tool_input.get("content", ""), tool_input.get("new_string", "")]
    chunks += [e.get("new_string", "") for e in tool_input.get("edits", []) or [] if isinstance(e, dict)]
    label = find_secret("\n".join(str(c) for c in chunks if c))
    if not label:
        return None
    return pre_tool_decision(
        "ask",
        f"my-agent-kit secret guard: this write to {path or 'a file'} appears to contain a {label}. "
        "Secrets belong in environment variables or an ignored .env file, not in source.",
    )


if __name__ == "__main__":
    run(handle)
