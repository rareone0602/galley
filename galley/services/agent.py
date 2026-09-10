"""Spawn Claude inside a session's worktree and stream what it does.

The agent is scoped to its own checkout, with the codebase mounted alongside so
Grep and Read work at local speed over the whole of it. It has no tools beyond
the ordinary file ones, and it may only *write* inside its own worktree: the
codebase is there to be read, so the agent can find out what the experiments
did before it describes them.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    query,
)

from ..bus import EventBus
from ..config import Config, child_env
from ..db import Database
from . import usage, worktree

SYSTEM_RULES = """
You are working inside Galley, on your own branch of a writing repository.

Your job is to write a patch, and only that.

- You never merge. Commit your work on this branch. A human reads every
  sentence you wrote, in a merge pane, and decides one at a time what reaches
  the manuscript. Many of your sentences will be rejected or rewritten; that is
  the design working, not a failure.
- You never publish. No pushing, no committing on the main branch, no touching
  the remote. Those are the human's.
- You never invent a number. If a claim needs a figure you cannot trace to
  something in front of you, write the claim without it and say so.
""".strip()

#: Added only when the config names a `code_mirror`. Saying this to an agent
#: that has no such directory would be an instruction it cannot follow, and a
#: standing invitation to hallucinate one.
CODEBASE_RULE = """
- You never edit the codebase. It is mounted beside the writing so you can read
  what the experiments actually did before you describe them; edits outside
  your worktree are refused.
""".strip()


def system_appendix(cfg: Config) -> str:
    """The rules the agent works under, and only the ones that are true here.

    Composed rather than fixed because a Galley is one repository plus whatever
    that repository happens to have. A paper with no companion codebase must
    not be told there is one."""
    rules = [SYSTEM_RULES]
    if cfg.paths.code_mirror is not None:
        rules.append(CODEBASE_RULE)
    return "\n".join(rules)


#: Events that are shown and never kept. A delta is one fragment of a sentence
#: that arrives again, whole, a moment later as an ordinary `text` event — so
#: writing it to the log would store the same paragraph a hundred times over
#: and make every reconnect replay it.
LIVE_ONLY = frozenset({"delta"})


class SessionLimitReached(RuntimeError):
    pass


@dataclass(frozen=True)
class Selection:
    """A block of text you highlighted in the editor, and where it came from."""

    path: str
    start: int
    end: int
    text: str

    @classmethod
    def from_request(cls, raw: Any) -> "Selection | None":
        if not isinstance(raw, dict):
            return None
        path, text = raw.get("path"), raw.get("text")
        if not path or not isinstance(text, str) or not text.strip():
            return None
        start = int(raw.get("start", 0))
        return cls(path=str(path), start=start, end=int(raw.get("end", start + len(text))), text=text)

    def line_span(self, whole_file: str) -> tuple[int, int]:
        first = whole_file.count("\n", 0, self.start) + 1
        return first, first + self.text.count("\n")


def compose_prompt(instruction: str, selection: Selection | None, whole_file: str = "") -> str:
    """What the agent is actually asked, when you asked it about a selection.

    The passage is quoted verbatim rather than described by offsets, because
    offsets go stale the moment either of you types. The agent finds the text.
    """
    if selection is None:
        return instruction
    first, last = selection.line_span(whole_file) if whole_file else (0, 0)
    where = f"`{selection.path}`"
    if first:
        where += f", line {first}" if first == last else f", lines {first}\u2013{last}"

    return (
        f"I have selected this passage in {where}:\n\n"
        f"<selection>\n{selection.text}\n</selection>\n\n"
        f"{instruction.strip()}\n\n"
        "Change only that passage, and only the adjacent sentences that stop "
        "reading correctly if you do not. Everything else in the file must come "
        "out byte-for-byte identical \u2014 I review your work sentence by "
        "sentence, and an unrelated reflow buries the change I asked for in "
        "noise. Edit the file in place; do not write a copy or a patch file."
    )


# Default deny. Writing to the paper on your own branch, and reading anything,
# is the whole job; there is no third thing an agent needs here.
#
# Bash is deliberately absent. Allowing it would undo every other line of this
# guard — `echo x > ../../code/train.py` is a write by another name — and the
# only shell an agent would legitimately want is `git commit`, which Galley does
# for it when the turn ends.
WRITING_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
READING_TOOLS = {"Read", "Grep", "Glob", "NotebookRead", "TodoWrite", "ExitPlanMode"}
# Where a writing tool names its target.
PATH_ARGS = ("file_path", "path", "notebook_path", "filePath")


def writes_only_inside(worktree: Path):
    """Refuse anything but reading, and writing inside the session's checkout.

    `add_dirs` grants access, not read-only access, and anything mounted beside
    the repository may be a live working tree with real work in it. The agent's
    job is to write a patch on its own branch, so the refusal is structural
    rather than a request in the prompt.
    """

    root = worktree.resolve()

    async def can_use_tool(name: str, args: dict, _context) -> object:
        if name in READING_TOOLS:
            return PermissionResultAllow()
        if name not in WRITING_TOOLS:
            return PermissionResultDeny(
                message=(
                    f"{name} is not available in Galley. Your job here is to "
                    "write prose into this worktree; reading is allowed "
                    "anywhere you have been given, and Galley commits your "
                    "work for you when the turn ends."
                )
            )
        raw = next((args[k] for k in PATH_ARGS if args.get(k)), None)
        if raw is None:
            return PermissionResultAllow()
        target = Path(str(raw))
        target = target if target.is_absolute() else root / target
        try:
            target.resolve().relative_to(root)
        except ValueError:
            return PermissionResultDeny(
                message=(
                    f"{target} is outside this session's worktree. Anything "
                    "mounted beside it is there to be read, not changed. Write "
                    f"only inside {root}, and only prose for the manuscript."
                )
            )
        return PermissionResultAllow()

    return can_use_tool


class AgentService:
    def __init__(self, cfg: Config, db: Database, bus: EventBus) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self._tasks: dict[str, asyncio.Task] = {}

    # -- lifecycle --------------------------------------------------------

    def create(
        self,
        prompt: str,
        slug: str | None = None,
        selection: Selection | None = None,
    ) -> dict:
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
            sel_path=selection.path if selection else None,
            sel_start=selection.start if selection else None,
            sel_end=selection.end if selection else None,
            sel_text=selection.text if selection else None,
        )
        return self.db.get_session(session_id) or {}

    def start(self, session_id: str, prompt: str | None = None) -> None:
        row = self.db.get_session(session_id)
        if row is None:
            raise KeyError(session_id)
        if session_id in self._tasks and not self._tasks[session_id].done():
            raise RuntimeError(f"session {session_id} is already running")
        text = prompt or row["prompt"]
        # Only the opening turn needs the selection spelled out; after that the
        # agent is already in the conversation and a follow-up is just a reply.
        if prompt is None and row.get("sel_path") and not row.get("claude_session_id"):
            selection = Selection(
                path=row["sel_path"],
                start=row["sel_start"] or 0,
                end=row["sel_end"] or 0,
                text=row["sel_text"] or "",
            )
            whole = ""
            source = Path(row["worktree_path"]) / selection.path
            if source.is_file():
                whole = source.read_text(errors="replace")
            text = compose_prompt(text, selection, whole)
        self.db.update_session(session_id, status="running", error=None)
        self._tasks[session_id] = asyncio.create_task(
            self._run(session_id, text),
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
        agent = self.cfg.agent
        return ClaudeAgentOptions(
            cwd=row["worktree_path"],
            can_use_tool=writes_only_inside(Path(row["worktree_path"])),
            # Opus by default. This is a workbench for one careful patch at a
            # time, read sentence by sentence by a human who will reject half
            # of it; the model is the cheapest part of that loop to get right.
            model=agent.model,
            fallback_model=agent.fallback_model,
            # Both None unless the config asks for them.
            max_turns=agent.max_turns,
            max_budget_usd=agent.max_budget_usd,
            # Sentences as they are written, rather than a paragraph at a time
            # when the block closes. The deltas are shown and thrown away; the
            # finished block is what gets kept.
            include_partial_messages=agent.stream,
            # Empty unless the project names a codebase: `add_dirs` grants
            # access, so an absent one must not become a path anyway.
            add_dirs=[str(d) for d in (self.cfg.paths.code_mirror,) if d is not None],
            permission_mode="acceptEdits",
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": system_appendix(self.cfg),
            },
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
                    if event["kind"] in LIVE_ONLY:
                        await self._emit_live(session_id, event["kind"], event["payload"])
                        continue
                    if event["kind"] == "session" and event["payload"].get("claude_session_id"):
                        self.db.update_session(
                            session_id,
                            claude_session_id=event["payload"]["claude_session_id"],
                        )
                    if event["kind"] == "result":
                        self._note_turn(event["payload"])
                    await self._emit(session_id, event["kind"], event["payload"])
            self.db.update_session(session_id, status="idle")
            await self._emit(session_id, "turn_end", _commit_worktree(Path(row["worktree_path"])))
        except asyncio.CancelledError:
            self.db.update_session(session_id, status="stopped")
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI, not swallowed
            self.db.update_session(session_id, status="error", error=str(exc))
            await self._emit(session_id, "error", {"error": str(exc)})

    def _note_turn(self, payload: dict) -> None:
        """What a turn cost, in seconds and in dollars.

        Kept in the usage log as well as the transcript because the transcript
        is per session and this question is not: what you want to know later is
        what a month of writing this way costs, and how long you spend waiting
        for it. The turn's text is not recorded — only its size.
        """
        if not self.cfg.usage.enabled:
            return
        usage.record(
            self.db,
            "agent.turn",
            {
                "ms": payload.get("duration_ms"),
                "turns": payload.get("num_turns"),
                "cost_usd": payload.get("total_cost_usd"),
            },
        )

    async def _emit(self, session_id: str, kind: str, payload: Any) -> None:
        event = self.db.append_event(kind, payload, session_id=session_id)
        await self.bus.publish(f"session:{session_id}", event)

    async def _emit_live(self, session_id: str, kind: str, payload: Any) -> None:
        """Show it now; keep nothing.

        `id` is None, and that is the signal the whole way down: the SSE route
        forwards it without advancing the cursor, so a tab that reconnects
        rebuilds the conversation from the finished blocks and never from
        half-written ones.
        """
        await self.bus.publish(
            f"session:{session_id}",
            {
                "id": None,
                "session_id": session_id,
                "job_id": None,
                "kind": kind,
                "payload": payload,
                "ts": time.time(),
            },
        )


def _commit_worktree(worktree: Path) -> dict:
    """Commit whatever the agent wrote, so the branch is the record.

    The agent has no shell, so this is Galley's to do. It is bookkeeping, not
    authorship: the merge pane reads the committed state, and the branch is what
    remains as provenance after the worktree is thrown away.
    """
    from . import git

    try:
        if git.is_clean(worktree):
            return {"committed": False, "reason": "nothing changed"}
        git.run(worktree, "add", "-A")
        git.run(worktree, "commit", "-m", "Claude: proposed changes")
        return {"committed": True, "sha": git.head_sha(worktree)[:10]}
    except git.GitError as exc:
        return {"committed": False, "reason": str(exc)}


def normalise(message: Any) -> list[dict]:
    """Turn one SDK message into flat events the UI can render.

    Kept deliberately structural: text, one event per tool call, one per tool
    result, and the usage numbers off the result message so the UI can show
    what a session cost.
    """
    name = type(message).__name__
    events: list[dict] = []

    if name == "StreamEvent":
        return _deltas(getattr(message, "event", {}) or {})

    if name == "RateLimitEvent":
        return _rate_limit(getattr(message, "rate_limit_info", None))

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


#: The delta shapes the API streams, and the event kind each one belongs to.
#: Anything else in the stream — message_start, block boundaries, usage
#: bookkeeping — is already carried by the finished message, so it is dropped.
DELTA_KINDS = {"text_delta": "text", "thinking_delta": "thinking"}


def _deltas(event: dict) -> list[dict]:
    """One fragment of a sentence, on its way to the screen.

    `index` is which block of the current message it belongs to, so a reply
    that thinks first and then writes lands in two places rather than one
    run-on. Nothing here is kept; see `LIVE_ONLY`.
    """
    if event.get("type") != "content_block_delta":
        return []
    delta = event.get("delta") or {}
    role = DELTA_KINDS.get(delta.get("type", ""))
    if role is None:
        return []
    text = delta.get("text") if role == "text" else delta.get("thinking")
    if not text:
        return []
    return [
        {
            "kind": "delta",
            "payload": {"index": int(event.get("index", 0)), "role": role, "text": text},
        }
    ]


def _rate_limit(info: Any) -> list[dict]:
    """How much of the subscription window is gone.

    Worth its own event because this is the one failure that looks like
    Galley breaking and is not: the agent simply stops answering, and the
    reason lives in a window that resets on a clock nothing on screen shows.
    """
    if info is None:
        return []
    return [
        {
            "kind": "rate_limit",
            "payload": {
                "status": getattr(info, "status", None),
                "window": getattr(info, "rate_limit_type", None),
                "used": getattr(info, "utilization", None),
                "resets_at": getattr(info, "resets_at", None),
            },
        }
    ]


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
