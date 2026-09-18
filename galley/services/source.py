"""What you can review: a branch, and where to read it from.

A Claude session is a branch with a conversation attached. That is the whole
idea here. Everything that asks *which checkout* — the diff, a build, the PDF,
the marked-up review, SyncTeX — is keyed by branch; only the things that ask
*which conversation* — the chat, a message, stop, compact, remove — stay keyed by
session. They had been sharing one key, and they are two different questions.

Reading a branch has two honest answers, and one rule picks between them:

    **Galley reads from disk any checkout it did not make.**

- A checkout Galley made is a session's, and Galley commits it at the end of
  every turn. The commit is the finished thought; the working tree mid-turn is a
  file the agent is halfway through rewriting, and putting that in the merge pane
  would offer you half a sentence. So a session's branch is read at its tip.
- A checkout anyone else made is read exactly as it stands. Galley did not write
  it and cannot know when it is finished. Another agent may leave work
  uncommitted for hours — `codex/pat-2026-09-18` sat as nine dirty files for most
  of an afternoon — and a review that could not see that would be a review of
  nothing at all.

Galley never writes to a checkout it did not make. Not the files, and not the
index either: that is why `git.changed_against` asks about untracked files
separately instead of running `git add -N`, which is the tidier command and
would quietly stage another agent's work. It is also why a build of someone
else's branch happens in `build_tree` — a copy of Galley's own — rather than
where latexmk would otherwise scatter its droppings.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from . import git, worktree

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class UnknownBranch(LookupError):
    """Asked to review a branch that is not there."""


def slug(branch: str) -> str:
    """A directory name for a branch's build outputs.

    Hashed as well as flattened, because flattening alone is not injective:
    `codex/pat` and `codex-pat` are different branches that would otherwise
    share a build directory, and one would serve the other's PDF.
    """
    safe = _UNSAFE.sub("-", branch).strip("-")[:48] or "branch"
    return f"{safe}-{hashlib.sha1(branch.encode()).hexdigest()[:8]}"


@dataclass(frozen=True)
class Source:
    """One thing you can review."""

    repo: Path
    branch: str
    #: 'session' while a conversation for it is still on the rail, else 'branch'.
    kind: str
    label: str
    #: The commit it forked from. Every change is measured from here.
    base: str
    #: A checkout Galley did not make, read as it stands — work in flight and
    #: all. None when the branch has no checkout, or has one Galley made.
    live: Path | None
    session_id: str | None
    #: Files changed in `live` but not committed. Always 0 without one.
    uncommitted: int
    #: When the branch tip was committed.
    updated_at: int

    @property
    def slug(self) -> str:
        return slug(self.branch)

    def changed(self) -> list[dict]:
        """What this source holds that its base does not, file by file.

        Two fields beyond the counts, and both exist to stop the merge pane
        offering something it should not:

        `gone` — the branch deleted this file. Without it the pane reads the
        deletion as "every sentence replaced by nothing", and taking that writes
        an empty file over your paper. Galley says what happened and leaves the
        deleting to you.

        `editable` — there is text here to compare. A figure that changed is
        worth naming and cannot be reviewed as sentences.
        """
        entries = (
            git.changed_against(self.live, self.base)
            if self.live is not None
            else git.changed_files(self.repo, self.base, self.branch)
        )
        out = []
        for entry in entries:
            gone = not self.has(entry["path"])
            out.append({**entry, "gone": gone, "editable": not entry["binary"] and not gone})
        return out

    def has(self, rel: str) -> bool:
        """Does this source have this file at all?"""
        if self.live is not None:
            return (self.live / rel).is_file()
        listed = git.run(self.repo, "ls-tree", "--name-only", self.branch, "--", rel, check=False)
        return bool(listed.strip())

    def read(self, rel: str) -> str:
        """One file as this source has it, empty if it does not have it."""
        if self.live is not None:
            path = self.live / rel
            # The encoding is named for the same reason `Deps.read_working`
            # names it: a paper is full of non-ASCII, and the process locale is
            # not a property of the file.
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        return git.show(self.repo, self.branch, rel)

    def at_base(self, rel: str) -> str:
        """The file as it was when this source forked, for telling who moved."""
        return git.show(self.repo, self.base, rel)

    def conflicts(self) -> list[str]:
        """Files in the middle of a merge, which are not sentences yet."""
        return git.conflicted(self.live) if self.live is not None else []

    def state(self) -> str:
        """A fingerprint that moves when the source does.

        The review holds a copy of the diff while you answer it, and the branch
        underneath can move while you do — another agent is a process, not a
        document. Comparing this with the one the diff was read at is how the
        pane knows to say so. It happened once while this was being written.
        """
        parts = [git.head_sha(self.repo, self.branch)]
        if self.live is not None:
            for entry in self.changed():
                path = self.live / entry["path"]
                stat = path.stat() if path.is_file() else None
                parts.append(f"{entry['path']}:{stat.st_mtime_ns if stat else 0}")
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        return {
            "branch": self.branch,
            "kind": self.kind,
            "label": self.label,
            "base": self.base,
            "session_id": self.session_id,
            "uncommitted": self.uncommitted,
            "updated_at": self.updated_at,
            "state": self.state(),
        }


def listing(cfg: Config, sessions: list[dict]) -> list[Source]:
    """Every branch you could review, newest first.

    Not the branch you are on: reviewing your working copy against itself is a
    comparison with no question in it, and it would come up empty with nothing
    on screen to explain why.

    This reads and never writes. Making a checkout to answer it would mean
    listing seven branches created seven git worktrees.
    """
    repo = cfg.paths.paper_repo
    here = {cfg.paper.main_branch, git.current_branch(repo)}
    rows = {row["branch"]: row for row in sessions}
    found = [
        _build(cfg, repo, entry["name"], rows.get(entry["name"]), entry["ts"])
        for entry in git.local_branches(repo)
        if entry["name"] not in here
    ]
    return sorted(found, key=lambda source: source.updated_at, reverse=True)


def find(cfg: Config, sessions: list[dict], branch: str) -> Source:
    """One source by branch name."""
    repo = cfg.paths.paper_repo
    if not git.branch_exists(repo, branch):
        raise UnknownBranch(f"no branch {branch} to review")
    rows = {row["branch"]: row for row in sessions}
    return _build(cfg, repo, branch, rows.get(branch), _committed_at(repo, branch))


def built_in(cfg: Config, source: Source) -> Path:
    """Where a build of this source happens. Names it; never makes it.

    Clicking a line in a PDF has to know which tree TeX ran in, and it must not
    perform a checkout to find out.
    """
    own = _checkout(source.repo, source.branch, ours=True)
    return own if own is not None else cfg.paths.state_dir / "checkouts" / source.slug


def build_tree(cfg: Config, source: Source) -> Path:
    """A directory holding this source's files, for a tool that needs one.

    latexmk and latexdiff take a tree, not a revision. A session's own worktree
    is Galley's to build in. Anything else gets a detached checkout of Galley's
    own under the state directory, brought to the tip and then topped up with
    whatever the real checkout has not committed — so what is built is what you
    are reviewing, and the other agent's directory is never written to.

    Galley's checkouts are disposable and always at the tip. Someone else's is
    read where it is and never touched.
    """
    own = _checkout(source.repo, source.branch, ours=True)
    if own is not None:
        return own

    path = cfg.paths.state_dir / "checkouts" / source.slug
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # A checkout removed from underneath us leaves a registration behind,
        # and `worktree add` then refuses a path git still believes in.
        git.run(source.repo, "worktree", "prune", check=False)
        git.run(source.repo, "worktree", "add", "--detach", str(path), source.branch)
    else:
        git.run(path, "checkout", "--detach", "--force", source.branch)
    if source.live is not None:
        worktree.mirror(source.live, path)
    return path


def _build(cfg: Config, repo: Path, branch: str, row: dict | None, ts: int) -> Source:
    # A removed session leaves its branch behind on purpose — "nothing Claude
    # wrote is lost" is what the Remove button promises, and until now that was
    # a promise with nowhere to go. It comes back here as an ordinary branch,
    # still labelled with the prompt that made it, so you can tell which one it
    # was, and with no session id, because there is no conversation left to open.
    alive = row is not None and row["status"] != "removed"
    live = _checkout(repo, branch, ours=False)
    return Source(
        repo=repo,
        branch=branch,
        kind="session" if alive else "branch",
        label=(row["prompt"] if row else branch),
        base=_base_of(cfg, repo, branch, row),
        live=live,
        session_id=row["id"] if alive and row else None,
        uncommitted=len(git.status(live)) if live is not None else 0,
        updated_at=ts,
    )


def _base_of(cfg: Config, repo: Path, branch: str, row: dict | None) -> str:
    """The commit a source forked from.

    A session's base is the seed commit that carried your uncommitted work into
    its worktree. The fork point rather than the branch name, and this is the
    reason: a session carries your uncommitted work in as its first commit, so
    diffing against the main branch would report your own unsaved paragraphs as
    the agent's work.

    A branch written somewhere else has no such commit, so its honest fork point
    is where it and your main branch last agreed.
    """
    if row and row["base_sha"]:
        return str(row["base_sha"])
    try:
        return git.merge_base(repo, cfg.paper.main_branch, branch)
    except git.GitError:
        # Unrelated histories. The whole branch is then the change, which is
        # true and readable, rather than an error where a review should be.
        return git.run(repo, "rev-list", "--max-parents=0", branch).splitlines()[-1].strip()


def _checkout(repo: Path, branch: str, ours: bool) -> Path | None:
    """The checkout of `branch`, if the side asking for it made it.

    Galley's own live under `<repo>/.worktrees/`; that is the structural fact,
    rather than the branch's name, so a branch renamed by hand cannot change who
    owns its files.
    """
    mine = repo / ".worktrees"
    for entry in worktree.trees(repo):
        # A detached checkout has no branch line at all, and a paper repository
        # collects them — FLM has three, left by pushes to Overleaf.
        if entry.get("branch") != branch:
            continue
        path = Path(entry["path"])
        if path.is_dir() and (path.parent == mine) == ours:
            return path
    return None


def _committed_at(repo: Path, branch: str) -> int:
    entries = git.log(repo, 1, branch)
    return entries[0]["ts"] if entries else 0
