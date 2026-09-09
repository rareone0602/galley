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
    project_path,
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
    """An ordinary compile runs in the paper, so `./x.tex` is the paper's x.tex."""
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    (repo / "sections" / "method.tex").write_text("x")

    assert resolve(Location("./sections/method.tex", 12), repo) == {
        "path": "sections/method.tex",
        "line": 12,
        "in_project": True,
    }


def test_a_file_from_the_tex_distribution_is_reported_as_not_yours(tmp_path: Path) -> None:
    repo = tmp_path / "paper"
    repo.mkdir()
    got = resolve(Location("/usr/share/texmf/article.cls", 40), repo)
    assert got["in_project"] is False and got["path"].endswith("article.cls")


def test_a_name_the_paper_does_not_have_is_not_claimed_as_the_papers(tmp_path: Path) -> None:
    """The scratch tree holds files the paper never had — .aux, .bbl, latexdiff's
    own workings. Naming one of them as yours would open an editor on nothing."""
    repo = tmp_path / "paper"
    repo.mkdir()
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "main.bbl").write_text("generated")
    assert project_path(str(tree / "main.bbl"), repo, tree) is None


def test_a_sessions_own_checkout_resolves_to_the_file_you_have_open(tmp_path: Path) -> None:
    """A session compiles `.worktrees/<slug>`, which sits *inside* the paper.

    Matching the repository first would answer with the agent's copy of the
    file — a real path, and the wrong one to put a cursor in."""
    repo = tmp_path / "paper"
    (repo / "sections").mkdir(parents=True)
    (repo / "sections" / "method.tex").write_text("x")
    worktree = repo / ".worktrees" / "tighten-the-abstract"
    (worktree / "sections").mkdir(parents=True)
    (worktree / "sections" / "method.tex").write_text("x")

    assert resolve(Location("./sections/method.tex", 12), repo, worktree) == {
        "path": "sections/method.tex",
        "line": 12,
        "in_project": True,
    }


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


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk is not installed")
def test_the_route_sends_the_pdf_to_a_line_you_are_writing(client, config) -> None:
    """Forward search over HTTP, and the two directions agreeing over HTTP.

    The point a double-click sends to `edit` has to come back inside one of the
    rectangles `view` returns for the line it named. Both speak big points from
    the top-left of the page, and this is what says so."""
    from galley.services import latex

    result = latex.compile_pdf(
        config.paths.paper_repo, "main.tex", config.paths.state_dir / "build"
    )
    assert result.ok, result.log_tail

    # The fixture is one paragraph of three sentences over two printed lines;
    # y=147 is the second of them, which source line 4 wrote.
    clicked = client.get("/api/synctex/edit", params={"page": 1, "x": 200, "y": 147}).json()
    assert clicked == {"path": "main.tex", "line": 4, "in_project": True}

    body = client.get("/api/synctex/view", params={"path": "main.tex", "line": 4}).json()
    assert body["line"] == 4 and body["fell_forward"] is False
    assert all(a["page"] == 1 for a in body["areas"])
    assert any(
        a["x"] <= 200 <= a["x"] + a["width"] and a["y"] <= 147 <= a["y"] + a["height"]
        for a in body["areas"]
    ), body["areas"]


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk is not installed")
def test_the_route_admits_when_it_has_fallen_forward(client, config) -> None:
    """Line 1 is `\\documentclass`, which prints nothing."""
    from galley.services import latex

    latex.compile_pdf(config.paths.paper_repo, "main.tex", config.paths.state_dir / "build")
    body = client.get("/api/synctex/view", params={"path": "main.tex", "line": 1}).json()
    assert body["asked_line"] == 1 and body["line"] > 1 and body["fell_forward"] is True


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk is not installed")
def test_a_line_that_printed_nothing_at_all_is_a_404_not_a_guess(client, config) -> None:
    from galley.services import latex

    latex.compile_pdf(config.paths.paper_repo, "main.tex", config.paths.state_dir / "build")
    response = client.get("/api/synctex/view", params={"path": "main.tex", "line": 9000})
    assert response.status_code == 404


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk is not installed")
def test_a_file_in_a_subdirectory_makes_the_round_trip_over_http(client, config) -> None:
    """Every section of a real paper is in a subdirectory, and the browser sends
    that slash percent-encoded. Both routes have to agree about the file as well
    as the place."""
    from galley.services import latex

    repo = config.paths.paper_repo
    (repo / "sections").mkdir()
    (repo / "sections" / "body.tex").write_text("One.\n\nTwo.\n\nThree.\n")
    (repo / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\input{sections/body}\n\\end{document}\n"
    )
    assert latex.compile_pdf(repo, "main.tex", config.paths.state_dir / "build").ok

    found = client.get("/api/synctex/view?path=sections%2Fbody.tex&line=3").json()
    assert found["path"] == "sections/body.tex" and found["line"] == 3
    area = found["areas"][0]

    back = client.get(
        "/api/synctex/edit",
        params={
            "page": area["page"],
            "x": area["x"] + area["width"] / 2,
            "y": area["y"] + area["height"] / 2,
        },
    ).json()
    assert back == {"path": "sections/body.tex", "line": 3, "in_project": True}


def test_a_pdf_with_no_synctex_says_so_rather_than_guessing(client, config) -> None:
    build = config.paths.state_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "main.pdf").write_bytes(b"%PDF-1.5\n")
    response = client.get("/api/synctex/edit", params={"page": 1, "x": 10, "y": 10})
    assert response.status_code == 404
    assert "synctex" in response.json()["detail"]


# -- forward search: the cursor is on a line, where is it in print? ----------


@needs_latex
@pytest.mark.parametrize(
    "line, y, what",
    [
        (3, 134.8, "the first paragraph"),
        (5, 146.7, "the paragraph that wraps"),
        (7, 191.6, "the section heading"),
        (8, 213.4, "the paragraph after the heading"),
    ],
)
def test_a_line_is_found_where_it_was_printed(
    compiled: Path, line: int, y: float, what: str
) -> None:
    """Asking for a line answers with the strip of page its words are on."""
    st = SyncTeX.read(compiled / "t.synctex.gz")
    view = st.view("t.tex", line, compiled)
    assert view is not None and view.areas, what
    assert view.line == line and not view.fell_forward
    assert any(a.page == 1 and a.contains(200, y) for a in view.areas), (
        f"{what}: expected one of {[a.as_dict() for a in view.areas]} to hold y={y}"
    )


@needs_latex
def test_a_wrapped_paragraph_is_found_on_both_of_its_printed_lines(compiled: Path) -> None:
    """One source line, two strips of page, because the sentence wrapped."""
    view = SyncTeX.read(compiled / "t.synctex.gz").view("t.tex", 5, compiled)
    assert view is not None
    assert len(view.areas) == 2
    assert [round(a.y, 1) for a in view.areas] == sorted(round(a.y, 1) for a in view.areas)


@needs_latex
@pytest.mark.parametrize("line", [1, 2])
def test_a_line_that_prints_nothing_falls_forward_and_says_so(compiled: Path, line: int) -> None:
    """`\\documentclass` and `\\begin{document}` put nothing on the page."""
    view = SyncTeX.read(compiled / "t.synctex.gz").view("t.tex", line, compiled)
    assert view is not None
    assert (view.asked_line, view.line, view.fell_forward) == (line, 3, True)


@needs_latex
def test_a_line_past_the_end_of_the_paper_has_no_answer(compiled: Path) -> None:
    assert SyncTeX.read(compiled / "t.synctex.gz").view("t.tex", 9999, compiled) is None


@needs_latex
def test_a_file_the_paper_never_read_has_no_answer(compiled: Path) -> None:
    assert SyncTeX.read(compiled / "t.synctex.gz").view("nowhere.tex", 1, compiled) is None


@needs_latex
@pytest.mark.parametrize(
    "x, y", [(200, 134.8), (250, 146.7), (200, 158.7), (200, 191.6), (200, 213.4)]
)
def test_a_click_survives_the_round_trip_back_to_the_page(
    compiled: Path, x: float, y: float
) -> None:
    """The two directions must agree, or one of them is lying.

    Point at a word, ask which line wrote it, then ask where that line went:
    one of the answers has to be the place you started from.
    """
    st = SyncTeX.read(compiled / "t.synctex.gz")
    where = st.edit(1, x, y)
    assert where is not None
    found = resolve(where, compiled)
    view = st.view(found["path"], found["line"], compiled)
    assert view is not None
    assert any(a.page == 1 and a.contains(x, y) for a in view.areas)


# -- the same round trip, on the paper that is actually being written --------

GALLEY = Path(__file__).resolve().parent.parent


def _the_real_paper() -> tuple[Path, Path] | None:
    """The live configuration's paper and its last build, if there is one.

    A four-line fixture cannot show that this works on a paper with figures,
    tables, floats and twenty-four pages. The one on this machine can.
    """
    from galley.config import CONFIG_NAME, load

    local = GALLEY / CONFIG_NAME
    if not local.is_file():
        return None
    try:
        cfg = load(local)
    except Exception:  # a machine where the paper repo has moved
        return None
    built = find(cfg.paths.state_dir / "build" / (Path(cfg.paper.main_tex).stem + ".pdf"))
    return (cfg.paths.paper_repo, built) if built else None


@pytest.mark.skipif(_the_real_paper() is None, reason="no compiled paper on this machine")
def test_every_line_of_the_real_paper_survives_the_round_trip() -> None:
    real = _the_real_paper()
    assert real is not None
    repo, built = real
    st = SyncTeX.read(built)

    # Probe from the data rather than a grid: the middle of each line of type
    # that a file of the paper's own contributed to. A grid mostly samples the
    # white space between lines, where reverse search answers with the nearest
    # thing and no round trip can hold.
    ours = {tag for tag, name in st.inputs.items() if project_path(name, repo)}
    probes = []
    for page in sorted(st.pages):
        for node, _ in st._walk(st.pages[page]):
            if not (node.is_box and node.width and node.height + node.depth):
                continue
            if any(c.tag in ours and not c.is_box for c in node.children):
                area = st._area(page, node)
                probes.append((page, area.x + area.width / 2, area.y + area.height / 2))
    assert len(probes) > 500, f"only {len(probes)} lines of type; is this the right build?"

    landed = missed = 0
    for page, x, y in probes[::8]:
        found = resolve(st.edit(page, x, y), repo)
        if not found["in_project"]:
            continue
        view = st.view(found["path"], found["line"], repo)
        assert view is not None, f"page {page} came from {found} and cannot be found again"
        if any(a.page == page and a.contains(x, y) for a in view.areas):
            landed += 1
        else:
            missed += 1
    assert landed > 100 and missed == 0, f"{missed} of {landed + missed} did not land back"


# -- the gap between two printed lines --------------------------------------

# A column holding two lines of type, with the leading between them, drawn the
# way the real file draws it: the column holds boxes and no words of its own,
# and it is labelled where the column was opened — line 99. One of those boxes
# is the empty one an environment leaves behind when it closes.
LEADING = """SyncTeX Version:1
Input:1:./body.tex
Output:pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
{1
[1,99:0,20000000:30000000,20000000,0
(1,99:6553600,19000000:13107200,100,0
)
(1,12:6553600,13107200:13107200,655360,0
x1,12:6553600,13107200
)
(1,20:6553600,14417920:13107200,655360,0
x1,20:6553600,14417920
)
]
}1
Postamble:
"""


def test_a_click_on_a_line_still_lands_on_that_line() -> None:
    st = SyncTeX.parse(LEADING.splitlines())
    assert (found := st.edit(1, 200, 194.23)) and found.line == 12
    assert (found := st.edit(1, 200, 214.23)) and found.line == 20


def test_a_click_in_the_leading_answers_with_a_line_not_the_column() -> None:
    """Between two printed lines nothing thin covers the point.

    The only box that does is the column holding the page, and its label is
    wherever the column was opened — on the real paper, the line of
    `\\end{abstract}`, which is nowhere near where you clicked. About a third
    of a paragraph's height is that gap, so the wrong answer is common rather
    than exotic.
    """
    st = SyncTeX.parse(LEADING.splitlines())
    found = st.edit(1, 200, 204.23)
    assert found is not None
    assert found.line != 99, "the column's own label is not a line of prose"
    assert found.line in (12, 20), "the nearer of the two lines it sits between"
