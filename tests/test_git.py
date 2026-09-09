"""Running git, and the one way it can fail that is not a failed command."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from galley.services import git


def test_a_git_command_that_never_answers_is_a_git_error(paper_repo: Path, git_helper) -> None:
    """A remote that accepts the connection and then says nothing hangs for the
    whole timeout. Every caller handles `GitError`; a `TimeoutExpired` escaping
    as itself turns a slow network into a 500 from the Sync button.

    An alias that sleeps stands in for the remote — it is a real git invocation
    that really does not return.
    """
    git_helper(paper_repo, "config", "alias.nap", "!sleep 5")

    started = time.monotonic()
    with pytest.raises(git.GitError) as caught:
        git.run(paper_repo, "nap", timeout=0.5)
    waited = time.monotonic() - started

    assert waited < 4, "it should give up at the timeout, not wait out the command"
    assert "gave up after 0.5s" in str(caught.value)
