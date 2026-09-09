"""What a scheduler has to be able to do, and nothing more.

Galley talks to a scheduler through four verbs. The design document was written
against Slurm; wsserver1 runs `gpuq`, which has no daemon and runs jobs in the
foreground. Both fit behind this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# The lifecycle from the design, plus UNREACHABLE, which is a *state* rather
# than an exception: a dropped VPN or an expired ticket must show in the job
# board, not crash the poller.
STATES = (
    "DRAFT",
    "SUBMITTED",
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "UNREACHABLE",
)
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
LIVE = frozenset({"SUBMITTED", "PENDING", "RUNNING", "UNREACHABLE"})


class SchedulerError(RuntimeError):
    pass


class QueueRefused(SchedulerError):
    """The submission was rejected before it reached the scheduler."""


@dataclass(frozen=True)
class Resources:
    gpus: int = 1
    hours: float = 8.0
    min_free_gb: int | None = None

    def validate(self, max_hours: float) -> None:
        if self.gpus < 0:
            raise QueueRefused("gpus must be >= 0")
        if not 0 < self.hours <= max_hours:
            raise QueueRefused(f"hours must be in (0, {max_hours}]")


@dataclass
class Observation:
    """What the scheduler says about a job right now."""

    state: str
    scheduler_id: str | None = None
    exit_code: int | None = None
    gpus: list[int] = field(default_factory=list)
    detail: str = ""


class Scheduler(Protocol):
    name: str

    def submit(self, workdir: Path, script: str, resources: Resources, tag: str) -> Observation:
        """Start the job and return immediately. Never waits for it to run."""

    def observe(self, tag: str, workdir: Path) -> Observation:
        """Current state of the job with this tag."""

    def tail(self, workdir: Path, lines: int = 40) -> str:
        """The last lines of the job's combined output."""

    def cancel(self, tag: str) -> None:
        """Stop a running job or drop a queued one."""
