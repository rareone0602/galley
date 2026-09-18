"""The merge pane: your text against a branch's, a sentence at a time."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from ..segment.diff import diff_text
from ..services import files
from .deps import Deps

MID_MERGE = (
    "{branch} is in the middle of a merge — {files} still have conflict markers "
    "in them. Finish or abort the merge in that checkout and this review will "
    "read it. Galley will not write there itself."
)


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/diff")
    def read_diff(branch: str = Query(...), path: str | None = Query(None)) -> dict:
        source = d.source_for(branch)
        if source is None:  # pragma: no cover - `branch` is a required query
            raise HTTPException(400, "a review needs a branch")

        # Conflict markers are not sentences, and the segmenter would happily
        # turn them into some. Say what is wrong with the checkout instead.
        conflicts = source.conflicts()
        if conflicts:
            raise HTTPException(
                409, MID_MERGE.format(branch=branch, files=", ".join(conflicts[:3]))
            )

        changed = source.changed()
        wanted = [f for f in changed if path is None or f["path"] == path]
        if path is not None and not wanted:
            raise HTTPException(404, f"{path} did not change on {branch}")

        out = []
        for entry in wanted:
            rel = entry["path"]
            yours = d.read_working(rel)
            # A file the branch deleted, or one that is not text, is named and
            # not offered. Diffing a deletion gives "every sentence replaced by
            # nothing", and taking that would write an empty file over your
            # paper — which is not what deleting a file means.
            usable = entry["editable"]
            ops = diff_text(yours, source.read(rel)) if usable else []
            out.append(
                {
                    "path": rel,
                    "ops": [op.as_dict() for op in ops],
                    "changes": sum(1 for op in ops if op.type == "change"),
                    "added": entry["added"],
                    "removed": entry["removed"],
                    "deleted": entry["gone"],
                    "editable": usable,
                    # Which of you moved this file since the fork. Yours having
                    # moved too is the case where taking theirs quietly undoes a
                    # sentence you wrote after they started.
                    "yours_moved": usable and yours != source.at_base(rel),
                    # What Save must still find on disk before it may write.
                    "sha": files.digest(yours),
                }
            )
        return {
            "branch": branch,
            "kind": source.kind,
            "label": source.label,
            "base": source.base,
            "state": source.state(),
            "files": out,
        }
