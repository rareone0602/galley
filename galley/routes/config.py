"""What the server was started with."""

from __future__ import annotations

from fastapi import FastAPI

from ..services import latex
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/config")
    def read_config() -> dict:
        cfg = d.cfg
        return {
            "paper_repo": str(cfg.paths.paper_repo),
            "code_mirror": str(cfg.paths.code_mirror),
            "main_branch": cfg.paper.main_branch,
            "main_tex": cfg.paper.main_tex,
            "overleaf": f"{cfg.paper.overleaf_remote}/{cfg.paper.overleaf_branch}",
            "max_concurrent_sessions": cfg.limits.max_concurrent_sessions,
            "latexdiff": latex.latexdiff_available(),
            "bind": f"{cfg.server.bind}:{cfg.server.port}",
        }
