# Skills

Habits, written down once.

Everything in here is loaded by the agent your Galley sessions talk to. It is
**yours to edit** — that is the point of it. When you notice the agent doing
the same wrong thing twice, the fix belongs in a file here rather than in the
prompt you type each time.

## How it reaches the agent

This directory is a local Claude Code plugin: `.claude-plugin/plugin.json`
declares it, and each skill is a folder under `skills/` with a `SKILL.md`
inside. Galley passes the whole directory to the SDK, so nothing has to be
installed and nothing is written into your paper's repository.

`[paths] skills_dir` in `galley.local.toml` points somewhere else if you would
rather keep them elsewhere. `[agent] skills` chooses which are on: `"all"` (the
default, including anything of your own under `~/.claude/skills`), `"none"`, or
a list of names.

## Adding one

Make a folder, write a `SKILL.md`, restart Galley. The frontmatter needs two
things:

```markdown
---
name: check-the-figures
description: Read this before writing any number into the manuscript.
---
```

The `description` is the only part the agent sees until it decides the skill is
relevant, so write it as *when to reach for this*, not as a title. The body
loads only once it does.

## One constraint worth knowing

**The agent has no shell.** Galley denies Bash on purpose — `echo x > ../file`
is a write by another name, and a shell would undo the guard that keeps the
agent inside its own worktree. So a skill here cannot tell it to run a script.
Everything in these files has to be doable with Read, Grep and Glob, which is
more than it sounds: those tools search the whole codebase mounted beside the
paper, at local speed.

The other constraint is the one that costs money: **everything the agent reads
stays in its context for the rest of the turn**, and a follow-up turn pays for
all of it again. A skill that sends the agent to read a file whole is a skill
that makes every later call dearer. Tell it what to Grep for and which lines to
read, not which file to open.
