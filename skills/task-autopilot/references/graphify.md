# Using a graphify code graph in the flow

graphify turns a codebase into `graphify-out/graph.json` (nodes = files,
functions, classes, endpoints; edges = calls, imports, references). A graph
built over a folder of several repos also shows cross-repo links
(UI -> API -> worker). The session brief prints the nearest graph's path and age.

## Prefer the MCP tools

If tools named `mcp__graphify__*` are available, use them: they answer from the
graph without shell calls.

## CLI fallback

If Windows Application Control blocks `graphify.exe`, call it through the
Python of its uv tool environment:

```powershell
$g = "$env:APPDATA\uv\tools\graphifyy\Scripts\python.exe"
$graph = "<root>/graphify-out/graph.json"
& $g -m graphify query "how does password reset work" --graph $graph --budget 1500
& $g -m graphify explain "<function or class>" --graph $graph
& $g -m graphify affected "<function, class, or file>" --depth 2 --graph $graph
& $g -m graphify path "<ui component>" "<api file>" --graph $graph
& $g -m graphify god-nodes --top 10 --graph $graph
```

Exit codes: 0 answer, 3 ambiguous name (candidates listed - pick one and
rerun), 4 no match (rephrase or fall back to grep). Raise `--budget` when the
output says it was truncated.

## How to use it per phase

| Phase | Command | Why |
| --- | --- | --- |
| Understand | `query "<task in plain words>"` | find the files and functions involved |
| Understand | `explain "<symbol>"` | what a hub does and who it talks to |
| Plan | `affected "<symbol>"` | blast radius before changing shared code |
| Plan | `path "<A>" "<B>"` | how a UI action reaches the backend |
| Hand over | `update <graph root>` | refresh the graph after code changes (no LLM) |

## Rules

- The graph is a map, not the territory: confirm every finding in the source
  before editing. INFERRED edges can be wrong.
- If the graph is more than about a week old or a query misses code you know
  exists, run `update` first.
- Never run a full rebuild with LLM extraction (`/graphify <path>` without
  `--update`) unless the user asks: it is slow and can cost API usage.
