# Where the code departs from the design

`docs/architecture.html` is the design. This file records every place the
implementation does something else, and why. Each entry is a decision you can
reverse; none of them is an accident.

The design's own closing line asks for exactly this: *"revise as M0–M3
contradict it."*

---

## 1. The cluster is this host, and the scheduler is `gpuq`

**Design:** a Mac, and a remote Slurm cluster reached over SSH — `rsync` a
pinned commit, `sbatch`, poll with `squeue`, settle with `sacct`.

**Reality on wsserver1:** there is no Mac and no Slurm. This box *is* the
compute host. Its scheduler is `gpuq`, and `gpuq` has no daemon: `gpuq submit`
claims a GPU and runs the command **in the foreground**, supervising it from
that process.

**What the code does.** `galley/services/scheduler/base.py` states the four
verbs a scheduler must answer — submit, observe, tail, cancel — and
`scheduler/gpuq.py` is the only implementation. Submission:

- pins `HEAD` of the code mirror and `git archive`s that commit into
  `$scratch/<job>/code` (no SSH, no rsync — the copy is local);
- writes `run.sh` and starts it inside a **detached tmux session**. tmux rather
  than a background process because a background child dies with the shell that
  spawned it, and the job must outlive both the Claude session that asked for
  it and the Galley backend;
- returns immediately, exactly as the design requires.

Observation reads the queue's own files rather than scraping console output:
`/var/lib/gpu_queue/jobs.json` (queued), `running.json` (running), and the tail
of `usage.jsonl` (the settled record). Every job is correlated by its `--name`
tag, which appears in all three.

**Not implemented:** an SSH + Slurm backend. Nothing on this machine could
exercise it, so it would ship unproven. The interface is there when a real
cluster is.

## 2. `gpuq` has no CPU-only lane, and only one of your jobs may queue

Two constraints the design could not have known:

- `gpuq` refuses `-g 0` ("`-g/--gpus` must be at least 1"). Galley rejects a
  zero-GPU request up front rather than laying down a run directory for a job
  the scheduler will decline.
- `gpuq` admits on a VRAM sample taken **at submit time**, so two of your own
  queued waiters are both admitted against the same freed card. Galley refuses
  a second submission while one of yours is queued (`limits.max_queued_jobs`,
  default 1) and says why.

## 3. Not every environment is atomic — or the whole paper is one segment

**Design:** "must pass `%` comments and `\begin/\end` blocks through as atomic
units."

Taken literally this is fatal: every paper is wrapped in `\begin{document}`, so
the entire manuscript becomes one unreviewable blob. The first end-to-end test
caught it.

**What the code does.** `ATOMIC_ENVIRONMENTS` in `segment/tokenizer.py` names
the environments whose contents are *not* prose — math, `verbatim`,
`lstlisting`, `tabular`, `tikzpicture` and friends. Everything else is
transparent: its `\begin` and `\end` become their own segments and the prose
between them is split normally. An atomic environment nested inside a
transparent one still comes through whole, so a `tikzpicture` inside a `figure`
is one unit while the `\caption` beside it is reviewable prose.

On the real paper this is the difference between 1,652 and 5,404 reviewable
sentences across 258 `.tex` files.

`\item` also starts a new unit, so a list is reviewed a point at a time.

## 4. The merge pane is built from the backend's ops, not `unifiedMergeView`

**Design:** "@codemirror/merge's `unifiedMergeView` gives per-chunk
accept/reject controls out of the box; feed it the sentence-segmented text."

**What the code does.** The backend returns a flat list of *ops* covering the
whole file: `equal` runs, and `change` runs carrying old text and new text side
by side. The resulting buffer is precisely

```
"".join(op.new if (op.type == "equal" or op.id in accepted) else op.old for op in ops)
```

so reject-everything is byte-for-byte the file you had and accept-everything is
byte-for-byte what Claude proposed. Both are asserted in `tests/test_diff.py`
against real paper sections, and again through the API in `tests/test_api.py`.

CodeMirror computes its own chunks from two documents, which would have made the
UI a second authority on what changed. Deriving the pane from the ops keeps one
owner for that fact, and keeps the design's real requirement — *Galley never
applies a partial patch* — provable rather than hoped for. The CodeMirror
dependencies were removed rather than left unused.

## 5. Two extra tools on the MCP surface

The design lists six. There are eight, and the two additions are both arrows
the design draws in §07 but gives no tool for:

- `record_result` — metrics → `results/<job>.json`, carrying the code SHA. The
  missing first arrow in the numbers chain.
- `review_my_changes` — the agent's own diff against the main branch, so it can
  read its change before handing it over.

Nothing that publishes was added: no push, no commit, no Overleaf credential,
no cancelling a job the session did not create.

## 6. `/mcp` needed dispatching, not mounting

A Starlette mount at `/mcp` only matches `/mcp/…`, so mounting the MCP app
there served it at `/mcp/mcp` and let the bare `/mcp` fall through to the static
file mount as a 405. `McpDispatch` in `app.py` hands that one path to the MCP
app in front of the router. `tests/test_mcp.py` pins both halves: `/mcp`
answers and `/mcp/mcp` does not.

The MCP transport's DNS-rebinding protection is configured from the bind
address, so the tool surface answers only to the host and port it runs on.

## 7. Artifacts live apart from run directories

`/scratch/users/$USER` on this host is deleted 30 days after a file's last
*modification*, and the mount is `noatime` — reading a file does not keep it
alive. Run directories can live there because they are rebuildable from the
pinned SHA. Pulled artifacts cannot, so `paths.artifacts_dir` is a separate
setting and points at durable storage.

## 8. Worktrees are excluded locally, not through `.gitignore`

`.worktrees/` is written into `.git/info/exclude`, which is local to the clone
and never enters a commit. Putting it in `.gitignore` would have modified a
tracked file and pushed that change to Overleaf.

## 9. The compile report reads the settled log, not the console

latexmk runs LaTeX several times. The first pass has no `.aux` and reports
*every* citation as undefined — 96 of them on the real paper, all of which are
resolved by the last pass. Reading latexmk's console output therefore reports
phantoms; `compile_pdf` reads the final `.log` instead, and returns nothing when
the paper is clean.

## 10. The pre-commit hook will not overwrite yours

The design's twenty-line hook is installed at startup, but only if there is no
pre-commit hook already, or the existing one is a previous copy of Galley's.
Someone else's hook is left alone.

## 11. Port and paths

`8124` rather than `7878`, bound to loopback; reach it with
`ssh -L 8124:localhost:8124 wsserver1`. The paper repo is the `FLM` checkout
itself, whose local branch is `master` and whose `origin` *is* the Overleaf git
bridge — so `main_branch`, `overleaf_remote` and `overleaf_branch` are all
configurable rather than hard-coded to `main`/`overleaf`/`master`.

---

## What has been exercised, and what has not

**Run against the real paper (258 `.tex` files, 13,153 segments):** the
tokenizer round-trips every file byte-for-byte; a one-sentence edit in a 54 KB
section produces exactly one reviewable change with word-level marks;
reject-all reproduces the file on disk; write-back lands the accepted buffer;
`latexmk` builds `main.pdf` with no errors and no undefined references; the MCP
handshake, tool list, and tool calls all answer on `/mcp`.

**Not yet exercised live:**

- **A real agent session.** `AgentService` spawns `claude-agent-sdk` with the
  billing keys stripped, but no session has been run — that spends subscription
  usage, so it is yours to start.
- **A real `gpuq` submission.** Every job would claim an H200, so the lifecycle
  is tested through a fake scheduler and the gpuq state parsing is tested
  against real record shapes in a temporary queue directory. The first genuine
  submission is the remaining unknown.
- **An Overleaf push.** `sync` refuses off-branch and on a dirty tree (both
  tested); the fetch/rebase/push itself has not been fired at the live bridge.
