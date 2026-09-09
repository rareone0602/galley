"""Every route Galley serves, grouped by the part of the screen it feeds.

Adding an area is two lines: import the module, and list it in `AREAS`. The
order is the order the routes are registered, which only matters where two
paths could match the same request — they do not today.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import build, config, diff, files, git, project, sessions, synctex
from .deps import Deps

AREAS = (config, sessions, files, diff, git, build, synctex, project)

__all__ = ["AREAS", "Deps", "register_all"]


def register_all(app: FastAPI, deps: Deps) -> None:
    for area in AREAS:
        area.register(app, deps)
