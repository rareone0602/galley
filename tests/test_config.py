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


# -- which Claude answers, and when a turn stops ------------------------------


def _agent_config(tmp_path: Path, paper_repo: Path, body: str):
    """A config that is nothing but a paper repo plus the [agent] table given."""
    path = _write(
        tmp_path / "agent" / "galley.local.toml",
        f"[paths]\npaper_repo = '{paper_repo}'\n"
        f"state_dir = '{tmp_path / 'agent-state'}'\n{body}",
    )
    return load(path)


def test_opus_is_the_default_and_nothing_stops_a_turn_early(bare_config) -> None:
    """The ask: a workbench for one careful patch at a time gets the best model.

    An alias rather than a dated id, so it keeps meaning the current Opus.
    """
    assert bare_config.agent.model == "opus"
    assert bare_config.agent.fallback_model is None
    assert bare_config.agent.max_turns is None
    assert bare_config.agent.max_budget_usd is None
    assert bare_config.agent.stream is True


def test_the_agent_table_is_read_when_it_is_there(tmp_path: Path, paper_repo: Path) -> None:
    cfg = _agent_config(
        tmp_path,
        paper_repo,
        "\n[agent]\nmodel = 'claude-sonnet-5'\nfallback_model = 'haiku'\n"
        "max_turns = 20\nmax_budget_usd = 2.5\nstream = false\n",
    )
    assert cfg.agent.model == "claude-sonnet-5"
    assert cfg.agent.fallback_model == "haiku"
    assert cfg.agent.max_turns == 20
    assert cfg.agent.max_budget_usd == 2.5
    assert cfg.agent.stream is False


def test_an_empty_model_is_the_same_as_not_naming_one(tmp_path: Path, paper_repo: Path) -> None:
    cfg = _agent_config(tmp_path, paper_repo, "\n[agent]\nmodel = ''\nfallback_model = '  '\n")
    assert cfg.agent.model == "opus"
    assert cfg.agent.fallback_model is None


@pytest.mark.parametrize(
    "table, said",
    [
        ("\n[agent]\nmax_turns = 0\n", "max_turns"),
        ("\n[agent]\nmax_budget_usd = 0\n", "max_budget_usd"),
        ("\n[agent]\nmax_budget_usd = -1.5\n", "max_budget_usd"),
    ],
)
def test_a_cap_that_would_stop_every_turn_is_refused(
    tmp_path: Path, paper_repo: Path, table: str, said: str
) -> None:
    """Zero turns means "never answer", which is a typo, not a setting."""
    with pytest.raises(ConfigError) as exc:
        _agent_config(tmp_path, paper_repo, table)
    assert said in str(exc.value)


# -- finding the config at all ------------------------------------------------


def test_the_environment_can_name_the_config(
    tmp_path: Path, paper_repo: Path, monkeypatch
) -> None:
    """How `--reload` survives: uvicorn's reloader re-imports the app in a new
    process that never saw the command line."""
    from galley.config import CONFIG_ENV_VAR, find_config

    named = _write(
        tmp_path / "elsewhere" / "galley.local.toml", f"[paths]\npaper_repo = '{paper_repo}'\n"
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(named))
    assert find_config(start=tmp_path) == named
    assert load().source == named


def test_an_environment_pointing_at_nothing_says_so(tmp_path: Path, monkeypatch) -> None:
    from galley.config import CONFIG_ENV_VAR, find_config

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "not-here.toml"))
    with pytest.raises(ConfigError) as exc:
        find_config()
    assert CONFIG_ENV_VAR in str(exc.value)
