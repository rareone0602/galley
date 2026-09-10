---
name: trace-a-number
description: Read this before writing any figure, percentage, count or comparison into the manuscript. How to trace a number to its owner, and what to write when you cannot.
---

# Every number in a paper has an owner

Before a figure goes into prose, you must be able to say where it came from.
Not "it seems about right" — the file and the line.

This is not pedantry. A number that nobody can trace is a number that cannot be
defended when a reviewer asks, and it is the single most expensive kind of
mistake to find late.

## Where to look, in order

1. **The place the project says measurements live.** Most research repositories
   name one. Read the project's `CLAUDE.md` or `README.md` first and find out
   where; it is usually a `docs/methods/` or equivalent, and usually says
   plainly that numbers are restated nowhere else.
2. **Generated data files.** Look for a header saying `AUTO-GENERATED`, a
   `provenance` file, or a manifest of checksums beside the values. If a number
   is emitted by a script, cite the emitted file — do not copy the digits into
   the prose and do not recompute them yourself.
3. **The manuscript itself.** `Grep` for the figure. If it already appears
   somewhere, use it in the same form, with the same units and the same
   rounding, and check you are not now stating it twice in two places.

`Grep` and `Glob` reach the whole codebase mounted beside the paper. Use them.

## When the number is not there

Write the sentence without it and say so. For example: *"the term arm improves
over the surface arm"* rather than an invented margin, followed by a note in
your reply that the margin needs a source.

An unfinished sentence is a five-minute fix. A confident wrong figure can
survive to print.

## Things that quietly go wrong

- **Units travel with the channel.** A number measured on one arm, corpus or
  tokenizer usually means something different on another. Check what the source
  was measuring before you reuse it.
- **A rounded number is a different number.** Copy the precision the source
  used. Do not tidy `0.4982` into `0.5` to make a sentence read better.
- **A name is not a definition.** Two files can both say "accuracy" and mean
  different cuts of the data. Read what was actually computed.
- **"Not measured" is not "zero"** and not "it failed". If a cell has no
  reading, say it has no reading.
