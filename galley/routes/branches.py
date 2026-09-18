"""The work you could review: every branch, whoever wrote it."""

from __future__ import annotations

from fastapi import FastAPI

from .deps import Deps


def register(app: FastAPI, d: Deps) -> None:
    @app.get("/api/branches")
    def list_branches() -> list[dict]:
        """Every branch with something in it to read, newest first.

        Sessions are in here too, because a session is a branch with a
        conversation attached. The rail shows one list for that reason: what you
        want to find is the work, and which agent wrote it is a property of the
        work rather than a place to go looking for it.

        Counting the changes means a `git diff` per branch. That is a handful of
        milliseconds each on a paper repository, and it is what makes the list
        worth reading — a branch with nothing in it is not work.
        """
        out = []
        for source in d.sources():
            changed = source.changed()
            out.append(
                {
                    **source.as_dict(),
                    "files": len(changed),
                    "added": sum(f["added"] or 0 for f in changed),
                    "removed": sum(f["removed"] or 0 for f in changed),
                }
            )
        return out
