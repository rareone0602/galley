"""Publishing, fired at real remotes.

`sync` is one of the two places Galley writes outside the working tree you can
see, and until now none of it had been run against a remote at all. Every test
here builds actual repositories: a bare repository on this disk is a real
remote, and nothing in `publish` knows or cares that it is not over a network.

The three cases a project can be in are all normal:

- no remote configured — a project that has never published anywhere;
- a remote with no such branch on it — a project that has never published yet;
- a remote with the branch on it — today's case, which must not change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from galley.services import publish


@pytest.fixture
def bare_remote(tmp_path: Path, git_helper) -> Path:
    """A real remote. The bridge Galley was built against is a bare repository
    you push to, and so is this one."""
    bare = tmp_path / "remote.git"
    git_helper(tmp_path, "init", "-q", "--bare", "-b", "master", str(bare))
    return bare


@pytest.fixture
def with_remote(config, paper_repo: Path, bare_remote: Path, git_helper):
    """The publish remote configured in git, but nothing pushed to it yet."""
    git_helper(paper_repo, "remote", "add", config.paper.publish_remote, str(bare_remote))
    return config


@pytest.fixture
def published(with_remote, paper_repo: Path, git_helper):
    """...and the first publish already done, which is the ordinary case.

    Pushed with git rather than through `sync`, so the tests that then call
    `sync` are not resting on it having worked.
    """
    cfg = with_remote
    git_helper(
        paper_repo,
        "push",
        "-q",
        cfg.paper.publish_remote,
        f"{cfg.paper.main_branch}:{cfg.paper.publish_branch}",
    )
    return cfg


@pytest.fixture
def other_writer(tmp_path: Path, bare_remote: Path, git_helper):
    """The second writer, in the shape `sync` meets it: someone else's clone
    pushing to the same branch while you were not looking."""

    def push(text: str, message: str, path: str = "main.tex") -> str:
        clone = tmp_path / "other"
        if not clone.is_dir():
            git_helper(tmp_path, "clone", "-q", str(bare_remote), str(clone))
            git_helper(clone, "config", "user.email", "other@localhost")
            git_helper(clone, "config", "user.name", "Other")
        (clone / path).write_text(text)
        git_helper(clone, "add", "-A")
        git_helper(clone, "commit", "-qm", message)
        git_helper(clone, "push", "-q", "origin", "HEAD")
        return git_helper(clone, "rev-parse", "HEAD").strip()

    return push


def _remote_head(bare: Path, branch: str, git_helper) -> str:
    return git_helper(bare, "rev-parse", f"refs/heads/{branch}").strip()


def _commit(repo: Path, git_helper, text: str, message: str, path: str = "main.tex") -> str:
    (repo / path).write_text(text)
    git_helper(repo, "add", "-A")
    git_helper(repo, "commit", "-qm", message)
    return git_helper(repo, "rev-parse", "HEAD").strip()


# -- what the panel says before you press anything -------------------------


def test_status_says_plainly_that_there_is_no_remote(config) -> None:
    """The case this used to answer in silence: no remote configured looked
    exactly like up to date, because both reported no counts."""
    body = publish.status(config)
    assert body["state"] == "no_remote"
    assert body["ahead"] is None and body["behind"] is None
    assert config.paper.publish_remote in body["blocked"]


def test_status_says_the_branch_is_not_on_the_remote_yet(with_remote) -> None:
    body = publish.status(with_remote)
    assert body["state"] == "unpushed"
    assert body["ahead"] is None and body["behind"] is None
    # Nothing is in the way: a first publish is a thing you are allowed to do.
    assert body["blocked"] is None


def test_status_counts_what_is_ahead_once_the_branch_is_there(
    published, paper_repo: Path, git_helper
) -> None:
    body = publish.status(published)
    assert body["state"] == "ready"
    assert (body["ahead"], body["behind"]) == (0, 0)

    _commit(paper_repo, git_helper, "one more sentence\n", "a change")
    body = publish.status(published)
    assert (body["ahead"], body["behind"]) == (1, 0)


def test_status_counts_what_the_remote_moved_by(
    published, paper_repo: Path, git_helper, other_writer
) -> None:
    other_writer("their wording\n", "the second writer")
    git_helper(paper_repo, "fetch", "-q", published.paper.publish_remote)
    body = publish.status(published)
    assert (body["ahead"], body["behind"]) == (0, 1)


# -- the refusals ----------------------------------------------------------


def test_sync_refuses_when_there_is_no_remote(config) -> None:
    result = publish.sync(config)
    assert not result.ok
    assert result.step == "precondition"
    assert config.paper.publish_remote in result.message


def test_sync_refuses_on_a_dirty_tree(published, paper_repo: Path) -> None:
    (paper_repo / "main.tex").write_text("edited, not committed\n")
    result = publish.sync(published)
    assert not result.ok
    assert result.step == "precondition"
    assert "uncommitted" in result.message


def test_sync_refuses_off_the_main_branch(published, paper_repo: Path, git_helper) -> None:
    git_helper(paper_repo, "checkout", "-q", "-b", "elsewhere")
    result = publish.sync(published)
    assert not result.ok
    assert result.step == "precondition"
    assert "not main" in result.message


def test_a_refusal_writes_nothing_to_the_remote(
    published, paper_repo: Path, bare_remote: Path, git_helper
) -> None:
    before = _remote_head(bare_remote, published.paper.publish_branch, git_helper)
    _commit(paper_repo, git_helper, "a change worth publishing\n", "a change")
    git_helper(paper_repo, "checkout", "-q", "-b", "elsewhere")
    assert not publish.sync(published).ok
    assert _remote_head(bare_remote, published.paper.publish_branch, git_helper) == before


@pytest.mark.parametrize("spoil", ["dirty tree", "wrong branch", "no remote"])
def test_the_panel_and_the_button_give_the_same_reason(
    with_remote, paper_repo: Path, git_helper, spoil: str
) -> None:
    """Two live authorities, pinned against each other: the sentence shown
    beside the disabled button is the one `sync` would have refused with."""
    if spoil == "dirty tree":
        (paper_repo / "main.tex").write_text("edited, not committed\n")
    elif spoil == "wrong branch":
        git_helper(paper_repo, "checkout", "-q", "-b", "elsewhere")
    else:
        git_helper(paper_repo, "remote", "remove", with_remote.paper.publish_remote)

    blocked = publish.status(with_remote)["blocked"]
    result = publish.sync(with_remote)
    assert blocked
    assert not result.ok and result.step == "precondition"
    assert result.message == blocked


def test_a_remote_it_cannot_reach_is_reported_not_confused_with_a_missing_one(
    config, paper_repo: Path, tmp_path: Path, git_helper
) -> None:
    """A remote that is configured but broken is an error to show you. A
    remote that is not configured is not — and the two must not read alike."""
    git_helper(
        paper_repo, "remote", "add", config.paper.publish_remote, str(tmp_path / "gone.git")
    )
    assert publish.status(config)["state"] == "unpushed"

    result = publish.sync(config)
    assert not result.ok
    assert result.step == "fetch"
    assert "gone.git" in result.message


# -- publishing ------------------------------------------------------------


def test_the_first_publish_creates_the_branch_on_the_remote(
    with_remote, paper_repo: Path, bare_remote: Path, git_helper
) -> None:
    """A repository you have never pushed. There is nothing to rebase onto,
    and saying so is more useful than failing in `pull`."""
    result = publish.sync(with_remote)
    assert result.ok and result.pushed
    assert result.step == "push"
    assert "first time" in result.message

    branch = with_remote.paper.publish_branch
    assert _remote_head(bare_remote, branch, git_helper) == git_helper(
        paper_repo, "rev-parse", "HEAD"
    ).strip()
    assert publish.status(with_remote)["state"] == "ready"


def test_a_clean_tree_on_the_main_branch_pulls_rebases_and_pushes(
    published, paper_repo: Path, bare_remote: Path, git_helper, other_writer
) -> None:
    """The whole compound button, with both writers having moved: theirs is
    still there afterwards, which is what never forcing buys you."""
    theirs = other_writer("a paragraph they added\n", "their work", path="theirs.tex")
    mine = _commit(paper_repo, git_helper, "a paragraph I added\n", "my work", path="mine.tex")

    result = publish.sync(published)
    assert result.ok and result.pushed
    assert result.step == "push"

    branch = published.paper.publish_branch
    head = _remote_head(bare_remote, branch, git_helper)
    # Their commit survived, and mine was replayed on top of it rather than
    # over it: a rebase moves my commit, so it is no longer the sha it was.
    assert theirs in git_helper(bare_remote, "rev-list", head)
    assert head != mine
    assert (paper_repo / "theirs.tex").is_file()
    assert publish.status(published)["ahead"] == 0


def test_rebase_only_does_not_push(
    published, paper_repo: Path, bare_remote: Path, git_helper, other_writer
) -> None:
    other_writer("a paragraph they added\n", "their work", path="theirs.tex")
    theirs = _remote_head(bare_remote, published.paper.publish_branch, git_helper)
    _commit(paper_repo, git_helper, "a paragraph I added\n", "my work", path="mine.tex")

    result = publish.sync(published, allow_push=False)
    assert result.ok and not result.pushed
    assert result.step == "rebase"
    assert (paper_repo / "theirs.tex").is_file()
    assert _remote_head(bare_remote, published.paper.publish_branch, git_helper) == theirs


def test_rebase_only_on_a_remote_without_the_branch_says_so(with_remote) -> None:
    result = publish.sync(with_remote, allow_push=False)
    assert result.ok and not result.pushed
    assert with_remote.paper.publish_branch in result.message


def test_a_conflict_comes_back_as_a_conflict_not_an_exception(
    published, paper_repo: Path, bare_remote: Path, git_helper, other_writer
) -> None:
    """Both writers rewrote the same sentence. That is a thing to hand to the
    merge pane, not a stack trace — and the remote must be untouched."""
    other_writer("their rewrite of the abstract\n", "their wording")
    before = _remote_head(bare_remote, published.paper.publish_branch, git_helper)
    _commit(paper_repo, git_helper, "my rewrite of the abstract\n", "my wording")

    result = publish.sync(published)
    assert not result.ok
    assert result.step == "rebase"
    assert result.conflicts == ["main.tex"]
    assert not result.pushed
    assert _remote_head(bare_remote, published.paper.publish_branch, git_helper) == before

    # The panel reports the same conflict, so the merge pane can be opened on it.
    assert publish.status(published)["conflicts"] == ["main.tex"]

    assert publish.abort_rebase(published).ok
    assert publish.status(published)["conflicts"] == []
    assert (paper_repo / "main.tex").read_text() == "my rewrite of the abstract\n"


# -- through the route -----------------------------------------------------


def test_the_status_route_carries_the_publish_remote(client) -> None:
    body = client.get("/api/git/status").json()
    assert body["publish"]["state"] == "no_remote"
    assert body["publish"]["remote"] == "origin"


def test_the_sync_route_answers_a_missing_remote_rather_than_failing(client) -> None:
    resp = client.post("/api/git/sync", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert not body["ok"] and body["step"] == "precondition"
