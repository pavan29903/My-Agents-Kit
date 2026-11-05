---
name: task-autopilot
description: Run a coding task end to end from one message - understand the repo (code graph first), plan, implement the smallest correct change, write tests including edge cases, run every check, then write a change document explaining what was done and how it works. Use automatically for any feature, bug fix, refactor, or change request that edits code; skip for questions, explanations, and one-line edits.
---

# Task autopilot

The user describes the task once. Carry it through every phase without being
asked for each step, loading the skills below at the right moment. Stop and ask
only for decisions that are genuinely the user's (scope, product behaviour,
anything destructive or outward-facing).

The my-agent-kit `verify_gate` Stop hook enforces phases 4-6: you cannot finish
while tests fail or were not run after your last code edit, or without a change
document when 3+ code files changed. Treat its feedback as a failing check.

## Project skills come first

The session brief lists **project skills** (from `.claude/skills` in the repo or
any parent folder). When one covers a phase, use it instead of the generic kit
skill and follow it exactly - it encodes how that project really works.
Typical matches: a project's isolated-test or test-harness skill for phases
4-5, its ship/release skill for phase 7, its production-investigation skill for
any read of production data, and domain skills for the files they name.

## Skills in the flow

| Phase | Use | When |
| --- | --- | --- |
| 1 Understand | `graphify` code graph, then `session-preflight` | always (graph only if one exists) |
| 2 Plan | `decision-lens` | real trade-offs between approaches |
| 2 Plan | `requirement-reconciliation` | the user supplied specs, decks, or changed scope |
| 3 Implement | `ponytail` | always: smallest correct change |
| 3 Implement | `debug-loop` | the task is a bug, or a fix did not work first time |
| 3 Implement | `security-audit` (focused) | auth, permissions, payments, uploads, user input to queries/shell |
| 3 Implement | `windows-shell` | commands fail or scripts must run on Windows |
| 4-5 Test, verify | project test skill, else `done-check` | always |
| 5 Verify | `playwright-verification` | a page or UI changed |
| 6 Document | template below | 3+ files, or any behaviour change worth explaining |
| 7 Hand over | project ship skill, else `git-ship` | user asked to commit/push |
| 7 Hand over | `project-checkpoint` | work is unfinished or spans sessions |
| 7 Hand over | `delivery-review`, `skill-forge` | the task was painful, or the same explanation was needed twice |

## 1. Understand

If the brief shows a **code graph** (`graphify-out/graph.json`), query it before
opening files - it finds the relevant files, callers, and cross-repo links in one
step. Use the graphify MCP tools when connected, else the CLI; commands and the
Windows note are in [references/graphify.md](references/graphify.md). Then read
the real files it points to: the graph can be days old, the source is the truth.

Run the `session-preflight` checks that matter: repo state, conventions, how
tests run, where similar code lives. Trace the real flow end to end.

## 2. Plan

- Small, clear task: state the approach in 2-3 lines and continue.
- 3+ files, a schema/API change, or real ambiguity: write a short plan (files,
  approach, risks, test plan). Before changing a shared function, model, or API,
  check its blast radius (`graphify affected "<name>"`, or grep every caller).
  Continue unless the plan needs a product decision only the user can make.
- Restate "done when" as checkable acceptance criteria.

## 3. Implement

Smallest correct change in the project's style. Fix root causes where all
callers route through. No secrets in code; no unrelated refactors.

## 4. Test, including edge cases

Use the project's test skill or framework. If the project has neither, use
pytest when installed, otherwise stdlib `unittest`, and tell the user. Cover the
happy path plus every edge case that applies from
[references/edge-cases.md](references/edge-cases.md). A bug fix gets a
regression test that fails without the fix (run the old code to prove it when
possible). Tests never touch real databases, external services, production
data, or paid APIs: fake them at the boundary.

## 5. Verify

Run the narrow tests, then the wider suite, lint, and type-check. Exercise the
real behaviour once (endpoint, CLI, or page). Quote real output.

## 6. Document

Write `docs/changes/<YYYY-MM-DD>-<short-topic>.md` (or follow the repo's docs
convention) from
[references/change-doc-template.md](references/change-doc-template.md): what
changed and why, how it works, how to use it, the edge-case table with results,
verification evidence, limitations. Write for a teammate who was not here.

## 7. Hand over

Summarise: what changed, test results, doc path, anything left for the user.
If a code graph exists and code changed, refresh it (`graphify update <repo
root>`, no LLM cost) so the next task sees current code. Offer to commit with
the project ship skill or `git-ship`; do not push unless the user asked or has
standing approval for this repo.
