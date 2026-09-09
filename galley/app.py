"""The Galley backend.

One job: let Claude propose a patch on its own branch, and let you accept it a
sentence at a time. Everything downstream of that — committing, pushing to
Overleaf, compiling — is yours, and the routes for it are here because they are
the things you do while reviewing, not because an agent touches them.

This module is the composer. The routes themselves live in `galley/routes/`,
one module per area of the screen; everything they share is on `Deps`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .bus import EventBus
from .config import Config, load
from .db import Database
from .routes import Deps, register_all
from .services import worktree
from .services.agent import AgentService
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

    deps = Deps(cfg=cfg, db=db, bus=bus, agents=agents, work=work)
    app.state.deps = deps
    register_all(app, deps)

    # The UI is mounted last: it claims "/", which would otherwise shadow the
    # API routes registered above it.
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
