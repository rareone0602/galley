"""Compiling, and reporting only what a human has to act on."""

from __future__ import annotations

import shutil

import pytest

from galley.services import latex

MINIMAL = r"""\documentclass{article}
\begin{document}
Hello. See \ref{sec:nowhere} and \cite{nobody}.
\end{document}
"""

BROKEN = r"""\documentclass{article}
\begin{document}
\thisCommandDoesNotExist
\end{document}
"""

pytestmark = pytest.mark.skipif(
    shutil.which("latexmk") is None, reason="latexmk is not installed"
)


def test_a_clean_paper_compiles_and_reports_nothing(tmp_path) -> None:
    (tmp_path / "ok.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n"
    )
    result = latex.compile_pdf(tmp_path, "ok.tex", tmp_path / "out")
    assert result.ok
    assert result.pdf and result.pdf.exists()
    assert result.errors == []
    assert result.undefined == []


def test_undefined_references_are_reported_from_the_settled_log(tmp_path) -> None:
    """latexmk's first pass calls every citation undefined; only the last pass
    is the truth. Reading the console instead of the .log reports phantoms."""
    (tmp_path / "refs.tex").write_text(MINIMAL)
    result = latex.compile_pdf(tmp_path, "refs.tex", tmp_path / "out")
    assert "sec:nowhere" in result.undefined
    assert "nobody" in result.undefined


def test_a_broken_paper_reports_the_error(tmp_path) -> None:
    (tmp_path / "bad.tex").write_text(BROKEN)
    result = latex.compile_pdf(tmp_path, "bad.tex", tmp_path / "out")
    assert not result.ok
    assert any("thisCommandDoesNotExist" in e or "Undefined control" in e for e in result.errors)


def test_latexdiff_is_reported_missing_rather_than_crashing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(latex.shutil, "which", lambda _: None)
    result = latex.latexdiff_pdf(tmp_path, tmp_path, "main.tex", tmp_path / "out")
    assert not result.ok
    assert "latexdiff is not installed" in result.errors[0]
