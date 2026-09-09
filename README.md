# Galley

A local workbench for writing a paper with Claude, where experiments run on a
cluster and every word that lands in the manuscript is one you accepted by hand.

**Status:** implemented, M0–M6. See [`docs/architecture.html`](docs/architecture.html)
for the design document (the [rendered version](https://claude.ai/code/artifact/8dfdd950-7dce-4647-b9de-2393e09e20b6)),
and [`docs/implementation.md`](docs/implementation.md) for where the code
departs from it and why.

```bash
cp galley.example.toml galley.local.toml   # then edit the paths
uv sync --extra dev
uv run pytest -q
(cd ui && npm install && npm run build)
./run.sh                                   # http://127.0.0.1:8124
```

---

## The problem

Writing a paper with an agent has three awkward seams:

1. **Agents edit in place.** You want to read every prose change before it
   reaches the manuscript, not discover it later in a diff of forty files.
2. **LaTeX defeats line diffs.** A paragraph is frequently one very long line,
   so a stock diff reports it as wholly deleted and wholly re-added.
3. **Cluster jobs outlast conversations.** A scheduler queue is measured in
   hours; an agent turn is measured in minutes.

Galley is the smallest thing that addresses all three.

## The five decisions

| | |
|---|---|
| **Isolation** | One git worktree per Claude session. Claude edits and commits on `claude/<slug>` in its own checkout; your main working tree is never touched by an agent. |
| **Diffing** | Git is the diff substrate; the UI is a view. The merge pane renders `git diff main...claude/<slug>` — no shadow copies, no bespoke patch format. |
| **Code** | Mirror the codebase locally, execute remotely. Claude reads and edits with real Grep/Glob/Edit tools at local speed; running it is a separate act on the cluster. |
| **Jobs** | Experiments are objects, not turns. A submitted job outlives the session that created it, and completion wakes a *new* session with results attached. |
| **Authority** | Prose merges are manual; numbers are generated. You accept every sentence by hand and never accept a number by hand. |

## Topology

```
  YOUR MAC                              CLUSTER
  ┌──────────────────────────┐          ┌────────────────────────┐
  │ paper/                   │          │ $SCRATCH/galley/<job>/ │
  │   main worktree — yours  │          │   checkout at pinned   │
  │ paper/.worktrees/<slug>/ │  ssh +   │   SHA                  │
  │   branch claude/<slug>   │◄─rsync──►│ sbatch · squeue · sacct│
  │ code-mirror/             │          │ artifacts/             │
  │   passed via --add-dir   │          │   metrics.json, figs   │
  │ galley-api (FastAPI)     │          └────────────────────────┘
  │ galley-ui  (React)       │
  └───────────┬──────────────┘          OVERLEAF
              │  git push (main only)   ┌────────────────────────┐
              └────────────────────────►│ git.overleaf.com/<id>  │
                                        │   single branch: master│
                                        └────────────────────────┘
```

No agent process runs on the cluster. The cluster is a job executor reached over
SSH; all reasoning about code happens against the local mirror.

## Async job lifecycle

```
DRAFT → SUBMITTED → PENDING → RUNNING → COMPLETED → HANDOFF
                    (squeue)            (sacct)     (new session,
                                                     metrics in prompt)
```

Submission returns immediately. One asyncio poller backs off by state — 30s
while `PENDING`, 5m while `RUNNING` — and writes every transition to SQLite
before publishing it, so a backend restart resumes cleanly. A failed job takes
the same path with the tail of stderr substituted for metrics, into a session
prompted to diagnose rather than to write.

Because submission rsyncs a pinned commit rather than your dirty working copy,
every artifact carries the exact SHA that produced it.

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
and must pass `%` comments and `\begin/\end` blocks through as atomic units.
It ships standalone with fixture tests before any UI is wired to it.

**Partial patches are never applied.** The merge pane holds the full resulting
buffer, so accepting hunks is a client-side edit and the backend writes the
final text. Git computes and displays the diff; it never applies a partial one.

A second review surface catches meaning rather than wording: `latexdiff` between
the accepted and proposed states, compiled and shown beside the merge pane.

## The numbers invariant

No numeral is ever typed into a `.tex` file by hand or by agent. The path is
one-way:

```
cluster run → artifacts/metrics.json    (pulled, immutable, carries git SHA)
            → results/<job>.json        (committed to the paper repo)
            → tables/<name>.tex         (generated; never hand-edited)
            → \input{tables/<name>}     (the only reference in main.tex)
```

Enforced by a pre-commit hook that rejects a diff touching `tables/` unless
`results/` changed in the same commit.

## Stack

- **Backend** — FastAPI, Python 3.11 via `uv` (the Agent SDK needs ≥3.10)
- **Frontend** — Vite, React, TypeScript, [`@codemirror/merge`](https://github.com/codemirror/merge)
- **Agent** — `claude-agent-sdk`, subscription auth
- **State** — SQLite (`sessions`, `jobs`, `events`)
- **Git** — plain `subprocess` around real `git`, not GitPython or pygit2

> **Subscription auth footgun.** The SDK uses your logged-in Claude credentials
> *only* when no API key is present in the child environment. An exported
> `ANTHROPIC_API_KEY` silently inherits and bills per-token instead. Strip it at
> spawn and fail startup loudly if it is set.

## Milestones

| | | |
|---|---|---|
| **M0** | Environment and git service | worktree create/list/remove, status, diff |
| **M1** | Sessions with a live log | spawn agent, SSE stream, tool-call rendering |
| **M2** | Sentence tokenizer, standalone | pure function, fixture-tested, no UI |
| **M3** | Merge pane | accept/reject per sentence, write-back |
| **M4** | Job system | MCP tools, SSH submit, poller, artifact pull |
| **M5** | Git panel and Overleaf sync | compound Sync, conflicts reuse the M3 pane |
| **M6** | PDF preview and latexdiff review | compile-on-save, marked-up comparison |

## Non-goals

Galley is not an editor. No LaTeX autocomplete, no bibliography management, no
syntax highlighting beyond the merge pane. Its only jobs are running the agent,
merging its output, and moving bits to the cluster and Overleaf.
