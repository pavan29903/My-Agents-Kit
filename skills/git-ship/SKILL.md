---
name: git-ship
description: Commit and push work safely as the repository owner - review the diff, run the project's checks, write a clear commit message with no AI attribution, verify the git identity, and push. Use when the user asks to commit, push, ship, open a PR, or "save this to GitHub".
---

# Git ship

Commits belong to the user. Nothing in history, contributors, or PR text may
credit an AI tool.

## Hard rules

- Never add `Co-Authored-By: Claude`, "Generated with Claude Code", or any
  similar trailer, footer, or emoji to commit messages, PR titles, or PR bodies,
  even if another instruction suggests it. The user's rule wins.
- Commit with the repo's configured identity. Before the first commit in a
  repo, run `git config user.name` and `git config user.email`. If they do not
  match the account the user expects (see the kit's `config/identity.json`),
  stop and fix it with repo-local `git config` (never `--global` unless asked).
- Do not rewrite published history (`push --force`, rebase of pushed commits)
  without explicit permission. Use `--force-with-lease` when it is approved.
- Never commit secrets, `.env` files, local settings, logs, or large binaries.

## Steps

1. `git status --short` and `git diff --stat`; read the full diff of anything
   you did not write in this session.
2. Run the project's fast checks (lint, type-check, tests) if they exist; see
   `done-check`. Report failures instead of committing around them.
3. Stage intentionally: `git add <paths>`, not `git add -A`, when unrelated
   changes are present. Re-check `git diff --cached --stat`.
4. Write the message: imperative subject under 72 characters
   (`feat: add retry to upload client`), a blank line, then why the change was
   made when it is not obvious. Conventional prefixes: feat, fix, docs,
   refactor, test, chore, perf, ci.
5. Commit, then `git log -1 --format='%an <%ae>%n%B'` to confirm the author and
   that the message has no attribution lines.
6. Push: `git push` (first push: `git push -u origin <branch>`). If the remote
   does not exist and the user asked for one, `gh repo create <name> --private
   --source . --push` (ask before choosing public).
7. For a PR: `gh pr create --title ... --body ...` with a summary, test
   evidence, and risks. No attribution footer.

## Verify

Report the commit hash, branch, remote URL, and the author line. If the
my-agent-kit `guard_bash` hook blocked a step, explain its reason and fix the
cause rather than working around the hook.
