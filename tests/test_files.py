"""The file browser, the editor's reads, and the selection that starts a session.

Everything here runs against a real git repository. What the rail shows is what
`git` says is in the project, so the tests ask git too.
"""

from __future__ import annotations

from pathlib import Path

from galley.services import files
from galley.services.agent import Selection, compose_prompt


# -- what counts as "the project" -------------------------------------------


def test_the_tree_is_what_git_tracks(paper_repo: Path, git_helper) -> None:
    (paper_repo / "sections").mkdir()
    (paper_repo / "sections" / "method.tex").write_text("\\section{Method}\n")
    (paper_repo / "refs.bib").write_text("@article{a,title={A}}\n")
    git_helper(paper_repo, "add", "-A")
    git_helper(paper_repo, "commit", "-qm", "sections")

    paths = files.listing(paper_repo)
    assert paths == ["main.tex", "refs.bib", "sections/method.tex"]


def test_untracked_files_are_in_the_project_but_ignored_ones_are_not(paper_repo: Path) -> None:
    (paper_repo / ".gitignore").write_text("*.aux\nbuild/\n")
    (paper_repo / "new-section.tex").write_text("\\section{New}\n")
    (paper_repo / "main.aux").write_text("junk")
    (paper_repo / "build").mkdir()
    (paper_repo / "build" / "main.pdf").write_bytes(b"%PDF-1.5")

    paths = files.listing(paper_repo)
    assert "new-section.tex" in paths, "a section you have not committed is still yours"
    assert "main.aux" not in paths
    assert not any(p.startswith("build/") for p in paths)


def test_session_worktrees_never_appear_in_the_tree(client, paper_repo: Path) -> None:
    client.post("/api/sessions", json={"prompt": "a session", "start": False})
    assert (paper_repo / ".worktrees").is_dir()
    assert not any(p.startswith(".worktrees") for p in files.listing(paper_repo))


def test_the_tree_nests_folders_first_then_files(paper_repo: Path) -> None:
    for rel in ("z.tex", "figures/plot.png", "sections/intro.tex", "a.bib"):
        target = paper_repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

    tree = files.tree(paper_repo)
    assert [n["name"] for n in tree] == ["figures", "sections", "a.bib", "main.tex", "z.tex"]
    figures = next(n for n in tree if n["name"] == "figures")
    assert figures["children"] == [
        {"name": "plot.png", "path": "figures/plot.png", "type": "image"}
    ]


def test_a_file_knows_whether_the_editor_can_open_it() -> None:
    assert files.kind_of("sections/method.tex") == "tex"
    assert files.kind_of("refs.bib") == "text"
    assert files.kind_of("figures/loss.png") == "image"
    assert files.kind_of("figures/loss.pdf") == "figure"
    assert files.is_text("main.tex") and not files.is_text("figures/loss.pdf")


# -- reading a file ----------------------------------------------------------


def test_reading_a_file_returns_it_verbatim(client, paper_repo: Path) -> None:
    body = client.get("/api/file", params={"path": "main.tex"}).json()
    assert body["content"] == (paper_repo / "main.tex").read_text()
    assert body["type"] == "tex"


def test_a_figure_is_reported_but_not_decoded(client, paper_repo: Path) -> None:
    blob = b"%PDF-1.5\n\x00\x01binary"
    (paper_repo / "plot.pdf").write_bytes(blob)
    body = client.get("/api/file", params={"path": "plot.pdf"}).json()
    assert body["content"] is None and body["type"] == "figure"
    assert body["bytes"] == len(blob)

    served = client.get("/api/blob", params={"path": "plot.pdf"})
    assert served.status_code == 200 and served.content.startswith(b"%PDF")


def test_a_path_cannot_climb_out_of_the_project(client) -> None:
    for route in ("/api/file", "/api/blob"):
        assert client.get(route, params={"path": "../../etc/passwd"}).status_code == 400
    assert client.put("/api/files/../escape.tex", json={"content": "x"}).status_code != 200


def test_the_tree_can_be_read_from_a_session_checkout(client) -> None:
    row = client.post("/api/sessions", json={"prompt": "read me", "start": False}).json()
    body = client.get("/api/tree", params={"session_id": row["id"]}).json()
    assert body["root"] == row["worktree_path"]
    assert [n["name"] for n in body["tree"]] == ["main.tex"]


# -- a session that starts from a selection ---------------------------------


def test_a_selection_is_recorded_on_the_session(client, paper_repo: Path) -> None:
    text = "The model improves over the baseline by a large margin across all settings."
    start = (paper_repo / "main.tex").read_text().index(text)
    row = client.post(
        "/api/sessions",
        json={
            "prompt": "hedge this",
            "start": False,
            "selection": {
                "path": "main.tex",
                "start": start,
                "end": start + len(text),
                "text": text,
            },
        },
    ).json()
    assert row["sel_path"] == "main.tex"
    assert row["sel_text"] == text
    assert row["sel_start"] == start


def test_an_empty_selection_is_simply_no_selection(client) -> None:
    row = client.post(
        "/api/sessions",
        json={"prompt": "no block", "start": False, "selection": {"path": "main.tex", "text": "  "}},
    ).json()
    assert row["sel_path"] is None


def test_the_prompt_quotes_the_passage_and_says_where_it_is() -> None:
    source = "line one\nline two\nthe selected sentence\nline four\n"
    selection = Selection(
        path="sections/method.tex",
        start=source.index("the selected"),
        end=source.index("the selected") + len("the selected sentence"),
        text="the selected sentence",
    )
    prompt = compose_prompt("Make it shorter.", selection, source)

    assert "<selection>\nthe selected sentence\n</selection>" in prompt
    assert "`sections/method.tex`, line 3" in prompt
    assert "Make it shorter." in prompt
    assert "byte-for-byte identical" in prompt


def test_a_multi_line_selection_reports_a_span() -> None:
    source = "a\nb\nc\nd\n"
    selection = Selection(path="p.tex", start=2, end=6, text="b\nc\n")
    assert "lines 2\u20134" in compose_prompt("go", selection, source)


def test_without_a_selection_the_prompt_is_yours_untouched() -> None:
    assert compose_prompt("just do this", None) == "just do this"
