"""The file browser, what the rail can do to it, and the selection that starts
a session.

Everything here runs against a real git repository. What the rail shows is what
`git` says is in the project, so the tests ask git too — and the four functions
that write to disk are the only thing between a bug here and somebody's paper,
so every refusal they make has a test of its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from galley.services import files
from galley.services.agent import Selection, compose_prompt


def _commit(git_helper, repo: Path, message: str = "more") -> None:
    git_helper(repo, "add", "-A")
    git_helper(repo, "commit", "-qm", message)


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


def test_an_unknown_extension_opens_as_text_rather_than_being_refused(
    client, paper_repo: Path
) -> None:
    """The old rule was an allowlist, so a file it had not heard of was binary."""
    (paper_repo / "helper.ts").write_text("export const x = 1\n")
    body = client.get("/api/file", params={"path": "helper.ts"}).json()
    assert body["content"] == "export const x = 1\n"
    assert body["type"] == "text" and body["editable"] is True


def test_the_bytes_decide_and_not_the_name(client, paper_repo: Path) -> None:
    """A `.txt` full of NULs is a payload; a `.dat` full of prose is not."""
    (paper_repo / "weights.txt").write_bytes(b"\x89PNG\r\n\x00\x00 not text")
    payload = client.get("/api/file", params={"path": "weights.txt"}).json()
    assert payload["content"] is None and payload["type"] == "binary"

    (paper_repo / "notes.dat").write_text("a line of prose\n")
    prose = client.get("/api/file", params={"path": "notes.dat"}).json()
    assert prose["content"] == "a line of prose\n" and prose["editable"] is True


def test_a_file_too_big_to_edit_is_shown_from_the_front_and_locked(
    client, paper_repo: Path
) -> None:
    line = "x" * 79 + "\n"
    (paper_repo / "build.log").write_text(line * (files.MAX_TEXT_BYTES // 80 + 200))
    body = client.get("/api/file", params={"path": "build.log"}).json()

    assert body["truncated"] is True and body["editable"] is False
    assert body["bytes"] > files.MAX_TEXT_BYTES  # the real size, not what was read
    assert len(body["content"]) <= files.PREVIEW_BYTES
    # Cut at a line ending, so the last line on screen is a whole one.
    assert body["content"].endswith("\n")


def test_a_preview_cut_mid_character_is_still_utf_8(paper_repo: Path) -> None:
    """A multi-byte character split by the cap would read as a bad encoding."""
    target = paper_repo / "long.txt"
    target.write_text("é" * (files.MAX_TEXT_BYTES))  # two bytes each, no newline
    body = files.read(paper_repo, "long.txt")
    assert body["truncated"] is True and body["encoding"] == "utf-8"
    assert "\ufffd" not in body["content"]


def test_a_file_that_is_not_utf_8_is_readable_but_never_writable(
    client, paper_repo: Path
) -> None:
    """Saving it back would put U+FFFD on disk over the real bytes."""
    (paper_repo / "old.bib").write_bytes("@book{a, author = {Grüß}}\n".encode("latin-1"))
    body = client.get("/api/file", params={"path": "old.bib"}).json()
    assert body["encoding"] == "unknown" and body["editable"] is False
    assert body["content"] is not None and "@book" in body["content"]


def test_a_path_cannot_climb_out_of_the_project(client) -> None:
    for route in ("/api/file", "/api/blob"):
        assert client.get(route, params={"path": "../../etc/passwd"}).status_code == 400
    assert client.put("/api/files/../escape.tex", json={"content": "x"}).status_code != 200


def test_the_tree_can_be_read_from_a_session_checkout(client) -> None:
    row = client.post("/api/sessions", json={"prompt": "read me", "start": False}).json()
    body = client.get("/api/tree", params={"session_id": row["id"]}).json()
    assert body["root"] == row["worktree_path"]
    assert [n["name"] for n in body["tree"]] == ["main.tex"]


# -- creating ----------------------------------------------------------------


def test_a_new_file_is_empty_and_in_the_project_without_being_staged(
    paper_repo: Path, git_helper
) -> None:
    rel = files.create(paper_repo, "", "results.tex")

    assert rel == "results.tex"
    assert (paper_repo / "results.tex").read_text() == ""
    assert "results.tex" in files.listing(paper_repo)
    assert git_helper(paper_repo, "status", "--porcelain").strip() == "?? results.tex"


def test_a_new_file_can_go_into_a_folder_that_is_already_there(paper_repo: Path) -> None:
    files.create(paper_repo, "", "sections", folder=True)
    assert files.create(paper_repo, "sections", "intro.tex") == "sections/intro.tex"
    assert "sections/intro.tex" in files.listing(paper_repo)


def test_a_new_folder_shows_in_the_rail_before_anything_is_in_it(paper_repo: Path) -> None:
    """Git tracks no directory, so an empty folder needs its own answer."""
    files.create(paper_repo, "", "figures", folder=True)

    assert (paper_repo / "figures").is_dir()
    assert "figures" in files.empty_folders(paper_repo, files.listing(paper_repo))
    tree = files.tree(paper_repo)
    assert {"name": "figures", "path": "figures", "type": "dir", "children": []} in tree


def test_a_folder_stops_being_empty_once_a_file_is_in_it(paper_repo: Path) -> None:
    files.create(paper_repo, "", "sections", folder=True)
    files.create(paper_repo, "sections", "intro.tex")

    sections = next(n for n in files.tree(paper_repo) if n["name"] == "sections")
    assert [c["name"] for c in sections["children"]] == ["intro.tex"]
    assert files.empty_folders(paper_repo, files.listing(paper_repo)) == []


def test_a_name_already_taken_is_refused(paper_repo: Path) -> None:
    with pytest.raises(files.Refused, match="already there"):
        files.create(paper_repo, "", "main.tex")


def test_a_folder_that_does_not_exist_yet_cannot_be_created_into(paper_repo: Path) -> None:
    with pytest.raises(files.Refused, match="no folder sections"):
        files.create(paper_repo, "sections", "intro.tex")


def test_a_name_latex_cannot_input_is_refused(paper_repo: Path) -> None:
    for name in ("my plot.pdf", "50%.tex", "cost$.tex", "a#b.tex", "sec{1}.tex"):
        with pytest.raises(files.InvalidName):
            files.create(paper_repo, "", name)
    assert files.listing(paper_repo) == ["main.tex"], "nothing was written on the way"


def test_a_name_cannot_carry_a_path_separator_or_a_leading_dot(paper_repo: Path) -> None:
    with pytest.raises(files.InvalidName, match="path separator"):
        files.create(paper_repo, "", "sections/intro.tex")
    with pytest.raises(files.InvalidName, match="start with a dot"):
        files.create(paper_repo, "", ".hidden.tex")
    with pytest.raises(files.InvalidName):
        files.create(paper_repo, "", "..")
    with pytest.raises(files.InvalidName, match="cannot be empty"):
        files.create(paper_repo, "", "")


def test_a_file_gitignore_would_hide_is_refused_rather_than_written(paper_repo: Path) -> None:
    """A file that cannot reach Overleaf cannot be part of the paper."""
    (paper_repo / ".gitignore").write_text("*.pdf\n")
    files.create(paper_repo, "", "figures", folder=True)

    with pytest.raises(files.Refused, match=r"\.gitignore covers figures/loss\.pdf"):
        files.create(paper_repo, "figures", "loss.pdf")
    assert not (paper_repo / "figures" / "loss.pdf").exists()


# -- renaming, which is also moving ------------------------------------------


def test_renaming_a_tracked_file_keeps_its_history(paper_repo: Path, git_helper) -> None:
    (paper_repo / "method.tex").write_text("\\section{Method}\n")
    _commit(git_helper, paper_repo, "method")

    assert files.rename(paper_repo, "method.tex", "approach.tex", main_tex="main.tex")

    status = git_helper(paper_repo, "status", "--porcelain").strip()
    assert status == "R  method.tex -> approach.tex", "git followed the file"
    assert (paper_repo / "approach.tex").read_text() == "\\section{Method}\n"
    assert not (paper_repo / "method.tex").exists()


def test_renaming_an_untracked_file_still_works(paper_repo: Path, git_helper) -> None:
    (paper_repo / "draft.tex").write_text("scratch\n")

    files.rename(paper_repo, "draft.tex", "notes.tex", main_tex="main.tex")

    assert (paper_repo / "notes.tex").read_text() == "scratch\n"
    assert git_helper(paper_repo, "status", "--porcelain").strip() == "?? notes.tex"


def test_renaming_into_a_folder_is_how_you_move_a_file(paper_repo: Path, git_helper) -> None:
    (paper_repo / "intro.tex").write_text("\\section{Intro}\n")
    _commit(git_helper, paper_repo, "intro")
    files.create(paper_repo, "", "sections", folder=True)

    files.rename(paper_repo, "intro.tex", "sections/intro.tex", main_tex="main.tex")

    assert "sections/intro.tex" in files.listing(paper_repo)
    assert "intro.tex" not in files.listing(paper_repo)


def test_renaming_a_folder_takes_what_is_in_it(paper_repo: Path, git_helper) -> None:
    (paper_repo / "parts").mkdir()
    (paper_repo / "parts" / "one.tex").write_text("one\n")
    _commit(git_helper, paper_repo, "parts")

    files.rename(paper_repo, "parts", "sections", main_tex="main.tex")

    assert files.listing(paper_repo) == ["main.tex", "sections/one.tex"]


def test_renaming_across_a_folder_that_does_not_exist_is_refused(paper_repo: Path) -> None:
    with pytest.raises(files.Refused, match="no folder sections"):
        files.rename(paper_repo, "main.tex", "sections/main.tex", main_tex="other.tex")
    assert (paper_repo / "main.tex").exists(), "nothing moved"


def test_renaming_onto_something_already_there_is_refused(paper_repo: Path) -> None:
    (paper_repo / "notes.tex").write_text("notes\n")
    with pytest.raises(files.Refused, match="already there"):
        files.rename(paper_repo, "notes.tex", "main.tex", main_tex="other.tex")
    assert (paper_repo / "notes.tex").read_text() == "notes\n"


def test_the_document_the_build_compiles_cannot_be_renamed_or_deleted(paper_repo: Path) -> None:
    with pytest.raises(files.Refused, match="galley.local.toml"):
        files.rename(paper_repo, "main.tex", "paper.tex", main_tex="main.tex")
    with pytest.raises(files.Refused, match="galley.local.toml"):
        files.delete(paper_repo, "main.tex", main_tex="main.tex")
    assert (paper_repo / "main.tex").exists()


def test_a_folder_holding_the_main_document_is_protected_too(paper_repo: Path) -> None:
    (paper_repo / "src").mkdir()
    (paper_repo / "src" / "main.tex").write_text("x")
    with pytest.raises(files.Refused, match="galley.local.toml"):
        files.rename(paper_repo, "src", "source", main_tex="src/main.tex")


def test_a_rename_destination_is_held_to_the_same_name_rule(paper_repo: Path) -> None:
    for destination in ("../escape.tex", "/tmp/escape.tex", ".hidden.tex", "a b.tex"):
        with pytest.raises(ValueError):
            files.rename(paper_repo, "main.tex", destination, main_tex="other.tex")
    assert (paper_repo / "main.tex").exists()


def test_renaming_something_that_is_not_there_says_so(paper_repo: Path) -> None:
    with pytest.raises(FileNotFoundError):
        files.rename(paper_repo, "ghost.tex", "spirit.tex", main_tex="main.tex")


# -- deleting -----------------------------------------------------------------


def test_deleting_a_tracked_file_stages_the_removal(paper_repo: Path, git_helper) -> None:
    (paper_repo / "old.tex").write_text("old\n")
    _commit(git_helper, paper_repo, "old")

    files.delete(paper_repo, "old.tex", main_tex="main.tex")

    assert not (paper_repo / "old.tex").exists()
    assert git_helper(paper_repo, "status", "--porcelain").strip() == "D  old.tex"


def test_deleting_a_tracked_file_with_unsaved_edits_still_removes_it(
    paper_repo: Path, git_helper
) -> None:
    """`git rm` refuses a modified file; the rail has already asked the user."""
    (paper_repo / "old.tex").write_text("old\n")
    _commit(git_helper, paper_repo, "old")
    (paper_repo / "old.tex").write_text("changed\n")

    files.delete(paper_repo, "old.tex", main_tex="main.tex")
    assert not (paper_repo / "old.tex").exists()


def test_deleting_an_untracked_file_just_removes_it(paper_repo: Path, git_helper) -> None:
    (paper_repo / "scratch.tex").write_text("x")
    files.delete(paper_repo, "scratch.tex", main_tex="main.tex")
    assert not (paper_repo / "scratch.tex").exists()
    assert git_helper(paper_repo, "status", "--porcelain").strip() == ""


def test_an_empty_folder_can_go_but_a_full_one_cannot(paper_repo: Path) -> None:
    files.create(paper_repo, "", "sections", folder=True)
    files.create(paper_repo, "sections", "intro.tex")

    with pytest.raises(files.Refused, match="not empty"):
        files.delete(paper_repo, "sections", main_tex="main.tex")
    assert (paper_repo / "sections" / "intro.tex").exists()

    files.delete(paper_repo, "sections/intro.tex", main_tex="main.tex")
    files.delete(paper_repo, "sections", main_tex="main.tex")
    assert not (paper_repo / "sections").exists()


def test_deleting_something_that_is_not_there_says_so(paper_repo: Path) -> None:
    with pytest.raises(FileNotFoundError):
        files.delete(paper_repo, "ghost.tex", main_tex="main.tex")


# -- taking an upload ---------------------------------------------------------


def test_an_uploaded_figure_lands_whole_and_joins_the_project(paper_repo: Path) -> None:
    files.create(paper_repo, "", "figures", folder=True)
    blob = b"%PDF-1.5\n\x00\x01a plot"

    rel = files.upload(paper_repo, "figures", "loss.pdf", blob)

    assert rel == "figures/loss.pdf"
    assert (paper_repo / rel).read_bytes() == blob
    assert rel in files.listing(paper_repo)
    assert not list(paper_repo.glob(".galley-upload-*")), "no half-written leftovers"


def test_an_upload_replaces_only_when_you_say_so(paper_repo: Path) -> None:
    files.upload(paper_repo, "", "loss.pdf", b"first")

    with pytest.raises(files.Refused, match="already there"):
        files.upload(paper_repo, "", "loss.pdf", b"second")
    assert (paper_repo / "loss.pdf").read_bytes() == b"first"

    files.upload(paper_repo, "", "loss.pdf", b"second", replace=True)
    assert (paper_repo / "loss.pdf").read_bytes() == b"second"


def test_an_upload_bigger_than_galley_takes_is_refused(paper_repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(files, "MAX_UPLOAD_BYTES", 8)
    with pytest.raises(files.Refused, match="Galley takes up to"):
        files.upload(paper_repo, "", "big.pdf", b"far too many bytes")
    assert not (paper_repo / "big.pdf").exists()


def test_an_upload_gitignore_would_hide_is_refused(paper_repo: Path) -> None:
    (paper_repo / ".gitignore").write_text("*.pdf\n")
    with pytest.raises(files.Refused, match=r"\.gitignore covers"):
        files.upload(paper_repo, "", "loss.pdf", b"%PDF")
    assert not (paper_repo / "loss.pdf").exists()


def test_an_upload_is_held_to_the_same_name_rule(paper_repo: Path) -> None:
    with pytest.raises(files.InvalidName):
        files.upload(paper_repo, "", "my plot.pdf", b"%PDF")
    with pytest.raises(files.InvalidName, match="path separator"):
        files.upload(paper_repo, "", "../loss.pdf", b"%PDF")


# -- nothing gets out of the project -----------------------------------------


def test_no_write_path_can_climb_out_of_the_project(paper_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    for parent in ("..", "../outside", "/etc"):
        with pytest.raises(ValueError):
            files.create(paper_repo, parent, "leak.tex")
        with pytest.raises(ValueError):
            files.upload(paper_repo, parent, "leak.pdf", b"x")
    with pytest.raises(ValueError):
        files.delete(paper_repo, "../outside", main_tex="main.tex")
    assert list(outside.iterdir()) == []


def test_a_symlink_pointing_out_of_the_project_is_not_a_way_in(
    paper_repo: Path, tmp_path: Path
) -> None:
    """`resolve()` follows the link before it checks, so this is the same check."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.tex").write_text("not yours\n")
    (paper_repo / "escape").symlink_to(outside)

    with pytest.raises(ValueError):
        files.create(paper_repo, "escape", "leak.tex")
    with pytest.raises(ValueError):
        files.upload(paper_repo, "escape", "leak.pdf", b"x")
    with pytest.raises(ValueError):
        files.rename(paper_repo, "main.tex", "escape/main.tex", main_tex="other.tex")
    with pytest.raises(ValueError):
        files.delete(paper_repo, "escape/secret.tex", main_tex="main.tex")

    assert [p.name for p in outside.iterdir()] == ["secret.tex"]


def test_deleting_a_symlink_removes_the_link_and_not_what_it_points_at(
    paper_repo: Path,
) -> None:
    """A write acts on the entry; only the containment check follows the link."""
    (paper_repo / "figures").mkdir()
    (paper_repo / "figures" / "loss.pdf").write_bytes(b"%PDF")
    (paper_repo / "latest.pdf").symlink_to("figures/loss.pdf")

    files.delete(paper_repo, "latest.pdf", main_tex="main.tex")

    assert not (paper_repo / "latest.pdf").is_symlink()
    assert (paper_repo / "figures" / "loss.pdf").read_bytes() == b"%PDF"


def test_galleys_own_plumbing_is_not_something_the_rail_can_remove(paper_repo: Path) -> None:
    for rel in (".git", ".git/config", ".worktrees", ".galley"):
        with pytest.raises(files.Refused, match="plumbing"):
            files.delete(paper_repo, rel, main_tex="main.tex")
    assert (paper_repo / ".git").is_dir()


# -- the routes the rail calls ------------------------------------------------


def test_the_rail_can_make_a_folder_and_a_file_in_it(client) -> None:
    made = client.post("/api/files", json={"name": "sections", "folder": True})
    assert made.status_code == 200 and made.json()["path"] == "sections"

    made = client.post("/api/files", json={"parent": "sections", "name": "intro.tex"})
    assert made.json()["path"] == "sections/intro.tex"

    tree = client.get("/api/tree").json()["tree"]
    sections = next(n for n in tree if n["name"] == "sections")
    assert [c["path"] for c in sections["children"]] == ["sections/intro.tex"]


def test_a_refusal_and_a_bad_name_are_told_apart(client) -> None:
    taken = client.post("/api/files", json={"name": "main.tex"})
    assert taken.status_code == 409, "the name is fine; the project says no"
    assert "already there" in taken.json()["detail"]

    bad = client.post("/api/files", json={"name": "my plot.pdf"})
    assert bad.status_code == 400, "retyping it would fix this one"

    escape = client.post("/api/files", json={"parent": "../..", "name": "leak.tex"})
    assert escape.status_code == 400
    assert escape.json()["detail"] == "path escapes the project"


def test_renaming_over_the_route_says_where_the_file_went(client, paper_repo: Path) -> None:
    (paper_repo / "draft.tex").write_text("x")
    moved = client.post("/api/files/rename", json={"path": "draft.tex", "to": "notes.tex"})

    assert moved.status_code == 200
    assert moved.json() == {"ok": True, "path": "notes.tex", "was": "draft.tex"}
    assert client.get("/api/file", params={"path": "notes.tex"}).status_code == 200


def test_deleting_over_the_route_removes_the_file_but_never_the_main_document(
    client, paper_repo: Path
) -> None:
    (paper_repo / "scratch.tex").write_text("x")
    assert client.delete("/api/files/scratch.tex").status_code == 200
    assert not (paper_repo / "scratch.tex").exists()

    kept = client.delete("/api/files/main.tex")
    assert kept.status_code == 409 and (paper_repo / "main.tex").exists()
    assert client.delete("/api/files/ghost.tex").status_code == 404


def test_a_figure_can_be_dropped_on_the_rail(client, paper_repo: Path) -> None:
    blob = b"%PDF-1.5\n\x00\x01a plot"
    dropped = client.post(
        "/api/files/upload", params={"name": "loss.pdf"}, content=blob
    )

    assert dropped.status_code == 200
    assert dropped.json() == {"ok": True, "path": "loss.pdf", "bytes": len(blob)}
    assert (paper_repo / "loss.pdf").read_bytes() == blob

    again = client.post("/api/files/upload", params={"name": "loss.pdf"}, content=b"newer")
    assert again.status_code == 409

    replaced = client.post(
        "/api/files/upload",
        params={"name": "loss.pdf", "replace": True},
        content=b"newer",
    )
    assert replaced.status_code == 200
    assert (paper_repo / "loss.pdf").read_bytes() == b"newer"


def test_a_body_that_is_not_what_the_route_asked_for_is_a_bad_request(
    client, paper_repo: Path
) -> None:
    (paper_repo / "draft.tex").write_text("x")
    assert client.post("/api/files", json={"name": ["intro.tex"]}).status_code == 400
    assert client.post("/api/files/rename", json={"path": "draft.tex"}).status_code == 400


def test_the_rail_can_do_a_whole_afternoons_tidying(client, paper_repo: Path) -> None:
    """Make a folder, write into it, move a file across, add a plot, tidy up."""
    client.post("/api/files", json={"name": "sections", "folder": True})
    client.post("/api/files", json={"name": "figures", "folder": True})
    client.post("/api/files", json={"name": "intro.tex"})
    client.post("/api/files/rename", json={"path": "intro.tex", "to": "sections/intro.tex"})
    client.post(
        "/api/files/upload",
        params={"name": "loss.pdf", "parent": "figures"},
        content=b"%PDF-1.5",
    )
    client.post("/api/files", json={"parent": "sections", "name": "scratch.tex"})
    assert client.delete("/api/files/sections/scratch.tex").status_code == 200

    tree = client.get("/api/tree").json()["tree"]
    assert [(n["name"], n["type"]) for n in tree] == [
        ("figures", "dir"),
        ("sections", "dir"),
        ("main.tex", "tex"),
    ]
    assert [c["path"] for c in tree[0]["children"]] == ["figures/loss.pdf"]
    assert [c["path"] for c in tree[1]["children"]] == ["sections/intro.tex"]


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
