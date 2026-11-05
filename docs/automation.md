# Automation guide

Two layers of automation, both installed by `kit install` (scheduled jobs need
one extra command).

```
You type one message
      |
      v
task-autopilot skill (auto-loaded by global CLAUDE.md rule)
  understand -> plan -> implement -> tests + edge cases -> verify -> change doc -> hand over
      |                     |                                   |
      |          post_edit hook: records edits,                 |
      |          formats with the project's formatter           |
      v                                                         v
verify_gate hook (on every Stop): tests pass? doc written? ---- no --> Claude keeps working
                                                       |
                                                      yes --> you get the result + notification

Task Scheduler (no Claude usage, report only)
  09:00 daily   morning-brief  -> ~/.my-agent-kit/reports/morning-brief-<date>.md + notification
  02:00 daily   nightly-audit  -> dependency vulnerabilities per repo
  Mon 10:00     weekly-kit     -> upstream skill updates for this kit
```

## 1. One-message workflow

Just describe the task:

```
Add rate limiting to the password-reset endpoint: max 3 per 10 minutes per user.
```

Claude follows `skills/task-autopilot` without being told: it reads the code,
plans (asks only real product questions), implements, writes tests including
edge cases from `skills/task-autopilot/references/edge-cases.md`, runs them,
and writes `docs/changes/<date>-<topic>.md` from the template in the same
folder.

### Which skills the flow uses

`task-autopilot` loads skills at the right phase (full table in
`skills/task-autopilot/SKILL.md`): graphify and `session-preflight` to
understand, `decision-lens` / `requirement-reconciliation` to plan, `ponytail`,
`debug-loop`, `security-audit` while implementing, `done-check` and
`playwright-verification` to verify, `git-ship`, `project-checkpoint`,
`delivery-review`, `skill-forge` at hand-over.

**Project skills win.** The session brief lists skills found in `.claude/skills`
of the repo or any parent folder. When one covers a phase (for example a
project's isolated-test or ship skill), Claude uses it instead of the generic
kit skill. The verify gate also recognises standalone test scripts
(`python test_x.py`, `node x.test.js`) as test runs.

**Code graph first.** When a `graphify-out/graph.json` exists in the repo or a
parent folder, Claude queries it before exploring files, checks blast radius
with `graphify affected` before changing shared code, and refreshes it with
`graphify update` after changes. Details and the Windows workaround (if
Application Control blocks graphify.exe, run it through its own Python) are in
`skills/task-autopilot/references/graphify.md`.

### Private settings

Anything employer- or machine-specific stays out of git, in `~/.my-agent-kit/`:

| File | Holds |
| --- | --- |
| `identity.json` | extra git identity rules (added to `config/identity.json`) |
| `automation.json` | job `roots`, per-folder overrides such as `"auto_tests": false` |
| `CLAUDE.private.md` | extra global rules, appended to the managed block by `kit install` |

### The verify gate

When Claude tries to finish, `hooks/verify_gate.py` checks every project it
edited code in during the session:

| Situation | What the gate does |
| --- | --- |
| A safe test runner is detected (pytest, unittest, jest, vitest, mocha, react-scripts, go, cargo) or set in `.kit.json` | Runs it. Failure blocks the stop and gives Claude the output. |
| No safe runner detected, or `"auto_tests": false` | Blocks until Claude has run tests itself after its last code edit. |
| 3+ code files changed and no doc under `docs/` (or README/CHANGELOG) | Blocks until the change document exists. |
| Only docs edited | Nothing to check. |
| Tests time out (default 300 s) or the runner cannot start | Reports it, does not block. |
| Still failing after 2 blocks | Lets Claude stop and shows "Verify gate gave up ... check before trusting this result". |

Tests are not rerun if the code has not changed since they last passed.

Why `auto_tests` exists: some test suites read `.env` files that point at real
databases or services. Set `"auto_tests": false` for such folders (privately, in
`~/.my-agent-kit/automation.json`); Claude then runs only tests it knows are
safe (mocked) and the gate checks that it did.

Custom `npm test` scripts such as `node test-webhook.js` are never run
automatically for the same reason.

### Auto-format

After each edit, `hooks/post_edit.py` formats the file only if the project
already uses that formatter (ruff/black config in `pyproject.toml`/`ruff.toml`,
or prettier config plus `node_modules/.bin/prettier`). Repos without a
formatter config are never reformatted, so work diffs stay clean.

### Per-project settings

Create `.kit.json` in a project root (or add an entry under `projects` in
`config/automation.json`, keyed by path prefix, if you do not want a file in
the repo):

```json
{
  "test": "venv\\Scripts\\python -m pytest tests/unit -q",
  "auto_tests": true,
  "test_timeout": 600,
  "docs_min_files": 5,
  "format": false,
  "gate": true,
  "max_gate_blocks": 2
}
```

Turn the gate off for one session: start Claude with `KIT_GATE=0` set.

## 2. Scheduled jobs

```powershell
kit schedule install      # create the three Windows scheduled tasks
kit schedule list         # show next run times
kit schedule remove       # delete them
kit jobs morning-brief --open   # run any job now
kit brief                 # print the latest morning brief
```

- Tasks live in Task Scheduler under `\my-agent-kit\`, run as you, without
  admin rights, and catch up after the PC was off or asleep at the scheduled
  time (`StartWhenAvailable`).
- Jobs only read: `git fetch`, `git status`, `npm audit`, `pip-audit`. They
  never edit, commit, upgrade, or push, and they use no Claude usage.
- Repos are discovered under `jobs.roots` in `config/automation.json`
  plus `~/.my-agent-kit/automation.json` (put your folders there), skipping `exclude`.
- Change times in `config/automation.json` -> `jobs`, then run
  `kit schedule install` again.
- Logs: `~/.my-agent-kit/jobs.log`. Reports: `~/.my-agent-kit/reports/`.

To act on a report, ask Claude, for example: "fix the critical npm audit
issues in <repo>" - the autopilot and gate then apply as usual.
