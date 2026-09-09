"""Overleaf is a dumb single-branch remote with a second writer attached.

It exposes one branch, force-push is unreliable, and the web editor means the
remote may have moved since you last looked — you are collaborating with
yourself. So there is exactly one compound button, and it always rebases first.

Claude's branches never go near it. Overleaf only ever sees prose you accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from . import git


@dataclass
class SyncResult:
    ok: bool
    step: str
    message: str
    conflicts: list[str] = field(default_factory=list)
    pushed: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "step": self.step,
            "message": self.message,
            "conflicts": self.conflicts,
            "pushed": self.pushed,
        }


def status(cfg: Config) -> dict:
    repo = cfg.paths.paper_repo
    branch = git.current_branch(repo)
    ahead = behind = None
    tracking = f"{cfg.paper.overleaf_remote}/{cfg.paper.overleaf_branch}"
    try:
        counts = git.run(repo, "rev-list", "--left-right", "--count", f"{tracking}...HEAD")
        behind, ahead = (int(x) for x in counts.split())
    except git.GitError:
        pass
    return {
        "branch": branch,
        "on_main": branch == cfg.paper.main_branch,
        "clean": git.is_clean(repo),
        "remote": cfg.paper.overleaf_remote,
        "remote_branch": cfg.paper.overleaf_branch,
        "ahead": ahead,
        "behind": behind,
        "conflicts": git.conflicted(repo),
    }


def sync(cfg: Config, allow_push: bool = True) -> SyncResult:
    """Pull with rebase, then push. Never force, never on a dirty tree."""
    repo = cfg.paths.paper_repo
    remote, remote_branch = cfg.paper.overleaf_remote, cfg.paper.overleaf_branch
    branch = git.current_branch(repo)

    if branch != cfg.paper.main_branch:
        return SyncResult(
            False,
            "precondition",
            f"you are on {branch}, not {cfg.paper.main_branch}. Sync only "
            "publishes prose you have already accepted onto the main branch.",
        )
    if not git.is_clean(repo):
        return SyncResult(
            False,
            "precondition",
            "the working tree has uncommitted changes. Commit or stash them "
            "first — a rebase onto a moved remote will not survive them.",
        )

    try:
        git.run(repo, "fetch", remote, remote_branch, timeout=180)
    except git.GitError as exc:
        return SyncResult(False, "fetch", str(exc))

    try:
        git.run(repo, "pull", "--rebase", remote, remote_branch, timeout=180)
    except git.GitError as exc:
        conflicts = git.conflicted(repo)
        if conflicts:
            return SyncResult(
                False,
                "rebase",
                "the remote moved and these files conflict. Resolve them in the "
                "merge pane — it is the same sentence-level tool you use for "
                "Claude's changes — then continue the rebase.",
                conflicts=conflicts,
            )
        return SyncResult(False, "rebase", str(exc))

    if not allow_push:
        return SyncResult(True, "rebase", "rebased onto the remote; push not requested")

    try:
        git.run(repo, "push", remote, f"{branch}:{remote_branch}", timeout=180)
    except git.GitError as exc:
        return SyncResult(False, "push", str(exc))
    return SyncResult(True, "push", f"pushed {branch} to {remote}/{remote_branch}", pushed=True)


def continue_rebase(cfg: Config) -> SyncResult:
    """After you have resolved the conflicts in the merge pane."""
    repo = cfg.paths.paper_repo
    remaining = git.conflicted(repo)
    if remaining:
        return SyncResult(
            False, "rebase", "still conflicted", conflicts=remaining
        )
    try:
        git.run(repo, "-c", "core.editor=true", "rebase", "--continue", timeout=120)
    except git.GitError as exc:
        return SyncResult(False, "rebase", str(exc), conflicts=git.conflicted(repo))
    return SyncResult(True, "rebase", "rebase continued; sync again to push")


def abort_rebase(cfg: Config) -> SyncResult:
    try:
        git.run(cfg.paths.paper_repo, "rebase", "--abort")
    except git.GitError as exc:
        return SyncResult(False, "rebase", str(exc))
    return SyncResult(True, "rebase", "rebase aborted; the tree is back where it was")


def stage_resolution(cfg: Config, path: str) -> None:
    """Mark one conflicted file resolved, after the merge pane wrote it."""
    git.run(cfg.paths.paper_repo, "add", "--", path)
