"""End-to-end tests: the real app, on a real (throwaway) git repository.

Nothing here mocks git. A session really creates a worktree, the diff really
comes from `git diff`, and the write-back really lands on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_config_reports_the_paper_and_the_backend(client) -> None:
    body = client.get("/api/config").json()
    assert body["main_branch"] == "main"
    assert body["backend"] == "gpuq"
    assert body["max_queued_jobs"] == 1


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


def test_the_precommit_hook_is_installed_at_startup(client, paper_repo) -> None:
    hook = paper_repo / ".git" / "hooks" / "pre-commit"
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111


def test_the_precommit_hook_rejects_a_table_edited_by_hand(client, paper_repo, git_helper) -> None:
    import subprocess

    (paper_repo / "tables" / "ablation.tex").write_text("% hand-written\n4.2\n")
    git_helper(paper_repo, "add", "tables/ablation.tex")
    proc = subprocess.run(
        ["git", "-C", str(paper_repo), "commit", "-m", "sneak a number in"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "changes tables/ but not results/" in proc.stderr


def test_the_precommit_hook_allows_a_table_that_came_from_results(
    client, paper_repo, git_helper
) -> None:
    import subprocess

    (paper_repo / "results").mkdir(exist_ok=True)
    (paper_repo / "results" / "abc.json").write_text('{"job_id": "abc"}')
    (paper_repo / "tables" / "ablation.tex").write_text("% generated\n")
    git_helper(paper_repo, "add", "results/abc.json", "tables/ablation.tex")
    proc = subprocess.run(
        ["git", "-C", str(paper_repo), "commit", "-m", "regenerate the table"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_a_results_file_with_no_job_behind_it_is_flagged(client, paper_repo) -> None:
    (paper_repo / "results").mkdir(exist_ok=True)
    (paper_repo / "results" / "invented.json").write_text('{"job_id": "nosuchjob"}')
    body = client.get("/api/git/status").json()
    assert "invented.json" in body["unbacked_results"]


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


def test_the_hook_installer_leaves_someone_elses_hook_alone(paper_repo) -> None:
    from galley.services.results import install_precommit_hook

    hooks = paper_repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    mine = hooks / "pre-commit"
    mine.write_text("#!/bin/sh\n# my own hook\nexit 0\n")
    assert install_precommit_hook(paper_repo) is None
    assert "my own hook" in mine.read_text()


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
