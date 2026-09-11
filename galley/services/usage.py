"""What you did with the workbench, so the workbench can be made better.

The point of this log is to answer questions you would otherwise guess at.
Which surfaces do you actually use? Does the select-ask-merge loop finish, or
do sessions get started and abandoned? When you review, what do you decide —
and how often do you rewrite rather than take either side? Where do you wait,
and where do you hit a wall? Which of these features has never once been used?

Two rules make it worth keeping.

**It records what you did, never what you wrote.** No sentence of the paper, no
prompt, no selection, no diff text ever reaches this table. Where the length of
something matters — how much text you hand to Claude, say — the length is
recorded and the text is not. `scrub` enforces that on the way in rather than
trusting every call site to remember, and `FORBIDDEN` is the list of keys that
carry manuscript prose.

**It never leaves the machine.** There is no network call here and no reporting
endpoint. It is a SQLite table beside your own paper, `galley usage` prints it,
and `galley usage --forget` deletes it.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass

from ..db import Database

#: Keys that would carry the manuscript itself. A call site that passes one of
#: these gets its length recorded instead of its value — the count is the part
#: that answers a question, and the prose is the part that must not be here.
FORBIDDEN = frozenset("text content prompt selection old new instruction body".split())

#: What separates a label from a sentence. Length alone does not: the longest
#: path in this paper is 69 characters and holds no space at all, while a
#: perfectly ordinary sentence of it is 65 characters and holds ten. So the
#: test is mostly about spaces, with a length bound behind it for the
#: pathological cases.
MAX_STRING = 120
MAX_SPACES = 4


def _is_a_label(value: str) -> bool:
    """A path, a tab name, a branch, a short reason — not a sentence."""
    return len(value) <= MAX_STRING and value.count(" ") <= MAX_SPACES


@dataclass(frozen=True)
class Kind:
    """One thing worth recording, and the question it helps answer."""

    name: str
    asks: str


#: The closed vocabulary. Closed on purpose: an open one becomes a pile nobody
#: reads, and "which of these has never happened" — the strongest signal there
#: is for taking a feature out — can only be asked of a list you wrote down.
KINDS: tuple[Kind, ...] = (
    # -- where you spend your attention
    Kind("app.open", "how often the workbench is opened at all"),
    Kind("tab.show", "which of the four surfaces you actually use"),
    Kind("file.open", "which files you work in, and which you never touch"),
    Kind("file.save", "how often you save, and how big the files are"),
    Kind("file.reload_from_disk", "how often something else moves a file under you"),
    Kind("preview.show", "which files are read in the pane rather than the editor"),
    Kind("preview.dismiss", "whether the preview is ever in the way of the paper"),
    Kind("rail.filter", "whether the file filter earns its place"),
    Kind("rail.action", "creating, renaming, deleting and uploading"),
    # -- the editor
    Kind("editor.zoom", "whether the text size is ever changed, and to what"),
    Kind("editor.complete", "whether \\cite and \\ref completion is used, and which kind"),
    Kind("editor.show_in_pdf", "whether forward search is used"),
    Kind("editor.jump_from_pdf", "whether double-clicking the PDF is used"),
    Kind("editor.buffer_restored", "how often unsaved work survives leaving a file"),
    # -- the loop this tool exists for
    Kind("session.create", "how a session starts: from a selection, or from the box"),
    Kind("session.message", "whether the conversation continues after the first turn"),
    Kind("session.stop", "how often an agent is stopped part-way"),
    Kind("session.compact", "whether a long conversation is ever folded to save tokens"),
    Kind("session.remove", "how often a worktree is put away"),
    Kind("agent.turn", "what a turn costs, in seconds and in dollars"),
    Kind("review.open", "how much arrives to review at a time"),
    Kind("review.decide", "take Claude's, keep yours, or write a third thing"),
    Kind("review.undo", "how often a decision is taken back"),
    Kind("review.save", "the loop finishing — what actually reaches the paper"),
    # -- waiting, and walls
    Kind("compile.run", "how long a build takes, and how often it fails"),
    Kind("compile.ask_fix", "whether a failed build is ever handed to Claude"),
    Kind("git.commit", "committing"),
    Kind("git.sync", "publishing, and what stopped it"),
    # `reason` on these two is a short slug, not the message: slugs group into
    # a count you can read, and a free-text message is both unaggregatable and
    # the likeliest way for the paper's own words to end up in here.
    Kind("error.shown", "every error the UI put in front of you"),
    Kind("refused", "every request the backend turned down, and why"),
)

KNOWN = {k.name: k for k in KINDS}


class UnknownKind(ValueError):
    """A kind not in the vocabulary. Add it to KINDS or do not record it."""


def scrub(detail: object) -> dict:
    """A detail dict reduced to what is safe and useful to keep.

    Numbers and booleans pass. Labels pass — a path, a tab name, a mode, a
    short reason. A forbidden key is replaced by its length in words, and
    anything else that reads like a sentence is replaced by its length in
    characters, because at that point it is prose and prose is the one thing
    this table must not hold.
    """
    if not isinstance(detail, dict):
        return {}
    out: dict = {}
    for key, value in detail.items():
        name = str(key)[:40]
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[name] = value
        elif value is None:
            out[name] = None
        elif isinstance(value, str):
            if name in FORBIDDEN:
                out[f"{name}_words"] = len(value.split())
            elif _is_a_label(value):
                out[name] = value
            else:
                out[f"{name}_chars"] = len(value)
        # Everything else — lists, nested dicts, objects — is dropped rather
        # than flattened: it is the shape most likely to smuggle prose in.
    return out


def record(db: Database, kind: str, detail: object = None, at: float | None = None) -> None:
    """Record one thing that happened. Never raises for a bad detail."""
    if kind not in KNOWN:
        raise UnknownKind(f"{kind} is not one of {len(KINDS)} known kinds")
    db.append_usage([(at or time.time(), kind, json.dumps(scrub(detail)))])


def record_batch(db: Database, entries: list[dict]) -> tuple[int, list[str]]:
    """A batch from the browser. Unknown kinds are reported, not written."""
    rows: list[tuple[float, str, str]] = []
    unknown: list[str] = []
    now = time.time()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", ""))
        if kind not in KNOWN:
            unknown.append(kind)
            continue
        at = entry.get("at")
        # The browser's clock is its own; anything not a plausible recent
        # timestamp is replaced rather than trusted.
        stamp = float(at) if isinstance(at, (int, float)) and 0 < at <= now + 300 else now
        rows.append((stamp, kind, json.dumps(scrub(entry.get("detail")))))
    return db.append_usage(rows), unknown


# -- reading it back ----------------------------------------------------------


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _tally(rows: list[dict], kind: str, key: str) -> Counter:
    return Counter(
        str(r["detail"].get(key)) for r in rows if r["kind"] == kind and key in r["detail"]
    )


def _numbers(rows: list[dict], kind: str, key: str) -> list[float]:
    return [
        float(r["detail"][key])
        for r in rows
        if r["kind"] == kind and isinstance(r["detail"].get(key), (int, float))
    ]


def report(db: Database, days: float = 30) -> dict:
    """The log turned into the questions it was kept to answer."""
    since = time.time() - days * 86400
    rows = db.usage_since(since)
    counts = Counter(r["kind"] for r in rows)
    span = db.usage_span() or {"first": None, "last": None, "n": 0}

    created = _tally(rows, "session.create", "from")
    decided = _tally(rows, "review.decide", "answer")
    compiles = _numbers(rows, "compile.run", "ms")
    failed_compiles = sum(
        1 for r in rows if r["kind"] == "compile.run" and r["detail"].get("ok") is False
    )
    turns = _numbers(rows, "agent.turn", "ms")

    return {
        "days": days,
        "since": since,
        "events": len(rows),
        "kept_since": span["first"],
        "kept_total": span["n"],
        "counts": dict(counts.most_common()),
        "never_used": [k.name for k in KINDS if not counts[k.name]],
        "attention": {
            "opened": counts["app.open"],
            "tabs": dict(_tally(rows, "tab.show", "tab").most_common()),
            "files_opened": counts["file.open"],
            "distinct_files": len({
                r["detail"].get("path") for r in rows if r["kind"] == "file.open"
            } - {None}),
            "busiest_files": _tally(rows, "file.open", "path").most_common(8),
            "saves": counts["file.save"],
        },
        "loop": {
            "sessions": dict(created.most_common()),
            "sessions_total": counts["session.create"],
            "continued": counts["session.message"],
            "stopped_early": counts["session.stop"],
            "reviews_opened": counts["review.open"],
            "saves": counts["review.save"],
            "changes_offered": int(sum(_numbers(rows, "review.open", "changes"))),
            "changes_taken": int(sum(_numbers(rows, "review.save", "taken"))),
            "changes_rewritten": int(sum(_numbers(rows, "review.save", "rewritten"))),
            "decisions": dict(decided.most_common()),
            "undos": counts["review.undo"],
        },
        "waiting": {
            "compiles": len(compiles),
            "compile_seconds": round(sum(compiles) / 1000, 1),
            "compile_median_seconds": (
                round(_median(compiles) / 1000, 1) if compiles else None
            ),
            "compiles_failed": failed_compiles,
            "agent_turns": len(turns),
            "agent_seconds": round(sum(turns) / 1000, 1),
            "agent_cost_usd": round(sum(_numbers(rows, "agent.turn", "cost_usd")), 4),
        },
        "friction": {
            "errors": counts["error.shown"],
            "where": dict(_tally(rows, "error.shown", "where").most_common(6)),
            "refusals": dict(_tally(rows, "refused", "reason").most_common(6)),
            "reloaded_under_you": counts["file.reload_from_disk"],
            "buffers_restored": counts["editor.buffer_restored"],
        },
    }


def _stamp(at: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at)) if at else "never"


def render(data: dict) -> str:
    """The report as something you read, not something you parse.

    One owner for the wording, so a panel added later says the same thing the
    command line does.
    """
    lines: list[str] = []
    add = lines.append

    if not data["events"]:
        return (
            f"Nothing recorded in the last {data['days']:g} days.\n"
            f"{data['kept_total']} entries in total, the oldest from "
            f"{_stamp(data['kept_since'])}."
        )

    add(f"Galley usage — the last {data['days']:g} days")
    add(f"{data['events']} things recorded, out of {data['kept_total']} kept "
        f"since {_stamp(data['kept_since'])}.")

    a = data["attention"]
    add("\nWhere your attention went")
    add(f"  opened the workbench   {a['opened']}")
    if a["tabs"]:
        add("  time on each surface   " + ", ".join(f"{k} {v}" for k, v in a["tabs"].items()))
    add(f"  files opened           {a['files_opened']} opens of {a['distinct_files']} files")
    for path, n in a["busiest_files"][:5]:
        add(f"      {n:>4}  {path}")
    add(f"  saves                  {a['saves']}")

    loop = data["loop"]
    add("\nThe loop this tool exists for")
    if loop["sessions"]:
        add("  sessions started       " + ", ".join(f"{k} {v}" for k, v in loop["sessions"].items()))
    else:
        add(f"  sessions started       {loop['sessions_total']}")
    add(f"  reviews opened         {loop['reviews_opened']}")
    add(f"  reviews saved          {loop['saves']}")
    if loop["reviews_opened"] and loop["saves"] < loop["reviews_opened"]:
        add(f"      {loop['reviews_opened'] - loop['saves']} opened and never saved")
    if loop["decisions"]:
        add("  what you decided       " + ", ".join(f"{k} {v}" for k, v in loop["decisions"].items()))
    add(f"  reached the paper      {loop['changes_taken']} taken, "
        f"{loop['changes_rewritten']} rewritten, of {loop['changes_offered']} offered")
    if loop["undos"]:
        add(f"  decisions taken back   {loop['undos']}")

    w = data["waiting"]
    add("\nWhere you waited")
    add(f"  builds                 {w['compiles']}, {w['compile_seconds']}s in total"
        + (f", {w['compile_median_seconds']}s typical" if w["compile_median_seconds"] else "")
        + (f", {w['compiles_failed']} failed" if w["compiles_failed"] else ""))
    add(f"  agent turns            {w['agent_turns']}, {w['agent_seconds']}s, "
        f"${w['agent_cost_usd']:.4f}")

    f = data["friction"]
    add("\nWhere you hit a wall")
    add(f"  errors shown           {f['errors']}"
        + (" — " + ", ".join(f"{k} {v}" for k, v in f["where"].items()) if f["where"] else ""))
    if f["refusals"]:
        add("  refused               " + ", ".join(f"{k} {v}" for k, v in f["refusals"].items()))
    if f["reloaded_under_you"]:
        add(f"  files moved under you  {f['reloaded_under_you']}")
    if f["buffers_restored"]:
        add(f"  unsaved work restored  {f['buffers_restored']}")

    if data["never_used"]:
        add("\nNever once used in this window")
        for name in data["never_used"]:
            add(f"  {name:<24} {KNOWN[name].asks}")

    return "\n".join(lines)
