# Skills per phase, code graph first, private settings

Date: 2026-11-05 · Repo: my-agent-kit · Branch: main · Status: done

## Summary
The autopilot now names the skill to use in each phase, prefers a project's own
skills over generic kit skills, and starts from a graphify code graph when one
exists. Employer- and machine-specific settings moved out of git into
`~/.my-agent-kit/`.

## What changed
| File | Change |
| --- | --- |
| `skills/task-autopilot/SKILL.md` | Phase-by-phase skill table; project skills first; graph-first understand step; blast-radius check; graph refresh at hand-over |
| `skills/task-autopilot/references/graphify.md` | MCP vs CLI, per-phase commands, Application Control workaround |
| `hooks/session_start.py` | Brief lists project skills (repo or parent `.claude/skills`) and the nearest code graph with its age |
| `hooks/post_edit.py` | Standalone test scripts count as test runs for the gate |
| `hooks/_common.py` | Private config lists are appended to shared ones |
| `scripts/kit.py` | `~/.my-agent-kit/CLAUDE.private.md` appended to the managed CLAUDE.md block |
| `config/*.json`, `global/CLAUDE.md`, docs | Employer-specific values removed; generic wording |

## Tests and edge cases
| Case | Expected | Result |
| --- | --- | --- |
| Skills and graph in a parent folder | listed with graph age | pass |
| Folder in `.claude/skills` without SKILL.md | ignored | pass |
| No skills, no graph | no extra lines | pass |
| Harness runs (venv python path, `& $py -W ignore x/test_y.py`, quoted paths, `node a.test.js`, `*_test.py`) | counted as tests | pass |
| `python build.py`, `node server.js`, `notepad test_x.py` | not counted | pass |
| Private identity rules | added after shared rules; scalar keys override | pass |
| Private prefix override | applies only under that prefix, case-insensitive | pass |
| `CLAUDE.private.md` | appended inside the managed block | pass |

`python -m unittest discover -s tests` -> OK.

## Limitations
Earlier commits already on GitHub contain employer-specific values in
`config/identity.json` and `config/automation.json`; removing them from history
needs a history rewrite and force push, which is the owner's decision.
