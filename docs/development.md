# Working on Galley

Everything you need to change Galley and know you have not broken it. If you
are here to *use* Galley, the `README.md` is shorter.

---

## Four commands

```bash
./run.sh        # Galley, for real. One port, the built UI, no reloading.
./dev.sh        # Galley, while you are changing it. Two servers, hot reload.
./check.sh      # Everything that can be checked without a browser.
./probe/run.sh  # And the part that needs one: a real browser, driven.
```

**`./run.sh`** starts the backend on the port in your config (8124 by default).
The backend serves `ui/dist`, so the UI is whatever you last built. This is the
one to run from tmux and leave running.

**`./dev.sh`** starts two servers instead. Vite serves the UI from source on
`http://127.0.0.1:5173` and forwards every `/api` request to the backend, which
restarts by itself when a Python file changes. Edit a component and the running
page swaps it in — no build, no reload, and the file you had open stays open.
**Open 5173, not 8124**, or you are looking at the old build. Ctrl-C stops both.

**`./check.sh`** runs the backend tests, then TypeScript, then a production
build, in the order that fails fastest. Run it before you commit. What it
cannot tell you is whether anything *works*.

**`./probe/run.sh`** is that part. It builds the UI, makes a throwaway project
under `$TMPDIR`, serves it on 8127, and drives a headless Chromium through it,
printing one line per thing you should be able to see. Run it after any change
to `ui/`. It never opens the real paper. See "Verifying UI work" below.

First time on a machine:

```bash
cp galley.example.toml galley.local.toml    # then edit [paths] paper_repo
uv sync --extra dev
(cd ui && npm install)
```

If `run.sh` starts building a virtualenv inside `$HOME` on a machine where that
is wrong, you are missing `run.env` — copy `run.env.example` and fill it in. It
is gitignored, and it is the only file that knows anything about your box.

---

## Where things live

```
galley/
  __main__.py      the `galley` command: serve, --reload, `galley usage`
  app.py           composes the app; mounts ui/dist last so it cannot shadow /api
  config.py        galley.local.toml -> a frozen Config
  db.py            SQLite: sessions, events, usage
  bus.py           in-process pub/sub, so one agent reaches every open tab
  routes/          one module per area of the API + deps.py, the shared object
  services/        the work itself: agent, git, worktree, latex, synctex, usage…
  segment/         sentence-granular LaTeX diffing
ui/src/
  App.tsx          the shell: panels, tabs, keyboard, what is open
  api/             one module per area, composed into one `api` object
  components/      one file per pane, with sub-folders where a pane grew parts
  editor/          languages and completion for CodeMirror
  styles/          one file per area, imported in cascade order by index.css
tests/             behavioural tests for the backend
probe/             the browser probe: fixture.py, drive.py, run.sh
```

---

## The seams

Each of these is a place where adding something is one or two lines and nothing
else has to change. They are the reason several people (or agents) can work on
Galley at once without colliding.

| To add… | Do this | And that is all |
|---|---|---|
| **an API area** | a module in `galley/routes/` with `register(app, deps)`, then import it and list it in `AREAS` | `galley/routes/__init__.py`, 2 lines |
| **a client for it** | a module in `ui/src/api/` exporting `somethingApi`, then import and spread it | `ui/src/api/index.ts`, 2 lines |
| **anything a route needs** | a method or field on `Deps` | `galley/routes/deps.py` |
| **a stylesheet** | a file in `ui/src/styles/`, then one `@import` in cascade order | `ui/src/styles/index.css`, 1 line |
| **a usage kind** | one `Kind(name, asks)` in `KINDS` | `galley/services/usage.py`. The vocabulary is closed on purpose: an unknown kind is refused and named back to the browser |
| **a language for the editor** | one entry in `BY_EXTENSION` or `BY_FILENAME` | `ui/src/editor/languages.ts` |
| **a preview for a kind of file** | a component in `ui/src/components/preview/`, one entry in `BY_EXTENSION`, one branch in `PreviewPane` | `ui/src/components/preview/kinds.ts` + `PreviewPane.tsx` |
| **a habit for the agent** | a folder with a `SKILL.md` under `galley-skills/skills/` | nothing else; restart Galley. See the README there |
| **a house style the agent must follow** | a `CLAUDE.md` in the paper repository | nothing in Galley. `setting_sources=["project"]` and a cwd inside the worktree mean the project's own file is read every turn, and Galley stays project-agnostic |

`Deps` is the object every route area receives — config, database, bus, agents,
the work table — plus the few questions more than one area asks (`require_session`,
`repo_for`, `read_working`, `note`). If two areas need the same helper, it goes
there rather than being imported sideways.

---

## Things that will cost you an afternoon

**The guard is a `PreToolUse` hook, and it has to be.** `can_use_tool` is the
SDK's replacement for the interactive permission prompt, so it is consulted
*only* for calls that would otherwise prompt — reads, a bare `echo`, and every
`Agent` spawn are approved by the CLI's own rules before it is asked. A helper
started a second helper that way with the rule forbidding it sitting there,
unconsulted. `decide()` in `galley/services/agent.py` owns the rule; the hook
and the callback both ask it. If you add a tool or a tier, change `decide`.

**Name the tools, or the model has no Grep.** Left unset, the CLI's default
tool set is some thirty tools with `Grep` and `Glob` *not* among them — they sit
behind a `ToolSearch` loader, which the guard refuses. One real session
searched the paper with `Read` alone that way. `TOOL_SURFACE` in
`galley/services/agent.py` is passed as `tools=` and is exactly what `decide`
allows; add a tool to one of the three sets and both the offer and the guard
change together. The cheap way to see what the model is actually offered is the
`init` system message's `tools` list.

**Restarting takes two Ctrl-C while a tab is open.** The chat pane holds an
SSE stream open, and uvicorn's graceful shutdown waits for it — "Waiting for
connections to close" — for as long as the browser keeps it. The second Ctrl-C
forces the quit; the text you typed after the first one was discarded with it,
so type `uv run galley` again.

**Never export `ANTHROPIC_API_KEY`.** The Agent SDK's child process inherits it
and bills the API per token instead of using your Claude subscription. Galley
strips it at spawn (`config.child_env`) and refuses to start if it finds one in
its own environment. `run.sh` unsets both it and `ANTHROPIC_AUTH_TOKEN` first.

**`applyOps` and `apply_ops` are twins.** `ui/src/api/diff.ts` and
`galley/segment/diff.py` must produce byte-identical output; `tests/test_diff.py`
pins them against each other. Change one, change the other. Galley never applies
a partial patch — the client assembles the whole buffer and writes it back — so
a divergence here silently corrupts a file rather than failing.

**The file's bytes decide whether it is text, not its extension.**
`kind_of()` in `galley/services/files.py` is a guess for the rail's icon;
`read()` is the answer, and it sniffs. Two files come back readable but *not*
writable, and `editable` is the field that says so: one whose bytes are not
UTF-8, because saving would put U+FFFD over the real ones, and one past
`MAX_TEXT_BYTES`, because Galley only read the front of it. Never decide
editability from `content !== null`.

**Galley's forms set `input { width: 100% }`.** Every form in the app wants
that; a checkbox does not, and the rule turns one into a full-width band with
the label pushed onto the next line. The one checkbox in Galley — a Markdown
task item — undoes it locally in `preview.css`. Types were clean, the DOM was
right, and only the screenshot showed it.

**PDF.js is pinned to 4.10.38.** Version 6 calls `URL.parse`, which needs
Chrome 126. Do not bump it without checking the browser you actually verify in.

**The `synctex` binary is not installed here.** `galley/services/synctex.py`
parses the `.synctex.gz` itself. Units: 65536 sp = 1 TeX point of 1/72.27 inch,
while a PDF big point is 1/72 inch — the conversion is the first thing to
suspect when a click lands on the wrong line.

**A session forks from your working copy, not from `master`.** The paper
normally has a dozen uncommitted files; forking from the last commit would hide
your recent sentences from the agent and make the merge pane read your own
unsaved paragraphs as changes Claude wants. `base_sha` is the commit
`worktree.seed_working_copy` made, and every diff is taken from it — never from
the branch name.

---

## Verifying UI work

There is no UI test runner, and adding one has been postponed deliberately: the
backend suite is thorough, and the person using Galley is the better UI test.
So a green `./check.sh` means the types agree and the bundle builds. It does not
mean a feature works.

The cautionary tale: LaTeX `\cite{}` completion once shipped with clean types, a
green backend, a correct index of 118 entries — and produced no output at all,
because the completion source was rebuilt on every keystroke and CodeMirror
identifies sources by identity, so it restarted the query forever. Nothing but a
real browser could have caught it.

So `./probe/run.sh` drives a real browser over the Chrome DevTools Protocol.
Playwright's Chromium is at
`~/.cache/ms-playwright/chromium-1117/chrome-linux/chrome`; Playwright itself
needs Node 20 and this box has 18, so the probe talks to CDP directly over
`websockets`. Set `GALLEY_PROBE_CHROME` to use another browser.

Three things about it worth knowing before you add a check:

- **It starts from a fresh browser profile every run.** The bundle is
  content-hashed but `index.html` is not, so a kept cache serves the *last*
  build and the run quietly checks code that is no longer there. That cost an
  afternoon: the DOM was right and the screenshot was of the previous build.
- **Write the check as the sentence you would say to someone.** "a task sits on
  one line with its box". When it fails, that sentence is the bug report.
- **Look at the screenshots.** They land in `$TMPDIR/galley-probe`. Three real
  defects here passed every assertion and were obvious in the picture — a check
  can only find what you thought to ask. The third was in the merge pane: every
  assertion about the in-place rewrite box passed, and the header behind it read
  `1 rewritten · 1 to go` when nothing had been typed. Clicking into a sentence
  had become an answer.

**The review pane is reachable without paying for a turn.** `POST /api/sessions`
with `start: false` builds the worktree and stops; committing in that worktree
is exactly what the end of a turn does, so `/api/diff` cannot tell the
difference. `seed_review()` in `probe/drive.py` does it, and the sentences it
rewrites live beside the fixture's own paper in `probe/fixture.py`, so the two
cannot drift apart. The same rule shapes the compile checks: the probe breaks
the fixture paper, compiles it, and reads the prompt behind **Ask Claude to fix**
out of `GET /api/compile` — everything except the press, which would start a
real turn on a real model. This matters more than it sounds: the merge pane is where
the time actually goes, and until this it was the one surface no check could
open.

---

## Before proposing UX work

Run `galley usage` first. It is a local record of how Galley is actually used —
which surfaces, whether the ask-and-merge loop finishes, what gets decided in
review, where the waiting is — and it names the features that have never once
been used, which is the thing guesswork gets wrong. It holds no sentence of the
paper and never leaves the machine. `galley usage --forget` deletes it.

---

## House rules

- **One owner per fact.** A constant, a sentence shown to the user, a path
  mapping — one module owns it and everyone else asks.
- **Behavioural tests only.** A test runs the feature, or pins two live
  authorities against each other. No test greps source text.
- **Explicit paths when you commit.** Never `git add -A`; never force-push.
- **British in prose, American in identifiers.** `Tokenisation` in a comment,
  `tokenization` in a path.
- Comments say *why*. The code already says what.
