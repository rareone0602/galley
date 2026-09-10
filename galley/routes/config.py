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
            # Null rather than absent: the UI has to tell "no codebase" from
            # "the server is an older one that did not say".
            "code_mirror": str(cfg.paths.code_mirror) if cfg.paths.code_mirror else None,
            "main_branch": cfg.paper.main_branch,
            "main_tex": cfg.paper.main_tex,
            # Whether the LaTeX half of Galley means anything here. Compiling,
            # SyncTeX and \cite completion all rest on this one file existing,
            # so the UI asks once rather than each of them failing separately.
            "builds_pdf": cfg.builds_a_pdf,
            "publish": f"{cfg.paper.publish_remote}/{cfg.paper.publish_branch}",
            "max_concurrent_sessions": cfg.limits.max_concurrent_sessions,
            # Which Claude answers. Worth showing: it is the one thing about a
            # session you cannot tell from reading what it wrote.
            "agent_model": cfg.agent.model,
            "latexdiff": latex.latexdiff_available(),
            # The browser asks once and then either records or does not; it
            # never posts into a switched-off log.
            "usage": cfg.usage.enabled,
            "bind": f"{cfg.server.bind}:{cfg.server.port}",
        }
