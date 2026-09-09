"""The job layer: pinning, the one-at-a-time guard, state, and artifacts.

The gpuq state parsing is exercised against real record shapes written into a
temporary queue directory, so these tests never touch the shared scheduler and
never claim a GPU. The lifecycle is driven through a fake scheduler, because
what is being tested is Galley's bookkeeping, not gpuq's.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from galley.bus import EventBus
from galley.db import Database
from galley.services.jobs import JobService, handoff_prompt
from galley.services.scheduler.base import Observation, QueueRefused, Resources
from galley.services.scheduler.gpuq import GpuqScheduler


class FakeScheduler:
    """Answers whatever the test tells it to, and records what it was asked."""

    name = "fake"

    def __init__(self) -> None:
        self.state = "PENDING"
        self.submitted: list[tuple[Path, str, Resources, str]] = []

    def submit(self, workdir, script, resources, tag):
        self.submitted.append((workdir, script, resources, tag))
        return Observation(state="SUBMITTED")

    def observe(self, tag, workdir):
        return Observation(state=self.state, exit_code=0 if self.state == "COMPLETED" else None)

    def tail(self, workdir, lines=40):
        return "some output"

    def cancel(self, tag):
        self.state = "CANCELLED"


@pytest.fixture
def service(config):
    db = Database(config.db_path)
    return JobService(config, db, EventBus(), FakeScheduler()), db


# -- submission pins the code ---------------------------------------------


def test_submission_archives_the_commit_not_the_working_copy(service, code_mirror) -> None:
    jobs, _ = service
    # A dirty working copy must not reach the cluster: the artifact has to
    # carry the SHA that actually produced it.
    (code_mirror / "train.py").write_text("print('uncommitted edit')\n")
    (code_mirror / "untracked.py").write_text("print('never committed')\n")

    job = jobs.submit(script="true", resources=Resources(gpus=1, hours=1), note="pin check")
    code = Path(job["workdir"]) / "code"
    assert (code / "train.py").read_text() == "print('hello')\n"
    assert not (code / "untracked.py").exists()

    import subprocess

    head = subprocess.run(
        ["git", "-C", str(code_mirror), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert job["code_sha"] == head


def test_the_run_directory_carries_its_provenance(service) -> None:
    jobs, _ = service
    job = jobs.submit(script="true", resources=Resources(), note="why this ran")
    provenance = json.loads((Path(job["workdir"]) / "provenance.json").read_text())
    assert provenance["code_sha"] == job["code_sha"]
    assert provenance["note"] == "why this ran"


def test_config_files_cannot_escape_the_run_directory(service) -> None:
    jobs, _ = service
    with pytest.raises(ValueError):
        jobs.submit(
            script="true",
            resources=Resources(),
            config_files={"../../escaped.yaml": "no"},
        )


def test_a_refused_submission_does_not_leave_a_live_job(service) -> None:
    jobs, db = service

    def refuse(*_a, **_k):
        raise QueueRefused("the queue says no")

    jobs.scheduler.submit = refuse
    with pytest.raises(QueueRefused):
        jobs.submit(script="true", resources=Resources(), note="doomed")
    assert db.live_jobs() == []


# -- the lifecycle ---------------------------------------------------------


async def test_a_finished_job_pulls_its_artifacts_and_announces_itself(service) -> None:
    jobs, db = service
    job = jobs.submit(script="true", resources=Resources(), note="run me")
    artifacts = Path(job["workdir"]) / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "metrics.json").write_text('{"eval": {"ce": 1.234}}')

    seen = []
    async with jobs.bus.subscribe("jobs") as queue:
        jobs.scheduler.state = "COMPLETED"
        await jobs._tick({})
        while not queue.empty():
            seen.append(await queue.get())

    row = db.get_job(job["id"])
    assert row["state"] == "COMPLETED"
    assert row["finished_at"] is not None
    assert row["artifacts_local"]
    assert json.loads(jobs.read_artifact(job["id"]))["eval"]["ce"] == 1.234
    assert any(e.get("kind") == "job_finished" for e in seen)


async def test_reading_results_before_they_are_pulled_fails_loudly(service) -> None:
    jobs, _ = service
    job = jobs.submit(script="true", resources=Resources(), note="not done")
    with pytest.raises(FileNotFoundError) as exc:
        jobs.read_artifact(job["id"])
    assert "no artifacts pulled yet" in str(exc.value)


async def test_an_artifact_path_cannot_escape_the_cache(service) -> None:
    jobs, _ = service
    job = jobs.submit(script="true", resources=Resources())
    jobs.fetch_artifacts(job["id"])
    with pytest.raises(ValueError):
        jobs.read_artifact(job["id"], "../../../etc/passwd")


async def test_the_poller_survives_a_scheduler_that_throws(service) -> None:
    """An unreachable scheduler is a job state, not a crashed poller."""
    jobs, _ = service
    jobs.submit(script="true", resources=Resources())

    def explode(*_a, **_k):
        raise OSError("vpn dropped")

    jobs.scheduler.observe = explode
    task = asyncio.create_task(jobs._run())
    await asyncio.sleep(0.2)
    assert not task.done()
    task.cancel()


# -- the handoff prompt ----------------------------------------------------


def test_a_completed_job_hands_off_its_metrics() -> None:
    prompt = handoff_prompt(
        {"id": "abc", "state": "COMPLETED", "exit_code": 0, "note": "n", "code_sha": "s"},
        '{"ce": 1.0}',
        "irrelevant output",
    )
    assert '{"ce": 1.0}' in prompt
    assert "write_result_table" in prompt


def test_a_failed_job_hands_off_stderr_and_asks_for_a_diagnosis() -> None:
    prompt = handoff_prompt(
        {"id": "abc", "state": "FAILED", "exit_code": 1, "note": "n", "code_sha": "s"},
        None,
        "Traceback: boom",
    )
    assert "Traceback: boom" in prompt
    assert "Diagnose the failure" in prompt
    assert "Do not write paper prose" in prompt


# -- gpuq's own reading of the queue --------------------------------------


@pytest.fixture
def queue_dir(tmp_path: Path) -> Path:
    d = tmp_path / "gpu_queue"
    d.mkdir()
    (d / "jobs.json").write_text("[]")
    (d / "running.json").write_text("[]")
    (d / "usage.jsonl").write_text("")
    return d


def _sched(queue_dir: Path) -> GpuqScheduler:
    return GpuqScheduler(user="me", queue_dir=queue_dir)


def test_one_queued_job_at_a_time(queue_dir, tmp_path) -> None:
    """gpuq admits on a VRAM sample taken at submit time, so two of your own
    queued waiters are both admitted against the same freed card."""
    (queue_dir / "jobs.json").write_text(
        json.dumps([{"id": 1, "user": "me", "name": "galley-a", "status": "queued"}])
    )
    with pytest.raises(QueueRefused) as exc:
        _sched(queue_dir).submit(tmp_path / "wd", "true", Resources(), "galley-b")
    assert "one at a time" in str(exc.value)


def test_someone_elses_queued_job_is_not_yours(queue_dir, tmp_path) -> None:
    (queue_dir / "jobs.json").write_text(
        json.dumps([{"id": 1, "user": "someone-else", "name": "x", "status": "queued"}])
    )
    sched = _sched(queue_dir)
    sched._refuse_if_already_queued()  # does not raise


def test_gpuq_has_no_cpu_only_lane(queue_dir, tmp_path) -> None:
    with pytest.raises(QueueRefused) as exc:
        _sched(queue_dir).submit(tmp_path / "wd", "true", Resources(gpus=0), "galley-z")
    assert "at least one GPU" in str(exc.value)


def test_state_comes_from_the_queue_files(queue_dir, tmp_path) -> None:
    sched = _sched(queue_dir)
    workdir = tmp_path / "wd"
    workdir.mkdir()

    (queue_dir / "jobs.json").write_text(
        json.dumps([{"id": 7, "user": "me", "name": "galley-x", "status": "queued"}])
    )
    assert sched.observe("galley-x", workdir).state == "PENDING"

    (queue_dir / "jobs.json").write_text("[]")
    (queue_dir / "running.json").write_text(
        json.dumps([{"id": 7, "user": "me", "name": "galley-x", "gpus": [2], "status": "running"}])
    )
    obs = sched.observe("galley-x", workdir)
    assert obs.state == "RUNNING" and obs.gpus == [2] and obs.scheduler_id == "7"


@pytest.mark.parametrize(
    "reason,code,expected",
    [
        ("completed", 0, "COMPLETED"),
        ("completed", 1, "FAILED"),
        ("timeout", 137, "FAILED"),
        ("cancelled", 143, "CANCELLED"),
    ],
)
def test_the_ledger_decides_the_final_state(queue_dir, tmp_path, reason, code, expected) -> None:
    sched = _sched(queue_dir)
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (queue_dir / "usage.jsonl").write_text(
        json.dumps(
            {"v": 2, "event": "end", "id": 7, "name": "galley-x",
             "exit_code": code, "end_reason": reason}
        )
        + "\n"
    )
    assert sched.observe("galley-x", workdir).state == expected


def test_a_truncated_ledger_tail_does_not_break_the_read(queue_dir, tmp_path) -> None:
    """The ledger is read from the tail, so the first line is usually half a
    record. That must be skipped, not raised."""
    sched = _sched(queue_dir)
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (queue_dir / "usage.jsonl").write_text(
        'ost":"wsserver1","name":"galley-x"}\n'
        + json.dumps({"v": 2, "event": "end", "id": 7, "name": "galley-x",
                      "exit_code": 0, "end_reason": "completed"})
        + "\n"
    )
    assert sched.observe("galley-x", workdir).state == "COMPLETED"


def test_a_job_gpuq_never_heard_of_is_unreachable_not_an_exception(queue_dir, tmp_path) -> None:
    workdir = tmp_path / "wd"
    workdir.mkdir()
    assert _sched(queue_dir).observe("galley-ghost", workdir).state == "UNREACHABLE"


def test_the_run_wrapper_reports_an_exit_code_the_ledger_has_not_seen(queue_dir, tmp_path) -> None:
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "exit_code").write_text("0\n")
    obs = _sched(queue_dir).observe("galley-x", workdir)
    assert obs.state == "COMPLETED"
    assert "not yet in the gpuq ledger" in obs.detail
