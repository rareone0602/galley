"""One git worktree per Claude session.

The worktree is disposable; the branch is the record. An agent gets its own
checkout on `claude/<slug>`, so your main working tree is never touched and two
sessions can run at once without seeing each other's edits.
"""

from __future__ import annotations

import re
import shutil
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


def create(repo: Path, slug: str, base: str) -> Worktree:
    """Add `.worktrees/<slug>` on a new branch `claude/<slug>` off `base`."""
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
    return Worktree(slug=slug, branch=branch, path=path, base_sha=git.head_sha(repo, base))


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
