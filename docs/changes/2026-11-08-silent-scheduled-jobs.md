# Scheduled jobs run without windows or login prompts

Date: 2026-11-08 · Repo: my-agent-kit · Branch: main · Status: done

## Summary
After a boot, missed jobs start at once (`StartWhenAvailable`). Each `git`,
`npm` or `uvx` call they made opened a console window, and `git fetch` on repos
whose remote names no account opened a GitHub login or account picker. Jobs
now start every child process windowless and tell Git never to prompt.

## What changed
| File | Change |
| --- | --- |
| `scripts/jobs.py` | `sh()` uses `CREATE_NO_WINDOW` and sets `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never` for every command |
| `tests/test_automation.py` | Regression test for the window flag; missing-tool and timeout cases |

## How it works
A fetch that needs a login now fails quietly and shows as `fetch failed` in
the morning brief instead of waiting for someone to click. To make such a
repo fetch, name its account so the credential manager can pick the saved
login: `git config --local credential.username <account>` (or put the
username in the remote URL).

## Verified
- All 80 tests pass.
- Ran the morning-brief task from Task Scheduler: it finished in 7 seconds
  (it previously hung on login prompts until terminated).
