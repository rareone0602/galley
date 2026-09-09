"""Double-clicking the PDF must land on the line that produced it.

These tests compile a real document with a real LaTeX and then ask the reader
where each visible line came from. Nothing is mocked: if the answer is wrong,
the cursor lands on the wrong sentence, which is the whole feature.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from galley.services.synctex import (
    BIG_POINTS_PER_POINT,
    SP_PER_POINT,
    Location,
    SyncTeX,
    find,
    resolve,
)

DOC = """\\documentclass{article}
\\begin{document}
First line of prose here.

Second paragraph, a little longer, so it wraps somewhere in the middle of the page and gives us a few boxes to look at.

\\section{A section}
Third paragraph after a heading.
\\end{document}
"""

needs_latex = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="no LaTeX on this machine"
)


@pytest.fixture(scope="module")
def compiled(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("synctex")
    (out / "t.tex").write_text(DOC)
    subprocess.run(
        ["pdflatex", "-synctex=1", "-interaction=nonstopmode", "t.tex"],
        cwd=out,
        capture_output=True,
        timeout=180,
    )
    return out


@needs_latex
def test_latex_writes_a_synctex_file_next_to_the_pdf(compiled: Path) -> None:
    assert find(compiled / "t.pdf") == compiled / "t.synctex.gz"


@needs_latex
def test_the_scale_is_pinned_by_texs_one_inch_origin(compiled: Path) -> None:
    """TeX puts the page body exactly one inch in, so that box says the scale."""
    st = SyncTeX.read(compiled / "t.synctex.gz")
    body = st.pages[1].children[0]
    assert body.x / SP_PER_POINT * BIG_POINTS_PER_POINT == pytest.approx(72.0, abs=0.01)


@needs_latex
@pytest.mark.parametrize(
    "x, y, line, what",
    [
        (200, 134.8, 3, "first paragraph"),
        (250, 146.7, 5, "second paragraph, first visual line"),
        (200, 158.7, 5, "second paragraph, where it wrapped"),
        (200, 191.6, 7, "the section heading"),
        (200, 213.4, 8, "the paragraph after the heading"),
    ],
)
def test_a_click_lands_on_the_line_that_wrote_it(
    compiled: Path, x: float, y: float, line: int, what: str
) -> None:
    st = SyncTeX.read(compiled / "t.synctex.gz")
    found = st.edit(1, x, y)
    assert found is not None, what
    assert found.line == line, f"{what}: {DOC.splitlines()[found.line - 1]!r}"


@needs_latex
def test_a_wrapped_paragraph_still_points_at_where_you_wrote_it(compiled: Path) -> None:
    """The two visual lines of one source paragraph agree on their source."""
    st = SyncTeX.read(compiled / "t.synctex.gz")
    first, second = st.edit(1, 250, 146.7), st.edit(1, 200, 158.7)
    assert first and second and first.line == second.line


@needs_latex
def test_a_click_off_the_page_still_answers_with_the_nearest_thing(compiled: Path) -> None:
    st = SyncTeX.read(compiled / "t.synctex.gz")
    assert st.edit(1, 5, 5) is not None


@needs_latex
def test_an_unknown_page_has_no_answer(compiled: Path) -> None:
    assert SyncTeX.read(compiled / "t.synctex.gz").edit(9, 200, 200) is None


# -- parsing, without needing LaTeX -----------------------------------------

MINIMAL = """SyncTeX Version:1
Input:1:./sections/method.tex
Input:2:/usr/share/texmf/tex/latex/base/article.cls
Output:pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
{1
[1,10:0,0:30000000,40000000,0
(1,12:6553600,13107200:13107200,655360,131072
x1,12:6553600,13107200
x1,13:9830400,13107200
)
]
}1
Postamble:
"""


def test_the_settings_and_the_input_map_are_read() -> None:
    st = SyncTeX.parse(MINIMAL.splitlines())
    assert st.inputs == {
        1: "./sections/method.tex",
        2: "/usr/share/texmf/tex/latex/base/article.cls",
    }
    assert (st.unit, st.magnification, st.x_offset, st.y_offset) == (1, 1000, 0, 0)
    assert list(st.pages) == [1]


def test_the_innermost_record_wins_over_the_box_around_it() -> None:
    """The line box is labelled where the paragraph ended; the words are not."""
    st = SyncTeX.parse(MINIMAL.splitlines())
    # Both clicks land inside the one line box, which is labelled line 12.
    left = st.edit(1, 100.0, 199.0)
    right = st.edit(1, 149.0, 199.0)
    assert left and left.line == 12
    assert right and right.line == 13, "the record nearer the click decides"


def test_a_project_file_comes_back_relative_to_the_project(tmp_path: Path) -> None:
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    (repo / "sections" / "method.tex").write_text("x")
    out = repo / ".galley" / "build"
    out.mkdir(parents=True)

    assert resolve(Location("./sections/method.tex", 12), repo, out) == {
        "path": "sections/method.tex",
        "line": 12,
        "in_project": True,
    }


def test_a_file_from_the_tex_distribution_is_reported_as_not_yours(tmp_path: Path) -> None:
    repo = tmp_path / "paper"
    repo.mkdir()
    got = resolve(Location("/usr/share/texmf/article.cls", 40), repo, repo / "build")
    assert got["in_project"] is False and got["path"].endswith("article.cls")


def test_the_marked_up_build_resolves_back_to_the_real_file(tmp_path: Path) -> None:
    """latexdiff compiles a scratch copy; its paths still name files of yours."""
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    (repo / "sections" / "method.tex").write_text("x")
    tree = tmp_path / "state" / "review" / "abc" / "tree"
    (tree / "sections").mkdir(parents=True)

    assert resolve(Location(str(tree / "sections" / "method.tex"), 7), repo, tree) == {
        "path": "sections/method.tex",
        "line": 7,
        "in_project": True,
    }


# -- through the API ---------------------------------------------------------


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk is not installed")
def test_the_route_answers_a_click_on_the_compiled_paper(client, config) -> None:
    """Compile the fixture paper, then ask where a point on page 1 came from."""
    from galley.services import latex

    result = latex.compile_pdf(
        config.paths.paper_repo, "main.tex", config.paths.state_dir / "build"
    )
    assert result.ok, result.log_tail

    # The fixture is one paragraph of three sentences, so it typesets as two
    # lines: the first carries source line 3, the second carries line 4.
    for y, line in ((135, 3), (147, 4)):
        body = client.get("/api/synctex/edit", params={"page": 1, "x": 200, "y": y}).json()
        assert body == {"path": "main.tex", "line": line, "in_project": True}


def test_a_pdf_with_no_synctex_says_so_rather_than_guessing(client, config) -> None:
    build = config.paths.state_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "main.pdf").write_bytes(b"%PDF-1.5\n")
    response = client.get("/api/synctex/edit", params={"page": 1, "x": 10, "y": 10})
    assert response.status_code == 404
    assert "synctex" in response.json()["detail"]
