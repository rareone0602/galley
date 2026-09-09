"""Read a `.synctex.gz` file: the map between the printed page and the source.

TeX writes this file when compiled with `-synctex=1`: for every box it puts on
a page it records which input file and line produced it, and where the box
ended up. Both directions fall out of that one table. Reverse search — where
did *this* come from — is a geometry question: which recorded box is under the
point you clicked? Forward search — where did *that* go — is a lookup: which
boxes carry this file and this line?

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


@dataclass(frozen=True)
class Area:
    """A rectangle on one page, in big points from the page's top-left.

    The same units and origin `edit()` takes, so a point that came out of
    reverse search can be handed straight back in.
    """

    page: int
    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict:
        return {
            "page": self.page,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
        }

    def contains(self, x_bp: float, y_bp: float) -> bool:
        return (
            self.x <= x_bp <= self.x + self.width
            and self.y <= y_bp <= self.y + self.height
        )

    def covers(self, other: "Area") -> bool:
        """`other` sits wholly inside this one, so marking both says it twice."""
        return (
            self.page == other.page
            and self.x <= other.x
            and self.y <= other.y
            and self.x + self.width >= other.x + other.width
            and self.y + self.height >= other.y + other.height
        )


@dataclass(frozen=True)
class View:
    """Where a source line ended up in print.

    `line` is the line that was actually found. It differs from `asked_line`
    when the line you asked about put nothing on the page — a comment, a blank
    line, a `\\begin{...}` — and the search fell forward to the next line that
    did. Saying so is the point: landing somewhere plausible without admitting
    it is not the same answer.
    """

    path: str
    asked_line: int
    line: int
    areas: list[Area]

    @property
    def fell_forward(self) -> bool:
        return self.line != self.asked_line

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "asked_line": self.asked_line,
            "line": self.line,
            "fell_forward": self.fell_forward,
            "areas": [a.as_dict() for a in self.areas],
        }


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

    # -- forward search ---------------------------------------------------

    def view(self, path: str, line: int, repo: Path, tree: Path | None = None) -> View | None:
        """Where on the page did this line of this file end up?

        `path` is project-relative, the form the editor and `resolve()` speak;
        the file records its own paths, so every one of them is put through the
        same mapping to find the ones that name this file.

        Which rectangle to report is the mirror of the rule reverse search
        uses. A word is recorded as a bare point, which has nothing to mark, so
        a point is reported as the box holding it — the line of type it sits
        in. A record that is already a box speaks for itself.

        Nested results are then reduced to the outer one. One source line can
        be recorded several times over — a line box and the maths inside it, a
        table row and its cells — and the outer rectangle is both the one you
        would look for and the one that still contains the inner. Reducing the
        other way would drop the place a click actually landed, and forward and
        reverse search would stop agreeing.
        """
        tags = {
            tag
            for tag, recorded in self.inputs.items()
            if project_path(recorded, repo, tree) == path
        }
        if not tags:
            return None

        by_line: dict[int, list[tuple[int, Node, Node | None]]] = {}
        for page, root in sorted(self.pages.items()):
            for node, box in self._walk(root):
                if node.tag in tags:
                    by_line.setdefault(node.line, []).append((page, node, box))
        if not by_line:
            return None

        # A comment or a blank line typesets nothing, so the honest answer is
        # the next line that did, said out loud rather than passed off as this
        # one. Only forward: the line above is a different sentence.
        found = line if line in by_line else min((n for n in by_line if n > line), default=None)
        if found is None:
            return None

        marked: list[Area] = []
        seen: set[tuple] = set()
        for page, node, box in by_line[found]:
            area = self._area(page, node if node.is_box else (box or node))
            key = (area.page, round(area.x, 2), round(area.y, 2), round(area.width, 2))
            if key not in seen:
                seen.add(key)
                marked.append(area)

        # The glue between paragraphs is recorded as a box of no height. There is
        # nothing there to mark, so it goes — unless it is all the line left
        # behind, in which case where it sits is still the answer.
        marked = [a for a in marked if a.width and a.height] or marked
        areas = [a for a in marked if not any(b.covers(a) for b in marked if b is not a)]
        areas.sort(key=lambda a: (a.page, a.y, a.x))
        return View(path=path, asked_line=line, line=found, areas=areas)

    def _area(self, page: int, node: Node) -> Area:
        """A record's rectangle. `y` is the baseline: height goes up, depth down."""
        width = node.width or 0
        return Area(
            page=page,
            x=self._to_bp(min(node.x, node.x + width) + self.x_offset),
            y=self._to_bp(node.y - node.height + self.y_offset),
            width=self._to_bp(abs(width)),
            height=self._to_bp(node.height + node.depth),
        )

    # -- units ------------------------------------------------------------

    @property
    def _big_points_per_sp(self) -> float:
        """One scale, so the two directions can never drift apart."""
        return self.unit * (self.magnification or 1000) / 1000 / SP_PER_POINT * BIG_POINTS_PER_POINT

    def _to_sp(self, big_points: float) -> int:
        return round(big_points / self._big_points_per_sp)

    def _to_bp(self, sp: float) -> float:
        return sp * self._big_points_per_sp

    def _deepest(self, root: Node, x: int, y: int) -> Node | None:
        """The line of type you pointed at, then the word inside it.

        Two steps because the box and its contents answer different questions.
        A line of text is one box, and TeX labels that box with the line where
        the *paragraph* ended — often a blank line. The words inside it carry
        the line they were actually written on, which is the one you want.

        Only boxes that hold words are considered, and when none of them holds
        the point the nearest one is taken. Both because of the leading: between
        two printed lines there is a gap that no line box covers, and the only
        box that does cover it is the column holding the whole page, whose own
        label is wherever that column was opened. About a third of a
        paragraph's height is that gap, so without this a click there answers
        with an unrelated file — on this paper, the line of `\\end{abstract}`.
        """
        lines = [
            node
            for node, _ in self._walk(root)
            if node.is_box and any(not child.is_box for child in node.children)
        ]
        inside_point = [node for node in lines if node.contains(x, y)]
        if inside_point:
            best = min(
                inside_point, key=lambda n: abs(n.width or 0) * (n.height + n.depth)
            )
        elif lines:
            best = min(lines, key=lambda n: n.distance(x, y))
        else:
            return None
        # Within the line, the nearest thing horizontally is the word you hit.
        inside = [c for c in best.children if not c.is_box] or best.children
        return min(inside, key=lambda c: abs(c.x - x), default=best)

    def _nearest(self, root: Node, x: int, y: int) -> Node | None:
        return min(
            (node for node, _ in self._walk(root)), key=lambda n: n.distance(x, y), default=None
        )

    def _walk(self, node: Node, box: Node | None = None):
        """Every record under `node`, each with the innermost box holding it.

        Forward search needs the holder: a word record is a point, and a point
        has no rectangle to mark. The box it sits in does.
        """
        for child in node.children:
            yield child, box
            if child.children:
                yield from self._walk(child, child if child.is_box else box)


def project_path(recorded: str, repo: Path, tree: Path | None = None) -> str | None:
    """The project file a TeX-recorded path names, or None if it names none.

    TeX writes paths as it saw them, relative to whatever directory it ran in.
    That directory is the paper itself for an ordinary compile, a session's
    worktree for the proposed build, and a scratch copy for the marked-up
    review — and the last two sit *inside* the repository, so the tree is tried
    first. Matching the repository first would answer `.worktrees/x/intro.tex`,
    which is a real file and the wrong one: it is the agent's copy, not the
    paragraph you have open.

    The file has to exist in the paper for the answer to count. A class or
    package from the TeX distribution therefore comes back None, because the
    editor has nothing to open, and so does anything the compile invented.

    This is the one owner of the rule. Both directions of SyncTeX use it, and so
    does the compile log, which records paths the same way — otherwise the log
    and the PDF could disagree about which file a line is in.
    """
    raw = Path(recorded)
    root = repo.resolve()
    run = (tree or repo).resolve()
    absolute = (raw if raw.is_absolute() else run / raw).resolve()
    for base in (run, root):
        try:
            inside = absolute.relative_to(base)
        except ValueError:
            continue
        if (root / inside).is_file():
            return str(inside)
    return None


def resolve(location: Location, repo: Path, tree: Path | None = None) -> dict:
    """A recorded location as something the editor can open."""
    inside = project_path(location.path, repo, tree)
    return {
        "path": inside or location.path,
        "line": location.line,
        "in_project": inside is not None,
    }


def find(pdf: Path) -> Path | None:
    """The synctex file beside a compiled PDF, gzipped or not."""
    for suffix in (".synctex.gz", ".synctex"):
        candidate = pdf.parent / (pdf.stem + suffix)
        if candidate.is_file():
            return candidate
    return None
