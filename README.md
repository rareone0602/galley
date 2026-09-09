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
| **Diffing** | Git is the diff substrate; the UI is a view. The merge pane renders `git diff main...claude/<slug>` — no shadow copies, no bespoke patch format. |
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
- **Frontend** — Vite, React, TypeScript
- **Agent** — `claude-agent-sdk`, subscription auth
- **State** — SQLite (`sessions`, `events`)
- **Git** — plain `subprocess` around real `git`, not GitPython or pygit2

> **Subscription auth footgun.** The SDK uses your logged-in Claude credentials
> *only* when no API key is present in the child environment. An exported
> `ANTHROPIC_API_KEY` silently inherits and bills per-token instead. Galley
> strips it at spawn and fails startup loudly if it is set.

## Non-goals

Galley is not an editor. No LaTeX autocomplete, no bibliography management, no
syntax highlighting beyond the merge pane. It does not run experiments, own a
scheduler, or generate your numbers — your repository already has tooling for
that, and a second owner for a fact is worse than none. Its only jobs are
running the agent, showing you what it proposed, and moving accepted prose to
Overleaf.
