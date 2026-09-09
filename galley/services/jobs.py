"""Experiments are objects, not turns.

A queue wait is measured in hours and a Claude turn in minutes, so submission
returns immediately and completion is an event that starts *new* work. The
session that submitted a job has usually ended long before it finishes.

Reproducibility falls out of this for free: submission archives a pinned commit
rather than your dirty working copy, so every artifact carries the exact SHA
that produced it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from ..bus import EventBus
from ..config import Config
from ..db import Database
from . import git
from .scheduler.base import LIVE, TERMINAL, Observation, QueueRefused, Resources, Scheduler

# Poll interval by state: a queued job may sit for hours, a running one is
# checked often enough to be useful and rarely enough to be free.
POLL_SECONDS = {
    "SUBMITTED": 10.0,
    "PENDING": 30.0,
    "RUNNING": 300.0,
    "UNREACHABLE": 60.0,
}
DEFAULT_POLL = 30.0


class JobService:
    def __init__(
        self, cfg: Config, db: Database, bus: EventBus, scheduler: Scheduler
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.scheduler = scheduler
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    # -- submit -----------------------------------------------------------

    def submit(
        self,
        script: str,
        resources: Resources,
        note: str = "",
        session_id: str | None = None,
        config_files: dict[str, str] | None = None,
    ) -> dict:
        """Pin the code, lay down a workdir, start the job, return at once."""
        code_repo = self.cfg.paths.code_mirror
        sha = git.head_sha(code_repo)
        job_id = uuid.uuid4().hex[:12]
        tag = f"galley-{job_id}"
        workdir = self.cfg.cluster.scratch / job_id

        self._export_pinned_tree(code_repo, sha, workdir / "code")
        for rel, content in (config_files or {}).items():
            target = (workdir / "code" / rel).resolve()
            target.relative_to((workdir / "code").resolve())  # no escaping the workdir
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

        (workdir / "provenance.json").write_text(
            json.dumps(
                {"job_id": job_id, "code_sha": sha, "repo": str(code_repo), "note": note},
                indent=2,
            )
        )

        self.db.create_job(
            id=job_id,
            session_id=session_id,
            state="DRAFT",
            host=self.scheduler.name,
            code_sha=sha,
            note=note,
            workdir=str(workdir),
            submit_cmd=script[:4000],
        )
        try:
            obs = self.scheduler.submit(workdir, script, resources, tag)
        except QueueRefused as exc:
            self._transition(job_id, Observation(state="CANCELLED", detail=str(exc)))
            raise
        self._transition(job_id, obs)
        self._wake.set()
        return self.db.get_job(job_id) or {}

    def _export_pinned_tree(self, repo: Path, sha: str, dest: Path) -> None:
        """A checkout of exactly `sha`, never of your working copy."""
        dest.mkdir(parents=True, exist_ok=True)
        archive = subprocess.Popen(
            ["git", "-C", str(repo), "archive", "--format=tar", sha],
            stdout=subprocess.PIPE,
        )
        extract = subprocess.Popen(["tar", "-x", "-C", str(dest)], stdin=archive.stdout)
        if archive.stdout:
            archive.stdout.close()
        extract.communicate()
        archive.wait()
        if extract.returncode != 0 or archive.returncode != 0:
            raise RuntimeError(f"could not export {sha} from {repo} into {dest}")

    # -- state ------------------------------------------------------------

    def _transition(self, job_id: str, obs: Observation) -> dict | None:
        """Write the new state, then publish it. Never the other way round."""
        row = self.db.get_job(job_id)
        if row is None or row["state"] == obs.state:
            if row and obs.scheduler_id and not row["scheduler_id"]:
                self.db.update_job(job_id, scheduler_id=obs.scheduler_id)
            return None

        fields: dict = {"state": obs.state}
        if obs.scheduler_id:
            fields["scheduler_id"] = obs.scheduler_id
        if obs.exit_code is not None:
            fields["exit_code"] = obs.exit_code
        if obs.state == "RUNNING" and not row["started_at"]:
            fields["started_at"] = time.time()
        if obs.state in TERMINAL:
            fields["finished_at"] = time.time()
        self.db.update_job(job_id, **fields)

        payload = {
            "job_id": job_id,
            "from": row["state"],
            "to": obs.state,
            "detail": obs.detail,
            "exit_code": obs.exit_code,
        }
        self.db.append_event("job_state", payload, job_id=job_id)
        return payload

    async def _transition_async(self, job_id: str, obs: Observation) -> dict | None:
        payload = self._transition(job_id, obs)
        if payload:
            await self.bus.publish("jobs", {"kind": "job_state", "payload": payload})
        return payload

    # -- artifacts --------------------------------------------------------

    def fetch_artifacts(self, job_id: str) -> Path:
        """Copy `artifacts/` out of the run directory into the local cache."""
        row = self.db.get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        src = Path(row["workdir"]) / "artifacts"
        dest = self.cfg.artifacts_dir / job_id
        dest.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            for item in src.iterdir():
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
        for name in ("stdout.log", "stderr.log", "provenance.json"):
            candidate = Path(row["workdir"]) / name
            if candidate.exists():
                shutil.copy2(candidate, dest / name)
        self.db.update_job(job_id, artifacts_local=str(dest))
        return dest

    def read_artifact(self, job_id: str, rel: str = "metrics.json") -> str:
        row = self.db.get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        if not row["artifacts_local"]:
            raise FileNotFoundError(
                f"job {job_id} has no artifacts pulled yet (state {row['state']}). "
                "Fetch them first; galley will not read from the run directory."
            )
        base = Path(row["artifacts_local"]).resolve()
        path = (base / rel).resolve()
        path.relative_to(base)
        if not path.is_file():
            raise FileNotFoundError(f"{rel} is not in job {job_id}'s artifacts")
        return path.read_text()

    def tail(self, job_id: str, lines: int = 40) -> str:
        row = self.db.get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        return self.scheduler.tail(Path(row["workdir"]), lines)

    def cancel(self, job_id: str) -> None:
        row = self.db.get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        self.scheduler.cancel(f"galley-{job_id}")
        self._transition(job_id, Observation(state="CANCELLED", detail="cancelled by you"))

    # -- the poller -------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="galley-job-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        """One task for all jobs, not a thread each.

        Every failure here is caught: a scheduler that cannot be reached is a
        job state, not a crashed poller.
        """
        next_due: dict[str, float] = {}
        while True:
            try:
                sleep_for = await self._tick(next_due)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the poller must not die
                await self.bus.publish(
                    "jobs", {"kind": "poller_error", "payload": {"error": str(exc)}}
                )
                sleep_for = DEFAULT_POLL
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=sleep_for)

    async def _tick(self, next_due: dict[str, float]) -> float:
        live = self.db.live_jobs()
        if not live:
            return DEFAULT_POLL
        now = time.monotonic()
        soonest = DEFAULT_POLL
        for row in live:
            job_id = row["id"]
            due = next_due.get(job_id, 0.0)
            if due > now:
                soonest = min(soonest, due - now)
                continue
            obs = await asyncio.to_thread(
                self.scheduler.observe, f"galley-{job_id}", Path(row["workdir"])
            )
            await self._transition_async(job_id, obs)
            if obs.state in TERMINAL:
                await self._on_finished(job_id, obs)
                next_due.pop(job_id, None)
                continue
            interval = POLL_SECONDS.get(obs.state, DEFAULT_POLL)
            next_due[job_id] = now + interval
            soonest = min(soonest, interval)
        return max(1.0, soonest)

    async def _on_finished(self, job_id: str, obs: Observation) -> None:
        """Pull the artifacts and announce a job ready for handoff."""
        try:
            dest = await asyncio.to_thread(self.fetch_artifacts, job_id)
            detail = str(dest)
        except Exception as exc:  # noqa: BLE001
            detail = f"artifact pull failed: {exc}"
        row = self.db.get_job(job_id) or {}
        event = self.db.append_event(
            "job_finished",
            {
                "job_id": job_id,
                "state": obs.state,
                "exit_code": obs.exit_code,
                "code_sha": row.get("code_sha"),
                "note": row.get("note"),
                "artifacts": detail,
            },
            job_id=job_id,
        )
        await self.bus.publish("jobs", event)


def handoff_prompt(job: dict, metrics: str | None, tail: str) -> str:
    """The prompt a completed job wakes a new session with.

    A failure gets the same path, with stderr in place of metrics, into a
    session asked to diagnose rather than to write.
    """
    header = (
        f"Job {job['id']} ({job.get('note') or 'no note'}) finished with state "
        f"{job['state']}, exit code {job.get('exit_code')}.\n"
        f"Code SHA: {job.get('code_sha')}\n"
        f"Artifacts: {job.get('artifacts_local')}\n\n"
    )
    if job["state"] == "COMPLETED" and metrics:
        return (
            header
            + "metrics.json:\n```json\n"
            + metrics[:8000]
            + "\n```\n\nRead the results and say what they show. If a number "
            "belongs in the paper, put it there with write_result_table — never "
            "by typing it into a .tex file."
        )
    return (
        header
        + "The job did not complete. Last output:\n```\n"
        + tail[-8000:]
        + "\n```\n\nDiagnose the failure. Do not write paper prose in this session."
    )
