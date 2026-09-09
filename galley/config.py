"""Load and validate `galley.local.toml`.

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
    code_mirror: Path
    state_dir: Path
    # Pulled artifacts are not rebuildable, so they get their own home. On a
    # host whose scratch is reaped by modification time, that home must not be
    # the same place as the run directories.
    artifacts_dir: Path


@dataclass(frozen=True)
class Cluster:
    backend: str = "gpuq"
    scratch: Path = Path("/scratch/galley")
    submit_flags: list[str] = field(default_factory=lambda: ["--queue"])
    max_job_hours: float = 12.0
    tmux_prefix: str = "galley"


@dataclass(frozen=True)
class Paper:
    main_branch: str = "master"
    overleaf_remote: str = "origin"
    overleaf_branch: str = "master"
    main_tex: str = "main.tex"


@dataclass(frozen=True)
class Server:
    bind: str = "127.0.0.1"
    port: int = 8124


@dataclass(frozen=True)
class Limits:
    max_concurrent_sessions: int = 2
    max_queued_jobs: int = 1


@dataclass(frozen=True)
class Config:
    paths: Paths
    cluster: Cluster
    paper: Paper
    server: Server
    limits: Limits
    source: Path

    @property
    def db_path(self) -> Path:
        return self.paths.state_dir / "galley.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.paths.artifacts_dir

    @property
    def worktrees_dir(self) -> Path:
        return self.paths.paper_repo / ".worktrees"


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

    def _path(section: dict, key: str, default: str | None = None) -> Path:
        value = section.get(key, default)
        if value is None:
            raise ConfigError(f"{path}: [{key}] is required")
        p = Path(str(value)).expanduser()
        return p if p.is_absolute() else (root / p).resolve()

    paths_raw = raw.get("paths", {})
    paths = Paths(
        paper_repo=_path(paths_raw, "paper_repo"),
        code_mirror=_path(paths_raw, "code_mirror"),
        state_dir=_path(paths_raw, "state_dir", str(root / ".galley")),
        artifacts_dir=_path(
            paths_raw, "artifacts_dir", str(root / ".galley" / "artifacts-cache")
        ),
    )

    c = raw.get("cluster", {})
    cluster = Cluster(
        backend=c.get("backend", "gpuq"),
        scratch=_path(c, "scratch", "/scratch/galley"),
        submit_flags=list(c.get("submit_flags", ["--queue"])),
        max_job_hours=float(c.get("max_job_hours", 12.0)),
        tmux_prefix=c.get("tmux_prefix", "galley"),
    )

    p = raw.get("paper", {})
    paper = Paper(
        main_branch=p.get("main_branch", "master"),
        overleaf_remote=p.get("overleaf_remote", "origin"),
        overleaf_branch=p.get("overleaf_branch", "master"),
        main_tex=p.get("main_tex", "main.tex"),
    )

    s = raw.get("server", {})
    server = Server(bind=s.get("bind", "127.0.0.1"), port=int(s.get("port", 8124)))

    limit = raw.get("limits", {})
    limits = Limits(
        max_concurrent_sessions=int(limit.get("max_concurrent_sessions", 2)),
        max_queued_jobs=int(limit.get("max_queued_jobs", 1)),
    )

    cfg = Config(paths, cluster, paper, server, limits, path)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    """Fail loudly and early, rather than halfway through a session."""
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
    if not cfg.paths.code_mirror.is_dir():
        raise ConfigError(f"code_mirror {cfg.paths.code_mirror} does not exist")
    if cfg.server.bind not in ("127.0.0.1", "localhost", "::1"):
        # Not fatal, but the MCP surface on this port can write files and spawn
        # agents, so it should never be a surprise.
        import warnings

        warnings.warn(
            f"galley is bound to {cfg.server.bind}, not loopback: the /mcp tool "
            "surface is reachable from the network.",
            stacklevel=2,
        )

    cfg.paths.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)


def child_env() -> dict[str, str]:
    """The environment an agent is spawned with: yours, minus the billing keys."""
    env = os.environ.copy()
    for var in BILLING_ENV_VARS:
        env.pop(var, None)
    return env
