from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# Galley refuses to start with a billing key in the environment. Tests are not
# an exception to that, but they must not depend on the developer's shell.
for _var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_var, None)

PAPER = """\\documentclass{article}
\\begin{document}
We evaluate on three benchmarks (\\S4.1).
The model improves over the baseline by a large margin across all settings.
We ablate the retrieval component in Table~\\ref{tab:ablation}.
\\end{document}
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def paper_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "paper"
    repo.mkdir(parents=True)
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "test@localhost")
    _git(repo, "config", "user.name", "Test")
    (repo / "main.tex").write_text(PAPER)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    return repo


@pytest.fixture
def code_mirror(tmp_path: Path) -> Path:
    repo = tmp_path / "code"
    repo.mkdir()
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "test@localhost")
    _git(repo, "config", "user.name", "Test")
    (repo / "train.py").write_text("print('hello')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    return repo


@pytest.fixture
def config(tmp_path: Path, paper_repo: Path, code_mirror: Path):
    from galley.config import load

    path = tmp_path / "galley.local.toml"
    path.write_text(
        f"""
[paths]
paper_repo    = "{paper_repo}"
code_mirror   = "{code_mirror}"
state_dir     = "{tmp_path / 'state'}"

[paper]
main_branch     = "main"
overleaf_remote = "origin"
overleaf_branch = "master"

[server]
bind = "127.0.0.1"
port = 8124
"""
    )
    return load(path)


@pytest.fixture
def client(config):
    from fastapi.testclient import TestClient

    from galley.app import create_app

    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def git_helper():
    return _git
