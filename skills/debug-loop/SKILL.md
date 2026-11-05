---
name: debug-loop
description: A disciplined debugging loop - reproduce, shrink, hypothesise, instrument, fix the root cause, and lock it in with a regression test. Use when something is broken, flaky, slow, or behaves differently than expected, especially after a first quick fix did not work.
---

# Debug loop

Guessing burns time; each loop iteration must produce evidence.

## Loop

1. **Reproduce.** Get the exact error, command, input, and environment. Write
   the reproduction down as one command. If you cannot reproduce it, say so
   and gather more data (logs, versions, config) instead of patching blind.
2. **Shrink.** Cut the input, the code path, and the environment until the
   failure is as small as it gets. Bisect when a known-good state exists
   (`git bisect run <repro command>`).
3. **Hypothesise.** List two or three candidate causes ranked by likelihood
   and cheapness to test. Pick the cheapest discriminating test.
4. **Instrument.** Add targeted logging, assertions, or a debugger break. Read
   the actual values; do not infer them. Check assumptions about versions,
   env vars, working directory, encodings, time zones, and caching.
5. **Fix the root cause**, where all callers route through, not the symptom in
   one caller. Search for sibling callers with the same bug.
6. **Lock it in.** Add a regression test that fails without the fix. Remove
   temporary instrumentation.
7. **Verify** with the original reproduction and the wider test suite.

## Stop rules

- Two failed fixes in a row: stop patching, return to step 1 or 2, and state
  what the evidence now says.
- After about 30 minutes without progress, summarise facts, ruled-out
  hypotheses, and the next experiment, and ask the user whether to continue.

## Windows specifics worth checking early

CRLF vs LF line endings, path separators and the 260-character path limit,
PowerShell 5.1 vs 7 differences, `python` resolving to the Microsoft Store
stub, file locks held by another process (`openfiles` / Resource Monitor), and
antivirus scanning slowing builds.

## Report

Root cause in one sentence, the evidence that proves it, the fix, the
regression test, and anything still unexplained.
