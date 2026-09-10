"""Load and validate `galley.local.toml`.

A Galley is one git repository plus the things that repository happens to have.
Only the repository is required. A companion codebase, a remote to publish to,
a LaTeX root — each is named here when the project has one, and its absence is
an ordinary state rather than a misconfiguration.

Two things are checked hard at startup, because both fail silently otherwise:
an inherited API key (which bills the API instead of your subscription), and a
paper repo that is not actually a git repository.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "galley.local.toml"

# If either of these is in the parent environment, the Agent SDK's child process
# inherits it and bills per token instead of using the logged-in subscription.
BILLING_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: Variables that describe *another* Claude Code session — the one you started
#: Galley from, if you started it from inside one. The agent here is its own
#: session and must not inherit someone else's identity, its message channel,
#: or its effort setting, which would quietly override the configured one.
#: `CLAUDE_CONFIG_DIR` is deliberately not in this list: that one is a setting,
#: not an identity, and stripping it would break a deliberately relocated
#: configuration.
SESSION_ENV_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
)


class ConfigError(RuntimeError):
    """Raised for a configuration problem the user must fix before starting."""


@dataclass(frozen=True)
class Paths:
    paper_repo: Path
    state_dir: Path
    #: A codebase mounted beside the paper so the agent can read what the
    #: experiments did before it describes them. Most projects have no such
    #: thing, so it is None unless the config names one.
    code_mirror: Path | None = None
    #: A directory of skills the agent may use — operational habits you write
    #: down once and improve as you notice what it keeps getting wrong. Galley
    #: ships one and this points at it unless you name another; it is a local
    #: plugin directory, `<dir>/skills/<name>/SKILL.md`.
    skills_dir: Path | None = None


@dataclass(frozen=True)
class Paper:
    main_branch: str = "master"
    #: The remote you publish to. Overleaf's git bridge is the case this was
    #: built for — one branch, no force-push, a second writer in the web
    #: editor — but any ordinary remote behaves the same way, and a project
    #: with no remote at all is a normal state.
    publish_remote: str = "origin"
    publish_branch: str = "master"
    #: The file latexmk compiles. A project that builds no PDF leaves it
    #: pointing at nothing, and the LaTeX half of Galley reports itself
    #: unavailable rather than failing when pressed.
    main_tex: str = "main.tex"


@dataclass(frozen=True)
class Server:
    bind: str = "127.0.0.1"
    port: int = 8124


@dataclass(frozen=True)
class Limits:
    max_concurrent_sessions: int = 2


@dataclass(frozen=True)
class Agent:
    """Which Claude answers, and the two ways a turn is allowed to end early.

    `model` is an alias rather than a dated identifier on purpose: "opus" keeps
    meaning the current Opus after the next release, and a workbench whose whole
    job is one careful patch at a time wants the most capable model, not the
    cheapest. Write a full id here instead when you need a specific one pinned.

    The caps are off by default. They exist because a session runs on your
    subscription window, and a turn that goes wrong goes wrong slowly.
    """

    model: str = "opus"
    #: Used when `model` is unavailable — rate-limited, or retired. Unset means
    #: the turn fails rather than quietly answering as something else.
    fallback_model: str | None = None
    #: Stop the turn after this many exchanges. None is the CLI's own default.
    max_turns: int | None = None
    #: Stop the turn when it has cost this much. None means no ceiling.
    max_budget_usd: float | None = None
    #: Show the agent's sentences as they are written rather than in bursts at
    #: the end of a block. Costs nothing; it is the same tokens, sooner.
    stream: bool = True
    #: How hard to work before answering, from "low" to "max". Prose in a paper
    #: is read by reviewers who are paid to disagree with it; this is not the
    #: place to save a few seconds.
    effort: str | None = "xhigh"
    #: "adaptive" lets the model decide when to think and for how long, which
    #: is what the current Opus wants. An integer is a fixed token budget for
    #: older models; "off" turns it off.
    thinking: str | int = "adaptive"
    #: How deep the agent may fan out to helpers of its own. 0 keeps it alone;
    #: 1 lets it delegate; 2 lets those helpers delegate once more. See
    #: `services.agent` for why 2 is the ceiling.
    fan_out_depth: int = 2
    #: Which skills the agent may use. "workbench" is the ones in your own
    #: skills directory and nothing else — a paper workbench has no use for
    #: Claude Code's own `security-review` or `keybindings-help`, and every
    #: skill offered costs a line of the agent's attention. "all" is everything
    #: the CLI can find, "none" is nothing, or name them yourself.
    skills: str | tuple[str, ...] = "workbench"

    @property
    def thinking_config(self) -> dict:
        """`thinking` in the shape the SDK's own type asks for."""
        if self.thinking == "off":
            return {"type": "disabled"}
        if isinstance(self.thinking, int):
            return {"type": "enabled", "budget_tokens": self.thinking}
        return {"type": "adaptive"}

    def skills_wanted(self, in_directory: "list[str] | None" = None) -> "str | list[str]":
        """`skills` in the shape the SDK's own type asks for.

        `[]` and `"all"` are the two ends: an empty list hides every skill,
        while "all" offers whatever the CLI can find. "workbench" is the middle
        — only what is in your own skills directory, whose names the caller
        works out and passes in, because this dataclass does not read disks.
        """
        if self.skills == "none":
            return []
        if self.skills == "workbench":
            return list(in_directory or [])
        if isinstance(self.skills, str):
            return self.skills
        return list(self.skills)


@dataclass(frozen=True)
class Usage:
    """Whether to keep a local record of how the workbench is used.

    On by default because it exists to answer questions about your own tool
    that are otherwise guesswork, and because it never leaves this machine —
    there is no reporting endpoint and no third party. It records what you did
    and never what you wrote; `galley usage` prints it and
    `galley usage --forget` deletes it.
    """

    enabled: bool = True


@dataclass(frozen=True)
class Config:
    paths: Paths
    paper: Paper
    server: Server
    limits: Limits
    source: Path
    usage: Usage = Usage()
    agent: Agent = Agent()

    @property
    def db_path(self) -> Path:
        return self.paths.state_dir / "galley.db"

    @property
    def worktrees_dir(self) -> Path:
        return self.paths.paper_repo / ".worktrees"

    @property
    def main_tex_path(self) -> Path:
        return self.paths.paper_repo / self.paper.main_tex

    @property
    def builds_a_pdf(self) -> bool:
        """Whether this project has the LaTeX root it says it has.

        The one owner of that question: compiling, SyncTeX and the completion
        index all only mean something when it is true, and the UI hides them
        rather than offering a button that cannot work.
        """
        return self.main_tex_path.is_file()


#: Names the config file for a Galley started without `--config`. Set by the
#: `--reload` path, because uvicorn's reloader re-imports the app in a fresh
#: process that never saw the command line.
CONFIG_ENV_VAR = "GALLEY_CONFIG"


def find_config(start: Path | None = None) -> Path:
    named = os.environ.get(CONFIG_ENV_VAR)
    if named:
        candidate = Path(named).expanduser().resolve()
        if not candidate.is_file():
            raise ConfigError(f"{CONFIG_ENV_VAR} points at {candidate}, which is not a file")
        return candidate
    here = (start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"no {CONFIG_NAME} found in {here} or any parent. "
        f"Copy galley.example.toml to {CONFIG_NAME} and edit it."
    )


#: What the SDK's `EffortLevel` allows. Pinned here so a typo is caught while
#: reading the config rather than at the first spawn.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

#: Galley's ceiling on how deep the agent may delegate. Two is not arbitrary:
#: `services.agent` bounds the tree by giving the bottom tier no way to spawn
#: at all, and there is no third tier to give that property to.
MAX_FAN_OUT_DEPTH = 2

#: Where Galley's own skills live, unless the config names somewhere else.
BUNDLED_SKILLS = Path(__file__).resolve().parent.parent / "galley-skills"


def _effort(value: object, source: Path) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "default"):
        return None
    if text not in EFFORT_LEVELS:
        raise ConfigError(
            f"{source}: [agent] effort must be one of {', '.join(EFFORT_LEVELS)}, not {text!r}"
        )
    return text


def _thinking(value: object, source: Path) -> str | int:
    """Either "adaptive", "off", or a fixed token budget."""
    if value is None or value is True:
        return "adaptive"
    if value is False:
        return "off"
    if isinstance(value, int):
        if value < 1:
            raise ConfigError(
                f"{source}: [agent] thinking = {value} would mean no thinking; write 'off'"
            )
        return value
    text = str(value).strip().lower()
    if text in ("adaptive", "on", ""):
        return "adaptive"
    if text in ("off", "disabled", "none"):
        return "off"
    raise ConfigError(
        f"{source}: [agent] thinking must be 'adaptive', 'off', or a token budget, not {text!r}"
    )


def _depth(value: object, source: Path) -> int:
    if value is None:
        return MAX_FAN_OUT_DEPTH
    if value is True:
        return MAX_FAN_OUT_DEPTH
    if value is False:
        return 0
    depth = int(value)
    if not 0 <= depth <= MAX_FAN_OUT_DEPTH:
        raise ConfigError(
            f"{source}: [agent] fan_out_depth must be between 0 and "
            f"{MAX_FAN_OUT_DEPTH}, not {depth}. Galley bounds the tree by "
            "giving the bottom tier no way to spawn, and there is no tier "
            "below the second to give that property to."
        )
    return depth


def _skills(value: object) -> str | tuple[str, ...]:
    # A bare true/false means on or off, and "on" is the workbench's own —
    # everything the CLI can find is a deliberate choice, spelled "all".
    if value is None or value is True:
        return "workbench"
    if value is False:
        return "none"
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip().lower()
    return text if text in ("all", "none", "workbench") else (text,)


def _text(value: object) -> str | None:
    """A setting that is either a real string or genuinely absent."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _count(value: object, key: str, source: Path) -> int | None:
    """A positive whole number, or nothing. Zero would mean "never answer"."""
    if value is None:
        return None
    number = int(value)
    if number < 1:
        raise ConfigError(f"{source}: [agent] {key} must be at least 1, not {number}")
    return number


def _amount(value: object, key: str, source: Path) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number <= 0:
        raise ConfigError(f"{source}: [agent] {key} must be more than 0, not {number:g}")
    return number


def load(path: Path | None = None) -> Config:
    path = (path or find_config()).resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.parent

    def _path(section: dict, key: str) -> Path | None:
        """A path from the config, relative ones taken from beside the file."""
        value = section.get(key)
        if value is None:
            return None
        resolved = Path(str(value)).expanduser()
        return resolved if resolved.is_absolute() else (root / resolved).resolve()

    def _required(section: dict, key: str) -> Path:
        value = _path(section, key)
        if value is None:
            raise ConfigError(f"{path}: [paths] {key} is required")
        return value

    paths_raw = raw.get("paths", {})
    paths = Paths(
        paper_repo=_required(paths_raw, "paper_repo"),
        state_dir=_path(paths_raw, "state_dir") or (root / ".galley"),
        code_mirror=_path(paths_raw, "code_mirror"),
        # Galley's own, unless the config points elsewhere. Absent if neither
        # exists, which is an ordinary state: skills are a habit, not a
        # requirement.
        skills_dir=_path(paths_raw, "skills_dir") or (
            BUNDLED_SKILLS if BUNDLED_SKILLS.is_dir() else None
        ),
    )

    p = raw.get("paper", {})
    paper = Paper(
        main_branch=p.get("main_branch", "master"),
        # `overleaf_*` were the original names, from the one project this was
        # built against, whose remote *is* the Overleaf git bridge. They still
        # load, and `validate` says so once, so an existing config keeps working.
        publish_remote=p.get("publish_remote", p.get("overleaf_remote", "origin")),
        publish_branch=p.get("publish_branch", p.get("overleaf_branch", "master")),
        main_tex=p.get("main_tex", "main.tex"),
    )

    s = raw.get("server", {})
    server = Server(bind=s.get("bind", "127.0.0.1"), port=int(s.get("port", 8124)))

    limit = raw.get("limits", {})
    limits = Limits(max_concurrent_sessions=int(limit.get("max_concurrent_sessions", 2)))

    u = raw.get("usage", {})
    usage = Usage(enabled=bool(u.get("enabled", True)))

    a = raw.get("agent", {})
    agent = Agent(
        model=str(a.get("model") or Agent.model),
        fallback_model=_text(a.get("fallback_model")),
        max_turns=_count(a.get("max_turns"), "max_turns", path),
        max_budget_usd=_amount(a.get("max_budget_usd"), "max_budget_usd", path),
        stream=bool(a.get("stream", True)),
        effort=_effort(a.get("effort", "xhigh"), path),
        thinking=_thinking(a.get("thinking"), path),
        fan_out_depth=_depth(a.get("fan_out_depth"), path),
        skills=_skills(a.get("skills")),
    )

    cfg = Config(paths, paper, server, limits, path, usage, agent)
    validate(cfg, raw_paper=p)
    return cfg


#: Config keys that changed name when Galley stopped assuming Overleaf.
RENAMED_KEYS = {"overleaf_remote": "publish_remote", "overleaf_branch": "publish_branch"}


def validate(cfg: Config, raw_paper: dict | None = None) -> None:
    """Fail loudly and early, rather than halfway through a session."""
    raw_paper = raw_paper or {}
    for old, new in RENAMED_KEYS.items():
        if old in raw_paper:
            print(
                f"galley: [paper] {old} is the old name for {new}; both load, "
                f"but rename it in {cfg.source.name} and this notice goes away."
            )

    leaked = [v for v in BILLING_ENV_VARS if os.environ.get(v)]
    if leaked:
        raise ConfigError(
            f"{', '.join(leaked)} is set in this environment. Galley strips it "
            "from the agent's child process, but its presence here means your "
            "shell would bill the API rather than your Claude subscription. "
            "Unset it and start again."
        )

    if not (cfg.paths.paper_repo / ".git").exists():
        raise ConfigError(f"paper_repo {cfg.paths.paper_repo} is not a git repository")
    # Named but absent is a mistake worth stopping for; not named at all is not.
    if cfg.paths.code_mirror is not None and not cfg.paths.code_mirror.is_dir():
        raise ConfigError(f"code_mirror {cfg.paths.code_mirror} does not exist")
    if cfg.paths.skills_dir is not None and not cfg.paths.skills_dir.is_dir():
        raise ConfigError(f"skills_dir {cfg.paths.skills_dir} does not exist")
    if cfg.server.bind not in ("127.0.0.1", "localhost", "::1"):
        # Not fatal, but this port can spawn agents and write files in the
        # paper repo, so it should never be a surprise.
        import warnings

        warnings.warn(
            f"galley is bound to {cfg.server.bind}, not loopback: anyone who "
            "can reach this port can spawn agents and write to the paper repo.",
            stacklevel=2,
        )

    cfg.paths.state_dir.mkdir(parents=True, exist_ok=True)


def child_env() -> dict[str, str]:
    """The environment an agent is spawned with: yours, minus two kinds of thing.

    The billing keys, which would charge the API instead of your subscription.
    And anything naming the Claude Code session Galley itself was started from
    — start Galley from inside one and the agent would otherwise inherit that
    session's id, its message socket and its effort level.
    """
    env = os.environ.copy()
    for var in (*BILLING_ENV_VARS, *SESSION_ENV_VARS):
        env.pop(var, None)
    return env
