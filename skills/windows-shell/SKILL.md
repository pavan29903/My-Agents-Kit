---
name: windows-shell
description: Practical rules for running commands on Windows from an agent - PowerShell 5.1 vs Git Bash syntax, paths, encodings, line endings, processes, ports, and long paths. Use when a command fails on Windows, when writing scripts that must run on Windows, or when porting macOS/Linux instructions.
---

# Windows shell

## Pick the shell deliberately

- **PowerShell 5.1** (`powershell.exe`) is the default here. It has no `&&`/`||`
  (use `A; if ($?) { B }`), no ternary, no `??`. `pwsh` (7+) has them but may
  not be installed.
- **Git Bash** understands POSIX syntax and forward-slash paths
  (`/d/src/app` or `D:/src/app`). Use it for `.sh` scripts and Unix tools.
- Do not mix: `$env:VAR` is PowerShell; `$VAR` and `export` are Bash.

## Common translations

| Unix | PowerShell |
| --- | --- |
| `cat f`, `head -n 20 f` | `Get-Content f`, `Get-Content f -TotalCount 20` |
| `grep -r pat .` | `Select-String -Path .\* -Pattern pat -Recurse` (or `rg`) |
| `rm -rf dir` | `Remove-Item -Recurse -Force dir` |
| `mkdir -p a/b` | `New-Item -ItemType Directory -Force a\b` |
| `which x` | `(Get-Command x).Source` |
| `VAR=1 cmd` | `$env:VAR = '1'; cmd` |
| `lsof -i :3000` | `Get-NetTCPConnection -LocalPort 3000 -State Listen` |
| `kill PID` | `Stop-Process -Id PID` |
| `ln -s src dst` (dir) | `cmd /c mklink /J dst src` (junction, no admin) |

## Gotchas

- **Encoding**: `Set-Content`/`Add-Content` default to the ANSI code page in
  5.1. Pass `-Encoding utf8`. Python reading files: always `encoding="utf-8"`.
- **Native stderr**: in 5.1, `2>&1` on a native exe wraps lines as errors and
  sets `$?` false even on success. Check `$LASTEXITCODE` instead.
- **Line endings**: set `* text=auto eol=lf` in `.gitattributes` for scripts
  that run in Bash; CRLF breaks shebang scripts.
- **Long paths**: paths over 260 chars fail in many tools. Clone to a short
  root (`D:\src`), or `git config --global core.longpaths true`.
- **python**: `python` may resolve to the Microsoft Store stub; check
  `(Get-Command python).Source`. Prefer `uv run`.
- **Execution policy**: if `.ps1` scripts are blocked, run
  `powershell -ExecutionPolicy Bypass -File script.ps1` rather than changing
  the machine policy.
- **File locks**: "being used by another process" means an editor, dev server,
  or antivirus holds the file; stop the process instead of retrying in a loop.
- **Quoting**: in PowerShell, single quotes are literal and double quotes
  expand `$`. Use the stop-parsing token `--%` for arguments full of symbols.
