"""End-to-end tests: the real app, on a real (throwaway) git repository.

Nothing here mocks git. A session really creates a worktree, the diff really
comes from `git diff`, and the write-back really lands on disk.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def test_config_reports_the_paper_and_the_backend(client) -> None:
    body = client.get("/api/config").json()
    assert body["main_branch"] == "main"
    assert body["max_concurrent_sessions"] == 2
    # Which Claude answers is the one thing about a session you cannot tell by
    # reading what it wrote, so the toolbar shows it.
    assert body["agent_model"] == "opus"


def test_creating_a_session_makes_a_worktree_and_a_branch(client, paper_repo, git_helper) -> None:
    row = client.post(
        "/api/sessions", json={"prompt": "tighten the ablation paragraph", "start": False}
    ).json()
    assert row["branch"] == "claude/tighten-the-ablation-paragraph"
    assert Path(row["worktree_path"]).is_dir()
    assert "claude/tighten-the-ablation-paragraph" in git_helper(paper_repo, "branch", "--list", "claude/*")


def test_two_sessions_from_the_same_prompt_get_distinct_branches(client) -> None:
    a = client.post("/api/sessions", json={"prompt": "same idea", "start": False}).json()
    b = client.post("/api/sessions", json={"prompt": "same idea", "start": False}).json()
    assert a["branch"] != b["branch"]
    assert b["branch"].endswith("-2")


def test_the_session_cap_is_enforced(client, config) -> None:
    # The cap counts *running* sessions; unstarted ones do not consume it.
    from galley.db import Database

    db = Database(config.db_path)
    for n in range(config.limits.max_concurrent_sessions):
        row = client.post("/api/sessions", json={"prompt": f"idea {n}", "start": False}).json()
        db.update_session(row["id"], status="running")
    resp = client.post("/api/sessions", json={"prompt": "one too many", "start": False})
    assert resp.status_code == 429
    assert "cap" in resp.json()["detail"]


def test_the_diff_is_sentence_shaped(client, paper_repo, git_helper) -> None:
    row = client.post("/api/sessions", json={"prompt": "rewrite the claim", "start": False}).json()
    worktree = Path(row["worktree_path"])
    text = (worktree / "main.tex").read_text()
    (worktree / "main.tex").write_text(
        text.replace(
            "by a large margin across all settings",
            "by 4.2 BLEU on average, though the gain narrows to 0.8",
        )
    )
    git_helper(worktree, "commit", "-qam", "rewrite")

    body = client.get("/api/diff", params={"session_id": row["id"]}).json()
    assert [f["path"] for f in body["files"]] == ["main.tex"]
    ops = body["files"][0]["ops"]
    changes = [o for o in ops if o["type"] == "change"]
    assert len(changes) == 1, "one sentence changed, so one change op"
    assert "4.2 BLEU" in changes[0]["new"]
    assert "\\S4.1" not in changes[0]["old"], "the untouched sentence is not in the change"
    assert any(w["op"] == "ins" for w in changes[0]["new_words"])


def test_accepting_nothing_and_everything_reproduce_both_sides(client, paper_repo, git_helper) -> None:
    row = client.post("/api/sessions", json={"prompt": "rewrite again", "start": False}).json()
    worktree = Path(row["worktree_path"])
    before = (paper_repo / "main.tex").read_text()
    (worktree / "main.tex").write_text(before.replace("three benchmarks", "five benchmarks"))
    git_helper(worktree, "commit", "-qam", "five")
    after = (worktree / "main.tex").read_text()

    ops = client.get("/api/diff", params={"session_id": row["id"]}).json()["files"][0]["ops"]
    reject_all = "".join(o["old"] if o["type"] == "change" else o["new"] for o in ops)
    accept_all = "".join(o["new"] for o in ops)
    assert reject_all == before
    assert accept_all == after


def test_write_back_writes_the_whole_buffer(client, paper_repo) -> None:
    merged = "\\documentclass{article}\n\\begin{document}\nAccepted.\n\\end{document}\n"
    body = client.put("/api/files/main.tex", json={"content": merged}).json()
    assert body["ok"]
    assert (paper_repo / "main.tex").read_text() == merged


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",              # absolute: pathlib lets it win over the repo root
        "sections/..%2f..%2fout.tex",  # encoded traversal, not normalised by the client
    ],
)
def test_write_back_refuses_to_escape_the_paper_repo(client, tmp_path, path: str) -> None:
    resp = client.put(f"/api/files/{path}", json={"content": "no"})
    assert resp.status_code == 400
    assert "escapes" in resp.json()["detail"]
    assert not (tmp_path / "out.tex").exists()


def test_git_status_shows_the_branch_and_the_worktrees(client, paper_repo) -> None:
    client.post("/api/sessions", json={"prompt": "a change", "start": False})
    body = client.get("/api/git/status").json()
    assert body["branch"] == "main"
    assert any(w["branch"].startswith("claude/") for w in body["worktrees"])


def test_commit_refuses_to_stage_everything(client) -> None:
    resp = client.post("/api/git/commit", json={"message": "x", "paths": []})
    assert resp.status_code == 400
    assert "explicit paths" in resp.json()["detail"]


def test_removing_a_session_drops_the_worktree_and_keeps_the_branch(
    client, paper_repo, git_helper
) -> None:
    row = client.post("/api/sessions", json={"prompt": "disposable", "start": False}).json()
    assert Path(row["worktree_path"]).is_dir()
    client.delete(f"/api/sessions/{row['id']}")
    assert not Path(row["worktree_path"]).is_dir()
    assert row["branch"] in git_helper(paper_repo, "branch", "--list", "claude/*")


def test_sync_refuses_on_a_dirty_tree(client, paper_repo) -> None:
    (paper_repo / "main.tex").write_text("dirty\n")
    body = client.post("/api/git/sync", json={}).json()
    assert not body["ok"]
    assert body["step"] == "precondition"
    assert "uncommitted" in body["message"]


def test_sync_refuses_off_the_main_branch(client, paper_repo, git_helper) -> None:
    git_helper(paper_repo, "checkout", "-q", "-b", "somewhere-else")
    body = client.post("/api/git/sync", json={}).json()
    assert not body["ok"]
    assert "not main" in body["message"]


def test_worktrees_are_excluded_locally_not_via_gitignore(client, paper_repo) -> None:
    """A session's checkout must not show up as untracked, and must never be
    committable — Overleaf should only ever see prose you accepted."""
    row = client.post("/api/sessions", json={"prompt": "any change", "start": False}).json()
    assert Path(row["worktree_path"]).is_dir()

    exclude = (paper_repo / ".git" / "info" / "exclude").read_text()
    assert ".worktrees/" in exclude
    assert not (paper_repo / ".gitignore").exists() or ".worktrees" not in (
        paper_repo / ".gitignore"
    ).read_text()

    status = client.get("/api/git/status").json()
    assert not any(f["path"].startswith(".worktrees") for f in status["files"])


# -- actually starting an agent -------------------------------------------


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


class TextBlock(_Text): ...


class AssistantMessage:
    def __init__(self, content) -> None:
        self.content = content


@pytest.fixture
def fake_agent(monkeypatch):
    """Stand in for the SDK, so the spawn path runs without spending anything."""
    seen = {}

    async def fake_query(prompt, options):
        seen["prompt"] = prompt
        seen["options"] = options
        # The agent writes a file, the way a real one would.
        (Path(options.cwd) / "main.tex").write_text("Rewritten by the agent.\n")
        yield AssistantMessage([TextBlock("Rewrote one sentence.")])

    monkeypatch.setattr("galley.services.agent.query", fake_query)
    return seen


async def test_starting_a_session_runs_the_agent_and_logs_it(client, fake_agent) -> None:
    """A sync route would run this in a worker thread, where creating the
    agent's asyncio task fails with 'no running event loop'."""
    row = client.post("/api/sessions", json={"prompt": "tighten one sentence"}).json()
    for _ in range(50):
        if client.get(f"/api/sessions/{row['id']}").json()["status"] == "idle":
            break
        await asyncio.sleep(0.05)

    session = client.get(f"/api/sessions/{row['id']}").json()
    assert session["status"] == "idle", session.get("error")
    assert fake_agent["prompt"] == "tighten one sentence"
    assert fake_agent["options"].cwd == row["worktree_path"]


async def test_the_agents_work_is_committed_and_shows_in_the_merge_pane(
    client, fake_agent
) -> None:
    row = client.post("/api/sessions", json={"prompt": "rewrite it"}).json()
    for _ in range(50):
        if client.get(f"/api/sessions/{row['id']}").json()["status"] == "idle":
            break
        await asyncio.sleep(0.05)

    body = client.get("/api/diff", params={"session_id": row["id"]}).json()
    assert [f["path"] for f in body["files"]] == ["main.tex"]
    assert any("Rewritten by the agent." in op["new"] for op in body["files"][0]["ops"])


async def test_a_follow_up_message_starts_another_turn(client, fake_agent) -> None:
    row = client.post("/api/sessions", json={"prompt": "first", "start": False}).json()
    assert client.post(f"/api/sessions/{row['id']}/message", json={"text": "second"}).json()["ok"]
    for _ in range(50):
        if client.get(f"/api/sessions/{row['id']}").json()["status"] == "idle":
            break
        await asyncio.sleep(0.05)
    assert fake_agent["prompt"] == "second"


async def test_a_failed_start_leaves_no_worktree_behind(client, paper_repo, monkeypatch) -> None:
    """Otherwise the slug is silently claimed and the next identical prompt
    becomes '-2' for no reason a human can see."""

    def explode(*_a, **_k):
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(client.app.state.agents, "start", explode)
    resp = client.post("/api/sessions", json={"prompt": "doomed session"})
    assert resp.status_code == 500
    assert "could not start the agent" in resp.json()["detail"]
    assert not (paper_repo / ".worktrees" / "doomed-session").exists()
    assert client.get("/api/git/status").json()["worktrees"] == []


async def test_a_session_whose_worktree_vanished_stops_being_listed(client, config) -> None:
    """A crash between creating the worktree and starting the agent used to
    leave a row pointing at nothing, which the rail still offered you."""
    import shutil

    from galley.app import create_app
    from fastapi.testclient import TestClient

    row = client.post("/api/sessions", json={"prompt": "will vanish", "start": False}).json()
    shutil.rmtree(row["worktree_path"])

    with TestClient(create_app(config)) as fresh:
        listed = [s["id"] for s in fresh.get("/api/sessions").json() if s["status"] != "removed"]
        assert row["id"] not in listed


# -- long jobs run in the background --------------------------------------
#
# Everything that starts one must be reached from an async route: a sync route
# runs in a worker thread, where asyncio.create_task raises "no running event
# loop". That bug shipped twice, so every such endpoint is exercised here.


async def test_compile_starts_in_the_background_and_reports_progress(client, monkeypatch) -> None:
    import galley.services.latex as latex

    monkeypatch.setattr(
        latex, "compile_pdf", lambda *a, **k: latex.CompileResult(True, None, [], "")
    )
    started = client.post("/api/compile", json={}).json()
    assert started["state"] in ("running", "done")

    for _ in range(50):
        state = client.get("/api/compile").json()
        if state["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert state["state"] == "done"
    assert state["ok"] is True
    assert state["elapsed_seconds"] is not None


async def test_review_starts_in_the_background(client, monkeypatch) -> None:
    import galley.services.latex as latex

    monkeypatch.setattr(
        latex, "latexdiff_pdf", lambda *a, **k: latex.CompileResult(True, None, [], "")
    )
    row = client.post("/api/sessions", json={"prompt": "review me", "start": False}).json()
    started = client.post("/api/review", json={"session_id": row["id"]}).json()
    assert started["state"] in ("running", "done")

    for _ in range(50):
        state = client.get(f"/api/review?session_id={row['id']}").json()
        if state["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert state["state"] == "done"


async def test_a_failing_job_is_reported_not_swallowed(client, monkeypatch) -> None:
    import galley.services.latex as latex

    def explode(*_a, **_k):
        raise RuntimeError("latexmk is not installed")

    monkeypatch.setattr(latex, "compile_pdf", explode)
    client.post("/api/compile", json={})
    for _ in range(50):
        state = client.get("/api/compile").json()
        if state["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert state["state"] == "failed"
    assert "latexmk is not installed" in state["error"]


async def test_asking_twice_joins_the_run_already_going(client, monkeypatch) -> None:
    """A second click must not start a second latexmk over the same output."""
    import time

    import galley.services.latex as latex

    calls = []

    def slow(*_a, **_k):
        calls.append(1)
        time.sleep(0.4)
        return latex.CompileResult(True, None, [], "")

    monkeypatch.setattr(latex, "compile_pdf", slow)
    client.post("/api/compile", json={})
    await asyncio.sleep(0.05)
    second = client.post("/api/compile", json={}).json()
    assert second["state"] == "running"

    for _ in range(60):
        if client.get("/api/compile").json()["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert len(calls) == 1


def test_a_review_needs_a_session(client) -> None:
    assert client.post("/api/review", json={}).status_code == 400


# -- a session starts from the paper as you see it, not as you last committed --


def test_a_session_carries_your_uncommitted_work_in(client, paper_repo: Path) -> None:
    source = paper_repo / "main.tex"
    source.write_text(source.read_text().replace("three benchmarks", "seven benchmarks"))
    (paper_repo / "unsaved.tex").write_text("\\section{Draft}\n")

    row = client.post("/api/sessions", json={"prompt": "carry it", "start": False}).json()
    tree = Path(row["worktree_path"])
    assert "seven benchmarks" in (tree / "main.tex").read_text()
    assert (tree / "unsaved.tex").is_file()


def test_your_own_edits_are_not_reported_as_the_agents(client, paper_repo: Path) -> None:
    source = paper_repo / "main.tex"
    source.write_text(source.read_text().replace("three benchmarks", "seven benchmarks"))

    row = client.post("/api/sessions", json={"prompt": "quiet", "start": False}).json()
    assert client.get(f"/api/sessions/{row['id']}").json()["files"] == []

    body = client.get("/api/diff", params={"session_id": row["id"]}).json()
    assert body["files"] == [], "the agent changed nothing, so there is nothing to merge"


def test_a_deleted_file_is_carried_in_as_a_deletion(client, paper_repo: Path, git_helper) -> None:
    (paper_repo / "old.tex").write_text("\\section{Old}\n")
    git_helper(paper_repo, "add", "-A")
    git_helper(paper_repo, "commit", "-qm", "add old")
    (paper_repo / "old.tex").unlink()

    row = client.post("/api/sessions", json={"prompt": "gone", "start": False}).json()
    assert not (Path(row["worktree_path"]) / "old.tex").exists()
