"""The usage log: writing to it, and reading it back.

Nothing here goes anywhere. There is no reporting endpoint and no third party;
the log is a table beside your own paper, and these two routes are the browser
putting things in it and you taking them out.
"""

from __future__ import annotations

from fastapi import Body, FastAPI, Query

from ..services import usage
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.post("/api/usage")
    def write_usage(body: dict = Body(default={})) -> dict:
        """A batch from the browser.

        Always 200, even when recording is off or the batch is nonsense: the
        client posts these in the background and must never be handed an error
        it would have to do something about.
        """
        if not d.cfg.usage.enabled:
            return {"recorded": 0, "enabled": False, "unknown": []}
        written, unknown = usage.record_batch(d.db, body.get("entries") or [])
        return {"recorded": written, "enabled": True, "unknown": sorted(set(unknown))}

    @app.get("/api/usage/report")
    def read_usage(days: float = Query(30, gt=0, le=3650)) -> dict:
        return usage.report(d.db, days)
