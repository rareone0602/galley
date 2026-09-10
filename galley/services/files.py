"""The project file tree, the way Overleaf's left rail shows it, and the four
things that rail can do to it: create, rename, delete, and take an upload.

Git decides what is in the project, not the filesystem: `git ls-files` plus the
untracked-but-not-ignored files is exactly the set you would see in a fresh
clone, so build artefacts, `.worktrees/`, and everything else `.gitignore`
covers never appear. That also means the tree is the same set of files that can
reach Overleaf, which is the only definition of "the project" that matters here.

The same rule decides what may be written. A path `.gitignore` covers is
refused rather than created, because a file that cannot reach Overleaf cannot
be part of the paper, and a silent success there looks exactly like a bug.
`resolve()` is the one containment check in Galley; every function below that
touches the disk goes through it.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import git

IMAGE_SUFFIXES = frozenset(".png .jpg .jpeg .gif .svg .webp".split())
# Not text, but the browser can draw them, so they open in their own view.
FIGURE_SUFFIXES = frozenset(".pdf .eps .ps".split())
# Bytes that are not text and never will be. Everything not named here is
# assumed to be readable, and `read()` settles it by looking at the file.
OPAQUE_SUFFIXES = frozenset(
    ".zip .gz .bz2 .xz .zst .7z .tar .tgz .rar "
    ".woff .woff2 .ttf .otf .eot "
    ".so .o .a .dylib .dll .exe .pyc .pyo .bin .dat "
    ".db .sqlite .sqlite3 .npy .npz .pt .pth .ckpt .safetensors .h5 .parquet "
    ".mp4 .mov .avi .mkv .webm .mp3 .wav .flac .ogg .ico".split()
)

# Enough of the front of a file to tell prose from a payload. Git uses the
# same trick and the same reasoning: a byte that cannot appear in text is
# conclusive, and reading the whole file to find one is not worth it. A NUL
# further in than this reads as text and arrives in the editor as U+FFFD.
SNIFF_BYTES = 8192
# What the editor will take responsibility for. Past this the browser tab
# stops being usable, and a file this size in a paper is a log rather than
# something you are about to edit.
MAX_TEXT_BYTES = 2 * 1024 * 1024
# So one is still readable: the front of it, read-only, and the bar says so.
PREVIEW_BYTES = 512 * 1024

HIDDEN = (".worktrees", ".git", ".galley")

# A new name has to survive three readers: LaTeX, which cannot `\input` a name
# containing a space or any of # % $ & { } ~ ^ \ ; git; and whatever filesystem
# Overleaf runs on. An allowlist is the honest intersection. A leading dot is
# out because a dotfile is plumbing rather than paper — and because it is how
# `..` would get in.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")

# A figure is a few megabytes. The whole body is held in memory while it is
# written, and anything past this is a slip rather than a plot.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class InvalidName(ValueError):
    """A name Galley will not put in a paper. The message says which rule."""


class Refused(ValueError):
    """The name is fine; the project is not in a state where this can happen.

    Something is already there, the folder is not empty, the file is the one
    the build compiles. Distinct from `InvalidName` because the caller can fix
    an invalid name by retyping it, and one of these by doing something else
    first.
    """


def kind_of(rel: str) -> str:
    """What the name suggests the file is, for the rail's icon.

    A guess, and deliberately a generous one: anything not known to be a
    picture, a figure or a payload is called text. The old rule was the other
    way round — an allowlist of extensions, everything else binary — and it
    was wrong twice over. A `.ts` or a `.lean` refused to open although the
    editor has a mode for it, and a project that was not this paper had a rail
    of files it could not read. `read()` is the one that decides for real, by
    looking at the bytes, so a wrong guess here costs an icon and nothing else.
    """
    name = Path(rel).name
    suffix = Path(rel).suffix.lower()
    # `.gitignore`, `Makefile`: no suffix, plainly editable text.
    if not suffix or name.startswith("."):
        return "text"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in FIGURE_SUFFIXES:
        return "figure"
    if suffix in OPAQUE_SUFFIXES:
        return "binary"
    return "tex" if suffix == ".tex" else "text"


def is_text(rel: str) -> bool:
    """Whether the name suggests text. The guess, not the answer."""
    return kind_of(rel) in ("tex", "text")


def listing(repo: Path) -> list[str]:
    """Every file in the project, sorted, as forward-slash relatives."""
    tracked = git.run(repo, "ls-files", "-z").split("\0")
    untracked = git.run(
        repo, "ls-files", "--others", "--exclude-standard", "-z"
    ).split("\0")
    paths = {p for p in (*tracked, *untracked) if p}
    return sorted(p for p in paths if not p.split("/")[0] in HIDDEN)


def empty_folders(repo: Path, files: list[str]) -> list[str]:
    """Folders you have made but not yet filled.

    Git does not track a directory, so a folder you have just created has no
    file in `listing()` to hang it on and the rail would look as though New
    folder had done nothing. `--directory` reports exactly the untracked
    folders git can see, and `--exclude-standard` has already dropped the ones
    `.gitignore` covers, so the rule of the module still holds.
    """
    out = git.run(repo, "ls-files", "--others", "--exclude-standard", "--directory", "-z")
    folders = [entry.rstrip("/") for entry in out.split("\0") if entry.endswith("/")]
    return [
        folder
        for folder in folders
        if folder.split("/")[0] not in HIDDEN
        and not any(f.startswith(folder + "/") for f in files)
    ]


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

    def folder(prefix: str) -> Node:
        """The node for a folder, making the chain above it if need be."""
        if prefix not in index:
            head, _, name = prefix.rpartition("/")
            node = Node(name, prefix, "dir")
            folder(head).children.append(node)
            index[prefix] = node
        return index[prefix]

    files = listing(repo)
    for rel in files:
        head, _, name = rel.rpartition("/")
        folder(head).children.append(Node(name, rel, kind_of(rel)))
    for rel in empty_folders(repo, files):
        folder(rel)

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
    through here, so there is no second copy to forget to fix. Because it
    resolves symlinks first, a link inside the project that points out of it
    escapes exactly like `../` does.
    """
    if not rel or rel.startswith("/"):
        raise ValueError("a path must be relative to the project root")
    target = (repo / rel).resolve()
    target.relative_to(repo.resolve())  # raises ValueError if it escapes
    return target


def read(repo: Path, rel: str) -> dict:
    """A file as the editor gets it, and whether it may be written back.

    Four answers, and the file's own bytes decide between them rather than its
    name: a picture, a payload with nothing to show, text, or the front of
    something too big to edit. `editable` is the one the editor obeys — the
    two ways a file can be readable but not writable are both ways of losing
    work silently, so neither is left to the caller to notice.
    """
    path = resolve(repo, rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    size = path.stat().st_size
    kind = kind_of(rel)
    if kind in ("image", "figure"):
        return _unreadable(rel, kind, size)

    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
        if b"\0" in head:
            return _unreadable(rel, "binary", size)
        truncated = size > MAX_TEXT_BYTES
        limit = PREVIEW_BYTES if truncated else MAX_TEXT_BYTES
        data = head + handle.read(max(0, limit - len(head)))

    if truncated:
        data = _whole_lines(data)
    try:
        text, encoding = data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        # Something in the file is not UTF-8 — a Latin-1 accent in an old
        # `.bib`, usually. It is still worth reading, so it is shown with the
        # bad bytes replaced; what it must not be is writable, because saving
        # would put those replacements on disk over the real ones.
        text, encoding = data.decode("utf-8", errors="replace"), "unknown"

    return {
        "path": rel,
        "type": "tex" if kind == "tex" else "text",
        "content": text,
        "bytes": size,
        "truncated": truncated,
        "encoding": encoding,
        "editable": not truncated and encoding == "utf-8",
    }


def _unreadable(rel: str, kind: str, size: int) -> dict:
    """A file with no text in it. The editor shows it, or says it cannot."""
    return {
        "path": rel,
        "type": kind,
        "content": None,
        "bytes": size,
        "truncated": False,
        "encoding": None,
        "editable": False,
    }


def _whole_lines(data: bytes) -> bytes:
    """Cut the preview back to the last complete line.

    The cap falls at a byte, which is halfway through a line and can be
    halfway through a character — and half a character decodes as U+FFFD,
    which would make a perfectly good UTF-8 file report itself as some other
    encoding. Cutting at the last newline settles both at once. A file with no
    newline in the first half-megabyte gets whole characters instead.
    """
    end = data.rfind(b"\n")
    if end != -1:
        return data[: end + 1]
    for trim in range(1, 4):
        try:
            data[:-trim].decode("utf-8")
        except UnicodeDecodeError:
            continue
        return data[:-trim]
    return data


# -- what a name may be -------------------------------------------------------


def check_name(name: str) -> str:
    """One segment of a path, as somebody typed it into the rail."""
    if not name:
        raise InvalidName("a name cannot be empty")
    if "/" in name or "\\" in name:
        raise InvalidName(
            f"{name!r}: a name cannot contain a path separator — "
            "make the folder first, then create the file inside it"
        )
    if name.startswith("."):
        raise InvalidName(f"{name!r}: a name cannot start with a dot")
    if not NAME.match(name):
        raise InvalidName(
            f"{name!r}: use letters, digits and . _ + - only. "
            "LaTeX cannot \\input a name with anything else in it."
        )
    return name


def check_path(rel: str) -> str:
    """Every segment of a new relative path, by the rule for a single name.

    Rename is also move, so its destination is a path rather than a name; each
    part of it still has to be a name a person could have typed.
    """
    if not rel:
        raise InvalidName("a path cannot be empty")
    for part in rel.split("/"):
        check_name(part)
    return rel


def is_ignored(repo: Path, rel: str) -> bool:
    """Would `.gitignore` hide this path from the project?

    Asked before writing rather than after: a plot dropped into a repository
    that ignores `*.pdf` would never reach Overleaf, and the useful answer is
    to say so, not to leave a file the rail cannot show.
    """
    return bool(git.run(repo, "check-ignore", "--", rel, check=False).strip())


def _refuse_plumbing(rel: str) -> None:
    """`.git`, `.worktrees` and `.galley` are not part of the paper.

    The rail never shows them, so nothing in the UI can ask for this — but the
    routes are reachable by hand, and `git rm -f .git` is not recoverable.
    """
    if rel.split("/")[0] in HIDDEN:
        raise Refused(f"{rel} is Galley's own plumbing, not part of the paper")


def _refuse_the_main_file(rel: str, main_tex: str, verb: str) -> None:
    """The build compiles one document by name; losing it breaks every build."""
    if rel == main_tex or main_tex.startswith(rel + "/"):
        raise Refused(
            f"{main_tex} is the document the build compiles, so it cannot be "
            f"{verb}. Change main_tex in galley.local.toml first."
        )


def _folder(repo: Path, parent: str) -> Path:
    """The folder something is about to go into. `""` is the project root."""
    if not parent:
        return repo.resolve()
    _refuse_plumbing(parent)
    target = resolve(repo, parent)
    if not target.is_dir():
        raise Refused(f"there is no folder {parent} — create it first")
    return target


def _joined(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def _entry(repo: Path, rel: str) -> Path:
    """The entry itself, having gone through the containment check.

    `resolve()` follows symlinks. That is what a read wants, and it is what
    makes the containment check honest — a link out of the project escapes
    exactly like `../` does. It is not what a write wants: deleting a link
    should remove the link, not the file at the far end of it.
    """
    resolve(repo, rel)
    return repo / rel


def _is_there(path: Path) -> bool:
    """A dangling symlink is still something in the way."""
    return path.exists() or path.is_symlink()


def _is_tracked(repo: Path, rel: str) -> bool:
    """Does git know this path? A folder counts if anything under it is tracked."""
    return bool(git.run(repo, "ls-files", "--", rel, check=False).strip())


# -- changing the project -----------------------------------------------------


def create(repo: Path, parent: str, name: str, *, folder: bool = False) -> str:
    """A new empty file, or a new folder, inside `parent`. Returns its path.

    Nothing is staged. An untracked file is already part of the project as far
    as the rail is concerned, and Galley never adds a path you did not tick.
    """
    check_name(name)
    _folder(repo, parent)
    rel = _joined(parent, name)
    target = _entry(repo, rel)
    if _is_there(target):
        raise Refused(f"{rel} is already there")
    if is_ignored(repo, rel):
        raise Refused(
            f".gitignore covers {rel}, so it would never reach Overleaf. "
            "Pick another name, or take the pattern out of .gitignore."
        )
    if folder:
        target.mkdir()
    else:
        target.touch()
    return rel


def rename(repo: Path, rel: str, new_rel: str, *, main_tex: str) -> str:
    """Rename a file or folder — which is also how you move one.

    `git mv` for anything tracked, so the history follows the file instead of
    showing a delete and an add. It is one operation: either it moves the file
    and updates the index, or it does neither, so a git failure cannot leave
    the working tree half-renamed.
    """
    _refuse_plumbing(rel)
    source = _entry(repo, rel)
    if not _is_there(source):
        raise FileNotFoundError(rel)
    _refuse_the_main_file(rel, main_tex, "renamed")

    check_path(new_rel)
    target = _entry(repo, new_rel)
    if target == source:
        return new_rel
    if _is_there(target):
        raise Refused(f"{new_rel} is already there")
    if not target.parent.is_dir():
        raise Refused(
            f"there is no folder {new_rel.rpartition('/')[0]} — create it first"
        )
    if is_ignored(repo, new_rel):
        raise Refused(
            f".gitignore covers {new_rel}, so the file would drop out of the "
            "project. Pick another name, or take the pattern out of .gitignore."
        )

    if _is_tracked(repo, rel):
        git.run(repo, "mv", "--", rel, new_rel)
    else:
        source.rename(target)
    return new_rel


def delete(repo: Path, rel: str, *, main_tex: str) -> None:
    """Remove a file, or an empty folder.

    A folder with anything in it is refused rather than emptied: one click
    should not be able to take a whole section of the paper with it, and the
    rail can always delete the contents first.
    """
    _refuse_plumbing(rel)
    target = _entry(repo, rel)
    if not _is_there(target):
        raise FileNotFoundError(rel)
    _refuse_the_main_file(rel, main_tex, "deleted")

    if target.is_dir() and not target.is_symlink():
        if any(target.iterdir()):
            raise Refused(f"{rel} is not empty — delete what is in it first")
        target.rmdir()
        return
    if _is_tracked(repo, rel):
        git.run(repo, "rm", "-f", "-q", "--", rel)
    else:
        target.unlink()


def upload(
    repo: Path, parent: str, name: str, data: bytes, *, replace: bool = False
) -> str:
    """Take an uploaded file — a figure, usually — into `parent`.

    Replacing is opt-in so that a dropped file cannot quietly overwrite a plot
    that took an afternoon to make.
    """
    check_name(name)
    _folder(repo, parent)
    rel = _joined(parent, name)
    if len(data) > MAX_UPLOAD_BYTES:
        raise Refused(
            f"{rel} is {len(data) // (1024 * 1024)} MB; Galley takes up to "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )
    target = _entry(repo, rel)
    if target.is_dir():
        raise Refused(f"{rel} is a folder")
    if _is_there(target) and not replace:
        raise Refused(f"{rel} is already there")
    if is_ignored(repo, rel):
        raise Refused(
            f".gitignore covers {rel}, so it would never reach Overleaf. "
            "Rename it, or take the pattern out of .gitignore."
        )
    _write_whole(target, data)
    return rel


def _write_whole(target: Path, data: bytes) -> None:
    """A figure arrives complete or not at all.

    An upload can fail halfway — the browser goes away, the disk fills — and
    writing straight into the project would leave a truncated PDF where a plot
    used to be. The rename at the end is the only moment the project changes.
    """
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".galley-upload-")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        # mkstemp is 0600; a file in the paper should read like the rest of it.
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
