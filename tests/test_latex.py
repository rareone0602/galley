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


# -- finding the real driver behind a root proxy --------------------------


def test_the_documentclass_is_found_in_the_root_file(tmp_path) -> None:
    (tmp_path / "main.tex").write_text(MINIMAL)
    assert latex.find_documentclass(tmp_path, "main.tex") == tmp_path / "main.tex"


def test_the_documentclass_is_found_through_a_root_proxy(tmp_path) -> None:
    """Overleaf requires the compiled file in the repository root, so a real
    paper's root main.tex is often one line inputting the driver."""
    (tmp_path / "publications").mkdir()
    (tmp_path / "main.tex").write_text("% a proxy, not a document\n\\input{publications/driver}\n")
    (tmp_path / "publications" / "driver.tex").write_text(MINIMAL)
    assert latex.find_documentclass(tmp_path, "main.tex") == tmp_path / "publications" / "driver.tex"


def test_a_commented_out_input_is_not_followed(tmp_path) -> None:
    (tmp_path / "main.tex").write_text("% \\input{ghost}\n\\documentclass{article}\n")
    assert latex.find_documentclass(tmp_path, "main.tex") == tmp_path / "main.tex"


def test_no_documentclass_anywhere_is_reported(tmp_path) -> None:
    (tmp_path / "main.tex").write_text("just prose, no class\n")
    assert latex.find_documentclass(tmp_path, "main.tex") is None


def test_a_review_with_no_changed_tex_says_so(tmp_path) -> None:
    result = latex.latexdiff_pdf(tmp_path, tmp_path, "main.tex", tmp_path / "out", changed=[])
    assert not result.ok
    assert "nothing to mark up" in result.errors[0]


def test_the_marked_up_build_finds_the_driver_and_compiles(tmp_path) -> None:
    """End to end on a proxy layout: the markup must reach the PDF."""
    accepted, proposed = tmp_path / "accepted", tmp_path / "proposed"
    for root, sentence in ((accepted, "The result is modest."), (proposed, "The result is large.")):
        (root / "sections").mkdir(parents=True)
        (root / "main.tex").write_text("\\input{publications/driver}\n")
        (root / "publications").mkdir(exist_ok=True)
        (root / "publications" / "driver.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\n\\input{sections/body}\n\\end{document}\n"
        )
        (root / "sections" / "body.tex").write_text(sentence + "\n")

    result = latex.latexdiff_pdf(
        accepted, proposed, "main.tex", tmp_path / "out", changed=["sections/body.tex"]
    )
    assert result.ok, result.errors
    assert result.pdf and result.pdf.name == "latexdiff.pdf" and result.pdf.exists()
    marked = (tmp_path / "out" / "tree" / "sections" / "body.tex").read_text()
    assert "DIFdel" in marked and "DIFadd" in marked


def test_the_accepted_tree_is_never_written_through_its_hard_links(tmp_path) -> None:
    """The scratch copy is hard-linked for speed; writing without unlinking
    first would edit the paper itself."""
    accepted, proposed = tmp_path / "accepted", tmp_path / "proposed"
    for root, sentence in ((accepted, "Before."), (proposed, "After.")):
        (root / "sections").mkdir(parents=True)
        (root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\n\\input{sections/body}\n\\end{document}\n"
        )
        (root / "sections" / "body.tex").write_text(sentence + "\n")

    latex.latexdiff_pdf(
        accepted, proposed, "main.tex", tmp_path / "out", changed=["sections/body.tex"]
    )
    assert (accepted / "sections" / "body.tex").read_text() == "Before.\n"
    assert (accepted / "main.tex").read_text().startswith("\\documentclass")
