"""The project file tree, the way Overleaf's left rail shows it.

Git decides what is in the project, not the filesystem: `git ls-files` plus the
untracked-but-not-ignored files is exactly the set you would see in a fresh
clone, so build artefacts, `.worktrees/`, and everything else `.gitignore`
covers never appear. That also means the tree is the same set of files that can
reach Overleaf, which is the only definition of "the project" that matters here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import git

# What the editor can open as text, and what it must not try to.
TEXT_SUFFIXES = frozenset(
    ".tex .bib .cls .sty .bst .txt .md .csv .tsv .json .yaml .yml .toml .bbl .cfg "
    ".sh .py .gnuplot .log .make .mk".split()
)
IMAGE_SUFFIXES = frozenset(".png .jpg .jpeg .gif .svg .webp".split())
# Not text and not previewable, but a paper is full of them.
FIGURE_SUFFIXES = frozenset(".pdf .eps .ps".split())

HIDDEN = (".worktrees", ".git", ".galley")


def kind_of(rel: str) -> str:
    name = Path(rel).name
    suffix = Path(rel).suffix.lower()
    # `.gitignore`, `Makefile`: no suffix, plainly editable text.
    if not suffix or name.startswith("."):
        return "text"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in FIGURE_SUFFIXES:
        return "figure"
    if suffix in TEXT_SUFFIXES:
        return "tex" if suffix == ".tex" else "text"
    return "binary"


def is_text(rel: str) -> bool:
    return kind_of(rel) in ("tex", "text")


def listing(repo: Path) -> list[str]:
    """Every path in the project, sorted, as forward-slash relatives."""
    tracked = git.run(repo, "ls-files", "-z").split("\0")
    untracked = git.run(
        repo, "ls-files", "--others", "--exclude-standard", "-z"
    ).split("\0")
    paths = {p for p in (*tracked, *untracked) if p}
    return sorted(p for p in paths if not p.split("/")[0] in HIDDEN)


@dataclass
class Node:
    name: str
    path: str
    type: str  # "dir" or the file kind
    children: list["Node"] = field(default_factory=list)

    def as_dict(self) -> dict:
        out: dict = {"name": self.name, "path": self.path, "type": self.type}
        if self.type == "dir":
            out["children"] = [c.as_dict() for c in self.children]
        return out


def tree(repo: Path) -> list[dict]:
    """Nest the flat listing into folders, directories first then files.

    Sorting happens once here rather than in the client, so the rail's order is
    the same whoever renders it.
    """
    root = Node("", "", "dir")
    index: dict[str, Node] = {"": root}

    for rel in listing(repo):
        parts = rel.split("/")
        prefix = ""
        for part in parts[:-1]:
            parent = index[prefix]
            prefix = f"{prefix}/{part}" if prefix else part
            if prefix not in index:
                node = Node(part, prefix, "dir")
                parent.children.append(node)
                index[prefix] = node
        index[prefix].children.append(Node(parts[-1], rel, kind_of(rel)))

    def order(node: Node) -> None:
        node.children.sort(key=lambda c: (c.type != "dir", c.name.lower()))
        for child in node.children:
            if child.type == "dir":
                order(child)

    order(root)
    return [c.as_dict() for c in root.children]


def resolve(repo: Path, rel: str) -> Path:
    """A path inside `repo`, or ValueError.

    One owner for the containment check: every route that names a file goes
    through here, so there is no second copy to forget to fix.
    """
    if not rel or rel.startswith("/"):
        raise ValueError("a path must be relative to the project root")
    target = (repo / rel).resolve()
    target.relative_to(repo.resolve())  # raises ValueError if it escapes
    return target


def read(repo: Path, rel: str) -> dict:
    path = resolve(repo, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    kind = kind_of(rel)
    if not is_text(rel):
        return {
            "path": rel,
            "type": kind,
            "content": None,
            "bytes": path.stat().st_size,
        }
    data = path.read_bytes()
    return {
        "path": rel,
        "type": kind,
        "content": data.decode("utf-8", errors="replace"),
        "bytes": len(data),
    }
