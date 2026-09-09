"""What every route area is handed.

The routes used to be closures inside `create_app`, which meant one file grew
every time the UI did. They are now grouped by area, and everything they used
to close over lives here, named once: the config, the database, the event bus,
the agent supervisor, the work table, and the four questions more than one area
asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from ..bus import EventBus
from ..config import Config
from ..db import Database
from ..services import git
from ..services.agent import AgentService
from ..services.work import WorkTable


@dataclass(frozen=True)
class Deps:
    cfg: Config
    db: Database
    bus: EventBus
    agents: AgentService
    work: WorkTable

    # -- the questions more than one area asks ---------------------------

    def require_session(self, session_id: str) -> dict:
        row = self.db.get_session(session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        return row

    def repo_for(self, session_id: str | None) -> Path:
        """The main worktree, or a session's checkout when one is named."""
        if not session_id:
            return self.cfg.paths.paper_repo
        return Path(self.require_session(session_id)["worktree_path"])

    def session_changes(self, row: dict) -> list[dict]:
        """What a session changed, measured from where it forked.

        The fork point rather than the branch name: a session carries your
        uncommitted work in as its first commit, so diffing against the main
        branch would report your own unsaved paragraphs as the agent's work.
        """
        base = row["base_sha"] or self.cfg.paper.main_branch
        return git.changed_files(self.cfg.paths.paper_repo, base, row["branch"])

    def pdf_path(self, session_id: str | None, review: bool) -> Path:
        stem = Path(self.cfg.paper.main_tex).stem
        if review and session_id:
            return self.cfg.paths.state_dir / "review" / session_id / "latexdiff.pdf"
        if session_id:
            return self.cfg.paths.state_dir / "build" / session_id / f"{stem}.pdf"
        return self.cfg.paths.state_dir / "build" / f"{stem}.pdf"

    def read_working(self, rel: str) -> str:
        """A file as it is on disk right now, empty if it is not there."""
        path = self.cfg.paths.paper_repo / rel
        return path.read_text(errors="replace") if path.is_file() else ""
