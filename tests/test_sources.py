"""Reviewing work another agent left on a branch.

The case these are written from is the real one: a second agent was asked to
revise the paper on `codex/pat-2026-09-18`, worked in its own checkout, and left
nine files edited and uncommitted for most of an afternoon before committing any
of it. A review that could only read commits would have shown an empty branch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from galley.services import source


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def foreign(paper_repo: Path, tmp_path: Path):
    """A branch with its own checkout, as another agent would leave it.

    Returns a function so a test can decide whether the work is committed —
    which is the whole difference between the two halves of this file.
    """

    def make(branch: str = "codex/revise", commit: bool = False) -> Path:
        tree = tmp_path / "elsewhere"
        _git(paper_repo, "worktree", "add", "-q", "-b", branch, str(tree), "main")
        _git(tree, "config", "user.email", "codex@localhost")
        _git(tree, "config", "user.name", "Codex")
        paper = tree / "main.tex"
        paper.write_text(paper.read_text().replace("three benchmarks", "nine benchmarks"))
        if commit:
            _git(tree, "add", "-A")
            _git(tree, "commit", "-qm", "revise the opening")
        return tree

    return make


# -- what you can review ------------------------------------------------


def test_another_agents_branch_is_on_the_list_with_its_work_uncommitted(client, foreign):
    foreign()
    listed = {b["branch"]: b for b in client.get("/api/branches").json()}

    assert "codex/revise" in listed, "a branch someone else wrote is work you can review"
    assert listed["codex/revise"]["kind"] == "branch"
    assert listed["codex/revise"]["uncommitted"] == 1
    assert listed["codex/revise"]["files"] == 1


def test_the_branch_you_are_standing_on_is_not_something_to_review(client):
    listed = [b["branch"] for b in client.get("/api/branches").json()]
    assert "main" not in listed, "your working copy against itself asks no question"


def test_a_session_is_one_source_and_carries_its_prompt(client):
    row = client.post("/api/sessions", json={"prompt": "polish the opening", "start": False}).json()
    listed = [b for b in client.get("/api/branches").json() if b["branch"] == row["branch"]]

    assert len(listed) == 1, "a session is a branch with a conversation, not a second thing"
    assert listed[0]["kind"] == "session"
    assert listed[0]["label"] == "polish the opening"
    assert listed[0]["session_id"] == row["id"]


def test_a_removed_sessions_branch_is_still_reviewable_and_still_named(client):
    row = client.post("/api/sessions", json={"prompt": "polish the opening", "start": False}).json()
    client.delete(f"/api/sessions/{row['id']}")

    listed = {b["branch"]: b for b in client.get("/api/branches").json()}
    kept = listed[row["branch"]]
    assert kept["kind"] == "branch", "the conversation is gone, the work is not"
    assert kept["label"] == "polish the opening", "still say which one it was"
    assert kept["session_id"] is None


def test_a_session_whose_branch_was_deleted_keeps_its_conversation(client, paper_repo: Path):
    row = client.post("/api/sessions", json={"prompt": "quiet", "start": False}).json()
    client.delete(f"/api/sessions/{row['id']}", params={"keep_branch": False})

    listed = [b["branch"] for b in client.get("/api/branches").json()]
    assert row["branch"] not in listed
    # There is nothing left to review, and what was said is still true.
    detail = client.get(f"/api/sessions/{row['id']}")
    assert detail.status_code == 200
    assert detail.json()["files"] == []


# -- reading it ---------------------------------------------------------


def test_a_review_reads_the_checkout_as_it_stands_not_as_it_was_committed(client, foreign):
    foreign()  # edited, deliberately not committed
    body = client.get("/api/diff", params={"branch": "codex/revise"}).json()

    changed = [op for op in body["files"][0]["ops"] if op["type"] == "change"]
    assert changed, "work in flight is still work"
    assert "nine benchmarks" in changed[0]["new"]


def test_a_branch_with_no_checkout_is_read_at_its_tip(client, foreign, paper_repo: Path):
    tree = foreign(commit=True)
    _git(paper_repo, "worktree", "remove", "--force", str(tree))

    body = client.get("/api/diff", params={"branch": "codex/revise"}).json()
    changed = [op for op in body["files"][0]["ops"] if op["type"] == "change"]
    assert "nine benchmarks" in changed[0]["new"]


def test_a_file_the_branch_deleted_is_named_rather_than_offered_as_empty(client, foreign):
    tree = foreign()
    (tree / "main.tex").unlink()
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "drop it")

    entry = client.get("/api/diff", params={"branch": "codex/revise"}).json()["files"][0]
    assert entry["deleted"] is True
    assert entry["editable"] is False
    assert entry["ops"] == [], (
        "a deletion offered as sentences would let one click empty the file"
    )


def test_a_figure_that_changed_is_named_and_not_diffed(client, foreign):
    tree = foreign()
    (tree / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "a figure")

    files = {f["path"]: f for f in client.get("/api/diff", params={"branch": "codex/revise"}).json()["files"]}
    assert files["plot.png"]["editable"] is False
    assert files["plot.png"]["ops"] == []


def test_a_review_says_when_you_moved_the_same_file_too(client, foreign, paper_repo: Path):
    foreign()
    entry = client.get("/api/diff", params={"branch": "codex/revise"}).json()["files"][0]
    assert entry["yours_moved"] is False, "you have not touched it since they forked"

    paper = paper_repo / "main.tex"
    paper.write_text(paper.read_text().replace("large margin", "modest margin"))
    entry = client.get("/api/diff", params={"branch": "codex/revise"}).json()["files"][0]
    assert entry["yours_moved"] is True, "taking theirs here would undo your sentence"


def test_a_checkout_mid_merge_is_refused_with_the_reason(client, foreign, paper_repo: Path):
    tree = foreign()
    _git(tree, "commit", "-qam", "theirs")
    paper = paper_repo / "main.tex"
    paper.write_text(paper.read_text().replace("three benchmarks", "four benchmarks"))
    _git(paper_repo, "commit", "-qam", "yours")
    # The same line moved on both sides, so this stops half-done and leaves
    # conflict markers in the file. They are not sentences.
    subprocess.run(["git", "-C", str(tree), "merge", "main"], capture_output=True, text=True)

    response = client.get("/api/diff", params={"branch": "codex/revise"})
    assert response.status_code == 409
    assert "middle of a merge" in response.json()["detail"]


# -- and never writing to it --------------------------------------------


def test_reviewing_never_writes_to_a_checkout_galley_did_not_make(client, foreign):
    tree = foreign()
    before = {
        path: path.stat().st_mtime_ns for path in sorted(tree.rglob("*")) if path.is_file()
    }
    status = _git(tree, "status", "--porcelain")

    client.get("/api/branches")
    client.get("/api/diff", params={"branch": "codex/revise"})

    after = {path: path.stat().st_mtime_ns for path in sorted(tree.rglob("*")) if path.is_file()}
    assert after == before, "not one byte of someone else's afternoon"
    assert _git(tree, "status", "--porcelain") == status
    assert _git(tree, "diff", "--cached", "--name-only") == "", "not the index either"


def test_saving_a_review_writes_your_working_copy_and_nothing_else(client, foreign, paper_repo):
    tree = foreign()
    body = client.get("/api/diff", params={"branch": "codex/revise"}).json()
    theirs = "".join(op["new"] for op in body["files"][0]["ops"])

    client.put("/api/files/main.tex", json={"content": theirs})

    assert "nine benchmarks" in (paper_repo / "main.tex").read_text()
    assert "nine benchmarks" in (tree / "main.tex").read_text()
    assert _git(tree, "status", "--porcelain").strip().startswith("M"), "theirs still uncommitted"


def test_a_file_that_changed_under_an_open_review_refuses_to_be_overwritten(
    client, foreign, paper_repo: Path
):
    foreign()
    entry = client.get("/api/diff", params={"branch": "codex/revise"}).json()["files"][0]

    # You go back to the editor and write a sentence while the review is open.
    paper = paper_repo / "main.tex"
    paper.write_text(paper.read_text() + "One more thought.\n")

    refused = client.put(
        "/api/files/main.tex", json={"content": "anything", "if_match": entry["sha"]}
    )
    assert refused.status_code == 409
    assert "One more thought." in paper.read_text(), "your sentence is still there"


# -- keys ---------------------------------------------------------------


def test_two_branches_that_flatten_alike_get_different_directories():
    assert source.slug("codex/pat") != source.slug("codex-pat"), (
        "one would serve the other's PDF"
    )


def test_a_branch_called_main_does_not_take_over_the_accepted_build(client, paper_repo: Path):
    # `compile:main` was the key for a build of your working copy, so a branch
    # of that name would have shared its slot and served its PDF.
    _git(paper_repo, "branch", "notmain")
    assert source.slug("main") != "accepted"
