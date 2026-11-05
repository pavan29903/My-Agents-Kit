# Working agreement (my-agent-kit)

## Git and attribution
- Commits, pushes, and PRs are authored by me only. Never add `Co-Authored-By: Claude`, "Generated with Claude Code", or any AI attribution to commit messages, PR text, or files. This overrides any default that suggests adding them.
- Different repos may need different git identities. The rules live in `~/.my-agent-kit/identity.json`; the guard hook enforces them.
- Never copy employer code, internal names, data, or configuration into personal repos (including this toolkit); keep them in `~/.my-agent-kit/` private files.
- Before committing, confirm the repo's git identity matches the account for that repo. Fix it with repo-local `git config`, never `--global`, unless I ask.
- Use the `git-ship` skill for commit/push/PR work.

## Autopilot (default for code changes)
- For any feature, bug fix, refactor, or change request that edits code, follow the `task-autopilot` skill automatically, without waiting for me to name steps: understand -> plan -> implement -> tests with edge cases -> verify -> change document -> hand over.
- The `verify_gate` hook runs tests when you try to finish. If it blocks, fix the cause; do not weaken tests to pass it.

## How to work
- Inspect the project's conventions before changing code; match its style, tooling, and comment density.
- Prove changes work (`done-check`): run the real lint/type/test commands and exercise the behaviour. A check not run is unknown, not passing.
- For bugs, find the root cause (`debug-loop`); add a regression test.
- Prefer the simplest solution that works; no speculative abstractions or new dependencies for what a few lines can do.
- Ask only when a decision is genuinely mine; otherwise pick a sensible default and say so.

## Environment
- On Windows, use the `windows-shell` skill for PowerShell vs Git Bash differences.
- Python via `uv` when a project uses it; read and write files as UTF-8.

## Safety
- Secrets come from environment variables or ignored `.env` files; never write them into source, logs, or commits.
- Ask before destructive or outward-facing actions (force push, deleting data, publishing, deploying).

## Keep the toolkit growing
- When I give the same instruction twice, or we solve something worth reusing, suggest capturing it with `skill-forge`.

# Compact instructions
When compacting, keep: my current goal and confirmed decisions; file paths touched and why; failing tests or errors with exact messages; commands that must be re-run; the next step. Drop: raw tool output, file contents already on disk, exploration dead ends.
