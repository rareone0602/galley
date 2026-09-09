"""One git worktree per Claude session.

The worktree is disposable; the branch is the record. An agent gets its own
checkout on `claude/<slug>`, so your main working tree is never touched and two
sessions can run at once without seeing each other's edits.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import git

BRANCH_PREFIX = "claude/"
_SLUG_OK = re.compile(r"[^a-z0-9]+")


def slugify(text: str, limit: int = 40) -> str:
    slug = _SLUG_OK.sub("-", text.lower()).strip("-")[:limit].strip("-")
    return slug or "session"


def unique_slug(repo: Path, base: str) -> str:
    slug, n = base, 2
    while git.branch_exists(repo, BRANCH_PREFIX + slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


@dataclass(frozen=True)
class Worktree:
    slug: str
    branch: str
    path: Path
    base_sha: str


EXCLUDE_MARKER = "# galley: session worktrees live here"


def ensure_ignored(repo: Path) -> None:
    """Keep `.worktrees/` out of the paper's status, without touching .gitignore.

    `.git/info/exclude` is local to this clone: it never enters a commit and so
    never reaches Overleaf, which is exactly the right place for a directory
    that only exists because Galley is running.
    """
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ""
    if EXCLUDE_MARKER in current:
        return
    suffix = "" if current.endswith("\n") or not current else "\n"
    exclude.write_text(current + suffix + f"{EXCLUDE_MARKER}\n.worktrees/\n")


def create(repo: Path, slug: str, base: str, seed: bool = True) -> Worktree:
    """Add `.worktrees/<slug>` on a new branch `claude/<slug>` off `base`.

    `seed` copies your *uncommitted* work into the new checkout and commits it
    first. Without it the agent forks from the last commit, so a sentence you
    typed a minute ago simply is not in the file it opens — and worse, the merge
    pane would then read your own unsaved paragraph as a change Claude wants to
    make. `base_sha` is the fork point either way, which is what the diff is
    taken against.
    """
    ensure_ignored(repo)
    slug = unique_slug(repo, slugify(slug))
    branch = BRANCH_PREFIX + slug
    path = repo / ".worktrees" / slug
    if path.exists():
        raise FileExistsError(f"{path} already exists")

    git.run(repo, "worktree", "add", str(path), "-b", branch, base)
    # A commit by the agent must be legible as one in `git log`.
    git.run(path, "config", "user.name", "Claude (galley)")
    git.run(path, "config", "user.email", "galley@localhost")

    base_sha = git.head_sha(repo, base)
    if seed:
        base_sha = seed_working_copy(repo, path) or base_sha
    return Worktree(slug=slug, branch=branch, path=path, base_sha=base_sha)


def seed_working_copy(repo: Path, tree: Path) -> str | None:
    """Mirror the main worktree's uncommitted state into `tree` and commit it.

    Returns the sha of the seed commit, or None if there was nothing to carry
    over. Untracked files come too: a new section you have not committed yet is
    part of the paper as far as you are concerned.
    """
    carried = 0
    for entry in git.status(repo):
        rel = entry.path
        if rel.split("/")[0] in (".worktrees", ".galley"):
            continue
        source, target = repo / rel, tree / rel
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            carried += 1
        elif target.exists():
            target.unlink()
            carried += 1

    if not carried or git.is_clean(tree):
        return None
    git.run(tree, "add", "-A")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    git.run(
        tree,
        "commit",
        "-m",
        f"Galley: your working copy at {stamp}\n\n"
        f"{carried} uncommitted file(s) carried in so the agent sees the paper "
        "as you do. This commit is the diff base; it is never pushed.",
    )
    return git.head_sha(tree)


def listing(repo: Path) -> list[dict]:
    out = git.run(repo, "worktree", "list", "--porcelain")
    trees: list[dict] = []
    current: dict = {}
    for line in out.splitlines():
        if not line:
            if current:
                trees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = {"path": value}
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in ("HEAD", "detached", "bare", "locked"):
            current[key] = value or True
    if current:
        trees.append(current)
    return [t for t in trees if t.get("branch", "").startswith(BRANCH_PREFIX)]


def remove(repo: Path, slug: str, keep_branch: bool = True) -> None:
    """Drop the checkout. The branch stays as provenance unless asked otherwise."""
    path = repo / ".worktrees" / slug
    git.run(repo, "worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    git.run(repo, "worktree", "prune")
    if not keep_branch:
        git.run(repo, "branch", "-D", BRANCH_PREFIX + slug, check=False)
