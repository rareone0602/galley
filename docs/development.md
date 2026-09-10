# Working on Galley

Everything you need to change Galley and know you have not broken it. If you
are here to *use* Galley, the `README.md` is shorter.

---

## Three commands

```bash
./run.sh      # Galley, for real. One port, the built UI, no reloading.
./dev.sh      # Galley, while you are changing it. Two servers, hot reload.
./check.sh    # Everything that can be checked without a browser.
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
cannot tell you is whether anything *works* — see "Verifying UI work" below.

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
tests/             behavioural tests for the backend. There are no UI tests.
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
| **a habit for the agent** | a folder with a `SKILL.md` under `galley-skills/skills/` | nothing else; restart Galley. See the README there |

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

**Never export `ANTHROPIC_API_KEY`.** The Agent SDK's child process inherits it
and bills the API per token instead of using your Claude subscription. Galley
strips it at spawn (`config.child_env`) and refuses to start if it finds one in
its own environment. `run.sh` unsets both it and `ANTHROPIC_AUTH_TOKEN` first.

**`applyOps` and `apply_ops` are twins.** `ui/src/api/diff.ts` and
`galley/segment/diff.py` must produce byte-identical output; `tests/test_diff.py`
pins them against each other. Change one, change the other. Galley never applies
a partial patch — the client assembles the whole buffer and writes it back — so
a divergence here silently corrupts a file rather than failing.

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

When something must be checked, drive a real browser over the Chrome DevTools
Protocol. Playwright's Chromium is at
`~/.cache/ms-playwright/chromium-1117/chrome-linux/chrome`; Playwright itself
needs Node 20 and this box has 18, so talk to CDP directly over `websockets`.

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
