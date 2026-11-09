# my-agent-kit

A toolkit for [Claude Code](https://claude.com/claude-code): skills, safety
hooks, global rules, and a small CLI that installs them in one step and keeps
skills copied from other repos up to date **without losing your own edits**.

It is for developers who use Claude Code every day and want it to:

- take a coding task from one message to tested, documented code;
- refuse to say "done" while tests fail;
- never commit with the wrong git account or with AI attribution;
- ask before writing secrets or running destructive commands;
- grow with your own skills, while still pulling improvements from upstream.

Windows-first (PowerShell / Git Bash), also built for macOS and Linux.
Standard-library Python 3.9+, no dependencies.

## Requirements

- Claude Code
- Git
- Python 3.9 or newer on `PATH` (`python` or `python3`)

## Quick start

Windows (PowerShell):

```powershell
git clone https://github.com/pavan29903/My-Agents-Kit my-agent-kit
cd my-agent-kit
.\kit doctor          # check tools, identity, install state
.\kit install         # link skills, merge hooks, add global rules
```

macOS / Linux:

```bash
git clone https://github.com/pavan29903/My-Agents-Kit my-agent-kit
cd my-agent-kit
./kit doctor
./kit install
```

Restart Claude Code after installing. Install once per machine, not per repo:
the kit works in every project. `kit audit` shows drift without changing
anything; `kit uninstall` removes everything the kit added.

What `install` touches, and how it stays safe:

| Part | Where | Safety |
| --- | --- | --- |
| Skills | `~/.claude/skills/<name>` | Links (directory junctions on Windows, no admin needed) pointing back here, so edits are live. Existing folders are moved to `~/.my-agent-kit/backups/` first. |
| Hooks | `~/.claude/settings.json` | Only entries pointing at this kit are added or replaced; your other settings and hooks are untouched. Backed up before every write. |
| Global rules | `~/.claude/CLAUDE.md` | Written inside a marked block; your own text outside it is kept. |

## Set up your git accounts (optional)

If you use more than one git account (for example work and personal), tell
the kit which one belongs where. Create `~/.my-agent-kit/identity.json`
(private, never committed):

```json
{
  "rules": [
    {"description": "personal", "remote_pattern": "github\\.com[:/]my-user/",
     "name": "my-user", "email": "me@example.com"},
    {"description": "work", "path_prefix": "D:/work",
     "name": "my-work-user", "email": "me@company.example"}
  ]
}
```

Commits and pushes in a matching repo are then blocked when the repo's git
identity is different, and the hook prints the exact fix. Other private files
in `~/.my-agent-kit/`: `automation.json` (per-folder settings, job folders)
and `CLAUDE.private.md` (extra rules added to your global CLAUDE.md).

## Automation (start here)

Describe a task in one message. Claude plans, implements, writes tests with
edge cases, verifies, and writes a change document by itself (`task-autopilot`),
and the `verify_gate` hook will not let it finish while tests fail or the
document is missing.

Optional report-only jobs check all your repos without using Claude: a
morning brief (what to pull, what is uncommitted), a dependency audit, and an
upstream skill check. Run them when you want:

```powershell
.\kit jobs morning-brief --open
.\kit jobs nightly-audit --open
.\kit brief                      # show the latest morning brief again
```

or schedule them (`kit schedule install`; Windows Task Scheduler, cron lines
elsewhere). Full guide: [docs/automation.md](docs/automation.md).

## What's inside

### Hooks (run automatically, every session)

| Hook | Event | What it does |
| --- | --- | --- |
| `guard_bash` | before Bash/PowerShell | **Blocks** commits, pushes, and PRs containing AI attribution (`Co-Authored-By: Claude`, "Generated with Claude Code"). **Blocks** commits/pushes when the repo's git identity does not match your rules, and prints the fix. **Asks** before force push, `reset --hard`, `clean -f`, deleting a drive root or home, disk formatting. |
| `secret_guard` | before Write/Edit | Asks before writing something that looks like a real key, token, private key, or password-bearing connection string. Placeholders and `${ENV}` references pass. |
| `session_start` | session start | Injects a 5-line repo brief: branch, ahead/behind, uncommitted files, last commit, checkpoint present, identity warning. After compaction it restores the saved checkpoint. |
| `context_watch` | each prompt, pre-compact | Warns once at 200k and 400k context tokens (configurable with `KIT_CTX_WARN_TOKENS`, `KIT_CTX_CRITICAL_TOKENS`). Saves a checkpoint before compaction. |
| `post_edit` | after Write/Edit, Bash | Records what changed per project; formats with the project's own formatter (never in repos without one); notes when Claude ran tests. |
| `verify_gate` | stop | Runs safe tests (or requires Claude to run them) and requires a change doc for 3+ changed files before Claude may finish. See [docs/automation.md](docs/automation.md). |
| `notify` | stop, notification | Desktop notification when Claude needs input or finishes a turn longer than 45 s (`KIT_NOTIFY_MIN_SECONDS`, `KIT_NOTIFY=0` to disable). |

All hooks fail open: a hook error never blocks your work; it is logged to
`~/.my-agent-kit/state/hook-errors.log` and reported by `kit doctor`.

Hooks always run; skills are chosen by the model. That is why the rules that
must never be skipped (attribution, identity, secrets, tests) are hooks.

### Skills

Written for this kit:

| Skill | Use it for |
| --- | --- |
| `task-autopilot` | Default for code changes: understand, plan, implement, edge-case tests, verify, change doc, hand over. |
| `git-ship` | Commit/push/PR as you: diff review, checks, clean message, identity check, no AI attribution. |
| `done-check` | Finds the project's real lint/type/test commands, runs them, exercises the change, reports evidence. |
| `debug-loop` | Reproduce, shrink, hypothesise, instrument, root-cause fix, regression test, with stop rules. |
| `skill-forge` | Turn a repeated workflow into a new skill, validate, install, commit. The kit grows itself. |
| `kit-maintain` | Upkeep: pull upstream skill changes, merge, adopt new ones, push. |
| `windows-shell` | PowerShell 5.1 vs Git Bash syntax, encodings, ports, junctions, long paths. |

Copied from [prudhvi1709/agents-toolkit](https://github.com/prudhvi1709/agents-toolkit)
and tracked for updates: `security-audit`, `decision-lens`, `delivery-review`,
`deployment-preflight`, `project-checkpoint`, `requirement-reconciliation`,
`agent-cli-design`, `investigative-analysis`, `ponytail`,
`playwright-verification`, `transcript-to-todo`, `session-preflight` (adapted
for Windows and identity checks).

Use a skill by name (`/debug-loop`) or just describe the task; Claude picks the
skill that fits.

## Keeping copied skills up to date

`config/upstreams.json` lists source repos; `config/upstream.lock.json` pins
each copied skill to an upstream commit with file hashes. That gives the kit a
three-way view (pinned base, upstream now, yours now):

```powershell
.\kit upstream status --fetch    # ok / UPDATE / MERGE / GONE per skill + new upstream skills
.\kit upstream diff security-audit
.\kit upstream update            # apply all safe updates; three-way merge where you edited too
.\kit upstream add agents-toolkit architecture-diagrams
.\kit upstream accept <skill>    # after resolving a conflict by hand
.\kit upstream forget <skill>    # stop tracking; the skill becomes fully yours
.\kit update                     # git pull + install + upstream status, in one go
```

- **UPDATE**: upstream changed, you did not: applied directly.
- **MERGE**: both changed: merged line by line with `git merge-file`. Clean
  merges apply; real conflicts go to `.upstream/review/<skill>/` and your files
  stay untouched until you accept.
- Upstreams are fetched as bare repos, so nothing is checked out and Windows'
  260-character path limit is never hit.
- Line-ending differences (CRLF/LF) never count as edits.

Add another source repo by adding an entry to `config/upstreams.json`.

## Making new skills

```powershell
.\kit new-skill api-scaffold -d "Scaffold a FastAPI service the way I like it. Use when starting a new API."
.\kit validate api-scaffold
.\kit install --only skills
```

Or just ask Claude: "make this a skill" (uses `skill-forge`).

## Make it yours

Fork it. Keep anything specific to you, your employer, or one project out of
the repo: put it in `~/.my-agent-kit/` (identity rules, job folders, extra
rules) or in that project's own `.claude/skills/`. The kit stays universal and
safe to share.

## Development

```powershell
python -m unittest discover -s tests -v
.\kit validate
```

Issues and pull requests are welcome.

## Layout

```
config/      hooks.json (hook manifest), identity.json, automation.json, upstreams.json, upstream.lock.json
docs/        automation guide and change documents
global/      CLAUDE.md merged into ~/.claude/CLAUDE.md
hooks/       hook scripts (stdin JSON -> stdout JSON)
scripts/     kit.py CLI, jobs.py report jobs
skills/      one folder per skill (SKILL.md + optional scripts/, references/)
tests/       unit tests
kit, kit.cmd, kit.ps1   wrappers
```

## Credits and license

MIT. Built on ideas and skills from
[prudhvi1709/agents-toolkit](https://github.com/prudhvi1709/agents-toolkit);
copied skills keep their original MIT notices, see `THIRD_PARTY_NOTICES.md`.
