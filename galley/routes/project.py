r"""The project index: what you can `\cite`, `\ref` and call, for the editor."""

from __future__ import annotations

from fastapi import FastAPI

from ..services import project
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/project/index")
    def read_project_index(session_id: str | None = None) -> dict:
        """Every label, bibliography key and macro the project defines.

        Meant to be asked again and again: the answer is cached until one of
        the files it was read from changes, so the editor can keep itself
        current without a cost per keystroke.
        """
        return project.index(d.repo_for(session_id)).as_dict()
