"""The project rail and the editor: what is in the paper, and its contents."""

from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from ..segment.tokenizer import segment
from ..services import files
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/tree")
    def read_tree(session_id: str | None = None) -> dict:
        """Everything git considers part of the project, nested into folders."""
        repo = d.repo_for(session_id)
        return {"root": str(repo), "tree": files.tree(repo)}

    @app.get("/api/file")
    def read_file(path: str = Query(...), session_id: str | None = None) -> dict:
        repo = d.repo_for(session_id)
        try:
            return files.read(repo, path)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"no such file: {path}") from exc
        except ValueError as exc:
            raise HTTPException(400, "path escapes the project") from exc

    @app.get("/api/blob")
    def read_blob(path: str = Query(...), session_id: str | None = None):
        """A figure, served as itself, so the editor can show it."""
        repo = d.repo_for(session_id)
        try:
            target = files.resolve(repo, path)
        except ValueError as exc:
            raise HTTPException(400, "path escapes the project") from exc
        if not target.is_file():
            raise HTTPException(404, f"no such file: {path}")
        return FileResponse(target)

    @app.get("/api/segments")
    def read_segments(path: str = Query(...)) -> dict:
        text = d.read_working(path)
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
        try:
            target = files.resolve(d.cfg.paths.paper_repo, path)
        except ValueError as exc:
            raise HTTPException(400, "path escapes the paper repository") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        d.db.append_event("file_written", {"path": path, "bytes": len(content)})
        return {"ok": True, "path": path, "bytes": len(content)}
