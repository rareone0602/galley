"""Sentence-granular diffs between two versions of a LaTeX file.

The output is a flat list of *ops* covering the whole file in order. An `equal`
op is text both sides agree on; a `change` op carries the old text and the new
text side by side. The resulting buffer is then, exactly::

    "".join(op.new if accepted[op.id] else op.old for op in ops)

which is why Galley never applies a partial patch. The client decides which
changes it accepts, assembles the whole buffer, and the backend writes that
buffer to the file. There is no patch offset to get wrong.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal

from .tokenizer import Segment, segment

OpType = Literal["equal", "change"]
WordOp = Literal["same", "del", "ins"]

_WORDS = re.compile(r"\s+|[^\s]+")


@dataclass
class WordSpan:
    op: WordOp
    text: str

    def as_dict(self) -> dict:
        return {"op": self.op, "text": self.text}


@dataclass
class DiffOp:
    """One run of the file: either agreed text, or a proposed change."""

    id: int
    type: OpType
    old: str
    new: str
    old_words: list[WordSpan] = field(default_factory=list)
    new_words: list[WordSpan] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "old": self.old,
            "new": self.new,
            "old_words": [w.as_dict() for w in self.old_words],
            "new_words": [w.as_dict() for w in self.new_words],
            "kinds": sorted(set(self.kinds)),
        }


def diff_text(old_src: str, new_src: str) -> list[DiffOp]:
    """Sentence-level ops turning `old_src` into `new_src`."""
    return diff_segments(segment(old_src), segment(new_src))


def diff_segments(old: list[Segment], new: list[Segment]) -> list[DiffOp]:
    matcher = difflib.SequenceMatcher(
        a=[s.key for s in old], b=[s.key for s in new], autojunk=False
    )
    ops: list[DiffOp] = []
    next_id = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_segs, new_segs = old[i1:i2], new[j1:j2]
        if tag == "equal":
            # Keep the *new* side's text: same sentences, possibly rewrapped.
            ops.append(
                DiffOp(
                    id=next_id,
                    type="equal",
                    old="".join(s.text for s in old_segs),
                    new="".join(s.text for s in new_segs),
                    kinds=[s.kind for s in old_segs],
                )
            )
            next_id += 1
            continue

        # A replace of equal length is n sentences rewritten one for one, and
        # each pair gets its own accept/reject. Anything else is one op.
        if tag == "replace" and len(old_segs) == len(new_segs):
            pairs = list(zip(old_segs, new_segs))
        else:
            pairs = [(old_segs, new_segs)]

        for a, b in pairs:
            a_list = [a] if isinstance(a, Segment) else list(a)
            b_list = [b] if isinstance(b, Segment) else list(b)
            old_text = "".join(s.text for s in a_list)
            new_text = "".join(s.text for s in b_list)
            ow, nw = word_diff(old_text, new_text)
            ops.append(
                DiffOp(
                    id=next_id,
                    type="change",
                    old=old_text,
                    new=new_text,
                    old_words=ow,
                    new_words=nw,
                    kinds=[s.kind for s in a_list + b_list],
                )
            )
            next_id += 1
    return _coalesce_equal(ops)


def _coalesce_equal(ops: list[DiffOp]) -> list[DiffOp]:
    """Merge neighbouring `equal` ops so the UI shows one context block."""
    out: list[DiffOp] = []
    for op in ops:
        if out and out[-1].type == "equal" == op.type:
            prev = out[-1]
            prev.old += op.old
            prev.new += op.new
            prev.kinds += op.kinds
            continue
        out.append(op)
    for n, op in enumerate(out):
        op.id = n
    return out


def word_diff(old: str, new: str) -> tuple[list[WordSpan], list[WordSpan]]:
    """Word-level spans inside one changed sentence, for inline emphasis."""
    a, b = _WORDS.findall(old), _WORDS.findall(new)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    old_spans: list[WordSpan] = []
    new_spans: list[WordSpan] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        at, bt = "".join(a[i1:i2]), "".join(b[j1:j2])
        if tag == "equal":
            _push(old_spans, "same", at)
            _push(new_spans, "same", bt)
        else:
            _push(old_spans, "del", at)
            _push(new_spans, "ins", bt)
    return old_spans, new_spans


def _push(spans: list[WordSpan], op: WordOp, text: str) -> None:
    if not text:
        return
    if spans and spans[-1].op == op:
        spans[-1].text += text
        return
    spans.append(WordSpan(op, text))


def apply_ops(ops: list[DiffOp], accepted: set[int]) -> str:
    """The buffer that results from accepting exactly `accepted`."""
    return "".join(
        op.new if (op.type == "equal" or op.id in accepted) else op.old for op in ops
    )
