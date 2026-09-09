"""Split LaTeX source into reviewable segments.

The unit of review in Galley is a sentence, not a line: a LaTeX paragraph is
often one very long line, and a line diff reports it as wholly deleted and
wholly re-added.

The one invariant this module guarantees, and the one the write-back path
depends on, is round-tripping::

    "".join(s.text for s in segment(src)) == src

Every character of the source lands in exactly one segment, trailing whitespace
included, so reassembling an accepted set of segments cannot corrupt the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterator, Literal

SegmentKind = Literal[
    "sentence",      # prose; the reviewable unit
    "comment",       # a % line, passed through whole
    "environment",   # \begin{..}..\end{..}, passed through whole
    "display_math",  # \[..\] or $$..$$, passed through whole
    "command",       # a \begin{..} or \end{..} of a prose-bearing environment
    "blank",         # a run of blank lines between paragraphs
]

# Words that end in a period without ending a sentence. Matched against the
# token immediately before the period, case-sensitively for the ones where case
# is meaningful (`No.` the number vs `no.`), case-insensitively for the rest.
ABBREVIATIONS = frozenset(
    """
    e.g i.e et al etc cf vs viz resp approx ca ibid
    Fig Figs Eq Eqs Sec Secs Ch App Alg Tab Tabs Thm Lem Def Defn Prop Cor Rem
    Ex Prob Conj Obs Assum Alg Lst Lemma Def
    Dr Prof Mr Mrs Ms Sr Jr St Mt
    vol vols no nos pp p ed eds trans repr
    """.split()
)

# `\@.` forces a sentence end; `.\ ` (period, backslash, space) forces the
# opposite. Both are LaTeX's own spacing controls and mean exactly this.
_FORCE_END = "\\@"
_TERMINATORS = ".!?"
# Characters allowed between the terminator and the space: closing quotes and
# brackets, and the closing brace of e.g. `\emph{... .}`.
_CLOSERS = "\"')]}»’”"

# Environments whose contents are NOT prose: splitting inside them is
# meaningless and reviewing them sentence by sentence is worse than useless.
# Everything else — `document` above all, but also `abstract`, `figure`,
# `itemize`, `theorem`, `proof` — is transparent: its \begin and \end become
# their own segments and the prose between them is split normally. Treating
# every environment as atomic would make a whole paper one unreviewable blob,
# because every paper is wrapped in \begin{document}.
ATOMIC_ENVIRONMENTS = frozenset(
    """
    equation eqnarray displaymath math align gather multline flalign alignat
    split cases aligned gathered array IEEEeqnarray dmath
    verbatim Verbatim semiverbatim alltt lstlisting minted listing code
    tabular tabularx tabulary longtable supertabular xtabular
    tikzpicture pgfpicture circuitikz forest tikzcd
    filecontents comment tcblisting picture
    """.split()
)

_ENV_BEGIN = re.compile(r"\\begin\s*\{([^}]*)\}")
_ENV_END = re.compile(r"\\end\s*\{([^}]*)\}")
_WORD_BEFORE = re.compile(r"([A-Za-z@.]+)$")


@dataclass(frozen=True)
class Segment:
    """One reviewable unit, carrying its own slice of the source."""

    kind: SegmentKind
    text: str
    start: int
    end: int

    @property
    def key(self) -> str:
        """Whitespace-collapsed content, for aligning two versions of a file.

        Reflowing a paragraph changes every line but no sentence; comparing on
        the key means such a change shows up as no diff at all.
        """
        return " ".join(self.text.split())

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


def segment(src: str) -> list[Segment]:
    """Split LaTeX source into segments that concatenate back to `src`."""
    return list(_segment(src))


def _segment(src: str) -> Iterator[Segment]:
    scanner = _Scanner(src)
    yield from scanner.run()


class _Scanner:
    def __init__(self, src: str) -> None:
        self.src = src
        self.n = len(src)
        self.i = 0
        self.seg_start = 0

    # -- emit helpers -----------------------------------------------------

    def _flush_prose(self, upto: int) -> Iterator[Segment]:
        """Emit the pending prose run [seg_start, upto) split into sentences."""
        if upto > self.seg_start:
            yield from _split_sentences(self.src, self.seg_start, upto)
        self.seg_start = upto

    def _emit(self, kind: SegmentKind, start: int, end: int) -> Segment:
        return Segment(kind=kind, text=self.src[start:end], start=start, end=end)

    # -- main loop --------------------------------------------------------

    def run(self) -> Iterator[Segment]:
        src, n = self.src, self.n
        while self.i < n:
            c = src[self.i]

            if c == "\\":
                nxt = src[self.i + 1] if self.i + 1 < n else ""
                if nxt == "[":
                    yield from self._atomic_delimited("display_math", "\\[", "\\]")
                    continue
                if nxt == "(":
                    self.i = _skip_paren_math(src, self.i, n)
                    continue
                if src.startswith("\\begin", self.i):
                    m = _ENV_BEGIN.match(src, self.i)
                    if m:
                        if m.group(1).rstrip("*") in ATOMIC_ENVIRONMENTS:
                            yield from self._atomic_environment(m.group(1), m.end())
                        else:
                            yield from self._env_marker(m.end())
                        continue
                if src.startswith("\\end", self.i):
                    m = _ENV_END.match(src, self.i)
                    if m:
                        yield from self._env_marker(m.end())
                        continue
                # Any other control sequence: skip the escaped char so that
                # `\%`, `\$`, `\\` never look like a comment or math start.
                self.i += 2 if nxt else 1
                continue

            if c == "%":
                yield from self._atomic_comment()
                continue

            if c == "$":
                if src.startswith("$$", self.i):
                    yield from self._atomic_delimited("display_math", "$$", "$$")
                else:
                    self._skip_inline_math()
                continue

            if c == "\n":
                # Two or more newlines separated only by blanks end a paragraph.
                j = self.i
                blanks = 0
                while j < n and src[j] in " \t\r\n":
                    if src[j] == "\n":
                        blanks += 1
                    j += 1
                if blanks >= 2:
                    yield from self._flush_prose(self.i)
                    yield self._emit("blank", self.i, j)
                    self.i = self.seg_start = j
                    continue
                self.i = j
                continue

            self.i += 1

        yield from self._flush_prose(n)

    # -- atomic units -----------------------------------------------------

    def _atomic_comment(self) -> Iterator[Segment]:
        """A `%` comment runs to, and includes, the end of its line."""
        yield from self._flush_prose(self.i)
        end = self.src.find("\n", self.i)
        end = self.n if end == -1 else end + 1
        yield self._emit("comment", self.i, end)
        self.i = self.seg_start = end

    def _env_marker(self, end: int) -> Iterator[Segment]:
        """Emit a lone `\\begin{..}` or `\\end{..}` and carry on inside it."""
        yield from self._flush_prose(self.i)
        start = self.i
        end = _eat_trailing_blank_line(self.src, end)
        yield self._emit("command", start, end)
        self.i = self.seg_start = end

    def _atomic_delimited(self, kind: SegmentKind, open_: str, close: str) -> Iterator[Segment]:
        yield from self._flush_prose(self.i)
        start = self.i
        end = self.src.find(close, start + len(open_))
        end = self.n if end == -1 else end + len(close)
        end = _eat_trailing_blank_line(self.src, end)
        yield self._emit(kind, start, end)
        self.i = self.seg_start = end

    def _atomic_environment(self, name: str, after_begin: int) -> Iterator[Segment]:
        """`\\begin{env}...\\end{env}`, matching nested copies of the same env."""
        yield from self._flush_prose(self.i)
        start = self.i
        depth = 1
        j = after_begin
        opener = re.compile(r"\\begin\s*\{" + re.escape(name) + r"\}")
        closer = re.compile(r"\\end\s*\{" + re.escape(name) + r"\}")
        while j < self.n and depth:
            mo, mc = opener.search(self.src, j), closer.search(self.src, j)
            if mc is None:
                j = self.n
                break
            if mo is not None and mo.start() < mc.start():
                depth += 1
                j = mo.end()
            else:
                depth -= 1
                j = mc.end()
        end = _eat_trailing_blank_line(self.src, j)
        yield self._emit("environment", start, end)
        self.i = self.seg_start = end

    def _skip_inline_math(self) -> None:
        """Step over `$...$` without emitting: math is opaque, never a break."""
        j = self.i + 1
        while j < self.n:
            if self.src[j] == "\\":
                j += 2
                continue
            if self.src[j] == "$":
                j += 1
                break
            j += 1
        self.i = j


def _eat_trailing_blank_line(src: str, end: int) -> int:
    """Absorb the newline that closes a block, so the next segment starts clean."""
    j = end
    while j < len(src) and src[j] in " \t":
        j += 1
    if j < len(src) and src[j] == "\n":
        return j + 1
    return end


# -- sentence splitting within a prose run --------------------------------


def _split_sentences(src: str, start: int, end: int) -> Iterator[Segment]:
    """Cut [start, end) at sentence boundaries, keeping every character."""
    i = start
    seg_start = start
    while i < end:
        c = src[i]

        if c == "\\":
            if src.startswith("\\item", i) and i > seg_start:
                # Each list item is its own unit, even without a full stop.
                yield _prose(src, seg_start, i)
                seg_start = i
                i += len("\\item")
                continue
            if src.startswith("\\(", i):
                i = _skip_paren_math(src, i, end)
                continue
            if src.startswith(_FORCE_END, i):
                # `\@.` — LaTeX's explicit "this really does end a sentence".
                j = i + len(_FORCE_END)
                if j < end and src[j] in _TERMINATORS:
                    j += 1
                    j = _eat_gap(src, j, end)
                    if j > seg_start:
                        yield _prose(src, seg_start, j)
                    i = seg_start = j
                    continue
            i += 2 if i + 1 < end else 1
            continue

        if c == "$":
            i = _skip_math(src, i, end)
            continue

        if c in "{":
            # Braces are opaque: a period inside \cite{}, \ref{} or \footnote{}
            # is never a sentence break.
            i = _skip_braces(src, i, end)
            continue

        if c in _TERMINATORS:
            j = _boundary_after(src, i, end)
            if j is not None:
                if j > seg_start:
                    yield _prose(src, seg_start, j)
                i = seg_start = j
                continue
            i += 1
            continue

        i += 1

    if end > seg_start:
        yield _prose(src, seg_start, end)


def _prose(src: str, a: int, b: int) -> Segment:
    return Segment(kind="sentence", text=src[a:b], start=a, end=b)


def _skip_math(src: str, i: int, end: int) -> int:
    j = i + 1
    while j < end:
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "$":
            return j + 1
        j += 1
    return end


def _skip_paren_math(src: str, i: int, end: int) -> int:
    """Step over `\\(...\\)` inline math, which is opaque like `$...$`."""
    j = src.find("\\)", i + 2)
    return end if j == -1 or j >= end else j + 2


def _skip_braces(src: str, i: int, end: int) -> int:
    depth = 0
    j = i
    while j < end:
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return end


def _eat_gap(src: str, j: int, end: int) -> int:
    """Take the closers and the inter-sentence space onto the sentence ending."""
    while j < end and src[j] in _CLOSERS:
        j += 1
    while j < end and src[j] in " \t":
        j += 1
    if j < end and src[j] == "\n":
        j += 1
    return j


def _boundary_after(src: str, i: int, end: int) -> int | None:
    """Return where the next sentence starts, or None if `src[i]` does not end one."""
    ch = src[i]

    # A run of three or more dots is an ellipsis: not a terminator.
    if ch == "." and (src.startswith("..", i) or (i >= 2 and src[i - 2 : i] == "..")):
        return None

    j = i + 1
    # `.\ ` is LaTeX for "this period does not end a sentence".
    if src.startswith("\\ ", j) or src.startswith("\\@", j):
        return None

    while j < end and src[j] in _CLOSERS:
        j += 1

    gap_start = j
    while j < end and src[j] in " \t":
        j += 1
    newlines = 0
    while j < end and src[j] in " \t\r\n":
        if src[j] == "\n":
            newlines += 1
        j += 1
    if j == gap_start:
        return None  # no whitespace: `4.2`, `foo.bar`
    if newlines >= 2:
        return None  # paragraph break; the outer scanner owns it

    if ch == "." and _is_abbreviation(src, i):
        return None

    if j >= end:
        return end
    if not _starts_a_sentence(src, j):
        return None

    # Keep exactly one line break with the sentence that precedes it.
    k = gap_start
    while k < end and src[k] in " \t":
        k += 1
    if k < end and src[k] == "\n":
        k += 1
    return max(k, gap_start) if k > gap_start else j


def _is_abbreviation(src: str, dot: int) -> bool:
    m = _WORD_BEFORE.search(src, 0, dot)
    if not m:
        return False
    word = m.group(1).rstrip(".")
    if not word:
        return False
    # A lone capital is an initial: "J. Smith", "M. Curie".
    if len(word) == 1 and word.isupper():
        return True
    if word in ABBREVIATIONS:
        return True
    return word.lower() in {a.lower() for a in ABBREVIATIONS}


def _starts_a_sentence(src: str, j: int) -> bool:
    c = src[j]
    if c.isupper():
        return True
    if c in "\\$`([\"“‘":
        return True
    if c.isdigit():
        return True
    return False
