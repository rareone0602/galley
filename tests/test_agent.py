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
    assert opts.add_dirs == [str(d) for d in config.paths.readable]
    # "acceptEdits" would approve a write before the guard is consulted, and
    # the guard is the only thing keeping the agent out of the codebase.
    assert opts.permission_mode == "default"


def test_the_agent_gets_no_extra_tool_surface(agents) -> None:
    """The only AI part is the SDK writing a patch.

    Claude works with its ordinary file tools inside its own worktree. There is
    no Galley tool server, so there is nothing it can reach that is not a file
    on the branch it was given.
    """
    assert not _options(agents).mcp_servers


def test_the_model_is_offered_exactly_what_the_guard_allows(agents, tmp_path) -> None:
    """Left unset, the CLI's default set hides Grep and Glob behind a loader
    the guard refuses, and offers a shell, cron and messaging that it refuses
    too. One session searched a paper with Read alone that way: sixteen whole
    files to move one line."""
    from galley.services.agent import decide

    offered = _options(agents).tools
    assert isinstance(offered, list)
    assert {"Read", "Grep", "Glob", "Edit", "Write"} <= set(offered)
    for refused in ("Bash", "ToolSearch", "SlashCommand"):
        assert refused not in offered
    for tool in offered:
        assert decide(tool, {"file_path": str(tmp_path / "x")}, tmp_path, 2, None).allowed, tool


def test_the_web_is_offered_and_allowed_together_or_neither(tmp_path) -> None:
    """The list says what is offered and the guard says what is allowed. One
    switch moves both, so a tool can never be dangled in front of the model
    only to be refused when it reaches for it."""
    from galley.services.agent import decide, tool_surface

    for tool in ("WebSearch", "WebFetch"):
        assert tool in tool_surface(web=True)
        assert decide(tool, {}, tmp_path, 2, None, web=True).allowed

        assert tool not in tool_surface(web=False)
        verdict = decide(tool, {}, tmp_path, 2, None, web=False)
        assert not verdict.allowed
        assert "[agent] web is false" in verdict.reason


async def test_a_turn_that_breaks_in_galley_still_shuts_the_cli_down(
    agents, monkeypatch
) -> None:
    """The CLI must not outlive the turn when Galley is what went wrong.

    An `async for` does not close its iterator when its body raises, so a bad
    event or a database that will not write left the SDK's generator suspended
    for the garbage collector to finalise at some later moment — with the CLI
    subprocess running and spending in the meantime. The assertion is made the
    instant the turn returns, because "eventually" is exactly the bug.

    Stop is a different path and was never this: cancelling raises inside the
    SDK's own `await`, so its teardown runs there. That is why this is tested
    through a failure rather than through the button.
    """
    import asyncio

    from claude_agent_sdk import SystemMessage

    closed = asyncio.Event()

    async def fake_query(prompt, options):
        try:
            for _ in range(100):
                yield SystemMessage(subtype="init", data={"session_id": "c-1", "model": "m"})
        finally:
            # The real teardown awaits: it closes the transport and waits for
            # the CLI process to exit.
            await asyncio.sleep(0)
            closed.set()

    def explode(_message):
        raise RuntimeError("something in Galley could not handle that")

    monkeypatch.setattr("galley.services.agent.query", fake_query)
    monkeypatch.setattr("galley.services.agent.normalise", explode)

    row = agents.create("breaks halfway")
    agents.start(row["id"])
    await asyncio.wait_for(agents._tasks[row["id"]], 5)

    assert closed.is_set(), "the SDK's teardown was left to the garbage collector"
    assert agents.db.get_session(row["id"])["status"] == "error"


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


def test_a_compaction_boundary_says_what_was_folded() -> None:
    """The CLI answers `/compact` with a boundary: how big the conversation
    was, and how big the summary that replaced it is."""
    from galley.services.agent import normalise

    events = normalise(
        SystemMessage(
            subtype="compact_boundary",
            data={
                "compact_metadata": {
                    "trigger": "manual",
                    "pre_tokens": 26222,
                    "post_tokens": 1871,
                    "duration_ms": 16575,
                }
            },
        )
    )
    assert events == [
        {
            "kind": "compact",
            "payload": {
                "trigger": "manual",
                "pre_tokens": 26222,
                "post_tokens": 1871,
                "duration_ms": 16575,
            },
        }
    ]


@pytest.mark.parametrize("subtype", ["status", "thinking_tokens"])
def test_the_clis_bookkeeping_about_itself_stays_out_of_the_log(subtype: str) -> None:
    """One real session stored 246 of these — a 'requesting' before every
    call, a running count of thinking tokens — and replayed them on every
    reconnect. None of it is a thing a reader of the conversation wants."""
    from galley.services.agent import normalise

    assert normalise(SystemMessage(subtype=subtype, data={"status": "requesting"})) == []


def test_the_context_is_the_sum_of_what_the_last_call_read() -> None:
    """Cached or not, it is all context, and all of it is paid for again on
    the next call. The number is what says whether compacting is worth it."""
    from galley.services.agent import context_size

    assert context_size(
        {"input_tokens": 7, "cache_read_input_tokens": 25857, "cache_creation_input_tokens": 260}
    ) == 26124
    assert context_size({"output_tokens": 98}) is None
    assert context_size(None) is None


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


@pytest.mark.parametrize("tool", ["Bash", "KillShell", "SlashCommand"])
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
        {
            "kind": "delta",
            "payload": {"index": 1, "role": "text", "text": "The ablation ", "agent": None},
        }
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


# -- thinking, effort, and the environment the child gets ------------------


def test_thinking_is_adaptive_and_the_effort_is_the_highest_that_is_not_max(agents) -> None:
    """Prose in a paper is read by reviewers paid to disagree with it."""
    opts = _options(agents)
    assert opts.thinking == {"type": "adaptive"}
    assert opts.effort == "xhigh"


@pytest.mark.parametrize(
    "setting, shape",
    [
        ("adaptive", {"type": "adaptive"}),
        ("off", {"type": "disabled"}),
        (12000, {"type": "enabled", "budget_tokens": 12000}),
    ],
)
def test_every_thinking_setting_has_a_shape_the_sdk_knows(config, setting, shape) -> None:
    import dataclasses

    from galley.config import Agent

    tuned = dataclasses.replace(config, agent=Agent(thinking=setting))
    assert tuned.agent.thinking_config == shape


def test_another_claude_session_does_not_travel_into_this_one(monkeypatch) -> None:
    """Start Galley from inside Claude Code and the agent would otherwise
    inherit that session's id, its message socket and its effort level."""
    from galley.config import SESSION_ENV_VARS

    for var in SESSION_ENV_VARS:
        monkeypatch.setenv(var, "from-the-parent-session")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/somewhere/deliberate")
    env = child_env()
    assert not any(v in env for v in SESSION_ENV_VARS)
    # A setting, not an identity: relocating the config must keep working.
    assert env["CLAUDE_CONFIG_DIR"] == "/somewhere/deliberate"


def test_only_the_projects_settings_are_loaded(agents) -> None:
    """Your own `~/.claude/settings.json` carries the permission mode you use
    for your own Claude Code, and it would apply here too."""
    assert _options(agents).setting_sources == ["project"]


# -- fanning out ----------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    """The guard as the CLI really reaches it: a PreToolUse hook, which is the
    only thing told about every call and about who made it."""
    from galley.services.agent import tool_guard

    wt = tmp_path / "worktree"
    wt.mkdir(parents=True)

    def guard_at(depth: int):
        hook = tool_guard(wt, depth)["PreToolUse"][0].hooks[0]

        async def ask(tool: str, args: dict, agent_type: str | None = None):
            out = await hook(
                {"tool_name": tool, "tool_input": args, "agent_type": agent_type}, "t1", {}
            )
            return out["hookSpecificOutput"]

        return ask

    return guard_at


@pytest.mark.parametrize("tool", ["Agent", "Task"])
async def test_working_alone_is_a_setting(tree, tool: str) -> None:
    out = await tree(0)(tool, {"subagent_type": "helper"})
    assert out["permissionDecision"] == "deny"
    assert "fan_out_depth is 0" in out["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["Agent", "Task"])
async def test_the_agent_you_asked_may_delegate(tree, tool: str) -> None:
    """Both names are the same act; which one the CLI uses is its business."""
    out = await tree(1)(tool, {"subagent_type": "helper"})
    assert out["permissionDecision"] == "allow"


async def test_at_depth_one_a_helper_may_not_delegate_again(tree) -> None:
    out = await tree(1)("Agent", {"subagent_type": "reader"}, "helper")
    assert out["permissionDecision"] == "deny"


async def test_at_depth_two_a_helper_may_start_readers_and_nothing_else(tree) -> None:
    """The ceiling, and the thing that actually enforces it: the hook is told
    which kind of agent is asking, so nobody has to be trusted about it."""
    guard = tree(2)
    assert (await guard("Agent", {"subagent_type": "reader"}, "helper"))[
        "permissionDecision"
    ] == "allow"
    refused = await guard("Agent", {"subagent_type": "helper"}, "helper")
    assert refused["permissionDecision"] == "deny"
    assert "may not start another agent" in refused["permissionDecisionReason"]


async def test_a_reader_starts_nothing_at_all(tree) -> None:
    for wanted in ("reader", "helper", "general-purpose"):
        out = await tree(2)("Agent", {"subagent_type": wanted}, "reader")
        assert out["permissionDecision"] == "deny", wanted


async def test_an_unnamed_kind_from_inside_a_helper_is_refused(tree) -> None:
    """A builtin agent type would otherwise be a third tier by another name."""
    out = await tree(2)("Agent", {"subagent_type": "general-purpose"}, "helper")
    assert out["permissionDecision"] == "deny"


async def test_the_hook_is_what_the_options_carry(agents) -> None:
    """`can_use_tool` is only consulted for calls that would otherwise prompt,
    and an `Agent` spawn never does. A helper started a second helper that way
    with the rule forbidding it sitting right there, unconsulted."""
    hooks = _options(agents).hooks
    assert list(hooks) == ["PreToolUse"]
    assert hooks["PreToolUse"][0].matcher is None, "every tool, not a subset"


async def test_both_enforcers_answer_from_the_same_rule(tmp_path) -> None:
    from galley.services.agent import tool_guard, writes_only_inside

    wt = tmp_path / "wt"
    wt.mkdir()
    hook = tool_guard(wt, 2)["PreToolUse"][0].hooks[0]
    callback = writes_only_inside(wt, 2)

    outside = {"file_path": str(tmp_path / "code" / "train.py")}
    from_hook = (
        await hook({"tool_name": "Edit", "tool_input": outside, "agent_type": None}, "t", {})
    )["hookSpecificOutput"]
    from_callback = await callback("Edit", outside, None)
    assert from_hook["permissionDecision"] == "deny"
    assert from_callback.behavior == "deny"
    assert from_hook["permissionDecisionReason"] == from_callback.message


def test_a_reader_cannot_ask_to_delegate_because_it_has_no_such_tool(config) -> None:
    from galley.services.agent import HELPER, READER, SPAWNING_TOOLS, helpers

    made = helpers(config)
    assert not SPAWNING_TOOLS & set(made[READER].tools)
    assert SPAWNING_TOOLS & set(made[HELPER].tools)
    # And a reader writes nothing.
    from galley.services.agent import WRITING_TOOLS

    assert not WRITING_TOOLS & set(made[READER].tools)


def test_helpers_are_only_defined_when_they_are_allowed(config) -> None:
    import dataclasses

    from galley.config import Agent

    alone = dataclasses.replace(config, agent=Agent(fan_out_depth=0))
    service = AgentService(alone, Database(alone.db_path), EventBus())
    opts = _options(service)
    assert opts.agents is None
    assert opts.forward_subagent_text is False


def test_what_a_helper_says_is_forwarded_and_labelled(agents) -> None:
    """Without this a helper is a black box: you see it start and finish."""
    from galley.services.agent import HELPER, READER, normalise

    opts = _options(agents)
    assert opts.forward_subagent_text is True
    assert set(opts.agents) == {HELPER, READER}

    events = normalise(
        AssistantMessage(
            content=[TextBlock(text="Six sections mention it.")], parent_tool_use_id="call-9"
        )
    )
    assert events[0]["payload"]["agent"] == "call-9"


# -- skills ---------------------------------------------------------------


def test_the_skills_directory_is_passed_as_a_local_plugin(agents, config) -> None:
    """Not `.claude/skills` in the paper: the paper is not Galley's to write in."""
    opts = _options(agents)
    assert opts.plugins == [{"type": "local", "path": str(config.paths.skills_dir)}]


def test_only_the_workbenchs_own_skills_are_offered(agents) -> None:
    """Claude Code ships its own — `security-review`, `keybindings-help` — and
    a paper workbench has no use for them. Every one offered costs attention."""
    from galley.services.agent import skills_in

    offered = _options(agents).skills
    assert offered == skills_in(agents.cfg.paths.skills_dir)
    assert all(name.startswith("galley-skills:") for name in offered)
    assert "galley-skills:paper-patch" in offered


def test_a_skill_name_is_read_from_the_manifest_not_assumed(config) -> None:
    """`<plugin>:<skill>` is how the CLI addresses one, and the plugin half
    comes from the manifest — so renaming it must not quietly stop matching."""
    import json

    from galley.services.agent import skills_in

    root = config.paths.skills_dir
    named = json.loads((root / ".claude-plugin" / "plugin.json").read_text())["name"]
    assert all(n.split(":")[0] == named for n in skills_in(root))


def test_the_manifest_is_the_shape_the_cli_accepts(config) -> None:
    """It failed silently the first time: `author` as a string is rejected by
    the CLI's own validator, and a rejected plugin loads no skills at all and
    says nothing about it."""
    import json

    manifest = json.loads(
        (config.paths.skills_dir / ".claude-plugin" / "plugin.json").read_text()
    )
    assert isinstance(manifest.get("name"), str) and manifest["name"]
    assert isinstance(manifest.get("author", {}), dict)


def test_your_own_mcp_servers_are_not_this_agents_business(agents) -> None:
    """Without this they arrive with their tool definitions and their
    instructions — text from elsewhere, in an agent editing a manuscript."""
    assert _options(agents).strict_mcp_config is True


def test_galley_ships_skills_that_a_shell_less_agent_can_follow(config) -> None:
    """The agent has no Bash, so a skill telling it to run a script is a
    skill it cannot follow."""
    root = config.paths.skills_dir
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    found = sorted(p.parent.name for p in root.glob("skills/*/SKILL.md"))
    assert found, "the bundled plugin has no skills in it"
    for name in found:
        body = (root / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert body.startswith("---"), f"{name} has no frontmatter"
        assert "description:" in body.split("---")[1], f"{name} has no description"


def test_skills_can_be_turned_off_or_narrowed(config) -> None:
    import dataclasses

    from galley.config import Agent

    mine = ["galley-skills:paper-patch"]
    for setting, wanted in [
        ("none", []),
        (("paper-patch",), ["paper-patch"]),
        ("all", "all"),
        ("workbench", mine),
    ]:
        tuned = dataclasses.replace(config, agent=Agent(skills=setting))
        assert tuned.agent.skills_wanted(mine) == wanted


def test_a_helpers_bookkeeping_stays_out_of_the_log() -> None:
    """Started, progressing, updated, finished: the tool call and its result
    already say all four, and a log a human reads beats a complete one."""
    from galley.services.agent import SUBAGENT_BOOKKEEPING, normalise

    for kind in SUBAGENT_BOOKKEEPING:
        message = type(kind, (_Block,), {})(uuid="u", session_id="s")
        assert normalise(message) == [], kind
