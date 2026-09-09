"""The tokenizer is the highest-risk component, so it is tested alone.

A bad split shows a false diff, and a false diff is a change you accept without
reading it. These tests run the real splitter on real paper text.
"""

from __future__ import annotations

import pathlib

import pytest

from galley.segment.tokenizer import Segment, segment

FIXTURES = sorted((pathlib.Path(__file__).parent / "fixtures").glob("*.tex"))


def sentences(src: str) -> list[str]:
    return [s.key for s in segment(src) if s.kind == "sentence"]


def kinds(src: str) -> list[str]:
    return [s.kind for s in segment(src)]


# -- the invariant the write-back path depends on -------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_round_trips_on_real_paper_sections(path: pathlib.Path) -> None:
    src = path.read_text(encoding="utf-8")
    assert "".join(s.text for s in segment(src)) == src


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_offsets_index_the_source(path: pathlib.Path) -> None:
    src = path.read_text(encoding="utf-8")
    segs = segment(src)
    assert segs[0].start == 0
    assert segs[-1].end == len(src)
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start
    for s in segs:
        assert src[s.start : s.end] == s.text


def test_round_trips_on_empty_and_trivial_input() -> None:
    for src in ["", "\n", "  ", "Hello.", "Hello.\n\n\nWorld.\n"]:
        assert "".join(s.text for s in segment(src)) == src


# -- abbreviations must not end a sentence --------------------------------


@pytest.mark.parametrize(
    "abbrev",
    ["e.g.", "i.e.", "et al.", "Fig.", "Eq.", "cf.", "vs.", "Sec.", "Thm.", "Prop."],
)
def test_abbreviation_does_not_split(abbrev: str) -> None:
    src = f"We follow the usual convention, {abbrev} We then report the result.\n"
    assert len(sentences(src)) == 1


def test_initials_do_not_split() -> None:
    assert len(sentences("Following J. Smith we adopt the same convention.\n")) == 1


def test_a_real_sentence_end_does_split() -> None:
    src = "We adopt the convention. We then report the result.\n"
    assert sentences(src) == [
        "We adopt the convention.",
        "We then report the result.",
    ]


def test_abbreviation_at_a_true_sentence_end_is_the_known_cost() -> None:
    # `... in Fig. 4. The next claim ...` splits, because the period that ends
    # the sentence follows "4", not "Fig".
    src = "The trend is shown in Fig. 4. The next claim is stronger.\n"
    assert len(sentences(src)) == 2


# -- numbers, citations, math ---------------------------------------------


def test_decimal_does_not_split() -> None:
    src = "The model improves by 4.2 BLEU on average across all settings.\n"
    assert len(sentences(src)) == 1


def test_period_inside_a_citation_does_not_split() -> None:
    src = "This follows \\citep{smith.2020, jones.et.al} for the same reason.\n"
    assert len(sentences(src)) == 1


def test_period_inside_a_brace_group_does_not_split() -> None:
    src = "See \\footnote{We omit this. It is standard.} for the argument.\n"
    assert len(sentences(src)) == 1


def test_inline_math_is_opaque() -> None:
    src = "We write $f(x) = 0.5. x$ throughout the derivation of the bound.\n"
    assert len(sentences(src)) == 1


def test_paren_math_is_opaque() -> None:
    src = "Prose runs of order \\(10^{0}\\) nats per token in every corpus.\n"
    assert len(sentences(src)) == 1


def test_ellipsis_does_not_split() -> None:
    src = "The sequence continues ... And so the bound holds everywhere.\n"
    assert len(sentences(src)) == 1


def test_latex_sentence_end_marker_splits() -> None:
    src = "We use the method of SMITH\\@. The result follows immediately.\n"
    assert len(sentences(src)) == 2


def test_latex_non_sentence_period_does_not_split() -> None:
    src = "We cite Fig.\\ 4. and continue the same clause without a break.\n"
    assert sentences(src)[0].startswith("We cite Fig.\\ 4.")


# -- atomic blocks ---------------------------------------------------------


def test_comment_is_its_own_segment() -> None:
    src = "Before.\n% a note to self. With a period.\nAfter the note.\n"
    segs = segment(src)
    assert [s.kind for s in segs] == ["sentence", "comment", "sentence"]
    assert segs[1].text == "% a note to self. With a period.\n"


def test_environment_is_atomic() -> None:
    src = "Before.\n\n\\begin{equation}\n  a = b. c = d.\n\\end{equation}\n\nAfter.\n"
    segs = [s for s in segment(src) if s.kind != "blank"]
    assert [s.kind for s in segs] == ["sentence", "environment", "sentence"]
    assert "a = b. c = d." in segs[1].text


def test_nested_environment_of_the_same_name_is_one_segment() -> None:
    src = (
        "\\begin{tabular}{ll}\n a & b \\\\\n"
        "\\begin{tabular}{c}\n inner \\\\\n\\end{tabular}\n"
        " & d \\\\\n\\end{tabular}\n"
    )
    segs = [s for s in segment(src) if s.kind == "environment"]
    assert len(segs) == 1
    assert segs[0].text.count("\\begin{tabular}") == 2


# -- prose-bearing environments are transparent ---------------------------


def test_the_document_environment_does_not_swallow_the_paper() -> None:
    """Every paper is wrapped in \\begin{document}.

    If that were atomic the whole manuscript would be one segment and the merge
    pane would have nothing to show.
    """
    src = (
        "\\begin{document}\n"
        "First claim here.\n"
        "Second claim here.\n"
        "\\end{document}\n"
    )
    segs = segment(src)
    assert [s.kind for s in segs] == ["command", "sentence", "sentence", "command"]
    assert sentences(src) == ["First claim here.", "Second claim here."]


@pytest.mark.parametrize("env", ["abstract", "figure", "theorem", "proof", "quote", "itemize"])
def test_prose_environments_are_transparent(env: str) -> None:
    src = f"\\begin{{{env}}}\nOne claim. Another claim.\n\\end{{{env}}}\n"
    assert len(sentences(src)) == 2


@pytest.mark.parametrize("env", ["equation", "align*", "verbatim", "lstlisting", "tikzpicture"])
def test_non_prose_environments_stay_atomic(env: str) -> None:
    src = f"\\begin{{{env}}}\nx = 1. y = 2.\n\\end{{{env}}}\n"
    segs = [s for s in segment(src) if s.kind == "environment"]
    assert len(segs) == 1
    assert "x = 1. y = 2." in segs[0].text


def test_an_atomic_environment_inside_a_transparent_one_stays_whole() -> None:
    src = (
        "\\begin{figure}\n"
        "\\begin{tikzpicture}\n \\draw (0,0) -- (1,1);\n\\end{tikzpicture}\n"
        "\\caption{A picture. With two sentences.}\n"
        "\\end{figure}\n"
    )
    segs = segment(src)
    assert [s.kind for s in segs].count("environment") == 1
    assert any(s.kind == "environment" and "tikzpicture" in s.text for s in segs)


def test_each_list_item_is_its_own_unit() -> None:
    src = "\\begin{itemize}\n\\item first point\n\\item second point\n\\end{itemize}\n"
    items = [s.key for s in segment(src) if s.kind == "sentence"]
    assert items == ["\\item first point", "\\item second point"]


def test_display_math_is_atomic() -> None:
    src = "Before.\n\n\\[\n  x = 1. y = 2.\n\\]\n\nAfter.\n"
    kinds_seen = [s.kind for s in segment(src) if s.kind != "blank"]
    assert kinds_seen == ["sentence", "display_math", "sentence"]


def test_escaped_percent_is_not_a_comment() -> None:
    src = "The share is 45\\% of the total corpus by token count.\n"
    segs = segment(src)
    assert all(s.kind != "comment" for s in segs)
    assert len(sentences(src)) == 1


def test_paragraph_break_is_its_own_blank_segment() -> None:
    src = "First para.\n\nSecond para.\n"
    assert [s.kind for s in segment(src)] == ["sentence", "blank", "sentence"]


# -- what the key is for ---------------------------------------------------


def test_key_collapses_rewrapping() -> None:
    a = Segment("sentence", "The model improves\nover the baseline.\n", 0, 0)
    b = Segment("sentence", "The model improves over the\nbaseline.\n", 0, 0)
    assert a.key == b.key
