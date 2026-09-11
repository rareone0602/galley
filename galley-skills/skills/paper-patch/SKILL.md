---
name: paper-patch
description: Read this before changing any prose in the manuscript. How to write a patch that survives a sentence-by-sentence review — what to touch, what to leave byte-identical, and how to hand the work over.
---

# Writing a patch someone will read line by line

Your change is not applied. It is *read*, one sentence at a time, in a merge
pane with your version on the left and theirs on the right, and answered one of
three ways: taken, rejected, or rewritten. Half of what you write will not
survive, and that is the design working.

Everything below follows from that one fact.

## Find before you read

Grep and Glob reach the whole paper and the codebase mounted beside it, at
local speed. Use them first. A file you read whole to locate one passage stays
in your context for every call after it; a Grep that names the line costs a
line. When the file is long, Read the window around that line — Read takes an
offset and a limit — rather than the file. And before you go looking at all,
read the project's own `CLAUDE.md`: it is short, and it says where things are
so that you do not have to find out.

## Change the passage you were asked about, and its neighbours only if you must

If a sentence you rewrote breaks the one after it — a dangling "This", a "both"
that is now three — fix that one too. Otherwise leave it. Every extra sentence
you touch is another thing for a human to read and decide about, and the change
they actually asked for gets buried in the noise.

## Everything else comes out byte-for-byte identical

This is the rule that matters most and is easiest to break by accident.

- **Do not reflow.** Keep the line discipline the file already uses, and change
  only the lines you meant to change. Where the source is written one sentence
  per line — check, it usually is, and the project's own instructions will say
  — a reworded sentence is one changed line, and a sentence you add gets a line
  of its own. Where it is wrapped at a column, leave the wrap where it is.
  Either way, rewrapping a paragraph turns a two-word fix into a
  whole-paragraph rewrite that a human then has to read line by line.
- **Do not tidy** whitespace, quotes, dashes, or spacing around `\cite` — not
  even where it is genuinely untidy — unless that is what you were asked to do.
- **Do not reorder** anything: sentences, items, fields in a `.bib` entry.
- **Do not touch the preamble**, `\usepackage`, or macro definitions unless the
  task is about them. They affect every page.

If you think something else needs fixing, say so in your reply. Do not fix it.

## Edit in place

Use Edit on the file itself. Never write a copy, a `.new`, or a patch file —
Galley diffs the worktree against where the session forked, so a copy shows up
as a whole new file and the actual change shows up nowhere.

## When you are done

Say what you changed and where, in plain prose, and name anything you were
unsure about. Do not paste the diff back; it is already on screen. Galley
commits the worktree for you when the turn ends, so there is no commit to make
and no branch to push.

## Three things that are never yours

- **Never merge.** A human decides what reaches the manuscript.
- **Never publish.** No pushing, no committing on the main branch, no remote.
- **Never invent a number.** See the `trace-a-number` skill.
