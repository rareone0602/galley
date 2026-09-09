"""The Galley backend: one FastAPI app that is also the agent's tool surface.

Routes are grouped the way the UI uses them: sessions and their live log, the
diff and the write-back, git and the Overleaf sync, jobs, and the PDF.
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
from starlette.types import Receive, Scope, Send

from . import mcp_server
from .bus import EventBus
from .config import Config, load
from .db import Database
from .segment.diff import diff_text
from .segment.tokenizer import segment
from .services import git, latex, overleaf, results, worktree
from .services.agent import AgentService, SessionLimitReached
from .services.jobs import JobService, handoff_prompt
from .services.scheduler.base import QueueRefused, Resources
from .services.scheduler.gpuq import GpuqScheduler

UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"


class McpDispatch:
    """Put the agent's tool surface at exactly `/mcp`, before routing happens.

    Mounting would not do: a Starlette mount at `/mcp` only matches `/mcp/...`,
    so the bare `/mcp` the design specifies would fall through to the static
    file mount and come back 405. Dispatching in front of the router hands that
    one path straight to the MCP app and leaves everything else untouched.
    """

    def __init__(self, app, mcp_app, path: str = "/mcp") -> None:
        self.app = app
        self.mcp_app = mcp_app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").rstrip("/") == self.path:
            scope = {**scope, "path": self.path, "raw_path": self.path.encode()}
            await self.mcp_app(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_scheduler(cfg: Config):
    if cfg.cluster.backend == "gpuq":
        return GpuqScheduler(
            tmux_prefix=cfg.cluster.tmux_prefix,
            max_hours=cfg.cluster.max_job_hours,
            max_queued=cfg.limits.max_queued_jobs,
            extra_flags=cfg.cluster.submit_flags,
        )
    raise RuntimeError(
        f"unknown cluster backend {cfg.cluster.backend!r}. "
        "The only backend implemented is 'gpuq'."
    )


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load()
    db = Database(cfg.db_path)
    bus = EventBus()
    scheduler = build_scheduler(cfg)
    jobs = JobService(cfg, db, bus, scheduler)
    agents = AgentService(cfg, db, bus)
    mcp = mcp_server.build(cfg, db, jobs)
    # DNS-rebinding protection: the tool surface answers only to the host and
    # port it is actually bound to, so a page in a browser cannot drive it.
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = {
        f"{cfg.server.bind}:{cfg.server.port}",
        f"localhost:{cfg.server.port}",
        f"127.0.0.1:{cfg.server.port}",
    }
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=sorted(allowed),
            allowed_origins=sorted(f"http://{h}" for h in allowed),
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp_app.router.lifespan_context(mcp_app):
            results.install_precommit_hook(cfg.paths.paper_repo)
            worktree.ensure_ignored(cfg.paths.paper_repo)
            jobs.start()
            try:
                yield
            finally:
                await jobs.stop()
                await agents.shutdown()
                db.close()

    app = FastAPI(title="Galley", version="0.1.0", lifespan=lifespan)
    app.state.cfg, app.state.db, app.state.bus = cfg, db, bus
    app.state.jobs, app.state.agents = jobs, agents

    # -- configuration and health ----------------------------------------

    @app.get("/api/config")
    def read_config() -> dict:
        return {
            "paper_repo": str(cfg.paths.paper_repo),
            "code_mirror": str(cfg.paths.code_mirror),
            "main_branch": cfg.paper.main_branch,
            "main_tex": cfg.paper.main_tex,
            "overleaf": f"{cfg.paper.overleaf_remote}/{cfg.paper.overleaf_branch}",
            "backend": cfg.cluster.backend,
            "scratch": str(cfg.cluster.scratch),
            "max_concurrent_sessions": cfg.limits.max_concurrent_sessions,
            "max_queued_jobs": cfg.limits.max_queued_jobs,
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

    @app.post("/api/sessions")
    def create_session(body: dict = Body(...)) -> dict:
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "a session needs a prompt")
        try:
            row = agents.create(prompt, body.get("slug"))
        except SessionLimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        if body.get("start", True):
            agents.start(row["id"])
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
    def send_message(session_id: str, body: dict = Body(...)) -> dict:
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
    def read_diff(
        session_id: str = Query(...),
        path: str | None = Query(None),
    ) -> dict:
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        repo = cfg.paths.paper_repo
        base, head = cfg.paper.main_branch, row["branch"]
        files = [f["path"] for f in git.changed_files(repo, base, head)]
        if path is not None and path not in files:
            raise HTTPException(404, f"{path} did not change on {head}")
        targets = [path] if path else files

        out = []
        for rel in targets:
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
        known = {r["id"] for r in db.list_jobs()}
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
            "unbacked_results": results.unbacked_results(repo, known),
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

    # -- jobs -------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs(state: str | None = None) -> list[dict]:
        return db.list_jobs(state=state)

    @app.post("/api/jobs")
    def submit_job(body: dict = Body(...)) -> dict:
        script = body.get("script") or ""
        if not script.strip():
            raise HTTPException(400, "a job needs a script")
        try:
            return jobs.submit(
                script=script,
                resources=Resources(
                    gpus=int(body.get("gpus", 1)), hours=float(body.get("hours", 8.0))
                ),
                note=body.get("note", ""),
            )
        except QueueRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/events")
    async def job_events(request: Request):
        async def stream():
            async with bus.subscribe("jobs") as queue:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield _sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        row = db.get_job(job_id)
        if row is None:
            raise HTTPException(404, f"no job {job_id}")
        row["tail"] = jobs.tail(job_id)
        return row

    @app.post("/api/jobs/{job_id}/fetch")
    def fetch_job(job_id: str) -> dict:
        try:
            dest = jobs.fetch_artifacts(job_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "artifacts": str(dest), "files": sorted(p.name for p in dest.iterdir())}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        try:
            jobs.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/handoff")
    def handoff(job_id: str) -> dict:
        """Open a new session prompted with this job's results."""
        row = db.get_job(job_id)
        if row is None:
            raise HTTPException(404, f"no job {job_id}")
        metrics = None
        try:
            metrics = jobs.read_artifact(job_id, "metrics.json")
        except (KeyError, FileNotFoundError):
            pass
        prompt = handoff_prompt(row, metrics, jobs.tail(job_id, 80))
        try:
            session = agents.create(prompt, slug=f"job-{job_id}")
        except SessionLimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        agents.start(session["id"])
        return session

    # -- LaTeX ------------------------------------------------------------

    @app.post("/api/compile")
    def compile_paper(body: dict = Body(default={})) -> dict:
        session_id = body.get("session_id")
        repo = cfg.paths.paper_repo
        outdir = cfg.paths.state_dir / "build"
        if session_id:
            row = db.get_session(session_id)
            if row is None:
                raise HTTPException(404, f"no session {session_id}")
            repo = Path(row["worktree_path"])
            outdir = cfg.paths.state_dir / "build" / session_id
        return latex.compile_pdf(repo, cfg.paper.main_tex, outdir).as_dict()

    @app.post("/api/review")
    def latexdiff_review(body: dict = Body(...)) -> dict:
        """The second review surface: the change as it will appear in print."""
        session_id = body.get("session_id")
        row = db.get_session(session_id) if session_id else None
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        return latex.latexdiff_pdf(
            cfg.paths.paper_repo,
            Path(row["worktree_path"]),
            cfg.paper.main_tex,
            cfg.paths.state_dir / "review" / session_id,
        ).as_dict()

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

    # -- the agent's tool surface, same process, loopback only ------------

    app.add_middleware(McpDispatch, mcp_app=mcp_app)

    if UI_DIST.is_dir():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse)
        def no_ui() -> str:
            return (
                "<h1>Galley backend is up</h1>"
                "<p>The UI has not been built. Run <code>npm install &amp;&amp; "
                "npm run build</code> in <code>ui/</code>.</p>"
                "<p>The API is under <code>/api</code>; the agent tool surface is "
                "at <code>/mcp</code>.</p>"
            )

    return app


def _read_working(repo: Path, rel: str) -> str:
    path = repo / rel
    return path.read_text(errors="replace") if path.is_file() else ""


def _sse(event: dict) -> str:
    return f"id: {event.get('id', 0)}\nevent: {event.get('kind', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"
