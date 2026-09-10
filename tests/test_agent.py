"""How the agent is launched.

These pin Galley's options against the SDK's own type definitions, so a version
bump that changes a shape fails here rather than at the first spawn. The spawn
itself is not exercised: it costs subscription usage.
"""

from __future__ import annotations

import os

import pytest
from claude_agent_sdk.types import SystemPromptPreset

from galley.bus import EventBus
from galley.config import BILLING_ENV_VARS, ConfigError, child_env, validate
from galley.db import Database
from galley.services.agent import AgentService


@pytest.fixture
def agents(config):
    return AgentService(config, Database(config.db_path), EventBus())


def _options(agents, **row):
    base = {"worktree_path": "/tmp", "claude_session_id": None}
    return agents._options({**base, **row})


# -- the billing footgun ---------------------------------------------------


def test_an_inherited_api_key_never_reaches_the_child(monkeypatch, agents) -> None:
    """An exported key silently bills the API instead of the subscription."""
    for var in BILLING_ENV_VARS:
        monkeypatch.setenv(var, "sk-should-not-travel")
    env = _options(agents).env
    assert not any(v in env for v in BILLING_ENV_VARS)
    assert "PATH" in env, "the rest of the environment is passed through"


def test_startup_refuses_when_a_billing_key_is_in_the_environment(monkeypatch, config) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-oops")
    with pytest.raises(ConfigError) as exc:
        validate(config)
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert "subscription" in str(exc.value)


def test_child_env_does_not_mutate_the_parent(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-still-mine")
    child_env()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-still-mine"


# -- the options match the SDK's own types --------------------------------


def test_the_agent_is_scoped_to_its_worktree_with_the_code_mirror(agents, config) -> None:
    opts = _options(agents, worktree_path="/somewhere/wt")
    assert opts.cwd == "/somewhere/wt"
    assert opts.add_dirs == [str(config.paths.code_mirror)]
    assert opts.permission_mode == "acceptEdits"


def test_the_agent_gets_no_extra_tool_surface(agents) -> None:
    """The only AI part is the SDK writing a patch.

    Claude works with its ordinary file tools inside its own worktree. There is
    no Galley tool server, so there is nothing it can reach that is not a file
    on the branch it was given.
    """
    assert not _options(agents).mcp_servers


def test_the_system_prompt_matches_the_sdk_preset_shape(agents) -> None:
    prompt = _options(agents).system_prompt
    assert set(prompt) <= set(SystemPromptPreset.__annotations__)
    assert prompt["type"] == "preset" and prompt["preset"] == "claude_code"


def test_the_agent_is_told_what_it_may_not_do(agents) -> None:
    appended = _options(agents).system_prompt["append"]
    assert "You never merge" in appended
    assert "You never publish" in appended
    assert "never invent a number" in appended


def test_a_follow_up_turn_resumes_the_stored_claude_session(agents) -> None:
    assert _options(agents).resume is None
    assert _options(agents, claude_session_id="abc-123").resume == "abc-123"


# -- turning SDK messages into log events ---------------------------------


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TextBlock(_Block): ...
class ToolUseBlock(_Block): ...
class ToolResultBlock(_Block): ...
class AssistantMessage(_Block): ...
class ResultMessage(_Block): ...
class SystemMessage(_Block): ...


def test_text_and_tool_calls_become_separate_events() -> None:
    from galley.services.agent import normalise

    msg = AssistantMessage(
        content=[
            TextBlock(text="I will look at the ablation."),
            ToolUseBlock(id="t1", name="Read", input={"file_path": "main.tex"}),
        ]
    )
    kinds = [e["kind"] for e in normalise(msg)]
    assert kinds == ["text", "tool_use"]


def test_a_huge_tool_input_is_shortened_for_the_log() -> None:
    from galley.services.agent import normalise

    msg = AssistantMessage(
        content=[ToolUseBlock(id="t", name="Write", input={"content": "x" * 20000})]
    )
    shown = normalise(msg)[0]["payload"]["input"]["content"]
    assert len(shown) < 20000
    assert "more characters" in shown


def test_the_claude_session_id_is_picked_up_from_the_system_message() -> None:
    from galley.services.agent import normalise

    events = normalise(SystemMessage(subtype="init", data={"session_id": "s-9", "model": "m"}))
    assert events[0]["payload"]["claude_session_id"] == "s-9"


def test_usage_is_surfaced_so_you_can_see_what_a_session_cost() -> None:
    from galley.services.agent import normalise

    events = normalise(
        ResultMessage(duration_ms=1200, num_turns=3, is_error=False, usage={}, total_cost_usd=0.02)
    )
    assert events[0]["payload"]["total_cost_usd"] == 0.02


# -- the codebase is mounted to be read, not changed -----------------------


@pytest.fixture
def guard(tmp_path):
    from galley.services.agent import writes_only_inside

    wt = tmp_path / "worktree"
    (wt / "sections").mkdir(parents=True)
    return writes_only_inside(wt), wt


async def test_an_edit_inside_the_worktree_is_allowed(guard) -> None:
    can_use_tool, wt = guard
    result = await can_use_tool("Edit", {"file_path": str(wt / "sections/intro.tex")}, None)
    assert result.behavior == "allow"


async def test_a_relative_path_resolves_against_the_worktree(guard) -> None:
    can_use_tool, _ = guard
    result = await can_use_tool("Write", {"file_path": "sections/new.tex"}, None)
    assert result.behavior == "allow"


@pytest.mark.parametrize("tool", ["Edit", "Write", "NotebookEdit", "MultiEdit"])
async def test_writing_into_the_codebase_is_refused(guard, tmp_path, tool: str) -> None:
    """add_dirs grants access, not read-only access, and the codebase mounted
    here is a live working tree."""
    can_use_tool, _ = guard
    result = await can_use_tool(tool, {"file_path": str(tmp_path / "code/train.py")}, None)
    assert result.behavior == "deny"
    assert "outside this session's worktree" in result.message


async def test_traversing_out_of_the_worktree_is_refused(guard) -> None:
    can_use_tool, _ = guard
    result = await can_use_tool("Edit", {"file_path": "../../../etc/passwd"}, None)
    assert result.behavior == "deny"


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "NotebookRead"])
async def test_reading_anywhere_is_still_allowed(guard, tmp_path, tool: str) -> None:
    can_use_tool, _ = guard
    result = await can_use_tool(tool, {"file_path": str(tmp_path / "code/train.py")}, None)
    assert result.behavior == "allow"


@pytest.mark.parametrize("tool", ["Bash", "WebFetch", "Task", "SlashCommand"])
async def test_everything_else_is_denied(guard, tool: str) -> None:
    """A shell would undo every other line of the guard: `echo x > ../file` is
    a write by another name."""
    can_use_tool, _ = guard
    result = await can_use_tool(tool, {"command": "echo hi > ../../code/train.py"}, None)
    assert result.behavior == "deny"
    assert "not available in Galley" in result.message


def test_galley_commits_the_branch_because_the_agent_has_no_shell(paper_repo, git_helper) -> None:
    from galley.services.agent import _commit_worktree
    from galley.services.worktree import create

    tree = create(paper_repo, "commit-check", "main")
    assert _commit_worktree(tree.path)["committed"] is False  # nothing changed yet

    (tree.path / "main.tex").write_text("A new sentence.\n")
    result = _commit_worktree(tree.path)
    assert result["committed"] is True
    assert "Claude: proposed changes" in git_helper(tree.path, "log", "-1", "--format=%s")
    assert git_helper(tree.path, "status", "--porcelain") == ""


# -- which model, and how its words reach the screen -----------------------


class StreamEvent(_Block): ...
class RateLimitEvent(_Block): ...


def test_the_agent_is_opus_unless_the_config_says_otherwise(agents) -> None:
    opts = _options(agents)
    assert opts.model == "opus"
    assert opts.fallback_model is None


def test_the_configured_model_and_caps_reach_the_sdk(config, tmp_path) -> None:
    """Pinned against the SDK's own dataclass: a renamed field fails here."""
    import dataclasses

    from galley.config import Agent

    tuned = dataclasses.replace(
        config,
        agent=Agent(model="claude-sonnet-5", fallback_model="haiku", max_turns=7, max_budget_usd=1.5),
    )
    service = AgentService(tuned, Database(tuned.db_path), EventBus())
    opts = _options(service)
    assert opts.model == "claude-sonnet-5"
    assert opts.fallback_model == "haiku"
    assert opts.max_turns == 7
    assert opts.max_budget_usd == 1.5


def test_partial_messages_are_asked_for_so_text_arrives_as_it_is_written(agents) -> None:
    assert _options(agents).include_partial_messages is True


def test_a_text_delta_becomes_one_live_fragment() -> None:
    from galley.services.agent import normalise

    events = normalise(
        StreamEvent(
            event={
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "The ablation "},
            }
        )
    )
    assert events == [
        {"kind": "delta", "payload": {"index": 1, "role": "text", "text": "The ablation "}}
    ]


def test_a_thinking_delta_is_kept_apart_from_the_reply() -> None:
    from galley.services.agent import normalise

    events = normalise(
        StreamEvent(
            event={
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Check §4.1 first"},
            }
        )
    )
    assert events[0]["payload"]["role"] == "thinking"
    assert events[0]["payload"]["text"] == "Check §4.1 first"


@pytest.mark.parametrize(
    "event",
    [
        {"type": "message_start", "message": {}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta"}},
    ],
)
def test_the_rest_of_the_stream_is_dropped(event: dict) -> None:
    """Everything else in the stream is already carried by the finished
    message, and showing it twice is the one thing a live log must not do."""
    from galley.services.agent import normalise

    assert normalise(StreamEvent(event=event)) == []


async def test_a_live_fragment_is_shown_and_never_written_down(agents) -> None:
    """The invariant behind the whole streaming path.

    A delta arrives again, whole, a moment later as an ordinary `text` event.
    Keeping both would store every paragraph a hundred times over and replay
    the half-written copies at every reconnect.
    """
    async with agents.bus.subscribe("session:s1") as queue:
        await agents._emit_live("s1", "delta", {"index": 0, "role": "text", "text": "hi"})
        published = await queue.get()

    assert published["id"] is None
    assert published["payload"]["text"] == "hi"
    assert agents.db.session_events("s1") == []


def test_an_ephemeral_event_gets_no_sse_id() -> None:
    """The browser resumes from the last id it saw; a fragment is in no log to
    resume from, so pointing it there would lose everything after it."""
    from galley.routes.sessions import _sse

    assert "id:" not in _sse({"id": None, "kind": "delta", "payload": {}})
    assert _sse({"id": 12, "kind": "text", "payload": {}}).startswith("id: 12\n")


def test_a_rate_limit_becomes_something_the_log_can_say() -> None:
    """The one failure that looks like Galley breaking and is not: the agent
    simply stops, and the reason is a window that resets on an unseen clock."""
    from galley.services.agent import normalise

    info = _Block(
        status="allowed_warning", rate_limit_type="five_hour", utilization=0.87, resets_at=1757500000
    )
    events = normalise(RateLimitEvent(rate_limit_info=info))
    assert events[0]["kind"] == "rate_limit"
    assert events[0]["payload"] == {
        "status": "allowed_warning",
        "window": "five_hour",
        "used": 0.87,
        "resets_at": 1757500000,
    }
