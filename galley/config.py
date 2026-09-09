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


def find_config(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        candidate = d / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"no {CONFIG_NAME} found in {here} or any parent. "
        f"Copy galley.example.toml to {CONFIG_NAME} and edit it."
    )


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

    cfg = Config(paths, paper, server, limits, path, usage)
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
    """The environment an agent is spawned with: yours, minus the billing keys."""
    env = os.environ.copy()
    for var in BILLING_ENV_VARS:
        env.pop(var, None)
    return env
