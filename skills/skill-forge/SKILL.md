---
name: skill-forge
description: Turn a repeated workflow, correction, or hard-won lesson into a new or improved skill in the user's my-agent-kit, then validate, install, and commit it. Use when the user says "make this a skill", "remember how to do this", "add this to my toolkit", or when the same instructions have been given twice.
---

# Skill forge

The kit gets more useful only when lessons are written down. This skill does
that in a few minutes, without bloating the kit.

The kit root is the folder two levels above this skill (`skills/<name>/` -> root; `kit doctor` prints it). Run its CLI as `python <kit root>/scripts/kit.py <command>`.

## Decide what to write

- **New skill**: a multi-step workflow with a clear trigger that will recur.
- **Edit an existing skill**: the lesson refines a workflow the kit already has.
  Search `skills/*/SKILL.md` first; prefer editing over adding.
- **Global rule** (`global/CLAUDE.md`): a one-line preference that applies to
  every task (style, tooling, safety). Keep that file short.
- **Hook**: something that must happen every time, deterministically, such as
  blocking a command. Instructions can be ignored; hooks cannot.
- **Nothing**: a one-off fact, or advice a capable model already follows.

## Write it

1. `kit new-skill <name> -d "<what it does>. Use when <trigger phrases>."`
   The description is how the agent decides to load the skill: name concrete
   triggers, keep it under about 300 characters.
2. Body: only what the model would not do by default - the exact commands,
   order, project conventions, gotchas, and the verification step. Keep it
   under 150 lines; move long reference material to `references/` and scripts
   to `scripts/` and mention each file from SKILL.md.
3. If a step is mechanical and repeatable, write a script instead of prose and
   test it.

## Ship it

```
kit validate <name>
kit install --only skills
```

Then commit with the `git-ship` rules (owner identity, no AI attribution):
`git -C <kit root> add skills/<name>` and commit
`feat(skill): add <name>` or `fix(skill): <what changed>`. Push only if the
user asked or has standing approval.

Editing a skill copied from upstream is fine: `kit upstream status` will show
it as customized and `kit upstream update` will three-way merge future upstream
changes with your edits.
