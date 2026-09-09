"""latexmk and latexdiff, and the PDF they produce.

Both take far too long to hold a request open — tens of seconds for a real
paper, minutes for latexdiff — so POST starts one (or joins one already
running) and GET says how it is getting on.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from ..services import latex
from .deps import Deps


NO_LATEX = (
    "this project has no {main_tex}, so there is nothing to compile. Point "
    "[paper] main_tex at the file latexmk should build, or leave it — a "
    "project that produces no PDF is a perfectly ordinary one."
)


def register(app: FastAPI, d: Deps) -> None:
    def _require_latex() -> None:
        """Refuse before starting a build that cannot succeed.

        latexmk on a file that is not there fails after several seconds with a
        message about the wrong thing. This says the true reason immediately.
        """
        if not d.cfg.builds_a_pdf:
            raise HTTPException(400, NO_LATEX.format(main_tex=d.cfg.paper.main_tex))

    def _compile_job(session_id: str | None):
        repo, outdir = d.cfg.paths.paper_repo, d.cfg.paths.state_dir / "build"
        if session_id:
            row = d.require_session(session_id)
            repo = Path(row["worktree_path"])
            outdir = d.cfg.paths.state_dir / "build" / session_id
        return lambda: latex.compile_pdf(repo, d.cfg.paper.main_tex, outdir).as_dict()

    def _review_job(session_id: str):
        row = d.require_session(session_id)
        worktree_path = Path(row["worktree_path"])
        changed = [f["path"] for f in d.session_changes(row)]
        return lambda: latex.latexdiff_pdf(
            d.cfg.paths.paper_repo,
            worktree_path,
            d.cfg.paper.main_tex,
            d.cfg.paths.state_dir / "review" / session_id,
            changed=changed,
        ).as_dict()

    # async, not sync: a sync route runs in a worker thread, where starting the
    # background task raises "no running event loop".
    @app.post("/api/compile")
    async def compile_paper(body: dict = Body(default={})) -> dict:
        _require_latex()
        session_id = body.get("session_id")
        return d.work.start(f"compile:{session_id or 'main'}", _compile_job(session_id))

    @app.get("/api/compile")
    def compile_status(session_id: str | None = None) -> dict:
        return d.work.state(f"compile:{session_id or 'main'}")

    @app.post("/api/review")
    async def latexdiff_review(body: dict = Body(...)) -> dict:
        """The second review surface: the change as it will appear in print."""
        _require_latex()
        session_id = body.get("session_id")
        if not session_id:
            raise HTTPException(400, "a review needs a session")
        return d.work.start(f"review:{session_id}", _review_job(session_id))

    @app.get("/api/review")
    def review_status(session_id: str) -> dict:
        return d.work.state(f"review:{session_id}")

    @app.get("/api/pdf")
    def read_pdf(session_id: str | None = None, review: bool = False):
        path = d.pdf_path(session_id, review)
        if not path.is_file():
            raise HTTPException(404, "no PDF yet; compile first")
        return FileResponse(path, media_type="application/pdf")
