---
name: survey-before-writing
description: Read this when a task spans several files, sections or claims. When to send helpers out in parallel, what to ask each one for, and when working alone is faster.
---

# Look before you write, and look in parallel

You can start helpers. Use them when the reading is wide and the writing is
narrow — which is most of the work in a paper.

## The two kinds

- **`reader`** — reads and reports back. Writes nothing, starts nobody. Send
  one per question when several files have to be searched at once.
- **`helper`** — takes a piece of the work end to end and can start `reader`s
  of its own. Use one when a part of the task is genuinely separable.

There is nothing below a reader. The tree stops there on purpose: a deep one
spends a subscription window fast and is unreadable in the log afterwards.

## When it is worth it

Fan out when the questions are **independent** and the answers are **small**:

- "Does §4 already state this margin, and in what form?" across six sections.
- "Which files mention the ablation, and what do they claim about it?"
- Checking four claims in a paragraph against four different sources.

Do it yourself when the work is **serial** — when what you read second depends
on what you found first — or when there is one file and you already know which.
Two round trips to a helper cost more than one Grep — and every helper starts
with the same fixed prefix you did, the system prompt, the tools and the
project's instructions, some fifteen thousand tokens before it has read a word.
Three readers to answer a question a Grep would have answered is three prefixes
for nothing.

## Ask for the answer, not the search

A bad task: *"look at the experiments section"*. A good one names the question,
the place, and the shape of the answer:

> In `sections/experiments.tex`, find every sentence that states a number about
> the ablation. Quote each one with its line number, and say which of them cite
> a source. Do not change anything.

A helper cannot see your conversation. Everything it needs must be in the task
you give it, and everything you learn is only what it says back.

## One writer

When it is time to write, write it yourself, or give exactly one helper each
file. Two agents editing the same file in the same turn is how a patch becomes
unreviewable — and the whole point here is that a human reads every sentence.
