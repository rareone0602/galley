"""The usage log: what it keeps, what it refuses to keep, and what it says.

The promise this log makes is that it records what you did and never what you
wrote. That is not a comment you can trust — it is a property, and these run it.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from galley.app import create_app
from galley.db import Database
from galley.services import usage

PARAGRAPH = (
    "The undisputed de facto language and proof environment is Lean 4, which "
    "is the main formal language we use in this paper."
)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "usage.db")


# -- the promise -------------------------------------------------------------


def test_a_sentence_of_the_paper_never_reaches_the_table(db) -> None:
    """Every key that could carry the manuscript, tried at once."""
    usage.record(
        db,
        "session.create",
        {
            "from": "selection",
            "text": PARAGRAPH,
            "prompt": PARAGRAPH,
            "selection": PARAGRAPH,
            "old": PARAGRAPH,
            "new": PARAGRAPH,
            "content": PARAGRAPH,
            "instruction": PARAGRAPH,
        },
    )
    stored = db.usage_since(0)[0]["detail"]
    written = " ".join(str(v) for v in stored.values())
    assert "Lean" not in written and "undisputed" not in written
    assert stored["from"] == "selection"
    assert stored["text_words"] == 22, "the size survives; the sentence does not"


def test_a_long_string_under_any_other_name_is_kept_as_its_length(db) -> None:
    """A key nobody thought of is still not allowed to hold a paragraph."""
    usage.record(db, "error.shown", {"where": "merge", "surprise": PARAGRAPH})
    stored = db.usage_since(0)[0]["detail"]
    assert stored == {"where": "merge", "surprise_chars": len(PARAGRAPH)}


def test_a_nested_structure_is_dropped_rather_than_flattened(db) -> None:
    """The shape most likely to smuggle prose in."""
    usage.record(db, "review.save", {"files": 2, "diff": {"old": PARAGRAPH}, "paths": [PARAGRAPH]})
    assert db.usage_since(0)[0]["detail"] == {"files": 2}


def test_a_short_string_is_kept_because_it_is_a_label_not_prose(db) -> None:
    usage.record(db, "file.open", {"path": "sections/intro.tex", "type": "tex"})
    assert db.usage_since(0)[0]["detail"]["path"] == "sections/intro.tex"


# -- the vocabulary ----------------------------------------------------------


def test_a_kind_nobody_declared_is_refused(db) -> None:
    """Closed on purpose: 'what has never been used' can only be asked of a
    list you wrote down."""
    with pytest.raises(usage.UnknownKind):
        usage.record(db, "editor.keystroke", {"key": "a"})


def test_a_batch_keeps_what_it_knows_and_names_what_it_does_not(db) -> None:
    written, unknown = usage.record_batch(
        db,
        [
            {"kind": "tab.show", "detail": {"tab": "review"}},
            {"kind": "tab.shwo", "detail": {"tab": "review"}},
            {"kind": "review.undo"},
        ],
    )
    assert written == 2
    assert unknown == ["tab.shwo"]


def test_a_browser_clock_from_the_future_is_not_trusted(db) -> None:
    """The timestamp comes from the client, and a wrong one would put an entry
    outside every window the report can ask about."""
    usage.record_batch(db, [{"kind": "review.undo", "at": time.time() + 86400 * 365}])
    assert db.usage_since(0)[0]["at"] <= time.time() + 1


# -- reading it back ---------------------------------------------------------


def test_the_report_counts_the_loop_rather_than_the_clicks(db) -> None:
    for _ in range(3):
        usage.record(db, "review.open", {"files": 1, "changes": 12})
    usage.record(db, "review.save", {"files": 1, "taken": 5, "rewritten": 2})
    for answer in ("claude", "claude", "keep", "rewrite"):
        usage.record(db, "review.decide", {"answer": answer})

    loop = usage.report(db, days=1)["loop"]
    assert loop["reviews_opened"] == 3 and loop["saves"] == 1
    assert loop["changes_offered"] == 36 and loop["changes_taken"] == 5
    assert loop["decisions"] == {"claude": 2, "keep": 1, "rewrite": 1}


def test_the_report_names_what_has_never_been_used(db) -> None:
    usage.record(db, "app.open")
    never = usage.report(db, days=1)["never_used"]
    assert "app.open" not in never
    assert "review.save" in never
    assert len(never) == len(usage.KINDS) - 1


def test_the_window_leaves_out_what_is_older_than_it(db) -> None:
    usage.record(db, "app.open", at=time.time() - 86400 * 10)
    usage.record(db, "app.open")
    assert usage.report(db, days=1)["events"] == 1
    assert usage.report(db, days=30)["events"] == 2


def test_an_empty_log_reads_as_a_sentence_not_a_crash(db) -> None:
    assert "Nothing recorded" in usage.render(usage.report(db, days=7))


def test_forgetting_is_real(db) -> None:
    usage.record(db, "app.open")
    usage.record(db, "app.open", at=time.time() - 86400 * 10)
    assert db.forget_usage(before=time.time() - 86400 * 5) == 1
    assert db.usage_span()["n"] == 1
    db.forget_usage()
    assert db.usage_span()["n"] == 0


# -- over the wire -----------------------------------------------------------


def test_the_route_records_a_batch_and_the_report_shows_it(client) -> None:
    posted = client.post(
        "/api/usage",
        json={"entries": [{"kind": "tab.show", "detail": {"tab": "editor"}}] * 4},
    )
    assert posted.json() == {"recorded": 4, "enabled": True, "unknown": []}
    assert client.get("/api/usage/report").json()["attention"]["tabs"] == {"editor": 4}


def test_a_switched_off_log_accepts_and_discards(paper_repo, tmp_path) -> None:
    """The browser posts in the background; handing it an error would be
    something it has to handle for no reason."""
    from galley.config import load

    path = tmp_path / "off" / "galley.local.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"[paths]\npaper_repo = '{paper_repo}'\nstate_dir = '{tmp_path / 'off-state'}'\n"
        "[paper]\nmain_branch = 'main'\n[usage]\nenabled = false\n"
    )
    cfg = load(path)
    assert cfg.usage.enabled is False
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/config").json()["usage"] is False
        answer = c.post("/api/usage", json={"entries": [{"kind": "app.open"}]})
        assert answer.status_code == 200 and answer.json()["recorded"] == 0
        assert c.get("/api/usage/report").json()["events"] == 0


def test_the_server_records_what_it_is_the_authority_on(client, monkeypatch) -> None:
    """A build's real duration outlives the tab that started it."""
    from galley.services import latex

    monkeypatch.setattr(
        latex, "compile_pdf", lambda *a, **k: latex.CompileResult(True, None, [], "")
    )
    client.post("/api/compile", json={})
    for _ in range(50):
        if client.get("/api/compile").json()["state"] != "running":
            break
        time.sleep(0.05)
    counts = client.get("/api/usage/report").json()["counts"]
    assert counts.get("compile.run") == 1
