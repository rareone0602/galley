"""SyncTeX: the map between the printed page and the source that made it."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from ..services import synctex
from .deps import Deps

NO_DATA = (
    "no synctex data for this PDF. Compile it again — Galley now asks latexmk "
    "for it, but a build from before that will not have one."
)


def register(app: FastAPI, d: Deps) -> None:
    def _read(session_id: str | None, review: bool):
        """The parsed map for a build, and the tree its paths are relative to.

        The marked-up review compiles a scratch copy of the tree; its recorded
        paths are resolved back to the files they came from.
        """
        pdf = d.pdf_path(session_id, review)
        build_dir = (
            d.cfg.paths.state_dir / "review" / session_id / "tree"
            if review and session_id
            else pdf.parent
        )
        source = synctex.find(pdf)
        if source is None:
            raise HTTPException(404, NO_DATA)
        return synctex.SyncTeX.read(source), build_dir

    @app.get("/api/synctex/edit")
    def synctex_edit(
        page: int = Query(..., ge=1),
        x: float = Query(...),
        y: float = Query(...),
        session_id: str | None = None,
        review: bool = False,
    ) -> dict:
        """Which source line produced what is at (x, y) on this page?

        `x` and `y` are big points from the top-left of the page, which is what
        a PDF viewer measures in. This is what a double-click on the paper
        asks, so the editor can put the cursor where you pointed.
        """
        parsed, build_dir = _read(session_id, review)
        location = parsed.edit(page, x, y)
        if location is None:
            raise HTTPException(404, f"nothing recorded at ({x}, {y}) on page {page}")
        return synctex.resolve(location, d.cfg.paths.paper_repo, build_dir)
