"""Desktop notification when Claude needs you or finishes a long turn.

Notification event: always notifies (permission prompt, idle input).
Stop event: notifies only when the turn ran longer than KIT_NOTIFY_MIN_SECONDS
(default 45), using the start time recorded by context_watch.py, so short turns
stay quiet. Windows uses a tray balloon via PowerShell; macOS uses osascript;
Linux uses notify-send. Set KIT_NOTIFY=0 to disable.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from _common import load_state, run

MIN_SECONDS = float(os.environ.get("KIT_NOTIFY_MIN_SECONDS", "45"))

PS_BALLOON = r"""
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = $env:KIT_TITLE
$n.BalloonTipText = $env:KIT_BODY
$n.Visible = $true
$n.ShowBalloonTip(6000)
Start-Sleep -Seconds 7
$n.Dispose()
"""


def send(title: str, body: str) -> None:
    env = dict(os.environ, KIT_TITLE=title, KIT_BODY=body)
    flags = 0
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", PS_BALLOON]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    elif sys.platform == "darwin":
        cmd = ["osascript", "-e", 'display notification (system attribute "KIT_BODY") '
                                  'with title (system attribute "KIT_TITLE")']
    else:
        cmd = ["notify-send", title, body]
    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=flags)


def handle(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if os.environ.get("KIT_NOTIFY", "1") == "0":
        return None
    project = Path(data.get("cwd") or ".").name
    event = data.get("hook_event_name")
    if event == "Notification":
        send(f"Claude needs you ({project})", str(data.get("message") or "Waiting for your input"))
    elif event == "Stop" and not data.get("stop_hook_active"):
        started = load_state(data.get("session_id", "unknown")).get("turn_started")
        if started and time.time() - started >= MIN_SECONDS:
            minutes = (time.time() - started) / 60
            send(f"Claude finished ({project})", f"Turn completed after {minutes:.1f} min")
    return None


if __name__ == "__main__":
    run(handle)
