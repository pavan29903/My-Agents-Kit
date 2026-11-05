# my-agent-kit

A universal, project-agnostic Claude Code toolkit.

- Commits carry no AI attribution; follow `skills/git-ship`.
- Keep every tracked file free of project-, employer-, and machine-specific details (names, paths, emails, internal code). Those belong in `~/.my-agent-kit/` private files.
- Python is standard library only and must run on 3.9 (no `match`, no `X | Y` types, no `newline=` on `Path.write_text`).
- Hooks must fail open and finish fast (well under their timeout); keep them free of network calls.
- After changing anything: `python -m unittest discover -s tests` and `python scripts/kit.py validate`.
- Skills copied from upstream may be edited freely; `kit upstream update` three-way merges future changes. Never hand-edit `config/upstream.lock.json`.
- `global/CLAUDE.md` is merged into `~/.claude/CLAUDE.md` by `kit install`; keep it short.
