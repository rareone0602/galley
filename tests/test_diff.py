"""The diff must be able to reconstruct both sides exactly.

Everything the merge pane does rests on this: reject-everything is the file you
had, accept-everything is the file Claude proposed, and any subset in between is
a real file rather than a mangled patch.
"""

from __future__ import annotations

import pathlib

import pytest

from galley.segment.diff import apply_ops, diff_text, word_diff

FIXTURES = sorted((pathlib.Path(__file__).parent / "fixtures").glob("*.tex"))

DESIGN_OLD = (
    "We evaluate on three benchmarks (\\S4.1).\n"
    "The model improves over the baseline by a large margin across all settings.\n"
    "We ablate the retrieval component in Table~\\ref{tab:ablation}.\n"
)
DESIGN_NEW = (
    "We evaluate on three benchmarks (\\S4.1).\n"
    "The model improves over the baseline by 4.2 BLEU on average, though the\n"
    "gain narrows to 0.8 on low-resource pairs.\n"
    "We ablate the retrieval component in Table~\\ref{tab:ablation}.\n"
)


def test_the_design_documents_example() -> None:
    ops = diff_text(DESIGN_OLD, DESIGN_NEW)
    assert [o.type for o in ops] == ["equal", "change", "equal"]
    assert "a large margin" in ops[1].old
    assert "4.2 BLEU" in ops[1].new


def test_rejecting_everything_reproduces_the_original() -> None:
    ops = diff_text(DESIGN_OLD, DESIGN_NEW)
    assert apply_ops(ops, set()) == DESIGN_OLD


def test_accepting_everything_reproduces_the_proposal() -> None:
    ops = diff_text(DESIGN_OLD, DESIGN_NEW)
    assert apply_ops(ops, {o.id for o in ops}) == DESIGN_NEW


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_round_trip_both_ways_on_real_sections(path: pathlib.Path) -> None:
    old = path.read_text(encoding="utf-8")
    new = old.replace("the ", "a ").replace("We ", "One ")
    ops = diff_text(old, new)
    assert apply_ops(ops, set()) == old
    assert apply_ops(ops, {o.id for o in ops}) == new


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_a_file_against_itself_has_no_changes(path: pathlib.Path) -> None:
    src = path.read_text(encoding="utf-8")
    ops = diff_text(src, src)
    assert all(o.type == "equal" for o in ops)


def test_a_partial_acceptance_is_a_real_file() -> None:
    old = "One. Two. Three.\n"
    new = "One prime. Two prime. Three prime.\n"
    ops = diff_text(old, new)
    changes = [o.id for o in ops if o.type == "change"]
    assert len(changes) == 3
    got = apply_ops(ops, {changes[1]})
    assert got == "One. Two prime. Three.\n"


def test_insertion_only_is_a_change_op_with_empty_old() -> None:
    ops = diff_text("One.\n", "One. Two.\n")
    inserted = [o for o in ops if o.type == "change" and not o.old.strip()]
    assert inserted and "Two." in inserted[0].new


def test_deletion_only_is_a_change_op_with_empty_new() -> None:
    ops = diff_text("One. Two.\n", "One.\n")
    deleted = [o for o in ops if o.type == "change" and not o.new.strip()]
    assert deleted and "Two." in deleted[0].old


def test_word_diff_marks_only_what_moved() -> None:
    old_w, new_w = word_diff("the quick brown fox", "the slow brown fox")
    assert [w.text for w in old_w if w.op == "del"] == ["quick"]
    assert [w.text for w in new_w if w.op == "ins"] == ["slow"]
    assert "".join(w.text for w in old_w) == "the quick brown fox"
    assert "".join(w.text for w in new_w) == "the slow brown fox"


def test_rewrapping_a_paragraph_is_not_a_change() -> None:
    old = "A sentence that is wrapped\nacross two lines here.\n"
    new = "A sentence that is wrapped across two lines here.\n"
    ops = diff_text(old, new)
    assert all(o.type == "equal" for o in ops)
