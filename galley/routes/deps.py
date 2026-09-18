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
from ..services import source, usage
from ..services.agent import AgentService
from ..services.source import Source
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

    def sources(self) -> list[Source]:
        """Every branch you could review, sessions among them."""
        return source.listing(self.cfg, self.db.list_sessions())

    def source_for(self, branch: str | None) -> Source | None:
        """The branch a request named, or None when it named your working copy."""
        if not branch:
            return None
        try:
            return source.find(self.cfg, self.db.list_sessions(), branch)
        except source.UnknownBranch as exc:
            raise HTTPException(404, str(exc)) from exc

    def tree_for(self, src: Source | None, review: bool) -> Path:
        """The tree TeX ran in for a build. Named, never made.

        Every path inside a SyncTeX file is written relative to it, and it is
        not always the paper: a branch builds in a checkout of Galley's own, and
        the marked-up review builds in a scratch copy beside its PDF.
        """
        if review and src is not None:
            return self.cfg.paths.state_dir / "review" / src.slug / "tree"
        if src is not None:
            return source.built_in(self.cfg, src)
        return self.cfg.paths.paper_repo

    def pdf_path(self, src: Source | None, review: bool) -> Path:
        """Where a build of this source lands.

        Keyed by the branch rather than by a session, so a branch's PDF outlives
        the conversation that made it. Old `build/<session id>/` directories are
        orphaned by that change and can stay orphaned: `state_dir/build` is
        throwaway output, never cached and never read back, so there is nothing
        to migrate and nobody should write a migration.
        """
        stem = Path(self.cfg.paper.main_tex).stem
        if review and src is not None:
            return self.cfg.paths.state_dir / "review" / src.slug / "latexdiff.pdf"
        if src is not None:
            return self.cfg.paths.state_dir / "build" / src.slug / f"{stem}.pdf"
        return self.cfg.paths.state_dir / "build" / f"{stem}.pdf"

    def note(self, kind: str, detail: dict | None = None) -> None:
        """Record something the *server* is the authority on.

        The browser records what you did with the UI; this records what
        actually happened — a build's real duration, a sync's real outcome —
        so the log stays true when the tab is closed or the page is reloaded
        mid-build. Switched off with the rest of it.
        """
        if self.cfg.usage.enabled:
            usage.record(self.db, kind, detail)

    def read_working(self, rel: str) -> str:
        """A file as it is on disk right now, empty if it is not there.

        The encoding is named rather than left to the process locale: a paper
        is full of non-ASCII, and a server started from a different shell must
        not read the same file differently.
        """
        path = self.cfg.paths.paper_repo / rel
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
