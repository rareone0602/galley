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


def latexdiff_pdf(
    accepted_repo: Path,
    proposed_repo: Path,
    main_tex: str,
    outdir: Path,
    timeout: float = 900,
) -> CompileResult:
    """Marked-up PDF of the whole paper, accepted state versus proposed state.

    `--flatten` is what makes this work on a real paper: main.tex is mostly
    \\input, and without it latexdiff would compare two files of include lines.
    """
    if not latexdiff_available():
        return CompileResult(
            ok=False,
            pdf=None,
            errors=["latexdiff is not installed; `tlmgr install latexdiff`"],
            undefined=[],
            log_tail="",
        )
    outdir.mkdir(parents=True, exist_ok=True)
    diff_tex = outdir / "latexdiff.tex"
    proc = subprocess.run(
        [
            "latexdiff",
            "--flatten",
            str(accepted_repo / main_tex),
            str(proposed_repo / main_tex),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return CompileResult(
            ok=False,
            pdf=None,
            errors=[f"latexdiff failed: {proc.stderr.strip()[:2000]}"],
            undefined=[],
            log_tail="",
        )
    diff_tex.write_text(proc.stdout)
    # Compile in the accepted tree so .sty, .bib, and figures all resolve.
    staged = accepted_repo / diff_tex.name
    staged.write_text(proc.stdout)
    try:
        return compile_pdf(accepted_repo, diff_tex.name, outdir, timeout=timeout)
    finally:
        staged.unlink(missing_ok=True)
