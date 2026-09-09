r"""What the project defines, so the editor can offer it back to you.

Overleaf completes `\cite{`, `\ref{` and your own macros as you type them. That
is the feature you feel on every paragraph — you defined `\qlambda` six weeks
ago and you are not going to remember it — and it needs one question answered:
what does this project define?

Three answers, from three kinds of file. Git decides which files those are:
`files.listing` already owns "what is in the project", so an ignored draft or a
build artefact never turns up in a completion list.

The parsing is deliberately literal. It reads what a file *says*, not what TeX
would *do* with it: a label built out of a counter, or a macro defined inside a
conditional, is invisible here. That is the honest answer rather than a wrong
one, and it is why every entry carries the file and line it came from — if the
completion list surprises you, it can be checked in a second.
"""

from __future__ import annotations

import re
import time
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from . import files

# Where each kind of definition lives. Macros are as often in a project's own
# package as in its prose — this paper keeps every name it has invented in
# flmnotation.sty — so the macro scan reads .sty and .cls too.
LABEL_SUFFIXES = frozenset({".tex"})
BIB_SUFFIXES = frozenset({".bib"})
MACRO_SUFFIXES = frozenset({".tex", ".sty", ".cls"})

# A caption or a section title is a hint, not the text itself; clip it to
# something that fits beside a key in a completion list.
CONTEXT_LIMIT = 120

# An environment that says nothing about what a label names. Everything else
# does: a label inside `figure` labels a figure.
TRANSPARENT_ENVIRONMENTS = frozenset({"document"})

# A `\def` parameter text is short by construction. If no body brace turns up
# within this many characters the definition is malformed, and guessing at it
# would be worse than skipping it.
DEF_PARAMETER_LIMIT = 200


@dataclass(frozen=True)
class Label:
    """A `\\label`, and enough context to tell two of them apart.

    `kind` is what the label names, read off the structure around it — the
    enclosing environment, or the sectioning command it follows. It is not the
    `fig:`/`tab:` prefix, which is a convention rather than a fact.
    """

    key: str
    file: str
    line: int
    kind: str | None
    context: str | None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "context": self.context,
        }


@dataclass(frozen=True)
class Citation:
    """One entry key in a `.bib` file, with what you choose a citation by."""

    key: str
    entry_type: str
    title: str | None
    author: str | None
    year: str | None
    file: str
    line: int

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "entry_type": self.entry_type,
            "title": self.title,
            "author": self.author,
            "year": self.year,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True)
class Macro:
    """A command or environment the project defines.

    `name` carries no backslash: a command is `qlambda`, an environment is
    `shadowbox`, and whoever renders it knows which it is from `kind`.
    """

    name: str
    kind: str  # "command" or "environment"
    args: int
    optional: bool  # the first argument is optional, as in \newcommand{..}[2][x]
    file: str
    line: int

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "args": self.args,
            "optional": self.optional,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True)
class ProjectIndex:
    labels: list[Label]
    citations: list[Citation]
    macros: list[Macro]
    files: int
    built_ms: float

    def as_dict(self) -> dict:
        return {
            "labels": [x.as_dict() for x in self.labels],
            "citations": [x.as_dict() for x in self.citations],
            "macros": [x.as_dict() for x in self.macros],
            "files": self.files,
            "built_ms": self.built_ms,
        }


# -- the index, and the one cache of it --------------------------------------

# Keyed by resolved repository path, because a session checkout is a different
# project with the same file names. The stamp is what makes it safe to hand the
# same object back: if any source file's size or mtime has moved, it is rebuilt.
_CACHE: dict[Path, tuple[tuple, ProjectIndex]] = {}


def index(repo: Path) -> ProjectIndex:
    """Everything the project defines, rebuilt only when a source file moves."""
    key = repo.resolve()
    sources = _sources(repo)
    stamp = _stamp(repo, sources)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    built = _build(repo, sources)
    _CACHE[key] = (stamp, built)
    return built


def _sources(repo: Path) -> list[str]:
    wanted = LABEL_SUFFIXES | BIB_SUFFIXES | MACRO_SUFFIXES
    return [p for p in files.listing(repo) if Path(p).suffix.lower() in wanted]


def _stamp(repo: Path, sources: list[str]) -> tuple:
    """The cheap fingerprint: which files there are, and how they last looked."""
    out = []
    for rel in sources:
        try:
            info = (repo / rel).stat()
            out.append((rel, info.st_mtime_ns, info.st_size))
        except OSError:
            # Listed by git but not on disk: a deleted file you have not
            # committed yet. Record the absence, so restoring it rebuilds.
            out.append((rel, -1, -1))
    return tuple(out)


def _build(repo: Path, sources: list[str]) -> ProjectIndex:
    started = time.perf_counter()
    labels: list[Label] = []
    citations: list[Citation] = []
    macros: list[Macro] = []
    read = 0

    for rel in sources:
        suffix = Path(rel).suffix.lower()
        try:
            text = (repo / rel).read_text(errors="replace")
        except OSError:
            continue
        read += 1
        if suffix in BIB_SUFFIXES:
            citations.extend(citations_in(text, rel))
        if suffix in LABEL_SUFFIXES:
            labels.extend(labels_in(text, rel))
        if suffix in MACRO_SUFFIXES:
            macros.extend(macros_in(text, rel))

    labels.sort(key=lambda x: (x.key, x.file, x.line))
    citations.sort(key=lambda x: (x.key.lower(), x.file, x.line))
    macros.sort(key=lambda x: (x.name.lower(), x.file, x.line))
    return ProjectIndex(
        labels=labels,
        citations=citations,
        macros=macros,
        files=read,
        built_ms=round((time.perf_counter() - started) * 1000, 1),
    )


# -- reading LaTeX literally -------------------------------------------------


def _mask_comments(text: str) -> str:
    r"""The document with comment text blanked out, every offset untouched.

    A `%` starts a comment unless it is escaped, and a backslash always eats
    the character after it — which makes `\%` a percent sign and `\\%` a line
    break followed by a comment. Comments become spaces rather than vanishing,
    because every line this module reports is an offset into this string.

    Verbatim environments are not honoured: a `%` inside one is treated as a
    comment. Nothing in a paper labels or defines anything inside verbatim.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
        elif text[i] == "%":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, char in enumerate(text):
        if char == "\n":
            starts.append(i + 1)
    return starts


def _line_of(starts: list[int], offset: int) -> int:
    return bisect_right(starts, offset)


def _skip_space(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _group(text: str, i: int) -> tuple[str, int] | None:
    """The balanced `{...}` at `i`: its contents, and where it ends."""
    if i >= len(text) or text[i] != "{":
        return None
    depth, j = 0, i
    while j < len(text):
        char = text[j]
        if char == "\\":
            j += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j], j + 1
        j += 1
    return None


def _optional(text: str, i: int) -> tuple[str, int] | None:
    """The `[...]` at `i`, if there is one. Braces inside it are respected."""
    if i >= len(text) or text[i] != "[":
        return None
    depth, j = 0, i + 1
    while j < len(text):
        char = text[j]
        if char == "\\":
            j += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "]" and depth == 0:
            return text[i + 1 : j], j + 1
        j += 1
    return None


def _collapse(text: str) -> str:
    """One line of whitespace-normalised source, with any `\\label` removed.

    LaTeX markup is left exactly as written. Rendering a caption properly means
    running TeX; guessing at it would put a wrong section name beside a key.
    """
    return re.sub(r"\s+", " ", re.sub(r"\\label\s*\{[^{}]*\}", " ", text)).strip()


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= CONTEXT_LIMIT else text[: CONTEXT_LIMIT - 1].rstrip() + "…"


# -- labels ------------------------------------------------------------------

# The commands that decide where a label sits. Longer names come first so the
# alternation cannot settle for `section` in the middle of `subsection`.
_STRUCTURE = re.compile(
    r"\\(?P<cmd>begin|end|label|caption"
    r"|part|chapter|subsubsection|subsection|section|subparagraph|paragraph)"
    r"\*?(?![A-Za-z])"
)
_SECTIONING = frozenset(
    "part chapter section subsection subsubsection paragraph subparagraph".split()
)


@dataclass
class _Environment:
    name: str
    start: int
    end: int
    caption: str | None = None


def labels_in(text: str, rel: str) -> list[Label]:
    r"""Every `\label` in one file, with what it appears to label.

    One pass, keeping a stack of open environments and the last sectioning
    title, so a label knows both what encloses it and what came before it.
    """
    doc = _mask_comments(text)
    starts = _line_starts(text)

    open_envs: list[_Environment] = []
    environments: list[_Environment] = []
    # (offset just past the title, command, title) in the order they appear.
    sections: list[tuple[int, str, str]] = []
    found: list[tuple[str, int]] = []

    pos = 0
    while True:
        match = _STRUCTURE.search(doc, pos)
        if match is None:
            break
        cmd = match.group("cmd")
        pos = match.end()

        if cmd in ("begin", "end"):
            group = _group(doc, _skip_space(doc, pos))
            if group is None:
                continue
            name, pos = group[0].strip().rstrip("*"), group[1]
            if cmd == "begin":
                env = _Environment(name=name, start=match.start(), end=len(doc))
                open_envs.append(env)
                environments.append(env)
            else:
                for depth in range(len(open_envs) - 1, -1, -1):
                    if open_envs[depth].name == name:
                        for unclosed in open_envs[depth:]:
                            unclosed.end = pos
                        del open_envs[depth:]
                        break

        elif cmd == "label":
            group = _group(doc, _skip_space(doc, pos))
            if group is None:
                continue
            found.append((group[0].strip(), match.start()))
            pos = group[1]

        else:
            # A caption or a title. Read it, but carry on scanning *inside* it:
            # `\caption{... \label{fig:x}}` is how half the floats in a paper
            # are written, and skipping the group would lose the label.
            at = _skip_space(doc, pos)
            skipped = _optional(doc, at)
            if skipped is not None:
                at = _skip_space(doc, skipped[1])
            group = _group(doc, at)
            if group is None:
                continue
            pos = at + 1
            if cmd == "caption":
                if open_envs and open_envs[-1].caption is None:
                    open_envs[-1].caption = group[0]
            else:
                sections.append((group[1], cmd, group[0]))

    return [
        _describe(key, offset, rel, starts, environments, sections, doc)
        for key, offset in found
    ]


def _describe(
    key: str,
    offset: int,
    rel: str,
    starts: list[int],
    environments: list[_Environment],
    sections: list[tuple[int, str, str]],
    doc: str,
) -> Label:
    enclosing = sorted(
        (e for e in environments if e.start <= offset < e.end), key=lambda e: e.start
    )
    section = None
    for entry in sections:
        if entry[0] <= offset:
            section = entry
        else:
            break

    # A label written straight after a heading names that heading; otherwise it
    # belongs to whatever encloses it.
    kind = None
    if section is not None and not doc[section[0] : offset].strip():
        kind = section[1]
    else:
        named = [e for e in enclosing if e.name not in TRANSPARENT_ENVIRONMENTS]
        if named:
            kind = named[-1].name

    captioned = next((e for e in reversed(enclosing) if e.caption), None)
    if captioned is not None:
        context = _collapse(captioned.caption or "")
    elif section is not None:
        context = _collapse(section[2])
    else:
        context = None

    return Label(
        key=key,
        file=rel,
        line=_line_of(starts, offset),
        kind=kind,
        context=_clip(context),
    )


# -- macros ------------------------------------------------------------------

_DEFINITION = re.compile(
    r"\\(?P<cmd>newcommand|renewcommand|providecommand|DeclareMathOperator"
    r"|newenvironment|renewenvironment|def)\*?(?![A-Za-z])"
)


def macros_in(text: str, rel: str) -> list[Macro]:
    r"""Every command and environment one file defines, and how many arguments.

    Names containing `@` are skipped. Those are a package's internals — you
    cannot even type one in a document without `\makeatletter` — and a vendored
    class file has hundreds of them.
    """
    doc = _mask_comments(text)
    starts = _line_starts(text)
    out: list[Macro] = []

    pos = 0
    while True:
        match = _DEFINITION.search(doc, pos)
        if match is None:
            break
        cmd = match.group("cmd")
        pos = match.end()

        if cmd in ("newenvironment", "renewenvironment"):
            group = _group(doc, _skip_space(doc, pos))
            if group is None:
                continue
            name, pos = group[0].strip(), group[1]
            args, optional, pos = _arity(doc, pos)
            kind = "environment"
        elif cmd == "def":
            found = _def_name_and_arity(doc, pos)
            if found is None:
                continue
            name, args, pos = found
            optional, kind = False, "command"
        else:
            named = _command_name(doc, pos)
            if named is None:
                continue
            name, pos = named
            if cmd == "DeclareMathOperator":
                # Its second argument is the operator's printed form, not a
                # parameter: `\DeclareMathOperator{\argmax}{arg\,max}`.
                args, optional = 0, False
            else:
                args, optional, pos = _arity(doc, pos)
            kind = "command"

        name = name.lstrip("\\").strip()
        if not name or "@" in name:
            continue
        out.append(
            Macro(
                name=name,
                kind=kind,
                args=args,
                optional=optional,
                file=rel,
                line=_line_of(starts, match.start()),
            )
        )

    return out


def _command_name(doc: str, i: int) -> tuple[str, int] | None:
    r"""The name after `\newcommand`, written either `{\foo}` or bare `\foo`."""
    i = _skip_space(doc, i)
    if i >= len(doc):
        return None
    if doc[i] == "{":
        group = _group(doc, i)
        return None if group is None else (group[0].strip(), group[1])
    if doc[i] == "\\":
        return _control_sequence(doc, i)
    return None


def _control_sequence(doc: str, i: int) -> tuple[str, int]:
    r"""The `\foo` at `i`. A control *symbol* like `\,` is one character long."""
    j = i + 1
    while j < len(doc) and doc[j].isalpha():
        j += 1
    if j == i + 1:
        j = min(i + 2, len(doc))
    return doc[i:j], j


def _arity(doc: str, i: int) -> tuple[int, bool, int]:
    r"""The `[2][default]` after a definition: how many arguments, and is the
    first one optional."""
    i = _skip_space(doc, i)
    count = _optional(doc, i)
    if count is None:
        return 0, False, i
    raw, i = count
    args = int(raw.strip()) if raw.strip().isdigit() else 0
    default = _optional(doc, _skip_space(doc, i))
    if default is None:
        return args, False, i
    return args, True, default[1]


def _def_name_and_arity(doc: str, i: int) -> tuple[str, int, int] | None:
    r"""`\def\foo#1#2{...}`: the name, and the `#n` in its parameter text."""
    i = _skip_space(doc, i)
    if i >= len(doc) or doc[i] != "\\":
        return None
    name, j = _control_sequence(doc, i)
    body = j
    limit = min(len(doc), j + DEF_PARAMETER_LIMIT)
    while body < limit and doc[body] != "{":
        body += 2 if doc[body] == "\\" else 1
    if body >= limit or doc[body] != "{":
        return None
    return name, len(re.findall(r"#\d", doc[j:body])), body


# -- BibTeX ------------------------------------------------------------------

_ENTRY = re.compile(r"@\s*(?P<type>[A-Za-z]+)\s*(?P<open>[{(])")
_FIELD_NAME = re.compile(r"[A-Za-z0-9_+:-]+")


def citations_in(text: str, rel: str) -> list[Citation]:
    """Every entry key in one `.bib` file, with what you cite it by.

    BibTeX is not a regular language, so this walks it: braces nest, a value
    may be braced, quoted, a bare number or an `@string` name, and any of those
    may be glued together with `#`. Anything between entries is ignored, which
    is what BibTeX itself does with it.
    """
    starts = _line_starts(text)
    strings: dict[str, str] = {}
    out: list[Citation] = []

    i = 0
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        match = _ENTRY.match(text, at)
        if match is None:
            i = at + 1
            continue
        entry_type = match.group("type").lower()
        closing = "}" if match.group("open") == "{" else ")"
        pos = match.end()

        if entry_type in ("comment", "preamble"):
            group = _group(text, match.end() - 1)
            i = group[1] if group else at + 1
            continue

        if entry_type == "string":
            fields, i = _fields(text, pos, closing, strings)
            strings.update(fields)
            continue

        key, pos = _entry_key(text, pos, closing)
        fields, i = _fields(text, pos, closing, strings)
        if key:
            out.append(
                Citation(
                    key=key,
                    entry_type=entry_type,
                    title=_bib_text(fields.get("title")),
                    author=_bib_text(fields.get("author") or fields.get("editor")),
                    year=_bib_text(fields.get("year")),
                    file=rel,
                    line=_line_of(starts, at),
                )
            )

    return out


def _entry_key(text: str, i: int, closing: str) -> tuple[str, int]:
    start = _skip_space(text, i)
    j = start
    while j < len(text) and text[j] != "," and text[j] != closing:
        j += 1
    return text[start:j].strip(), j


def _fields(
    text: str, i: int, closing: str, strings: dict[str, str]
) -> tuple[dict[str, str], int]:
    """The `name = value` pairs up to the entry's closing delimiter.

    The first spelling of a field wins, which is BibTeX's own rule for a
    repeated field.
    """
    fields: dict[str, str] = {}
    while i < len(text) and text[i] != closing:
        if text[i].isspace() or text[i] == ",":
            i += 1
            continue
        name = _FIELD_NAME.match(text, i)
        if name is None:
            i += 1
            continue
        i = _skip_space(text, name.end())
        if i < len(text) and text[i] == "=":
            value, i = _bib_value(text, i + 1, strings)
            fields.setdefault(name.group(0).lower(), value)
        else:
            i = name.end()
    return fields, min(i + 1, len(text))


def _bib_value(text: str, i: int, strings: dict[str, str]) -> tuple[str, int]:
    parts: list[str] = []
    while True:
        i = _skip_space(text, i)
        if i >= len(text):
            return "".join(parts), i
        if text[i] == "{":
            group = _group(text, i)
            if group is None:
                return "".join(parts), len(text)
            parts.append(group[0])
            i = group[1]
        elif text[i] == '"':
            quoted, i = _quoted(text, i)
            parts.append(quoted)
        else:
            word = _FIELD_NAME.match(text, i)
            if word is None:
                return "".join(parts), i
            parts.append(strings.get(word.group(0).lower(), word.group(0)))
            i = word.end()
        after = _skip_space(text, i)
        if after < len(text) and text[after] == "#":
            i = after + 1
            continue
        return "".join(parts), i


def _quoted(text: str, i: int) -> tuple[str, int]:
    """A `"..."` value. The closing quote is the one at brace depth zero."""
    depth, j = 0, i + 1
    while j < len(text):
        char = text[j]
        if char == "\\":
            j += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == '"' and depth == 0:
            return text[i + 1 : j], j + 1
        j += 1
    return text[i + 1 :], len(text)


# TeX's accent commands, and the letters that are commands in their own right.
# Both sets are closed and small, which is why they are worth doing completely:
# an author you cannot read is an author you cannot recognise, and half these
# names in a bibliography of this size carry one.
_ACCENTS = {
    '"': "\u0308",  # ä
    "'": "\u0301",  # é
    "`": "\u0300",  # è
    "^": "\u0302",  # ô
    "~": "\u0303",  # ñ
    "=": "\u0304",  # ō
    ".": "\u0307",  # ż
    "u": "\u0306",  # ă
    "v": "\u030c",  # š
    "H": "\u030b",  # ő
    "r": "\u030a",  # å
    "k": "\u0328",  # ą
    "c": "\u0327",  # ç
    "d": "\u0323",  # ḍ
    "b": "\u0331",  # ḇ
}
_LETTERS = {
    "ss": "ß", "o": "ø", "O": "Ø", "l": "ł", "L": "Ł",
    "aa": "å", "AA": "Å", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ",
    "i": "ı", "j": "ȷ",
}
# The trailing whitespace goes with it: TeX ends a control word at the space
# and does not print it, so `Stra\ss e` is one word on the page.
_LETTER = re.compile(r"\\(ss|AA|aa|AE|ae|OE|oe|[OoLlij])(?![A-Za-z])\s*")
_ACCENT = re.compile(
    r"\\([\"'`^~=.]|[uvHrkdbc](?![A-Za-z]))\s*(?:\{([^{}]*)\}|(\\[A-Za-z]+|[^\\{}]))"
)
# A brace group straight after a command is that command's argument; any other
# brace group is BibTeX's capitalisation armour. Only the armour comes off, so
# `{{Llemma}: ...}` reads plainly while `\emph{...}` stays legible.
_COMMAND_ARGUMENT = re.compile(r"\\[A-Za-z]+\s*(?=\{)")


def _bib_text(value: str | None) -> str | None:
    r"""A field as it should read in a list.

    Three things happen. TeX's accents become the letter they accent, so
    `Martin-L\"{o}f` is a name you recognise. BibTeX's braces are
    capitalisation armour — `{{Llemma}: ...}` — so they go. And the whole
    thing becomes one line.

    Nothing else is interpreted: a title wrapped in `\textsc{}` keeps it. That
    would be rendering LaTeX, and the completion list is not a typesetter.
    """
    if value is None:
        return None
    text = _LETTER.sub(lambda m: _LETTERS[m.group(1)], value)
    text = _ACCENT.sub(_accented, text)
    arguments = _argument_braces(text)
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
            continue
        if text[i] not in "{}" or i in arguments:
            out.append(text[i])
        i += 1
    joined = unicodedata.normalize("NFC", re.sub(r"\s+", " ", "".join(out)).strip())
    return joined or None


def _argument_braces(text: str) -> set[int]:
    """Where the braces belonging to a command are, so they survive stripping."""
    keep: set[int] = set()
    for match in _COMMAND_ARGUMENT.finditer(text):
        group = _group(text, match.end())
        if group is None:
            continue
        keep.add(match.end())
        keep.add(group[1] - 1)
    return keep


def _accented(match: re.Match[str]) -> str:
    """One accent command, as the accented letter.

    A dotless i or j gets its dot back: `\\"{\\i}` is written that way because
    the diaeresis takes the dot's place, and the letter meant is `ï`.
    """
    base = (match.group(2) if match.group(2) is not None else match.group(3)) or ""
    base = _LETTER.sub(lambda m: _LETTERS[m.group(1)], base)
    return base.replace("ı", "i").replace("ȷ", "j") + _ACCENTS[match.group(1)]
