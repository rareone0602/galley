"""Git, and publishing to the remote. Every route here is yours, never the agent's."""

from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException

from ..services import git, publish, worktree
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
            "publish": publish.status(d.cfg),
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
            d.note("git.commit", {"ok": False, "paths": len(paths)})
            raise HTTPException(400, str(exc)) from exc
        d.note("git.commit", {"ok": True, "paths": len(paths)})
        return {"ok": True, "sha": sha}

    @app.post("/api/git/sync")
    def git_sync(body: dict = Body(default={})) -> dict:
        result = publish.sync(d.cfg, allow_push=body.get("push", True)).as_dict()
        # `step` is where it got to, which is the useful half of the answer:
        # a refusal at "precondition" and a failure at "push" are different
        # problems with the same ok=False.
        d.note(
            "git.sync",
            {"ok": result["ok"], "step": result["step"], "pushed": result["pushed"]},
        )
        return result

    @app.post("/api/git/rebase/{action}")
    def git_rebase(action: str) -> dict:
        if action == "continue":
            return publish.continue_rebase(d.cfg).as_dict()
        if action == "abort":
            return publish.abort_rebase(d.cfg).as_dict()
        raise HTTPException(400, "action must be continue or abort")
