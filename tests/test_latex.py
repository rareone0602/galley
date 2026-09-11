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

needs_latexmk = pytest.mark.skipif(
    shutil.which("latexmk") is None, reason="latexmk is not installed"
)


def only(problems, severity):
    return [p for p in problems if p.severity == severity]


@needs_latexmk
def test_a_clean_paper_compiles_and_reports_nothing(tmp_path) -> None:
    (tmp_path / "ok.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n"
    )
    result = latex.compile_pdf(tmp_path, "ok.tex", tmp_path / "out")
    assert result.ok
    assert result.pdf and result.pdf.exists()
    assert result.problems == []


@needs_latexmk
def test_undefined_references_are_reported_from_the_settled_log(tmp_path) -> None:
    """latexmk's first pass calls every citation undefined; only the last pass
    is the truth. Reading the console instead of the .log reports phantoms."""
    (tmp_path / "refs.tex").write_text(MINIMAL)
    result = latex.compile_pdf(tmp_path, "refs.tex", tmp_path / "out")
    said = only(result.problems, "warning")
    assert [p.message for p in said if "sec:nowhere" in p.message]
    assert [p.message for p in said if "nobody" in p.message]
    # Both are on line 3, and both are warnings: the paper still builds. LaTeX
    # adds its own "There were undefined references" at the end, which names no
    # line because it is about the document rather than a place in it.
    assert result.ok
    assert {(p.path, p.line) for p in said if p.line} == {("refs.tex", 3)}
    assert [p.message for p in said if not p.line] == ["There were undefined references."]


@needs_latexmk
def test_a_broken_paper_points_at_the_line_that_broke(tmp_path) -> None:
    (tmp_path / "bad.tex").write_text(BROKEN)
    result = latex.compile_pdf(tmp_path, "bad.tex", tmp_path / "out")
    assert not result.ok
    failures = only(result.problems, "error")
    assert failures, result.log_tail
    assert any("Undefined control sequence" in p.message for p in failures)
    # The line it names has to be the line that actually broke.
    named = {(p.path, p.line) for p in failures if p.line}
    assert named == {("bad.tex", 3)}
    assert BROKEN.splitlines()[2] == "\\thisCommandDoesNotExist"


@needs_latexmk
def test_an_error_deep_in_an_included_file_names_that_file(tmp_path) -> None:
    """The root file compiles; the section it pulls in is the one at fault."""
    (tmp_path / "sections").mkdir()
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n\\input{sections/body}\n\\end{document}\n"
    )
    (tmp_path / "sections" / "body.tex").write_text(
        "One.\n\nTwo.\n\n\\noSuchCommandHere\n\nFour.\n"
    )
    result = latex.compile_pdf(tmp_path, "main.tex", tmp_path / "out")
    assert not result.ok
    assert ("sections/body.tex", 5) in {(p.path, p.line) for p in only(result.problems, "error")}


@needs_latexmk
def test_an_overfull_box_is_a_warning_on_the_line_that_overflowed(tmp_path) -> None:
    """Overfull boxes do not stop the build, and the log never names their file:
    it has to be worked out from which file TeX had open."""
    (tmp_path / "wide.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nFine.\n"
        "\\hbox to 5pt{averyverylongwordindeed}\n\\end{document}\n"
    )
    result = latex.compile_pdf(tmp_path, "wide.tex", tmp_path / "out")
    assert result.ok, "an overfull box is not a failure"
    boxes = [p for p in only(result.problems, "warning") if "Overfull" in p.message]
    assert [(p.path, p.line) for p in boxes] == [("wide.tex", 4)]


@needs_latexmk
def test_latexdiff_is_reported_missing_rather_than_crashing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(latex.shutil, "which", lambda _: None)
    result = latex.latexdiff_pdf(tmp_path, tmp_path, "main.tex", tmp_path / "out")
    assert not result.ok
    assert "latexdiff is not installed" in result.problems[0].message
    assert result.problems[0].severity == "error"


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


@needs_latexmk
def test_a_review_with_no_changed_tex_says_so(tmp_path) -> None:
    result = latex.latexdiff_pdf(tmp_path, tmp_path, "main.tex", tmp_path / "out", changed=[])
    assert not result.ok
    assert "nothing to mark up" in result.problems[0].message


@needs_latexmk
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
    assert result.ok, result.problems
    assert result.pdf and result.pdf.name == "latexdiff.pdf" and result.pdf.exists()
    marked = (tmp_path / "out" / "tree" / "sections" / "body.tex").read_text()
    assert "DIFdel" in marked and "DIFadd" in marked


@needs_latexmk
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


# -- reading the log, which needs no LaTeX ----------------------------------
#
# TeX's transcript is the awkward part of this feature, and these pin the
# places it is easy to get wrong. Every one of them is a shape taken from a
# real log of this paper or of a deliberately broken document.


def _log(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def _repo_with_a_section(tmp_path, lines: int = 60):
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "intro.tex").write_text(
        "".join(f"line {n}\n" for n in range(1, lines + 1))
    )
    return tmp_path


def test_a_warning_is_pinned_to_the_file_tex_had_open(tmp_path) -> None:
    """The log never says which file an undefined reference is in. The only
    evidence is which file TeX had open when it said so."""
    repo = _repo_with_a_section(tmp_path)
    problems = latex.parse_log(
        _log(
            "(./sections/intro.tex",
            "",
            "LaTeX Warning: Reference `fig:one' on page 1 undefined on input line 41.",
            "",
            ")",
        ),
        repo,
    )
    assert [(p.severity, p.path, p.line) for p in problems] == [
        ("warning", "sections/intro.tex", 41)
    ]


def test_a_warning_broken_over_two_lines_still_finds_its_line(tmp_path) -> None:
    """TeX wraps the transcript at `max_print_line`, so `on input line 41` is
    split wherever the count happens to fall."""
    repo = _repo_with_a_section(tmp_path)
    problems = latex.parse_log(
        _log(
            "(./sections/intro.tex",
            "",
            "LaTeX Warning: Citation `smith2020' on page 3 undefined on input",
            "line 41.",
            "",
            ")",
        ),
        repo,
    )
    assert [(p.path, p.line) for p in problems] == [("sections/intro.tex", 41)]
    assert "smith2020" in problems[0].message


def test_a_bracket_inside_a_box_warning_does_not_close_the_file(tmp_path) -> None:
    """Under an overfull box TeX prints the type it set, brackets and all. Read
    as transcript, `2021)` closes the file, and everything after it is blamed on
    whatever was open before."""
    repo = _repo_with_a_section(tmp_path)
    problems = latex.parse_log(
        _log(
            "(./sections/intro.tex",
            "Underfull \\hbox (badness 3942) in paragraph at lines 12--12",
            "[]|\\T1/ptm/m/n/9 (+20) GSM8K et al.[][], 2021) and more",
            " []",
            "",
            "LaTeX Warning: Reference `fig:two' on page 1 undefined on input line 20.",
            "",
            ")",
        ),
        repo,
    )
    assert [(p.severity, p.path, p.line) for p in problems] == [
        ("warning", "sections/intro.tex", 12),
        ("warning", "sections/intro.tex", 20),
    ]


def test_a_line_the_file_is_too_short_to_have_is_not_reported(tmp_path) -> None:
    """When the bracket counting has drifted the line number lands in the wrong
    file. The message is worth keeping; the place is not."""
    repo = _repo_with_a_section(tmp_path, lines=12)
    problems = latex.parse_log(
        _log(
            "(./sections/intro.tex",
            "",
            "LaTeX Warning: Reference `fig:one' on page 1 undefined on input line 4000.",
            "",
            ")",
        ),
        repo,
    )
    assert [(p.path, p.line) for p in problems] == [(None, None)]
    assert "fig:one" in problems[0].message


def test_a_failure_in_a_package_is_reported_without_a_place_to_go(tmp_path) -> None:
    """`-file-line-error` names the file, but a file of the TeX distribution is
    not one the editor can open."""
    problems = latex.parse_log(
        _log("/usr/share/texmf/tex/latex/natbib/natbib.sty:1104: Undefined control sequence."),
        tmp_path,
    )
    assert [(p.severity, p.path, p.line) for p in problems] == [("error", None, None)]
    assert problems[0].message == "Undefined control sequence."


def test_the_same_box_reported_three_times_is_listed_once(tmp_path) -> None:
    """A long table repeats one warning verbatim. Three copies are not three
    things to fix."""
    repo = _repo_with_a_section(tmp_path)
    problems = latex.parse_log(
        _log(
            "(./sections/intro.tex",
            *sum(
                (["Underfull \\hbox (badness 10000) in paragraph at lines 36--37", " []", ""]
                 for _ in range(3)),
                [],
            ),
            ")",
        ),
        repo,
    )
    assert len(problems) == 1 and problems[0].line == 36


def test_an_error_with_no_file_in_front_takes_texs_own_pointer(tmp_path) -> None:
    """Without `-file-line-error` TeX writes `! message` and then `l.<n>`."""
    repo = _repo_with_a_section(tmp_path)
    problems = latex.parse_log(
        _log(
            "(./sections/intro.tex",
            "! Undefined control sequence.",
            "l.17 \\noSuchCommand",
            "",
            ")",
        ),
        repo,
    )
    assert [(p.severity, p.path, p.line) for p in problems] == [
        ("error", "sections/intro.tex", 17)
    ]


# -- handing a failure to Claude --------------------------------------------


def result(ok, problems, log_tail="") -> latex.CompileResult:
    return latex.CompileResult(ok=ok, pdf=None, problems=problems, log_tail=log_tail)


def test_a_build_that_worked_is_nothing_to_ask_about() -> None:
    assert latex.fix_request(result(True, [])) is None


def test_the_request_names_the_file_and_line_that_broke() -> None:
    asked = latex.fix_request(
        result(False, [latex.Problem("error", "Undefined control sequence.", "sections/intro.tex", 41)])
    )
    assert asked is not None
    # The first line becomes the session's title in the rail, so it has to say
    # what went wrong rather than that something did.
    head = asked.splitlines()[0]
    assert head == "Fix the compile error: Undefined control sequence. (sections/intro.tex:41)"
    assert "sections/intro.tex:41  Undefined control sequence." in asked


def test_warnings_are_left_out_of_the_request() -> None:
    """A real paper carries dozens; none of them is why the build failed."""
    asked = latex.fix_request(
        result(
            False,
            [
                latex.Problem("warning", "Overfull \\hbox (12pt too wide)", "main.tex", 9),
                latex.Problem("error", "Missing $ inserted.", "main.tex", 12),
            ],
        )
    )
    assert asked is not None
    assert "Overfull" not in asked
    assert asked.splitlines()[0] == "Fix the compile error: Missing $ inserted. (main.tex:12)"


def test_several_errors_are_counted_and_the_first_one_is_named() -> None:
    problems = [
        latex.Problem("error", f"Error number {n}.", "main.tex", n) for n in range(1, 4)
    ]
    asked = latex.fix_request(result(False, problems))
    assert asked is not None
    assert asked.splitlines()[0] == "Fix 3 compile errors, the first: Error number 1. (main.tex:1)"
    for n in range(1, 4):
        assert f"main.tex:{n}  Error number {n}." in asked


def test_a_long_list_is_cut_and_says_it_was() -> None:
    problems = [
        latex.Problem("error", f"Error number {n}.", "main.tex", n)
        for n in range(1, latex.MAX_NAMED_ERRORS + 4)
    ]
    asked = latex.fix_request(result(False, problems))
    assert asked is not None
    assert "… and 3 more" in asked
    assert f"Error number {latex.MAX_NAMED_ERRORS}." in asked
    assert f"Error number {latex.MAX_NAMED_ERRORS + 1}." not in asked


def test_an_error_with_nowhere_to_go_does_not_invent_a_line() -> None:
    """Being sent to the wrong sentence is worse than being sent nowhere."""
    asked = latex.fix_request(result(False, [latex.Problem("error", "Emergency stop.")]))
    assert asked is not None
    assert "no file named  Emergency stop." in asked
    assert ":1" not in asked


def test_a_failure_the_parser_could_not_read_still_asks_with_the_log() -> None:
    asked = latex.fix_request(result(False, [], log_tail="! TeX capacity exceeded\n"))
    assert asked is not None
    assert asked.splitlines()[0] == "The paper does not compile, and the log names no error"
    assert "! TeX capacity exceeded" in asked


def test_the_request_tells_the_agent_not_to_rewrite_the_paper() -> None:
    """The one thing this prompt exists to prevent: a missing brace coming
    back as a reworded section, with every sentence of it to review."""
    asked = latex.fix_request(result(False, [latex.Problem("error", "Missing }.", "main.tex", 3)]))
    assert asked is not None
    assert "do not reword, reformat or rewrap anything you are not fixing" in asked


@needs_latexmk
def test_a_real_broken_build_produces_a_request_naming_the_real_line(tmp_path) -> None:
    (tmp_path / "bad.tex").write_text(BROKEN)
    asked = latex.fix_request(latex.compile_pdf(tmp_path, "bad.tex", tmp_path / "out"))
    assert asked is not None
    assert "bad.tex:3" in asked
    assert "Undefined control sequence" in asked


@needs_latexmk
def test_latexmks_own_verdict_is_not_a_second_error(tmp_path) -> None:
    """One broken command is one error.

    `==> Fatal error occurred, no output PDF file produced!` is how latexmk
    says the build stopped. It is an error by every test the parser has, it
    repeats the line of the real one, and it is nowhere to send anyone."""
    (tmp_path / "bad.tex").write_text(BROKEN)
    built = latex.compile_pdf(tmp_path, "bad.tex", tmp_path / "out")
    assert not built.ok
    assert [p.message for p in only(built.problems, "error")] == ["Undefined control sequence."]
    asked = latex.fix_request(built)
    assert asked is not None
    assert asked.splitlines()[0] == "Fix the compile error: Undefined control sequence. (bad.tex:3)"
