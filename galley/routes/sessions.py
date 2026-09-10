"""Claude sessions: create one, talk to it, watch it work, put it away."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..services import worktree
from ..services.agent import Selection, SessionLimitReached
from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        rows = d.db.list_sessions()
        for row in rows:
            row["running"] = d.agents.is_running(row["id"])
        return rows

    # These two must be async: starting an agent creates an asyncio task, and a
    # sync route runs in a worker thread where there is no running loop.
    @app.post("/api/sessions")
    async def create_session(body: dict = Body(...)) -> dict:
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "a session needs a prompt")
        try:
            row = d.agents.create(
                prompt, body.get("slug"), Selection.from_request(body.get("selection"))
            )
        except SessionLimitReached as exc:
            raise HTTPException(429, str(exc)) from exc
        d.note(
            "session.create",
            # How a session begins is the question: from a passage you
            # highlighted, or from the box with the whole paper in mind.
            {"from": "selection" if body.get("selection") else "prompt",
             "prompt": prompt},
        )
        if body.get("start", True):
            try:
                d.agents.start(row["id"])
            except Exception as exc:  # noqa: BLE001
                # The worktree exists but nothing is using it. Leaving it behind
                # would silently claim the slug, so the next session with the
                # same prompt would be "-2" for no reason a human can see.
                worktree.remove(d.cfg.paths.paper_repo, row["slug"], keep_branch=False)
                d.db.update_session(row["id"], status="error", error=str(exc))
                raise HTTPException(500, f"could not start the agent: {exc}") from exc
        return row

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        row = d.require_session(session_id)
        row["running"] = d.agents.is_running(session_id)
        row["files"] = d.session_changes(row)
        return row

    @app.post("/api/sessions/{session_id}/message")
    async def send_message(session_id: str, body: dict = Body(...)) -> dict:
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "an empty message goes nowhere")
        d.require_session(session_id)
        d.note("session.message", {"text": text})
        try:
            d.agents.start(session_id, text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict:
        d.note("session.stop", {"was_running": d.agents.is_running(session_id)})
        await d.agents.stop(session_id)
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    async def remove_session(session_id: str, keep_branch: bool = True) -> dict:
        row = d.require_session(session_id)
        await d.agents.stop(session_id)
        worktree.remove(d.cfg.paths.paper_repo, row["slug"], keep_branch=keep_branch)
        d.db.update_session(session_id, status="removed", ended_at=time.time())
        d.note("session.remove", {"kept_branch": keep_branch})
        return {"ok": True, "branch_kept": keep_branch}

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str, request: Request, after: int = 0):
        d.require_session(session_id)

        async def stream():
            # Replay from the durable log first, so a reconnect rebuilds the
            # whole conversation rather than resuming mid-sentence.
            last = after
            for event in d.db.session_events(session_id, after=last):
                last = event["id"]
                yield _sse(event)
            async with d.bus.subscribe(f"session:{session_id}") as queue:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.get("id") is None:
                        # Live text on its way to the screen. It is in no log,
                        # so it never advances the cursor and never replays.
                        yield _sse(event)
                        continue
                    if event["id"] <= last:
                        continue
                    last = event["id"]
                    yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


def _sse(event: dict) -> str:
    # No `id:` line for an ephemeral event. The browser remembers the last id
    # it saw and asks to resume from it after a dropped connection; pointing
    # that at a fragment which was never written down would lose everything
    # after it.
    head = f"id: {event['id']}\n" if event.get("id") is not None else ""
    return (
        head
        + f"event: {event.get('kind', 'message')}\n"
        + f"data: {json.dumps(event, default=str)}\n\n"
    )
