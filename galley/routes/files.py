"""The project rail and the editor: what is in the paper, and its contents.

Everything here that changes the project changes the main worktree, never a
session's checkout. Moving a file about is your edit, not the agent's, and
doing it on Claude's branch would only give you something else to merge back.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..segment.tokenizer import segment
from ..services import files
from .deps import Deps


@contextmanager
def _refusals() -> Iterator[None]:
    """Turn what the service refuses into the status code that says why.

    Both of the service's own errors are `ValueError`s, so they are caught
    ahead of the bare one `resolve()` raises when a path leaves the project.
    """
    try:
        yield
    except files.InvalidName as exc:
        raise HTTPException(400, str(exc)) from exc
    except files.Refused as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no such file: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(400, "path escapes the project") from exc


def _string(body: dict, key: str, default: str | None = None) -> str:
    value = body.get(key, default)
    if not isinstance(value, str):
        raise HTTPException(400, f"{key} must be a string")
    return value.strip()


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/tree")
    def read_tree(session_id: str | None = None) -> dict:
        """Everything git considers part of the project, nested into folders."""
        repo = d.repo_for(session_id)
        return {"root": str(repo), "tree": files.tree(repo)}

    @app.get("/api/file")
    def read_file(path: str = Query(...), session_id: str | None = None) -> dict:
        repo = d.repo_for(session_id)
        with _refusals():
            return files.read(repo, path)

    @app.get("/api/blob")
    def read_blob(path: str = Query(...), session_id: str | None = None):
        """A figure, served as itself, so the editor can show it."""
        repo = d.repo_for(session_id)
        with _refusals():
            target = files.resolve(repo, path)
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
        with _refusals():
            target = files.resolve(d.cfg.paths.paper_repo, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not characters, and UTF-8 named rather than left to the
        # process locale. A paper is full of non-ASCII: `len(content)` on a
        # str would under-report every em dash and every accented name, and a
        # server started from a different shell would write a different file.
        written = content.encode("utf-8")
        target.write_bytes(written)
        d.db.append_event("file_written", {"path": path, "bytes": len(written)})
        return {"ok": True, "path": path, "bytes": len(written)}

    # -- the four things Overleaf's rail can do ---------------------------

    @app.post("/api/files")
    def create_entry(body: dict = Body(...)) -> dict:
        """A new empty file, or a new folder, inside `parent` ("" is the root)."""
        parent = _string(body, "parent", "")
        name = _string(body, "name", "")
        folder = bool(body.get("folder"))
        with _refusals():
            rel = files.create(d.cfg.paths.paper_repo, parent, name, folder=folder)
        d.db.append_event("file_created", {"path": rel, "folder": folder})
        return {"ok": True, "path": rel}

    @app.post("/api/files/rename")
    def rename_entry(body: dict = Body(...)) -> dict:
        """Rename, which is also move: `to` is a whole path, not a name."""
        path = _string(body, "path", "")
        to = _string(body, "to", "")
        with _refusals():
            rel = files.rename(
                d.cfg.paths.paper_repo, path, to, main_tex=d.cfg.paper.main_tex
            )
        d.db.append_event("file_renamed", {"from": path, "to": rel})
        return {"ok": True, "path": rel, "was": path}

    @app.post("/api/files/upload")
    async def upload_file(
        request: Request,
        name: str = Query(...),
        parent: str = Query(""),
        replace: bool = Query(False),
    ) -> dict:
        """A file dropped onto the rail, sent as the raw request body.

        Not a multipart form: `python-multipart` is not one of Galley's
        declared dependencies, and one file per request needs no envelope
        around it.
        """
        data = await request.body()
        with _refusals():
            rel = files.upload(
                d.cfg.paths.paper_repo, parent, name, data, replace=replace
            )
        d.db.append_event("file_uploaded", {"path": rel, "bytes": len(data)})
        return {"ok": True, "path": rel, "bytes": len(data)}

    @app.delete("/api/files/{path:path}")
    def delete_entry(path: str) -> dict:
        with _refusals():
            files.delete(d.cfg.paths.paper_repo, path, main_tex=d.cfg.paper.main_tex)
        d.db.append_event("file_deleted", {"path": path})
        return {"ok": True, "path": path}
