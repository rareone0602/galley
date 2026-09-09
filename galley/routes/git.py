"""Git and Overleaf. Every route here is something you do, never the agent."""

from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException

from ..services import git, overleaf, worktree
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/git/status")
    def git_status() -> dict:
        repo = d.cfg.paths.paper_repo
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
            "overleaf": overleaf.status(d.cfg),
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
            sha = git.commit(d.cfg.paths.paper_repo, message, paths)
        except git.GitError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "sha": sha}

    @app.post("/api/git/sync")
    def git_sync(body: dict = Body(default={})) -> dict:
        return overleaf.sync(d.cfg, allow_push=body.get("push", True)).as_dict()

    @app.post("/api/git/rebase/{action}")
    def git_rebase(action: str) -> dict:
        if action == "continue":
            return overleaf.continue_rebase(d.cfg).as_dict()
        if action == "abort":
            return overleaf.abort_rebase(d.cfg).as_dict()
        raise HTTPException(400, "action must be continue or abort")
