"""The merge pane: your text against Claude's, a sentence at a time."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from ..segment.diff import diff_text
from ..services import git
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/diff")
    def read_diff(session_id: str = Query(...), path: str | None = Query(None)) -> dict:
        row = d.require_session(session_id)
        repo = d.cfg.paths.paper_repo
        base, head = row["base_sha"] or d.cfg.paper.main_branch, row["branch"]
        changed = [f["path"] for f in d.session_changes(row)]
        if path is not None and path not in changed:
            raise HTTPException(404, f"{path} did not change on {head}")

        out = []
        for rel in path and [path] or changed:
            old = d.read_working(rel)
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
