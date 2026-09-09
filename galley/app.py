"""The Galley backend.

One job: let Claude propose a patch on its own branch, and let you accept it a
sentence at a time. Everything downstream of that — committing, pushing to
Overleaf, compiling — is yours, and the routes for it are here because they are
the things you do while reviewing, not because an agent touches them.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .bus import EventBus
from .config import Config, load
from .db import Database
from .segment.diff import diff_text
from .segment.tokenizer import segment
from .services import git, latex, overleaf, worktree
from .services.agent import AgentService, SessionLimitReached
from .services.work import WorkTable

UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    db = Database(cfg.db_path)
    bus = EventBus()
    agents = AgentService(cfg, db, bus)
    work = WorkTable()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worktree.ensure_ignored(cfg.paths.paper_repo)
        _retire_vanished_sessions(db)
        try:
            yield
        finally:
            await work.shutdown()
            await agents.shutdown()
            db.close()

    app = FastAPI(title="Galley", version="0.1.0", lifespan=lifespan)
    app.state.cfg, app.state.db, app.state.bus = cfg, db, bus
    app.state.agents, app.state.work = agents, work

    # -- configuration ----------------------------------------------------

    @app.get("/api/config")
    def read_config() -> dict:
        return {
            "paper_repo": str(cfg.paths.paper_repo),
            "code_mirror": str(cfg.paths.code_mirror),
            "main_branch": cfg.paper.main_branch,
            "main_tex": cfg.paper.main_tex,
            "overleaf": f"{cfg.paper.overleaf_remote}/{cfg.paper.overleaf_branch}",
            "max_concurrent_sessions": cfg.limits.max_concurrent_sessions,
            "latexdiff": latex.latexdiff_available(),
            "bind": f"{cfg.server.bind}:{cfg.server.port}",
        }

    # -- sessions ---------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        rows = db.list_sessions()
        for row in rows:
            row["running"] = agents.is_running(row["id"])
        return rows

    # These two must be async: starting an agent creates an asyncio task, and a
    # sync route runs in a worker thread where there is no running loop.
    @app.post("/api/sessions")
    async def create_session(body: dict = Body(...)) -> dict:
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "a session needs a prompt")
        try:
            row = agents.create(prompt, body.get("slug"))
        except SessionLimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        if body.get("start", True):
            try:
                agents.start(row["id"])
            except Exception as exc:  # noqa: BLE001
                # The worktree exists but nothing is using it. Leaving it behind
                # would silently claim the slug, so the next session with the
                # same prompt would be "-2" for no reason a human can see.
                worktree.remove(cfg.paths.paper_repo, row["slug"], keep_branch=False)
                db.update_session(row["id"], status="error", error=str(exc))
                raise HTTPException(500, f"could not start the agent: {exc}") from exc
        return row

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        row["running"] = agents.is_running(session_id)
        row["files"] = git.changed_files(
            cfg.paths.paper_repo, cfg.paper.main_branch, row["branch"]
        )
        return row

    @app.post("/api/sessions/{session_id}/message")
    async def send_message(session_id: str, body: dict = Body(...)) -> dict:
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "an empty message goes nowhere")
        if db.get_session(session_id) is None:
            raise HTTPException(404, f"no session {session_id}")
        try:
            agents.start(session_id, text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict:
        await agents.stop(session_id)
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    async def remove_session(session_id: str, keep_branch: bool = True) -> dict:
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        await agents.stop(session_id)
        worktree.remove(cfg.paths.paper_repo, row["slug"], keep_branch=keep_branch)
        db.update_session(session_id, status="removed", ended_at=time.time())
        return {"ok": True, "branch_kept": keep_branch}

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str, request: Request, after: int = 0):
        if db.get_session(session_id) is None:
            raise HTTPException(404, f"no session {session_id}")

        async def stream():
            # Replay from the durable log first, so a reconnect rebuilds the
            # whole conversation rather than resuming mid-sentence.
            last = after
            for event in db.session_events(session_id, after=last):
                last = event["id"]
                yield _sse(event)
            async with bus.subscribe(f"session:{session_id}") as queue:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.get("id", 0) <= last:
                        continue
                    last = event["id"]
                    yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- the merge pane ---------------------------------------------------

    @app.get("/api/diff")
    def read_diff(session_id: str = Query(...), path: str | None = Query(None)) -> dict:
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        repo = cfg.paths.paper_repo
        base, head = cfg.paper.main_branch, row["branch"]
        files = [f["path"] for f in git.changed_files(repo, base, head)]
        if path is not None and path not in files:
            raise HTTPException(404, f"{path} did not change on {head}")

        out = []
        for rel in path and [path] or files:
            old = _read_working(repo, rel)
            new = git.show(repo, head, rel)
            ops = diff_text(old, new)
            out.append(
                {
                    "path": rel,
                    "ops": [op.as_dict() for op in ops],
                    "changes": sum(1 for op in ops if op.type == "change"),
                }
            )
        return {"session_id": session_id, "base": base, "head": head, "files": out}

    @app.get("/api/segments")
    def read_segments(path: str = Query(...)) -> dict:
        text = _read_working(cfg.paths.paper_repo, path)
        return {"path": path, "segments": [s.as_dict() for s in segment(text)]}

    @app.put("/api/files/{path:path}")
    def write_file(path: str, body: dict = Body(...)) -> dict:
        """Write the merged buffer into the main worktree.

        The client sends the whole resulting file. Galley never applies a
        partial patch, which removes the entire class of patch-offset and
        context-mismatch bugs.
        """
        content = body.get("content")
        if not isinstance(content, str):
            raise HTTPException(400, "content must be the whole resulting file")
        repo = cfg.paths.paper_repo
        target = (repo / path).resolve()
        try:
            target.relative_to(repo.resolve())
        except ValueError as exc:
            raise HTTPException(400, "path escapes the paper repository") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        db.append_event("file_written", {"path": path, "bytes": len(content)})
        return {"ok": True, "path": path, "bytes": len(content)}

    # -- git and Overleaf -------------------------------------------------

    @app.get("/api/git/status")
    def git_status() -> dict:
        repo = cfg.paths.paper_repo
        return {
            "branch": git.current_branch(repo),
            "head": git.head_sha(repo)[:10],
            "clean": git.is_clean(repo),
            "files": [
                {"path": s.path, "index": s.index, "worktree": s.worktree}
                for s in git.status(repo)
            ],
            "worktrees": worktree.listing(repo),
            "log": git.log(repo, 10),
            "overleaf": overleaf.status(cfg),
        }

    @app.post("/api/git/commit")
    def git_commit(body: dict = Body(...)) -> dict:
        message = (body.get("message") or "").strip()
        paths = body.get("paths") or []
        if not message:
            raise HTTPException(400, "a commit needs a message")
        if not paths:
            raise HTTPException(400, "galley stages explicit paths, never everything")
        try:
            sha = git.commit(cfg.paths.paper_repo, message, paths)
        except git.GitError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "sha": sha}

    @app.post("/api/git/sync")
    def git_sync(body: dict = Body(default={})) -> dict:
        return overleaf.sync(cfg, allow_push=body.get("push", True)).as_dict()

    @app.post("/api/git/rebase/{action}")
    def git_rebase(action: str) -> dict:
        if action == "continue":
            return overleaf.continue_rebase(cfg).as_dict()
        if action == "abort":
            return overleaf.abort_rebase(cfg).as_dict()
        raise HTTPException(400, "action must be continue or abort")

    # -- LaTeX ------------------------------------------------------------

    # latexmk takes tens of seconds on a real paper and latexdiff takes
    # minutes, so both are started rather than awaited. POST kicks one off (or
    # joins one already running); GET says how it is getting on.

    def _compile_job(session_id: str | None):
        repo, outdir = cfg.paths.paper_repo, cfg.paths.state_dir / "build"
        if session_id:
            row = db.get_session(session_id)
            if row is None:
                raise HTTPException(404, f"no session {session_id}")
            repo = Path(row["worktree_path"])
            outdir = cfg.paths.state_dir / "build" / session_id
        return lambda: latex.compile_pdf(repo, cfg.paper.main_tex, outdir).as_dict()

    def _review_job(session_id: str):
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        worktree_path = Path(row["worktree_path"])
        changed = [
            f["path"]
            for f in git.changed_files(cfg.paths.paper_repo, cfg.paper.main_branch, row["branch"])
        ]
        return lambda: latex.latexdiff_pdf(
            cfg.paths.paper_repo,
            worktree_path,
            cfg.paper.main_tex,
            cfg.paths.state_dir / "review" / session_id,
            changed=changed,
        ).as_dict()

    # async, not sync: a sync route runs in a worker thread, where starting the
    # background task raises "no running event loop".
    @app.post("/api/compile")
    async def compile_paper(body: dict = Body(default={})) -> dict:
        session_id = body.get("session_id")
        return work.start(f"compile:{session_id or 'main'}", _compile_job(session_id))

    @app.get("/api/compile")
    def compile_status(session_id: str | None = None) -> dict:
        return work.state(f"compile:{session_id or 'main'}")

    @app.post("/api/review")
    async def latexdiff_review(body: dict = Body(...)) -> dict:
        """The second review surface: the change as it will appear in print."""
        session_id = body.get("session_id")
        if not session_id:
            raise HTTPException(400, "a review needs a session")
        return work.start(f"review:{session_id}", _review_job(session_id))

    @app.get("/api/review")
    def review_status(session_id: str) -> dict:
        return work.state(f"review:{session_id}")

    @app.get("/api/pdf")
    def read_pdf(session_id: str | None = None, review: bool = False):
        stem = Path(cfg.paper.main_tex).stem
        if review and session_id:
            path = cfg.paths.state_dir / "review" / session_id / "latexdiff.pdf"
        elif session_id:
            path = cfg.paths.state_dir / "build" / session_id / f"{stem}.pdf"
        else:
            path = cfg.paths.state_dir / "build" / f"{stem}.pdf"
        if not path.is_file():
            raise HTTPException(404, "no PDF yet; compile first")
        return FileResponse(path, media_type="application/pdf")

    if UI_DIST.is_dir():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse)
        def no_ui() -> str:
            return (
                "<h1>Galley backend is up</h1>"
                "<p>The UI has not been built. Run <code>npm install &amp;&amp; "
                "npm run build</code> in <code>ui/</code>.</p>"
                "<p>The API is under <code>/api</code>.</p>"
            )

    return app


def _retire_vanished_sessions(db: Database) -> None:
    """A session whose checkout is gone cannot be used; stop listing it.

    Worktrees outlive the process, but not always: one removed by hand, or by a
    crash between creating it and starting the agent, leaves a row pointing at
    nothing. Clicking it would only produce an error.
    """
    for row in db.list_sessions():
        if row["status"] != "removed" and not Path(row["worktree_path"]).is_dir():
            db.update_session(
                row["id"], status="removed", error="worktree no longer exists"
            )


def _read_working(repo: Path, rel: str) -> str:
    path = repo / rel
    return path.read_text(errors="replace") if path.is_file() else ""


def _sse(event: dict) -> str:
    return (
        f"id: {event.get('id', 0)}\n"
        f"event: {event.get('kind', 'message')}\n"
        f"data: {json.dumps(event, default=str)}\n\n"
    )
