"""Compiling the paper, and the second review surface.

Text merging catches wording; it does not catch meaning. `latexdiff` between
the accepted state and the proposed one, compiled, is how you notice a claim
that got quietly strengthened while every individual sentence looked fine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The compile log records the paths of the files it read exactly the way the
# synctex file does, so the same rule turns them back into files of yours. One
# owner for it, or the log and the PDF could disagree about which file a line
# is in.
from .synctex import project_path


@dataclass(frozen=True)
class Problem:
    """One thing the compile says you should go and look at.

    `path` and `line` are filled in only when the log said so plainly or the
    guess could be checked against the file itself. When they are None the
    message still stands on its own — being sent to the wrong sentence is worse
    than being sent nowhere.
    """

    severity: str  # "error" or "warning"
    message: str
    path: str | None = None
    line: int | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
        }


@dataclass
class CompileResult:
    ok: bool
    pdf: Path | None
    problems: list[Problem]
    log_tail: str

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "pdf": str(self.pdf) if self.pdf else None,
            "problems": [p.as_dict() for p in self.problems],
            "log_tail": self.log_tail,
        }


def compile_pdf(repo: Path, main_tex: str, outdir: Path, timeout: float = 600) -> CompileResult:
    """Run latexmk and report only what a human has to act on."""
    outdir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "latexmk",
            "-pdf",
            # Writes <stem>.synctex.gz beside the PDF: the map from a point on
            # the page back to the source line that produced it.
            "-synctex=1",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-outdir={outdir}",
            main_tex,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stem = Path(main_tex).stem
    log_path = outdir / f"{stem}.log"
    log = log_path.read_text(errors="replace") if log_path.exists() else ""
    pdf = outdir / f"{stem}.pdf"
    # Read the *settled* state, not latexmk's console output. latexmk runs
    # LaTeX several times; the first pass has no .aux and reports every single
    # citation as undefined. Those warnings are gone by the last pass, which is
    # what the .log holds. Reporting the console would send you chasing
    # ninety-six problems that no longer exist.
    return CompileResult(
        ok=proc.returncode == 0 and pdf.exists(),
        pdf=pdf if pdf.exists() else None,
        problems=parse_log(log or proc.stdout + "\n" + proc.stderr, repo),
        log_tail="\n".join(log.splitlines()[-60:]) or proc.stderr[-4000:],
    )


# -- reading the log ---------------------------------------------------------
#
# TeX's log is a transcript, not a report, and only one thing in it is a
# straight answer: `-file-line-error` puts `file:line:` in front of an error.
# Everything else has to be worked out from where in the transcript the message
# appears, so everything else is checked before it is believed.

# `./sections/intro.tex:41: Undefined control sequence.`
_FILE_LINE = re.compile(r"^\s*(\S*?\.\w{1,5}):(\d+):\s*(.*\S)\s*$")
# `! Undefined control sequence.` — the same error without the file in front.
_BANG = re.compile(r"^! (.*\S)\s*$")
# TeX's own pointer at the offending line, inside an error block.
_TEX_LINE = re.compile(r"^l\.(\d+)\b")
_WARNING = re.compile(r"^(?:LaTeX|Package [\w@.-]+|Class [\w@.-]+)(?: Font)? Warning: (.*)$")
_INPUT_LINE = re.compile(r"\bon input line (\d+)")
_BOX = re.compile(r"^(?:Over|Under)full \\[hv]box ")
_BOX_LINES = re.compile(r"\bat lines? (\d+)(?:--\d+)?")
# A continuation of a package's message, indented under `(packagename)`.
_CONTINUED = re.compile(r"^\([\w@.-]+\)\s*")
# What follows a `(` in the transcript when TeX opens a file, rather than when
# it is simply printing a bracket in a message.
_LOOKS_LIKE_A_FILE = re.compile(r"/|\.[A-Za-z][\w-]{0,4}$")

# A runaway log can hold thousands of identical box warnings; past this many
# the list has stopped being a list of things to do.
MAX_PROBLEMS = 200


def parse_log(text: str, repo: Path) -> list[Problem]:
    """Everything in a compile log worth putting in front of a person.

    `repo` is the tree TeX ran in, which for the marked-up review is the
    scratch copy rather than the paper; either way the paths come back relative
    to it, which is what the editor opens.
    """
    lines = text.splitlines()
    # Where TeX is reading from. It writes `(name` on opening a file and `)` on
    # closing it, so the innermost open file is the one a message without its
    # own filename is talking about.
    open_files: list[str | None] = []
    problems: list[Problem] = []
    lengths: dict[str, int | None] = {}

    def add(severity: str, message: str, path: str | None = None, line: int | None = None) -> None:
        problem = Problem(severity, " ".join(message.split()), path, line)
        if problem.message and problem not in problems:
            problems.append(problem)

    def here(line: int | None) -> tuple[str | None, int | None]:
        """The innermost open file, if it can carry a line that far.

        The only check available is whether the file is one of yours and is
        long enough. It is weak, but it is a check: when the bracket counting
        has drifted the answer is usually a package file or a short one, and
        that is caught here rather than in the editor.
        """
        recorded = next((f for f in reversed(open_files) if f), None)
        if recorded is None or line is None:
            return None, None
        path = project_path(recorded, repo)
        if path is None:
            return None, None
        if path not in lengths:
            source = repo / path
            lengths[path] = len(source.read_bytes().splitlines()) if source.is_file() else None
        total = lengths[path]
        return (path, line) if total is not None and line <= total else (None, None)

    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1

        match = _FILE_LINE.match(raw)
        if match:
            recorded, number, message = match.group(1), int(match.group(2)), match.group(3)
            path = project_path(recorded, repo)
            add("error", message, path, number if path else None)
            index = _skip_block(lines, index)
            continue

        match = _BANG.match(raw)
        if match:
            block, index = _read_block(lines, index)
            pointer = next(
                (int(m.group(1)) for m in map(_TEX_LINE.match, block) if m), None
            )
            add("error", match.group(1), *here(pointer))
            continue

        if _BOX.match(raw):
            found = _BOX_LINES.search(raw)
            number = int(found.group(1)) if found else None
            add("warning", raw, *here(number))
            # The block under a box warning is the typeset material itself, and
            # its brackets are prose, not files.
            index = _skip_block(lines, index)
            continue

        match = _WARNING.match(raw)
        if match:
            block, index = _read_block(lines, index)
            # A warning runs on over several lines, and where it breaks depends
            # on `max_print_line`. Joined back together, "on input line 41" is
            # in one piece wherever TeX chose to wrap it.
            whole = " ".join([match.group(1)] + [_CONTINUED.sub("", b) for b in block])
            found = _INPUT_LINE.search(whole)
            add("warning", whole, *here(int(found.group(1)) if found else None))
            continue

        _track_open_files(open_files, raw)

    return problems[:MAX_PROBLEMS]


def _read_block(lines: list[str], index: int) -> tuple[list[str], int]:
    """The rest of a message: TeX ends one with an empty line."""
    block: list[str] = []
    while index < len(lines) and lines[index].strip():
        block.append(lines[index])
        index += 1
    return block, index


def _skip_block(lines: list[str], index: int) -> int:
    return _read_block(lines, index)[1]


def _track_open_files(open_files: list[str | None], line: str) -> None:
    """Follow TeX's brackets, so a message can be attributed to a file.

    Every `(` is pushed, whether or not it opens a file, because the matching
    `)` will pop something regardless: counting only the real files would let a
    `(badness 3942)` in a message close the file it was reported from.
    """
    index = 0
    while index < len(line):
        character = line[index]
        if character == "(":
            end = index + 1
            while end < len(line) and line[end] not in "()[] \t":
                end += 1
            name = line[index + 1 : end]
            open_files.append(name if name and _LOOKS_LIKE_A_FILE.search(name) else None)
            index = end
        else:
            if character == ")" and open_files:
                open_files.pop()
            index += 1


def latexdiff_available() -> bool:
    return shutil.which("latexdiff") is not None


_DOCUMENTCLASS = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{[^}]*\}")
_INPUT = re.compile(r"^[^%\n]*?\\(?:input|include)\s*\{([^}]+)\}", re.M)

DIF_PREAMBLE_MARK = "%DIF PREAMBLE EXTENSION ADDED BY LATEXDIFF"
_PREAMBLE_BLOCK = re.compile(
    re.escape(DIF_PREAMBLE_MARK) + r".*?%DIF END PREAMBLE EXTENSION ADDED BY LATEXDIFF",
    re.S,
)


def _dif_preamble(workdir: Path, timeout: float = 60) -> str:
    """latexdiff's own macro definitions, taken from a throwaway diff.

    They are normally injected into whichever file carries `\\documentclass`.
    Galley diffs section files, which carry no preamble, so the block has to be
    fetched once and put into the paper's main file by hand.
    """
    a, b = workdir / "_dif_a.tex", workdir / "_dif_b.tex"
    a.write_text("\\documentclass{article}\n\\begin{document}\nold\n\\end{document}\n")
    b.write_text("\\documentclass{article}\n\\begin{document}\nnew\n\\end{document}\n")
    try:
        proc = subprocess.run(
            ["latexdiff", str(a), str(b)], capture_output=True, text=True, timeout=timeout
        )
    finally:
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)
    match = _PREAMBLE_BLOCK.search(proc.stdout)
    return match.group(0) if match else ""


def find_documentclass(tree: Path, main_tex: str, depth: int = 4) -> Path | None:
    """The file that actually carries `\\documentclass`, following `\\input`.

    A paper's root file is often only a proxy — Overleaf requires the compiled
    file to sit in the repository root, so a project whose real driver lives at
    publications/<kind>/<name>/main.tex keeps a one-line root file that inputs
    it. Putting latexdiff's preamble in the proxy would load packages before the
    document class and the build dies on "Command \\abovecaptionskip already
    defined".
    """
    seen: set[Path] = set()
    queue = [(tree / main_tex, 0)]
    while queue:
        path, level = queue.pop(0)
        if path in seen or level > depth or not path.is_file():
            continue
        seen.add(path)
        text = path.read_text(errors="replace")
        if _DOCUMENTCLASS.search(text):
            return path
        for rel in _INPUT.findall(text):
            child = tree / (rel if rel.endswith(".tex") else rel + ".tex")
            queue.append((child, level + 1))
    return None


def latexdiff_pdf(
    accepted_repo: Path,
    proposed_repo: Path,
    main_tex: str,
    outdir: Path,
    changed: list[str] | None = None,
    timeout: float = 900,
) -> CompileResult:
    """Marked-up PDF of the paper: accepted state against the proposed one.

    Diffs **only the files that changed**, rather than latexdiff's `--flatten`.
    That is not a micro-optimisation. On a real paper `--flatten` diffs the
    whole flattened source and is pathological: on FLM it burned five minutes of
    CPU and produced nothing at all, while diffing the one changed section takes
    0.12 seconds. Same marked-up PDF, four orders of magnitude apart.

    The accepted tree is hard-linked into a scratch copy so the compile has the
    real figures, styles and bibliography without duplicating them; the diffed
    files are unlinked before being written, so the originals are never touched.
    """
    if not latexdiff_available():
        return CompileResult(
            ok=False,
            pdf=None,
            problems=[Problem("error", "latexdiff is not installed; `tlmgr install latexdiff`")],
            log_tail="",
        )
    changed = [c for c in (changed or []) if c.endswith(".tex")]
    if not changed:
        return CompileResult(
            ok=False,
            pdf=None,
            problems=[
                Problem(
                    "error",
                    "no .tex file changed on this branch, so there is nothing to mark up",
                )
            ],
            log_tail="",
        )

    outdir.mkdir(parents=True, exist_ok=True)
    tree = outdir / "tree"
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
    # Hard links: near-instant, and cheap on disk. Every write below unlinks
    # first, so the paper's own files can never be modified through them.
    subprocess.run(["cp", "-al", str(accepted_repo), str(tree)], check=True)
    shutil.rmtree(tree / ".git", ignore_errors=True)
    shutil.rmtree(tree / ".worktrees", ignore_errors=True)

    marked = 0
    for rel in changed:
        accepted_file, proposed_file = accepted_repo / rel, proposed_repo / rel
        if not proposed_file.is_file():
            continue
        proc = subprocess.run(
            ["latexdiff", str(accepted_file), str(proposed_file)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)  # break the hard link, never write through it
        target.write_text(proc.stdout)
        marked += 1

    if not marked:
        return CompileResult(
            ok=False,
            pdf=None,
            problems=[Problem("error", "latexdiff produced no markup for any changed file")],
            log_tail="",
        )

    driver = find_documentclass(tree, main_tex)
    if driver is None:
        return CompileResult(
            ok=False,
            pdf=None,
            problems=[
                Problem(
                    "error",
                    f"no \\documentclass found from {main_tex}; "
                    "cannot place the markup preamble",
                )
            ],
            log_tail="",
        )
    source = driver.read_text(errors="replace")
    if DIF_PREAMBLE_MARK not in source:
        preamble = _dif_preamble(tree, timeout=60)
        match = _DOCUMENTCLASS.search(source)
        assert match is not None  # find_documentclass only returns files that match
        source = source[: match.end()] + "\n" + preamble + source[match.end() :]
        driver.unlink(missing_ok=True)
        driver.write_text(source)

    result = compile_pdf(tree, main_tex, outdir, timeout=timeout)
    # The UI fetches this under a fixed name.
    if result.pdf and result.pdf.exists():
        target = outdir / "latexdiff.pdf"
        if result.pdf != target:
            shutil.copy2(result.pdf, target)
        return CompileResult(True, target, result.problems, result.log_tail)
    return result
