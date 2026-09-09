"""The remote you publish to, and the one button that publishes to it.

Overleaf's git bridge is the remote this was built against, and it set the
shape: one branch, force-push unreliable, and a second writer sitting in the
web editor, so the remote may have moved since you last looked — you are
collaborating with yourself. Hence exactly one compound button, and it always
rebases first. Any ordinary remote behaves correctly under those rules, so the
mechanism is named after the job rather than after Overleaf.

A project with no publish remote is a normal state, not a broken one. The
panel says so and the button says why it is unavailable, rather than failing
when you press it.

Claude's branches never go near any of this. The remote only ever sees prose
you accepted.
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


def _remotes(repo: Path) -> list[str]:
    """The remotes git actually has, which need not include the configured one."""
    return git.run(repo, "remote").splitlines()


def _tracking_sha(repo: Path, remote: str, branch: str) -> str:
    """The remote-tracking ref for that branch, or "" if we have never seen it.

    A ref this machine already holds, not a question for the remote: `status`
    is asked on every panel load and must not wait on a network.
    """
    return git.run(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/remotes/{remote}/{branch}",
        check=False,
    ).strip()


def _remote_has_branch(repo: Path, remote: str, branch: str) -> bool:
    """Whether the remote has that branch. One round trip, before anything writes.

    `ls-remote --exit-code` exits 2 when the ref is simply not there and 128
    when it could not talk to the remote at all — the one distinction `fetch`
    throws away by reporting both as a fatal error. A branch that does not
    exist yet is a first publish; a remote that cannot be reached is a problem
    to report, so the caller gets a bool for the first and a GitError for the
    second.
    """
    try:
        git.run(repo, "ls-remote", "--exit-code", remote, f"refs/heads/{branch}", timeout=180)
        return True
    except git.GitError as exc:
        if exc.returncode == 2:
            return False
        raise


def _blocked(cfg: Config, branch: str, clean: bool) -> str | None:
    """Why Sync cannot run right now, or None when it can.

    The one owner of these three sentences: the panel shows this beside the
    disabled button and `sync` refuses with exactly the same words, so what
    you read before pressing is what you would have got by pressing.

    A missing remote comes last because the panel's heading already names the
    remote's state. By the time you reach this line you know whether there is
    one, and what you still want to know is whether anything else is in the
    way as well.
    """
    if branch != cfg.paper.main_branch:
        return (
            f"you are on {branch}, not {cfg.paper.main_branch}. Sync only "
            "publishes prose you have already accepted onto the main branch."
        )
    if not clean:
        return (
            "the working tree has uncommitted changes. Commit or stash them "
            "first — a rebase onto a moved remote will not survive them."
        )
    if cfg.paper.publish_remote not in _remotes(cfg.paths.paper_repo):
        return (
            f"this repository has no remote named {cfg.paper.publish_remote}. "
            "A project with nowhere to publish is a normal state; add the "
            "remote, or point publish_remote at the one you have."
        )
    return None


def status(cfg: Config) -> dict:
    """Where the publish remote stands, answered locally and immediately.

    Nothing here touches the network, so `state` is what this machine knows:

    - `ready` — the remote is configured and we hold a ref for its branch, so
      `ahead` and `behind` are counts.
    - `unpushed` — the remote is configured, but this machine has never seen
      that branch on it. Only `sync` can tell you whether it is there, and it
      asks the remote itself.
    - `no_remote` — git has no remote by that name. Ordinary for a new
      project, and the reason this used to report "up to date" in silence.
    """
    repo = cfg.paths.paper_repo
    branch = git.current_branch(repo)
    clean = git.is_clean(repo)
    remote, remote_branch = cfg.paper.publish_remote, cfg.paper.publish_branch

    if remote not in _remotes(repo):
        state = "no_remote"
    elif _tracking_sha(repo, remote, remote_branch):
        state = "ready"
    else:
        state = "unpushed"

    ahead = behind = None
    if state == "ready":
        counts = git.run(
            repo, "rev-list", "--left-right", "--count", f"{remote}/{remote_branch}...HEAD"
        )
        behind, ahead = (int(x) for x in counts.split())

    return {
        "branch": branch,
        "on_main": branch == cfg.paper.main_branch,
        "clean": clean,
        "remote": remote,
        "remote_branch": remote_branch,
        "state": state,
        "blocked": _blocked(cfg, branch, clean),
        "ahead": ahead,
        "behind": behind,
        "conflicts": git.conflicted(repo),
    }


def sync(cfg: Config, allow_push: bool = True) -> SyncResult:
    """Pull with rebase, then push. Never force, never on a dirty tree."""
    repo = cfg.paths.paper_repo
    remote, remote_branch = cfg.paper.publish_remote, cfg.paper.publish_branch
    branch = git.current_branch(repo)

    refusal = _blocked(cfg, branch, git.is_clean(repo))
    if refusal:
        return SyncResult(False, "precondition", refusal)

    try:
        on_remote = _remote_has_branch(repo, remote, remote_branch)
    except git.GitError as exc:
        return SyncResult(False, "fetch", str(exc))

    # Nothing to rebase onto when the branch is not there yet, and `pull`
    # would fail rather than say so. The push below creates it.
    if on_remote:
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
        if on_remote:
            return SyncResult(True, "rebase", "rebased onto the remote; push not requested")
        return SyncResult(
            True,
            "rebase",
            f"{remote} has no {remote_branch} to rebase onto; push not requested",
        )

    try:
        git.run(repo, "push", remote, f"{branch}:{remote_branch}", timeout=180)
    except git.GitError as exc:
        return SyncResult(False, "push", str(exc))
    if on_remote:
        return SyncResult(
            True, "push", f"pushed {branch} to {remote}/{remote_branch}", pushed=True
        )
    return SyncResult(
        True,
        "push",
        f"published {branch} as {remote}/{remote_branch} for the first time; "
        "there was nothing to rebase onto.",
        pushed=True,
    )


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
    """Mark one conflicted file resolved, after the merge pane wrote it.

    TODO(po-hung): nothing calls this yet, and the conflict flow is unfinished
    without it — `continue_rebase` refuses while any path is still unmerged, so
    the merge pane's Save has to stage the file it just wrote. No route does.
    """
    git.run(cfg.paths.paper_repo, "add", "--", path)
