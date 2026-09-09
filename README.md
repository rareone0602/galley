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

The editor is CodeMirror 6 — the same editor Overleaf uses — and it picks its
highlighting from the file: LaTeX for the paper, and the right mode for the
scripts, configs and `.bib` beside it. A file it has no mode for stays plain
text and says so, because wrong colours are worse than none. `Cmd/Ctrl-S`
writes the file, and `Ctrl` with `+`, `-` or `0`
sizes the text (the editor's, not the whole page's). What appears in the file tree is
git's answer (`git ls-files` plus untracked-but-not-ignored), so build output and
session worktrees never show up, and the rail is exactly the set of files that
can reach Overleaf. The rail also does what Overleaf's does — new file, new
folder, rename, delete, drag a figure in — and refuses the things that would
quietly break the paper, `main.tex` and ignored paths among them.

**Nothing you type is lost by leaving.** Every file you have open keeps its own
buffer, cursor, scroll position and undo history, so switching files or tabs is
a move rather than a discard, and the browser warns before you close the window
on unsaved work. If something else writes a file while you have unsaved changes
in it — a merge, usually — your text stays and the bar offers to reload,
which is itself undoable.

**`\cite{`, `\ref{` and your own macros complete.** Read from the project's
own `.bib` and `.tex` — and from `.sty` and `.cls`, which is where a paper
usually keeps the names it invented. Each option carries what you would
otherwise open another file to check: the author and year beside a citation, the
section or caption beside a label, the argument count beside a macro.

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

It is a review tool rather than a long scroll: a keyboard for stepping and
deciding (with the keys shown in the pane, not buried in a docstring), a header
that reads `Change 3 of 17 · 5 taken · 2 rewritten`, ticks down the side showing
where the changes are and which are decided, take-all and keep-all per file with
an undo stack, and a **Save** that first says which files it would write and
what size each becomes.

**There are three answers per change, not two.** Double-click either side, or
press the pencil, and the row becomes a text box seeded from the side you were
reading; what you type wins over both. Claude's draft is a suggestion, and the
sentence that lands is the one you decided on.

A second review surface catches meaning rather than wording: `latexdiff` between
the accepted and proposed states, compiled and shown beside the merge pane.

## The PDF

Rendered by PDF.js, the renderer Overleaf uses, rather than handed to the
browser's viewer — because that buys one gesture: **double-click a word and the
editor goes to the line that wrote it.**

It goes the other way too: `Ctrl/Cmd+Alt+J`, or **Show in PDF**, takes the line
you are writing and marks where it printed. A line that printed nothing falls
forward to the next one that did, and says so.

Both work by reading the `.synctex.gz` TeX writes beside the PDF. TeX Live's
`synctex` command does the same job, but it is a separate package and is not
installed everywhere a paper compiles, so Galley parses the file itself. If a
double-click says there is no SyncTeX data, the PDF was built before this
existed — compile it again.

When a build fails, the errors are listed rather than dumped, each one clickable
through to the line that broke. LaTeX's log names a file for almost nothing, so
the file is inferred from the transcript and then checked against the project;
where it cannot be checked the message is kept and the location dropped, because
a wrong line number is worse than none.

## Publishing

Overleaf is the remote this was built against, and it is a dumb single-branch
remote with a second writer attached — one branch, force-push unreliable, and
you in the web editor moving it while you are not looking. So there is one
compound **Sync** button: refuse unless you are on the main branch with a clean
tree, `pull --rebase`, route any conflict into the same sentence-level merge
pane, then push. Never a force. Claude's branches stay local, so the remote
only ever sees prose you already accepted.

Any ordinary remote behaves the same way, so the config calls it
`publish_remote` / `publish_branch` rather than naming the mechanism after one
service. **A project with no remote is fine**: the panel says there is nowhere
to publish to, and the button carries the reason rather than failing when
pressed. The sentence beside the disabled button is the one pressing it would
have produced — the backend owns it, and a test pins the two against each
other.

## Using it for another project

A Galley is one git repository plus whatever that repository happens to have. A
companion codebase, a remote, a LaTeX root — name each in `galley.local.toml`
when the project has one, and Galley reports the absent ones as absent instead
of failing when you press the button. Only `paper_repo` is required.

Put a `galley.local.toml` beside the other project and start `galley` there:
it walks up from the working directory to find one, or takes `--config`. Give
the second one a different `[server] port` and `[paths] state_dir` so the two do
not fight over one database. Anything a particular machine needs — where the
virtualenv lives, where big temporary files go — belongs in `run.env` beside
`run.sh`; see `run.env.example`.

Without a LaTeX root the PDF, SyncTeX and `\cite`/`\ref` completion switch
themselves off and say why; the rail, the editor, Claude and the merge pane all
work regardless.

## Stack

- **Backend** — FastAPI, Python 3.11+ via `uv`
- **Frontend** — Vite, React, TypeScript, CodeMirror 6, PDF.js
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
editor is CodeMirror 6 (MIT) with modes from `@codemirror/legacy-modes` rather
than Overleaf's own Lezer grammar. The PDF is drawn by PDF.js
(Apache-2.0), pinned to the 4.x line because 6.x needs Chrome 126. The palette
is Overleaf's published design tokens. No Overleaf source is vendored here.

## Non-goals

Galley is a plain source editor, not Overleaf. No LaTeX autocomplete, no
bibliography management, no rich-text mode, no collaborators, no comments. It
does not run experiments, own a scheduler, or generate your numbers — your repository already has tooling for
that, and a second owner for a fact is worse than none. Its only jobs are
running the agent, showing you what it proposed, and moving accepted prose to
Overleaf.
