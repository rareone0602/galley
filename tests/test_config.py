"""What Galley does when the project has only itself.

Galley was built against one paper that happens to have a companion codebase,
an Overleaf remote and a LaTeX root. None of those is required, and a project
without them must still work — with the parts that cannot apply saying so,
rather than failing when pressed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from galley.app import create_app
from galley.bus import EventBus
from galley.config import ConfigError, load
from galley.db import Database
from galley.services.agent import CODEBASE_RULE, AgentService, system_appendix


@pytest.fixture
def bare_client(bare_config):
    with TestClient(create_app(bare_config)) as c:
        yield c


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


# -- the loader --------------------------------------------------------------


def test_a_project_needs_nothing_but_itself(bare_config) -> None:
    assert bare_config.paths.code_mirror is None
    assert bare_config.paths.paper_repo.is_dir()
    assert bare_config.paths.state_dir.is_dir()


def test_a_paper_repo_is_the_one_thing_you_must_name(tmp_path: Path) -> None:
    path = _write(tmp_path / "galley.local.toml", "[paths]\nstate_dir = 'state'\n")
    with pytest.raises(ConfigError, match="paper_repo is required"):
        load(path)


def test_a_codebase_that_is_named_but_missing_is_still_an_error(
    tmp_path: Path, paper_repo: Path
) -> None:
    """Absent is fine; named and wrong is a typo you want to hear about."""
    path = _write(
        tmp_path / "typo" / "galley.local.toml",
        f"[paths]\npaper_repo = '{paper_repo}'\ncode_mirror = '{tmp_path / 'nope'}'\n",
    )
    with pytest.raises(ConfigError, match="does not exist"):
        load(path)


def test_the_old_overleaf_key_names_still_load(tmp_path: Path, paper_repo: Path) -> None:
    """An existing galley.local.toml must not stop working on an upgrade."""
    path = _write(
        tmp_path / "old" / "galley.local.toml",
        f"[paths]\npaper_repo = '{paper_repo}'\n"
        "[paper]\noverleaf_remote = 'upstream'\noverleaf_branch = 'trunk'\n",
    )
    cfg = load(path)
    assert (cfg.paper.publish_remote, cfg.paper.publish_branch) == ("upstream", "trunk")


def test_the_new_names_win_when_both_are_present(tmp_path: Path, paper_repo: Path) -> None:
    path = _write(
        tmp_path / "both" / "galley.local.toml",
        f"[paths]\npaper_repo = '{paper_repo}'\n"
        "[paper]\noverleaf_remote = 'old'\npublish_remote = 'new'\n",
    )
    assert load(path).paper.publish_remote == "new"


def test_whether_the_project_builds_a_pdf_is_read_from_the_disk(
    config, bare_config
) -> None:
    assert config.builds_a_pdf and config.main_tex_path.is_file()
    assert not bare_config.builds_a_pdf


# -- what the agent is told --------------------------------------------------


def test_an_agent_with_no_codebase_is_not_told_there_is_one(bare_config, config) -> None:
    """Naming a directory that is not mounted is an instruction it cannot
    follow, and an invitation to imagine the contents."""
    assert CODEBASE_RULE in system_appendix(config)
    assert CODEBASE_RULE not in system_appendix(bare_config)
    assert "never merge" in system_appendix(bare_config).lower()


def test_an_agent_with_no_codebase_is_granted_no_extra_directory(bare_config) -> None:
    """`add_dirs` grants access, so an absent codebase must not become a path
    anyway — least of all the string "None"."""
    agents = AgentService(bare_config, Database(bare_config.db_path), EventBus())
    options = agents._options({"worktree_path": "/tmp", "claude_session_id": None})
    assert options.add_dirs == []


# -- what the routes say -----------------------------------------------------


def test_the_config_route_reports_the_absences(bare_client) -> None:
    body = bare_client.get("/api/config").json()
    assert body["code_mirror"] is None
    assert body["builds_pdf"] is False
    assert body["publish"] == "origin/master"


def test_compiling_refuses_before_it_starts_rather_than_after(bare_client) -> None:
    """latexmk on a file that is not there fails slowly and about the wrong
    thing. The refusal names the setting you would change."""
    response = bare_client.post("/api/compile", json={})
    assert response.status_code == 400
    assert "nothing-here.tex" in response.json()["detail"]
    assert "main_tex" in response.json()["detail"]


def test_the_marked_up_review_refuses_for_the_same_reason(bare_client) -> None:
    response = bare_client.post("/api/review", json={"session_id": "whatever"})
    assert response.status_code == 400
    assert "main_tex" in response.json()["detail"]


def test_everything_that_does_not_need_latex_still_works(bare_client, bare_config) -> None:
    """The general core: the rail, reading a file, writing one back."""
    tree = bare_client.get("/api/tree").json()
    assert any(node["name"] == "main.tex" for node in tree["tree"])

    body = bare_client.get("/api/file", params={"path": "main.tex"}).json()
    assert "three benchmarks" in body["content"]

    written = bare_client.put("/api/files/main.tex", json={"content": "rewritten\n"})
    assert written.status_code == 200
    assert (bare_config.paths.paper_repo / "main.tex").read_text() == "rewritten\n"


def test_the_completion_index_is_empty_rather_than_absent(bare_client) -> None:
    """A project with no .bib and no macros still answers; it just has nothing
    to offer, which the editor renders as no suggestions."""
    body = bare_client.get("/api/project/index").json()
    assert body["citations"] == [] and body["macros"] == []
