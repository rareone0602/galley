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
        """The parsed map for a build, and the tree TeX ran in.

        Every path in the file is written relative to that tree, and the tree
        is not always the paper: a session compiles its own worktree, and the
        marked-up review compiles a scratch copy. Both sit inside the
        repository, so the tree has to be named rather than inferred from where
        the PDF landed.
        """
        pdf = d.pdf_path(session_id, review)
        tree = (
            d.cfg.paths.state_dir / "review" / session_id / "tree"
            if review and session_id
            else d.repo_for(session_id)
        )
        source = synctex.find(pdf)
        if source is None:
            raise HTTPException(404, NO_DATA)
        return synctex.SyncTeX.read(source), tree

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
        parsed, tree = _read(session_id, review)
        location = parsed.edit(page, x, y)
        if location is None:
            raise HTTPException(404, f"nothing recorded at ({x}, {y}) on page {page}")
        return synctex.resolve(location, d.cfg.paths.paper_repo, tree)

    @app.get("/api/synctex/view")
    def synctex_view(
        path: str = Query(..., description="project-relative, as the file tree names it"),
        line: int = Query(..., ge=1),
        session_id: str | None = None,
        review: bool = False,
    ) -> dict:
        """Where on the page did this source line end up?

        The other direction: the cursor is on a line and the PDF should go to
        it. The answer is rectangles in big points from each page's top-left,
        the same measure `edit` takes back. If the line itself printed nothing
        the search falls forward to the next line that did, and says so in
        `fell_forward` rather than passing the neighbour off as your line.
        """
        parsed, tree = _read(session_id, review)
        found = parsed.view(path, line, d.cfg.paths.paper_repo, tree)
        if found is None:
            raise HTTPException(404, f"nothing in this build came from line {line} of {path}")
        return found.as_dict()
