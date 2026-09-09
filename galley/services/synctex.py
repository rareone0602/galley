"""Read a `.synctex.gz` file, so double-clicking the PDF lands on the source.

TeX writes this file when compiled with `-synctex=1`: for every box it puts on
a page it records which input file and line produced it, and where the box
ended up. Reverse search is then a geometry question — which recorded box is
under the point you clicked?

TeX Live ships a `synctex` command that answers exactly this, but it is a
separate package and is not installed everywhere a paper compiles. The format is
small and documented, so Galley reads it directly: no second thing to install,
and the feature works wherever `latexmk` does.

Coordinates. The file stores scaled points (sp): 65536 sp to one TeX point, and
one TeX point is 1/72.27 inch. A PDF viewer works in *big* points, 1/72 inch.
The origin is the top-left of the page with y running down, which is what a
canvas uses too, so only the scale differs.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path

SP_PER_POINT = 65536
BIG_POINTS_PER_POINT = 72 / 72.27


@dataclass
class Node:
    """One recorded box or point. `width` is None for a point record."""

    tag: int
    line: int
    x: int
    y: int
    width: int | None = None
    height: int = 0
    depth: int = 0
    children: list["Node"] = field(default_factory=list)

    @property
    def is_box(self) -> bool:
        return self.width is not None

    def contains(self, x: int, y: int) -> bool:
        """The point is inside this box. `y` is the baseline; height goes up."""
        if self.width is None:
            return False
        left, right = min(self.x, self.x + self.width), max(self.x, self.x + self.width)
        return left <= x <= right and self.y - self.height <= y <= self.y + self.depth

    def distance(self, x: int, y: int) -> float:
        if self.width is None:
            return ((self.x - x) ** 2 + (self.y - y) ** 2) ** 0.5
        left, right = min(self.x, self.x + self.width), max(self.x, self.x + self.width)
        dx = max(left - x, 0, x - right)
        dy = max((self.y - self.height) - y, 0, y - (self.y + self.depth))
        return (dx * dx + dy * dy) ** 0.5


@dataclass(frozen=True)
class Location:
    path: str
    line: int


# `<kind><tag>,<line>[,<column>]:<x>,<y>[:<more>]` — kern and glue carry a
# width after that, boxes carry width, height and depth.
_RECORD = re.compile(r"^([\[\(hvxkg$])(\d+),(\d+)(?:,\d+)?:(-?\d+),(-?\d+)(?::(.*))?$")
_INPUT = re.compile(r"^Input:(\d+):(.*)$")
_SETTING = re.compile(r"^(Unit|X Offset|Y Offset|Magnification):(-?\d+)$")


class SyncTeX:
    """A parsed `.synctex.gz`: which file and line produced what, and where."""

    def __init__(self) -> None:
        self.inputs: dict[int, str] = {}
        self.pages: dict[int, Node] = {}
        self.unit = 1
        self.x_offset = 0
        self.y_offset = 0
        self.magnification = 1000

    # -- reading ----------------------------------------------------------

    @classmethod
    def read(cls, path: Path) -> "SyncTeX":
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", errors="replace") as handle:  # type: ignore[operator]
            return cls.parse(handle)

    @classmethod
    def parse(cls, lines) -> "SyncTeX":
        self = cls()
        stack: list[Node] = []
        page: Node | None = None

        for raw in lines:
            line = raw.rstrip("\n")
            if not line:
                continue

            match = _INPUT.match(line)
            if match:
                self.inputs[int(match.group(1))] = match.group(2)
                continue
            match = _SETTING.match(line)
            if match:
                key, value = match.group(1), int(match.group(2))
                setattr(
                    self,
                    {"Unit": "unit", "X Offset": "x_offset", "Y Offset": "y_offset"}.get(
                        key, "magnification"
                    ),
                    value,
                )
                continue

            if line.startswith("{"):
                number = int(line[1:] or 0)
                page = Node(tag=0, line=0, x=0, y=0, width=0)
                self.pages[number] = page
                stack = [page]
                continue
            if line.startswith("}"):
                page, stack = None, []
                continue
            if line[0] in "])":
                if len(stack) > 1:
                    stack.pop()
                continue

            match = _RECORD.match(line)
            if not match or not stack:
                continue
            kind, tag, source_line, x, y, rest = match.groups()
            node = Node(tag=int(tag), line=int(source_line), x=int(x), y=int(y))
            if rest:
                parts = rest.split(",")
                if len(parts) >= 3:
                    node.width, node.height, node.depth = (int(p) for p in parts[:3])
                elif len(parts) == 1:
                    node.width = int(parts[0])  # a kern: width but no height
            stack[-1].children.append(node)
            if kind in "[(":
                stack.append(node)

        return self

    # -- reverse search ---------------------------------------------------

    def edit(self, page: int, x_bp: float, y_bp: float) -> Location | None:
        """Which source line produced what is at (x, y) big points on `page`?"""
        root = self.pages.get(page)
        if root is None:
            return None
        x = self._to_sp(x_bp) - self.x_offset
        y = self._to_sp(y_bp) - self.y_offset

        node = self._deepest(root, x, y) or self._nearest(root, x, y)
        if node is None:
            return None
        path = self.inputs.get(node.tag)
        return Location(path=path, line=node.line) if path else None

    def _to_sp(self, big_points: float) -> int:
        points = big_points / BIG_POINTS_PER_POINT
        return round(points * SP_PER_POINT * 1000 / (self.magnification or 1000) / self.unit)

    def _deepest(self, root: Node, x: int, y: int) -> Node | None:
        """The smallest box under the point, then its nearest child.

        Two steps because the box and its contents answer different questions.
        A line of text is one box, and TeX labels that box with the line where
        the *paragraph* ended — often a blank line. The words inside it carry
        the line they were actually written on, which is the one you want.
        """
        best: Node | None = None
        best_area = None
        for node in self._walk(root):
            if not node.is_box or not node.contains(x, y):
                continue
            area = abs(node.width or 0) * (node.height + node.depth)
            if best_area is None or area < best_area:
                best, best_area = node, area
        if best is None:
            return None
        # Within the line, the nearest thing horizontally is the word you hit.
        inside = [c for c in best.children if not c.is_box] or best.children
        return min(inside, key=lambda c: abs(c.x - x), default=best)

    def _nearest(self, root: Node, x: int, y: int) -> Node | None:
        candidates = [n for n in self._walk(root) if n is not root]
        return min(candidates, key=lambda n: n.distance(x, y), default=None)

    def _walk(self, node: Node):
        for child in node.children:
            yield child
            if child.children:
                yield from self._walk(child)


def resolve(location: Location, repo: Path, build_dir: Path) -> dict:
    """Turn a recorded path into one the editor can open.

    TeX records the path as it saw it, which is relative to wherever latexmk
    ran. A file inside the project comes back repo-relative; a class or package
    from the TeX distribution comes back as itself, marked as not ours, because
    the editor has nothing to open.
    """
    raw = Path(location.path)
    root = repo.resolve()
    for base in (repo, build_dir):
        absolute = (raw if raw.is_absolute() else base / raw).resolve()
        try:
            return {
                "path": str(absolute.relative_to(root)),
                "line": location.line,
                "in_project": True,
            }
        except ValueError:
            pass
        # The marked-up review compiles a scratch copy of the tree, so its
        # paths sit outside the repository while naming files inside it.
        try:
            inside = absolute.relative_to(build_dir.resolve())
        except ValueError:
            continue
        if (root / inside).is_file():
            return {"path": str(inside), "line": location.line, "in_project": True}
    return {"path": location.path, "line": location.line, "in_project": False}


def find(pdf: Path) -> Path | None:
    """The synctex file beside a compiled PDF, gzipped or not."""
    for suffix in (".synctex.gz", ".synctex"):
        candidate = pdf.parent / (pdf.stem + suffix)
        if candidate.is_file():
            return candidate
    return None
