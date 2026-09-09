"""The `gpuq` backend: the scheduler on wsserver1.

`gpuq` has no daemon. `gpuq submit` claims a GPU and runs your command in the
*foreground*, and that process supervises the job. So "submit and return
immediately" means: start it inside a detached tmux session and let it live
there. tmux is used rather than a bare background process because a background
child dies with the shell that spawned it, and this job must outlive both the
Claude session that asked for it and the Galley backend itself.

State is read from the queue's own files rather than scraped from console
output, and every job is correlated by the `--name` tag, which appears in all
three: the queued list, the running list, and the usage ledger.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from .base import Observation, QueueRefused, Resources, SchedulerError

DEFAULT_QUEUE_DIR = Path("/var/lib/gpu_queue")

# Reading the whole ledger on every poll is waste; a job that just ended is
# always in the last little bit of it.
LEDGER_TAIL_BYTES = 512 * 1024


class GpuqScheduler:
    name = "gpuq"

    def __init__(
        self,
        user: str | None = None,
        tmux_prefix: str = "galley",
        max_hours: float = 12.0,
        max_queued: int = 1,
        extra_flags: list[str] | None = None,
        queue_dir: Path | None = None,
    ) -> None:
        self.user = user or os.environ.get("USER", "")
        self.queue_dir = queue_dir or DEFAULT_QUEUE_DIR
        self.tmux_prefix = tmux_prefix
        self.max_hours = max_hours
        self.max_queued = max_queued
        self.extra_flags = list(extra_flags or ["--queue"])

    @property
    def queued_file(self) -> Path:
        return self.queue_dir / "jobs.json"

    @property
    def running_file(self) -> Path:
        return self.queue_dir / "running.json"

    @property
    def ledger_file(self) -> Path:
        return self.queue_dir / "usage.jsonl"

    # -- submit -----------------------------------------------------------

    def submit(self, workdir: Path, script: str, resources: Resources, tag: str) -> Observation:
        resources.validate(self.max_hours)
        if resources.gpus < 1:
            # gpuq's own rule: "-g/--gpus must be at least 1". There is no
            # CPU-only lane, so a zero-GPU request would simply be rejected at
            # submit time, after the workdir had been laid down.
            raise QueueRefused(
                "gpuq has no CPU-only lane: every job claims at least one GPU. "
                "Ask for gpus >= 1."
            )
        self._refuse_if_already_queued()

        workdir.mkdir(parents=True, exist_ok=True)
        run_sh = workdir / "run.sh"
        run_sh.write_text(script if script.startswith("#!") else "#!/usr/bin/env bash\n" + script)
        run_sh.chmod(0o755)
        (workdir / "artifacts").mkdir(exist_ok=True)

        gpuq_cmd = [
            "gpuq", "submit",
            "--name", tag,
            "-g", str(resources.gpus),
            "-t", str(resources.hours),
            *self.extra_flags,
        ]
        if resources.min_free_gb is not None:
            gpuq_cmd += ["-m", str(resources.min_free_gb)]
        gpuq_cmd += ["--", "bash", str(run_sh)]

        # The wrapper records the exit status even if the ledger lags, and keeps
        # the tmux window open long enough for the status to be written.
        inner = (
            f"cd {shlex.quote(str(workdir))} && "
            f"{shlex.join(gpuq_cmd)} > stdout.log 2> stderr.log; "
            f"echo $? > exit_code"
        )
        session = self._tmux_name(tag)
        proc = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "bash", "-lc", inner],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SchedulerError(f"could not start tmux session {session}: {proc.stderr.strip()}")

        (workdir / "submit.json").write_text(
            json.dumps(
                {"tag": tag, "tmux": session, "cmd": gpuq_cmd, "resources": resources.__dict__},
                indent=2,
            )
        )
        return Observation(state="SUBMITTED", detail=f"tmux session {session}")

    def _refuse_if_already_queued(self) -> None:
        """gpuq admits on a VRAM sample taken at submit time.

        Two of your own jobs waiting in the queue will both be admitted against
        the same freed card. One at a time, always.
        """
        mine = [j for j in _read_json_list(self.queued_file) if j.get("user") == self.user]
        if len(mine) >= self.max_queued:
            ids = ", ".join(str(j.get("id")) for j in mine)
            raise QueueRefused(
                f"you already have {len(mine)} job(s) queued on gpuq ({ids}). "
                "Two queued waiters are admitted against the same freed card, "
                "so galley submits one at a time. Wait for it to start."
            )

    # -- observe ----------------------------------------------------------

    def observe(self, tag: str, workdir: Path) -> Observation:
        try:
            running = _read_json_list(self.running_file)
            queued = _read_json_list(self.queued_file)
        except OSError as exc:
            return Observation(state="UNREACHABLE", detail=f"queue dir unreadable: {exc}")

        for job in running:
            if job.get("name") == tag:
                return Observation(
                    state="RUNNING",
                    scheduler_id=str(job.get("id")),
                    gpus=list(job.get("gpus") or []),
                    detail=f"GPU {','.join(map(str, job.get('gpus') or []))}",
                )
        for job in queued:
            if job.get("name") == tag:
                return Observation(
                    state="PENDING",
                    scheduler_id=str(job.get("id")),
                    detail="waiting for a free GPU",
                )

        end = self._ledger_end_record(tag)
        if end is not None:
            code = end.get("exit_code")
            reason = end.get("end_reason", "")
            state = "COMPLETED" if code == 0 and reason == "completed" else "FAILED"
            if reason in ("cancelled", "killed"):
                state = "CANCELLED"
            return Observation(
                state=state,
                scheduler_id=str(end.get("id")),
                exit_code=code,
                detail=reason,
            )

        # No ledger record yet. Our own wrapper may already know.
        code = _read_exit_code(workdir)
        if code is not None:
            return Observation(
                state="COMPLETED" if code == 0 else "FAILED",
                exit_code=code,
                detail="from the run wrapper; not yet in the gpuq ledger",
            )

        if self._tmux_alive(tag):
            return Observation(state="SUBMITTED", detail="gpuq is starting up")
        return Observation(
            state="UNREACHABLE",
            detail="no queue entry, no ledger record, and the tmux session is gone",
        )

    def _ledger_end_record(self, tag: str) -> dict | None:
        try:
            size = self.ledger_file.stat().st_size
            with self.ledger_file.open("rb") as fh:
                fh.seek(max(0, size - LEDGER_TAIL_BYTES))
                blob = fh.read().decode("utf-8", "replace")
        except OSError:
            return None
        best = None
        for line in blob.splitlines():
            if tag not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a truncated first line from the tail seek
            if rec.get("name") == tag and rec.get("event") == "end":
                best = rec
        return best

    # -- tail and cancel --------------------------------------------------

    def tail(self, workdir: Path, lines: int = 40) -> str:
        chunks = []
        for name in ("stdout.log", "stderr.log"):
            path = workdir / name
            if not path.exists():
                continue
            text = _tail_text(path, lines)
            if text.strip():
                chunks.append(f"--- {name} ---\n{text}")
        return "\n".join(chunks) or "(no output yet)"

    def cancel(self, tag: str) -> None:
        job_id = None
        for job in _read_json_list(self.running_file) + _read_json_list(self.queued_file):
            if job.get("name") == tag:
                job_id = job.get("id")
                break
        if job_id is not None:
            subprocess.run(["gpuq", "kill", str(job_id)], capture_output=True, text=True)
        subprocess.run(
            ["tmux", "kill-session", "-t", self._tmux_name(tag)], capture_output=True, text=True
        )

    # -- tmux -------------------------------------------------------------

    def _tmux_name(self, tag: str) -> str:
        return f"{self.tmux_prefix}-{tag}"

    def _tmux_alive(self, tag: str) -> bool:
        proc = subprocess.run(
            ["tmux", "has-session", "-t", self._tmux_name(tag)],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0


def _read_json_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text() or "[]")
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        return list(data.values())
    return list(data) if isinstance(data, list) else []


def _read_exit_code(workdir: Path) -> int | None:
    path = workdir / "exit_code"
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _tail_text(path: Path, lines: int) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 64 * 1024))
            blob = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(blob.splitlines()[-lines:])
