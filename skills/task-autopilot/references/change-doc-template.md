# <Title: what was done, in plain words>

Date: YYYY-MM-DD · Repo: <name> · Branch: <branch> · Status: done | done with follow-ups

## Summary
Two or three sentences: what changed and why it was needed.

## What changed
| File | Change |
| --- | --- |
| `path/to/file.py` | Added X; changed Y |

## How it works
The flow from entry point to result, step by step. Include a small diagram
(Mermaid or ASCII) when there are 3+ moving parts.

## How to use it
Exact commands, API requests with example payloads and responses, config or
env var names (never values), UI steps.

## Tests and edge cases
| Case | Expected | Result |
| --- | --- | --- |
| Happy path | 201 with id | pass |
| Empty input | 422 with message | pass |
| Not applicable | concurrency: no shared state | - |

Test command and summary output, e.g. `pytest -q` -> `48 passed in 3.2s`.

## Verification
What was exercised for real (endpoint call, page load, CLI run) and what was
observed. Checks not run, and why.

## Limitations and follow-ups
Known gaps, risks, and the next steps worth doing.
