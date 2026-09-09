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

# latexmk's own summary lines are noise; these are the two things worth seeing.
_ERROR = re.compile(r"^(?:!.*|.*?:\d+:.*?Error.*|.*?\.tex:\d+:.*)$", re.MULTILINE)
_UNDEFINED = re.compile(
    r"(?:LaTeX Warning: (?:Reference|Citation) `([^']+)' on page .*? undefined"
    r"|Package natbib Warning: Citation `([^']+)' on page .*? undefined)"
)


@dataclass
class CompileResult:
    ok: bool
    pdf: Path | None
    errors: list[str]
    undefined: list[str]
    log_tail: str

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "pdf": str(self.pdf) if self.pdf else None,
            "errors": self.errors,
            "undefined": self.undefined,
            "log_tail": self.log_tail,
        }


def compile_pdf(repo: Path, main_tex: str, outdir: Path, timeout: float = 600) -> CompileResult:
    """Run latexmk and report only what a human has to act on."""
    outdir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "latexmk",
            "-pdf",
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
        errors=_collect_errors(log or proc.stdout + "\n" + proc.stderr),
        undefined=_collect_undefined(log),
        log_tail="\n".join(log.splitlines()[-60:]) or proc.stderr[-4000:],
    )


def _collect_errors(text: str) -> list[str]:
    seen: list[str] = []
    for line in _ERROR.findall(text):
        line = line.strip()
        if line and line not in seen and not line.startswith("! ==="):
            seen.append(line)
    return seen[:40]


def _collect_undefined(text: str) -> list[str]:
    names: list[str] = []
    for ref, cite in _UNDEFINED.findall(text):
        name = ref or cite
        if name and name not in names:
            names.append(name)
    return names


def latexdiff_available() -> bool:
    return shutil.which("latexdiff") is not None


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
            errors=["latexdiff is not installed; `tlmgr install latexdiff`"],
            undefined=[],
            log_tail="",
        )
    changed = [c for c in (changed or []) if c.endswith(".tex")]
    if not changed:
        return CompileResult(
            ok=False,
            pdf=None,
            errors=["no .tex file changed on this branch, so there is nothing to mark up"],
            undefined=[],
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
            errors=["latexdiff produced no markup for any changed file"],
            undefined=[],
            log_tail="",
        )

    main_path = tree / main_tex
    source = main_path.read_text(errors="replace")
    if DIF_PREAMBLE_MARK not in source:
        preamble = _dif_preamble(tree, timeout=60)
        if "\\begin{document}" in source:
            source = source.replace("\\begin{document}", preamble + "\n\\begin{document}", 1)
        else:
            source = preamble + "\n" + source
        main_path.unlink(missing_ok=True)
        main_path.write_text(source)

    result = compile_pdf(tree, main_tex, outdir, timeout=timeout)
    # The UI fetches this under a fixed name.
    if result.pdf and result.pdf.exists():
        target = outdir / "latexdiff.pdf"
        if result.pdf != target:
            shutil.copy2(result.pdf, target)
        return CompileResult(True, target, result.errors, result.undefined, result.log_tail)
    return result
