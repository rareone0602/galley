"""A thin, honest wrapper around the real `git` binary.

No GitPython, no pygit2. Git's porcelain is stable, its plumbing
(`--porcelain=v2`, `--numstat`) exists for exactly this, and shelling out means
what Galley sees is what your terminal sees.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {stderr.strip()}")
        self.args_ = args
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class FileStatus:
    path: str
    index: str
    worktree: str

    @property
    def staged(self) -> bool:
        return self.index not in (".", "?")

    @property
    def untracked(self) -> bool:
        return self.index == "?"


def run(repo: Path, *args: str, check: bool = True, timeout: float = 120) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # A remote that accepts the connection and then says nothing hangs here
        # for the full timeout. Every caller already handles GitError; letting
        # this one escape as itself turns a slow network into a 500.
        raise GitError(list(args), -1, f"gave up after {timeout:g}s with no answer") from exc
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc.stdout


def head_sha(repo: Path, rev: str = "HEAD") -> str:
    return run(repo, "rev-parse", rev).strip()


def current_branch(repo: Path) -> str:
    return run(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def toplevel(repo: Path) -> Path:
    return Path(run(repo, "rev-parse", "--show-toplevel").strip())


def is_clean(repo: Path) -> bool:
    return not run(repo, "status", "--porcelain").strip()


def status(repo: Path) -> list[FileStatus]:
    """Working-tree status, parsed from `--porcelain=v2` rather than guessed."""
    out = run(repo, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    entries: list[FileStatus] = []
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if not rec:
            continue
        head = rec[0]
        if head == "1":
            parts = rec.split(" ", 8)
            entries.append(FileStatus(parts[8], parts[1][0], parts[1][1]))
        elif head == "2":
            parts = rec.split(" ", 9)
            entries.append(FileStatus(parts[9], parts[1][0], parts[1][1]))
            i += 1  # rename/copy records are followed by the original path
        elif head == "?":
            entries.append(FileStatus(rec[2:], "?", "?"))
        elif head == "u":
            parts = rec.split(" ", 10)
            entries.append(FileStatus(parts[10], "U", "U"))
    return entries


def conflicted(repo: Path) -> list[str]:
    out = run(repo, "diff", "--name-only", "--diff-filter=U")
    return [line for line in out.splitlines() if line]


def changed_files(repo: Path, base: str, head: str) -> list[dict]:
    """Files that differ between two revisions, with insert/delete counts."""
    out = run(repo, "diff", "--numstat", "-z", f"{base}...{head}")
    files: list[dict] = []
    fields = [f for f in out.split("\0") if f]
    i = 0
    while i < len(fields):
        parts = fields[i].split("\t")
        if len(parts) < 3:
            i += 1
            continue
        added, removed, path = parts[0], parts[1], parts[2]
        if path == "":  # rename: the two paths follow as separate records
            i += 1
            path = fields[i + 1] if i + 1 < len(fields) else ""
            i += 1
        files.append(
            {
                "path": path,
                "added": None if added == "-" else int(added),
                "removed": None if removed == "-" else int(removed),
                "binary": added == "-",
            }
        )
        i += 1
    return files


def show(repo: Path, rev: str, path: str) -> str:
    """A file's content at a revision; empty string if it did not exist."""
    try:
        return run(repo, "show", f"{rev}:{path}")
    except GitError:
        return ""


def merge_base(repo: Path, a: str, b: str) -> str:
    return run(repo, "merge-base", a, b).strip()


def branch_exists(repo: Path, name: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def commit(repo: Path, message: str, paths: list[str]) -> str:
    """Stage exactly these paths and commit them. Never `git add -A`."""
    if not paths:
        raise GitError(["commit"], 1, "no paths given; galley never stages everything")
    run(repo, "add", "--", *paths)
    run(repo, "commit", "-m", message, "--", *paths)
    return head_sha(repo)


def log(repo: Path, limit: int = 20, rev: str = "HEAD") -> list[dict]:
    out = run(
        repo, "log", f"-{limit}", "--format=%H%x1f%an%x1f%at%x1f%s", rev, check=False
    )
    entries = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            entries.append(
                {"sha": parts[0], "author": parts[1], "ts": int(parts[2]), "subject": parts[3]}
            )
    return entries
