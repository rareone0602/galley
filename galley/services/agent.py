"""Spawn Claude inside a session's worktree and stream what it does.

The agent is scoped to its own checkout, with the code mirror mounted alongside
so Grep/Glob/Edit work at local speed over the whole codebase. Its only extra
tools are Galley's own, over MCP on loopback.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from ..bus import EventBus
from ..config import Config, child_env
from ..db import Database
from . import worktree

SYSTEM_APPENDIX = """
You are working inside Galley, on your own branch of a paper repository.

Your job is to write a patch, and only that.

- You never merge. Commit your work on this branch. A human reads every
  sentence you wrote, in a merge pane, and decides one at a time what reaches
  the manuscript. Many of your sentences will be rejected or rewritten; that is
  the design working, not a failure.
- You never publish. No pushing, no committing on the main branch, no touching
  the Overleaf remote. Those are the human's.
- You never invent a number. Every figure in this paper is generated from a
  measured artefact by the repository's own tooling. If a claim needs a number
  you cannot trace to one, write the claim without it and say so.

The codebase is mounted read-write beside the paper so you can read what the
experiments actually did before you describe them.
""".strip()


class SessionLimitReached(RuntimeError):
    pass


class AgentService:
    def __init__(self, cfg: Config, db: Database, bus: EventBus) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self._tasks: dict[str, asyncio.Task] = {}

    # -- lifecycle --------------------------------------------------------

    def create(self, prompt: str, slug: str | None = None) -> dict:
        """Make the worktree and the session row. Does not start the agent."""
        running = self.db.active_session_count()
        cap = self.cfg.limits.max_concurrent_sessions
        if running >= cap:
            raise SessionLimitReached(
                f"{running} session(s) already running and the cap is {cap}. "
                "Worktrees make it easy to run several agents at once and "
                "exhaust your subscription window; raise max_concurrent_sessions "
                "in galley.local.toml if you mean to."
            )

        repo = self.cfg.paths.paper_repo
        tree = worktree.create(repo, slug or prompt, self.cfg.paper.main_branch)
        session_id = uuid.uuid4().hex[:12]
        self.db.create_session(
            id=session_id,
            slug=tree.slug,
            branch=tree.branch,
            worktree_path=str(tree.path),
            prompt=prompt,
            status="created",
            base_sha=tree.base_sha,
        )
        return self.db.get_session(session_id) or {}

    def start(self, session_id: str, prompt: str | None = None) -> None:
        row = self.db.get_session(session_id)
        if row is None:
            raise KeyError(session_id)
        if session_id in self._tasks and not self._tasks[session_id].done():
            raise RuntimeError(f"session {session_id} is already running")
        self.db.update_session(session_id, status="running", error=None)
        self._tasks[session_id] = asyncio.create_task(
            self._run(session_id, prompt or row["prompt"]),
            name=f"galley-agent-{session_id}",
        )

    async def stop(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.db.update_session(session_id, status="stopped")

    async def shutdown(self) -> None:
        for session_id in list(self._tasks):
            await self.stop(session_id)

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done())

    # -- the run ----------------------------------------------------------

    def _options(self, row: dict) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=row["worktree_path"],
            add_dirs=[str(self.cfg.paths.code_mirror)],
            permission_mode="acceptEdits",
            system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_APPENDIX},
            # The one that matters: an inherited ANTHROPIC_API_KEY silently
            # bills the API instead of the logged-in subscription.
            env=child_env(),
            resume=row["claude_session_id"] or None,
        )

    async def _run(self, session_id: str, prompt: str) -> None:
        row = self.db.get_session(session_id)
        assert row is not None
        await self._emit(session_id, "prompt", {"text": prompt})
        try:
            async for message in query(prompt=prompt, options=self._options(row)):
                for event in normalise(message):
                    if event["kind"] == "session" and event["payload"].get("claude_session_id"):
                        self.db.update_session(
                            session_id,
                            claude_session_id=event["payload"]["claude_session_id"],
                        )
                    await self._emit(session_id, event["kind"], event["payload"])
            self.db.update_session(session_id, status="idle")
            await self._emit(session_id, "turn_end", {})
        except asyncio.CancelledError:
            self.db.update_session(session_id, status="stopped")
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI, not swallowed
            self.db.update_session(session_id, status="error", error=str(exc))
            await self._emit(session_id, "error", {"error": str(exc)})

    async def _emit(self, session_id: str, kind: str, payload: Any) -> None:
        event = self.db.append_event(kind, payload, session_id=session_id)
        await self.bus.publish(f"session:{session_id}", event)


def normalise(message: Any) -> list[dict]:
    """Turn one SDK message into flat events the UI can render.

    Kept deliberately structural: text, one event per tool call, one per tool
    result, and the usage numbers off the result message so the UI can show
    what a session cost.
    """
    name = type(message).__name__
    events: list[dict] = []

    if name == "SystemMessage":
        data = getattr(message, "data", {}) or {}
        events.append(
            {
                "kind": "session",
                "payload": {
                    "subtype": getattr(message, "subtype", ""),
                    "claude_session_id": data.get("session_id"),
                    "model": data.get("model"),
                },
            }
        )
        return events

    if name in ("AssistantMessage", "UserMessage"):
        role = "assistant" if name == "AssistantMessage" else "user"
        for block in getattr(message, "content", []) or []:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                events.append({"kind": "text", "payload": {"role": role, "text": block.text}})
            elif block_type == "ThinkingBlock":
                events.append(
                    {"kind": "thinking", "payload": {"text": getattr(block, "thinking", "")}}
                )
            elif block_type == "ToolUseBlock":
                events.append(
                    {
                        "kind": "tool_use",
                        "payload": {
                            "id": block.id,
                            "name": block.name,
                            "input": _shorten(block.input),
                        },
                    }
                )
            elif block_type == "ToolResultBlock":
                events.append(
                    {
                        "kind": "tool_result",
                        "payload": {
                            "id": getattr(block, "tool_use_id", ""),
                            "is_error": bool(getattr(block, "is_error", False)),
                            "content": _stringify(getattr(block, "content", "")),
                        },
                    }
                )
            elif isinstance(block, str):
                events.append({"kind": "text", "payload": {"role": role, "text": block}})
        return events

    if name == "ResultMessage":
        events.append(
            {
                "kind": "result",
                "payload": {
                    "duration_ms": getattr(message, "duration_ms", None),
                    "num_turns": getattr(message, "num_turns", None),
                    "is_error": getattr(message, "is_error", False),
                    "usage": getattr(message, "usage", None),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "result": _stringify(getattr(message, "result", "")),
                },
            }
        )
        return events

    events.append({"kind": "other", "payload": {"type": name, "repr": _stringify(message)[:2000]}})
    return events


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item) for item in value
        )
    return str(value)


def _shorten(value: Any, limit: int = 4000) -> Any:
    """Tool inputs can be whole files; the log shows the shape, not the payload."""
    if isinstance(value, dict):
        return {k: _shorten(v, limit // 2) for k, v in value.items()}
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"\n… [{len(value) - limit} more characters]"
    return value
