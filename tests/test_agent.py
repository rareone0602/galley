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
