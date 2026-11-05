# One-message autopilot, verify gate, and scheduled jobs

Date: 2026-11-05 · Repo: my-agent-kit · Branch: main · Status: done (needs `kit install` and `kit schedule install` on the PC)

## Summary
Tasks can now be given in one message: Claude plans, implements, tests edge
cases, verifies, and documents on its own, and a Stop hook stops it from
finishing while checks fail. Three report-only scheduled jobs keep an eye on
all repos without using Claude.

## What changed
| File | Change |
| --- | --- |
| `skills/task-autopilot/` | New skill with edge-case checklist and change-doc template |
| `global/CLAUDE.md` | Rule: use task-autopilot automatically for code changes |
| `hooks/_project.py` | Project root, settings, safe test-command and formatter detection |
| `hooks/post_edit.py` | Records edits per project, formats with the project's own formatter, notes test runs |
| `hooks/verify_gate.py` | Stop gate: runs tests or requires Claude to run them; requires a change doc for 3+ files |
| `scripts/jobs.py` | morning-brief, nightly-audit, weekly-kit, Task Scheduler install/list/remove |
| `scripts/kit.py` | `kit jobs`, `kit schedule`, `kit brief` |
| `config/automation.json` | Gate defaults, job settings (private overrides in `~/.my-agent-kit/`) |
| `config/hooks.json` | PostToolUse and Stop gate registered |
| `docs/automation.md` | User guide |
| `tests/test_automation.py` | 45 tests for the above |

## How it works
See the diagram in `docs/automation.md`. Hooks share session state in
`~/.my-agent-kit/state/session-<id>.json`: `dirty` (edited files per project),
`last_code_edit`, `tested_at`, `passed` (file mtimes at the last green run),
and `gate_blocks` (consecutive blocks, capped at 2).

## How to use it
```powershell
python scripts/kit.py install            # load the new hooks
python scripts/kit.py schedule install   # create the scheduled tasks
```
Then describe tasks normally in Claude Code. Read reports with `kit brief`.

## Tests and edge cases
| Case | Expected | Result |
| --- | --- | --- |
| No edits in session | gate silent | pass |
| Only docs edited | no test run, state cleared | pass |
| Tests pass | stop allowed, summary message | pass |
| Tests fail | block with output tail | pass |
| Still failing after 2 blocks | gate gives up, warns user, resets | pass |
| Fix after a block | passes, counter reset | pass |
| 3+ code files, no doc | block for doc; tests not rerun after doc written | pass |
| README/CHANGELOG/docs path counts as doc; `src/notes.md` does not | as stated | pass |
| Gate off via `.kit.json` / `KIT_GATE=0` | no run | pass |
| Test command times out | process tree killed, not blocking | pass |
| No safe test command | block until Claude runs tests after its last edit | pass |
| Tests run before the last edit | do not count | pass |
| `auto_tests: false` | project command never auto-run | pass |
| Path-prefix override from the private config | `auto_tests` false only under that prefix | pass |
| Project folder deleted mid-session | dropped quietly | pass |
| Two projects, one failing | only the failing one reported | pass |
| Custom npm scripts (`node test-webhook.js`, echo, curl) | never auto-run | pass |
| react-scripts | runs once with `CI=true --watchAll=false` | pass |
| JS runner without node_modules | not run | pass |
| Tests only inside venv/node_modules | not treated as project tests | pass |
| Broken `.kit.json` | ignored, defaults used | pass |
| Formatter without project config / not installed | file untouched | pass |
| Formatter fails | reported to Claude, no crash | pass |
| Edits under `.claude/`, non-edit tools, missing path | ignored | pass |
| Hook given invalid JSON | exits 0, no output | pass |
| Repo discovery: non-repos, excluded dirs, missing roots | skipped | pass |
| Repo behind / dirty / no upstream / teammate commits | reported | pass |
| npm audit and pip-audit JSON, garbage output, missing tool, summary text after JSON | parsed or reported as "could not run" | pass |
| Task XML with spaces and `&` in paths; invalid times/days | valid XML; invalid input rejected | pass |
| `schedule --dry-run` | no system calls | pass |
| `pythonw` with no console | no crash | pass |

`python -m unittest discover -s tests` -> `Ran 74 tests ... OK`. `kit validate` -> 19 skills, 0 errors.

## Verification
- Morning brief run read-only (`--no-fetch`) against the 20 real repos: report
  written; stashes moved to an FYI column after the first run flagged 13 repos.
- Real audits on a Python repo (pip-audit) and a Node repo (npm audit) returned
  parsed vulnerability counts. The first run exposed a parsing bug
  (summary text after JSON), fixed and covered by a test.
- Hooks exercised end to end through stdin/stdout like Claude Code calls them.
- `kit schedule install` created all three tasks. The first attempt failed for
  morning-brief because a log-on trigger needs admin rights; removed it.
- Not run: the gate inside a live Claude session (needs `kit install`).

## Limitations and follow-ups
- Stop and notify hooks run in parallel, so a "Claude finished" notification
  can appear even when the gate sends Claude back to work.
- `tested_at` records that a test command ran, not that it passed; the skill
  still requires quoting real output.
- Morning brief CI status is not included (GitHub CLI is not logged in).
