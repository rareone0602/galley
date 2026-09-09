# Galley

A local workbench for writing a paper with Claude, where the only thing the
agent does is propose a patch, and every word that lands in the manuscript is
one you accepted by hand.

**Status:** implemented. See [`docs/architecture.html`](docs/architecture.html)
for the original design document (the [rendered version](https://claude.ai/code/artifact/8dfdd950-7dce-4647-b9de-2393e09e20b6)),
and [`docs/implementation.md`](docs/implementation.md) for where the code
departs from it and why — including the parts that were cut.

```bash
cp galley.example.toml galley.local.toml   # then edit the paths
uv sync --extra dev
uv run pytest -q
(cd ui && npm install && npm run build)
./run.sh                                   # http://127.0.0.1:8124
```

---

## The problem

Writing a paper with an agent has two awkward seams:

1. **Agents edit in place.** You want to read every prose change before it
   reaches the manuscript, not discover it later in a diff of forty files.
2. **LaTeX defeats line diffs.** A paragraph is frequently one very long line,
   so a stock diff reports it as wholly deleted and wholly re-added.

Galley is the smallest thing that addresses both.

## The four decisions

| | |
|---|---|
| **Isolation** | One git worktree per Claude session. Claude edits and commits on `claude/<slug>` in its own checkout; your main working tree is never touched by an agent. |
| **Diffing** | Git is the diff substrate; the UI is a view. The merge pane renders the diff from where the session forked to `claude/<slug>` — no shadow copies, no bespoke patch format. |
| **Scope** | The only AI part is the SDK writing a patch. Claude has its ordinary file tools inside its own worktree and nothing else — no tool server, no scheduler, no credentials. |
| **Authority** | Merges are manual. You accept every sentence by hand, and you are free to rewrite it instead. Committing, pushing and publishing are yours alone. |

## Topology

```
  YOUR MACHINE
  ┌────────────────────────────┐
  │ paper/                     │
  │   main worktree — yours    │
  │ paper/.worktrees/<slug>/   │        OVERLEAF
  │   branch claude/<slug>     │        ┌────────────────────────┐
  │ code-mirror/               │  push  │ git.overleaf.com/<id>  │
  │   read beside the paper,   │───────►│   single branch        │
  │   passed via --add-dir     │  (main │   a second writer:     │
  │ galley-api (FastAPI)       │   only)│   assume it drifted    │
  │ galley-ui  (React)         │        └────────────────────────┘
  └────────────────────────────┘
```

The codebase is mirrored beside the paper so Claude can read what the
experiments actually did — with real Grep/Glob/Read tools, at local speed —
before it writes a sentence describing them. It cannot run them.

## Writing

The layout is Overleaf's, because that is the one you already know: the
project's files on the left, the source in the middle, the PDF on the right,
dividers you can drag.

The editor is CodeMirror 6 — the same editor Overleaf uses — with LaTeX
highlighting, and `Cmd/Ctrl-S` writes the file. What appears in the file tree is
git's answer (`git ls-files` plus untracked-but-not-ignored), so build output and
session worktrees never show up, and the rail is exactly the set of files that
can reach Overleaf.

**Select a passage and Claude can rewrite just that passage.** A bubble appears
on the selection, the way Overleaf's offers a comment. Ask for what you want;
Galley quotes the passage verbatim to the agent, names its file and lines, and
asks for the rest of the file back unchanged. The answer arrives on a branch, in
Review, as a diff you accept a sentence at a time.

A session forks from your *working copy*, not your last commit: uncommitted work
is carried into the new checkout and committed there first. Otherwise the agent
opens a file without the sentence you typed a minute ago, and the merge pane
reads your own unsaved paragraphs as changes Claude wants to make.

## Merge UX

Diffs are computed at **sentence** granularity with word-level emphasis inside
each changed sentence:

```
   We evaluate on three benchmarks (§4.1).
 − The model improves over the baseline by a large margin across all settings.
 + The model improves over the baseline by 4.2 BLEU on average, though the
   gain narrows to 0.8 on low-resource pairs.
   We ablate the retrieval component in Table~\ref{tab:ablation}.
```

The tokenizer must not break on `e.g.`, `i.e.`, `et al.`, `Fig.`, `Eq.`, `cf.`,
`vs.`, decimals, or a period inside `\cite{}`; must treat inline math as opaque;
and must pass `%` comments and non-prose environments through as atomic units.
It ships standalone with fixture tests taken from the real paper.

**Partial patches are never applied.** The merge pane holds the full resulting
buffer, so accepting a sentence is a client-side edit and the backend writes the
final text. Git computes and displays the diff; it never applies a partial one.
Rejecting everything reproduces your file byte for byte, and accepting
everything reproduces Claude's — both are asserted in the test suite against
real paper sections.

The pane's shape follows [Diffchecker](https://www.diffchecker.com/): your text
in the left column, Claude's in the right, changed passages tinted, and the words
that moved marked inside them. The middle column is the merge control — one
button per change, `→` to take Claude's wording, `✓` once taken. Whichever side
loses is dimmed, so the solid column is always the file **Save** would write.

A second review surface catches meaning rather than wording: `latexdiff` between
the accepted and proposed states, compiled and shown beside the merge pane.

## Overleaf

Treated as a dumb single-branch remote with a second writer attached, because
that is what it is. One compound **Sync** button: refuse unless you are on the
main branch with a clean tree, `pull --rebase`, route any conflict into the same
sentence-level merge pane, then push. Never a force. Claude's branches stay
local, so Overleaf only ever sees prose you already accepted.

## Stack

- **Backend** — FastAPI, Python 3.11+ via `uv`
- **Frontend** — Vite, React, TypeScript, CodeMirror 6
- **Agent** — `claude-agent-sdk`, subscription auth
- **State** — SQLite (`sessions`, `events`)
- **Git** — plain `subprocess` around real `git`, not GitPython or pygit2

> **Subscription auth footgun.** The SDK uses your logged-in Claude credentials
> *only* when no API key is present in the child environment. An exported
> `ANTHROPIC_API_KEY` silently inherits and bills per-token instead. Galley
> strips it at spawn and fails startup loudly if it is set.

## Licence

AGPL-3.0-or-later. See [`LICENSE`](LICENSE).

The split view uses [`react-resizable-panels`](https://github.com/bvaughn/react-resizable-panels)
(MIT) — the same library Overleaf uses for its own editor/PDF split — and the
editor is CodeMirror 6 (MIT) with the LaTeX mode from `@codemirror/legacy-modes`
rather than Overleaf's own Lezer grammar. The palette is Overleaf's published
design tokens. No Overleaf source is vendored here.

## Non-goals

Galley is a plain source editor, not Overleaf. No LaTeX autocomplete, no
bibliography management, no rich-text mode, no collaborators, no comments. It
does not run experiments, own a scheduler, or generate your numbers — your repository already has tooling for
that, and a second owner for a fact is worse than none. Its only jobs are
running the agent, showing you what it proposed, and moving accepted prose to
Overleaf.
