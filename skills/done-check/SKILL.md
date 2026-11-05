---
name: done-check
description: Prove a change works before calling it done - detect and run the project's real build, lint, type-check, and test commands, exercise the changed behaviour, and report evidence. Use before saying a task is finished, before committing, or when the user asks "does it work?" or "is it ready?".
---

# Done check

"Done" means observed working, not "the code looks right". A check that was
not run is unknown, not passing.

## 1. Find the commands (do not guess)

Read, in order, whatever exists: `package.json` scripts, `pyproject.toml`
(`[tool.pytest]`, ruff, mypy), `Makefile`/`justfile`/`Taskfile.yml`,
`.github/workflows/*.yml` (the CI commands are the ground truth), `Cargo.toml`,
`go.mod`, `*.csproj`, README "Development" sections, and `CLAUDE.md`.

Typical mapping when the repo declares them:

| Stack | Lint / types | Tests |
| --- | --- | --- |
| Node | `npm run lint`, `npx tsc --noEmit` | `npm test` |
| Python (uv) | `uv run ruff check .`, `uv run mypy .` | `uv run pytest -q` |
| Go | `go vet ./...` | `go test ./...` |
| Rust | `cargo clippy` | `cargo test` |
| .NET | `dotnet build` | `dotnet test` |

## 2. Run narrow, then wide

1. The tests closest to the change (single file or `-k` filter) for fast feedback.
2. The full fast suite plus lint and type-check.
3. Exercise the actual behaviour: call the endpoint, run the CLI with real
   arguments, or load the page (`playwright-verification` for UI).
4. If you fixed a bug, show the reproduction failing before and passing after,
   or add a regression test that does.

## 3. Report

```
Checks:   lint ok | types ok | tests 142 passed, 0 failed (pytest -q)
Behaviour: POST /upload with 12 MB file -> 201, retry path exercised once
Not run:  e2e suite (needs staging DB)
Verdict:  ready | ready with warnings | not ready (+ the blocking item)
```

Quote real output for failures. Never edit a test only to make it pass unless
the test itself is wrong, and say so when you do.
