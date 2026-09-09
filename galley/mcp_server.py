"""The tool surface Claude gets, over MCP on loopback.

A narrow, verb-shaped surface: enough to run science, not enough to publish.
There is deliberately no git push, no commit on the main branch, no Overleaf
credential, and no way to cancel a job this session did not create. Publishing
and cancelling stay with the human.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import Config
from .db import Database
from .services import latex, results
from .services.jobs import JobService
from .services.scheduler.base import QueueRefused, Resources


def build(cfg: Config, db: Database, jobs: JobService) -> MCPServer:
    server = MCPServer(
        name="galley",
        instructions=(
            "Galley's own tools. Experiments are submitted, never awaited. "
            "Numbers reach the paper only through write_result_table."
        ),
    )

    @server.tool(
        description=(
            "Submit an experiment to the cluster. Pins the code mirror at its "
            "current commit, lays down a run directory, starts the job, and "
            "returns a job id immediately — it never waits for the queue. The "
            "run outlives this conversation; its results wake a later session.\n\n"
            "Write anything you want kept into $GALLEY_ARTIFACTS (metrics.json, "
            "figures, logs); that directory is pulled back when the job ends."
        )
    )
    async def submit_job(
        script: str,
        note: str = "",
        gpus: int = 1,
        hours: float = 8.0,
        config_files: dict[str, str] | None = None,
    ) -> str:
        """Run a shell script on the cluster at a pinned commit."""
        preamble = (
            "set -euo pipefail\n"
            'export GALLEY_ARTIFACTS="$(cd "$(dirname "$0")" && pwd)/artifacts"\n'
            'cd "$(dirname "$0")/code"\n'
        )
        try:
            job = jobs.submit(
                script="#!/usr/bin/env bash\n" + preamble + script,
                resources=Resources(gpus=gpus, hours=hours),
                note=note,
                config_files=config_files,
            )
        except QueueRefused as exc:
            return f"REFUSED: {exc}"
        return json.dumps(
            {
                "job_id": job["id"],
                "state": job["state"],
                "code_sha": job["code_sha"],
                "note": job["note"],
                "hint": "Do not poll this. The job will wake a new session when it ends.",
            },
            indent=2,
        )

    @server.tool(
        description=(
            "State, elapsed time, exit code and the last lines of a job's output. "
            "Use it to orient yourself, not to wait: a queue wait is measured in "
            "hours and this conversation is not."
        )
    )
    async def job_status(job_id: str, lines: int = 40) -> str:
        row = db.get_job(job_id)
        if row is None:
            return f"no job {job_id}"
        elapsed = None
        if row["started_at"]:
            end = row["finished_at"] or __import__("time").time()
            elapsed = round(end - row["started_at"], 1)
        return json.dumps(
            {
                "job_id": job_id,
                "state": row["state"],
                "scheduler_id": row["scheduler_id"],
                "exit_code": row["exit_code"],
                "elapsed_seconds": elapsed,
                "code_sha": row["code_sha"],
                "note": row["note"],
                "artifacts_pulled": bool(row["artifacts_local"]),
                "tail": jobs.tail(job_id, lines),
            },
            indent=2,
        )

    @server.tool(
        description=(
            "Recent jobs with their notes and states. A fresh session uses this "
            "to find out what has already been run and what is still in flight."
        )
    )
    async def list_jobs(state: str | None = None, limit: int = 20) -> str:
        rows = db.list_jobs(state=state)[:limit]
        return json.dumps(
            [
                {
                    "job_id": r["id"],
                    "state": r["state"],
                    "note": r["note"],
                    "code_sha": (r["code_sha"] or "")[:10],
                    "exit_code": r["exit_code"],
                    "submitted_at": r["submitted_at"],
                }
                for r in rows
            ],
            indent=2,
        )

    @server.tool(
        description=(
            "Read a file from a finished job's pulled artifacts. Fails loudly if "
            "the artifacts have not been fetched yet — it will never read from "
            "the live run directory, so what you read is what was archived."
        )
    )
    async def read_results(job_id: str, path: str = "metrics.json") -> str:
        try:
            return jobs.read_artifact(job_id, path)
        except (KeyError, FileNotFoundError) as exc:
            return f"ERROR: {exc}"

    @server.tool(
        description=(
            "Compile the paper with latexmk and report only what needs acting on: "
            "errors and undefined references. Not the whole log."
        )
    )
    async def compile_paper() -> str:
        result = latex.compile_pdf(
            cfg.paths.paper_repo, cfg.paper.main_tex, cfg.paths.state_dir / "build"
        )
        return json.dumps(
            {
                "ok": result.ok,
                "errors": result.errors,
                "undefined_references": result.undefined,
            },
            indent=2,
        )

    @server.tool(
        description=(
            "Record a finished job's metrics into results/<job>.json in the paper "
            "repo. This is the only way a measured number enters the manuscript's "
            "history, and it carries the code SHA that produced it."
        )
    )
    async def record_result(job_id: str, metrics_path: str = "metrics.json") -> str:
        row = db.get_job(job_id)
        if row is None:
            return f"no job {job_id}"
        try:
            raw = jobs.read_artifact(job_id, metrics_path)
        except (KeyError, FileNotFoundError) as exc:
            return f"ERROR: {exc}"
        try:
            metrics = json.loads(raw)
        except json.JSONDecodeError as exc:
            return f"ERROR: {metrics_path} is not JSON ({exc})"
        path = results.record_result(cfg.paths.paper_repo, row, metrics)
        return f"wrote {path.relative_to(cfg.paths.paper_repo)}"

    @server.tool(
        description=(
            "Regenerate tables/<table_key>.tex from the recorded results of these "
            "jobs. This is the ONLY sanctioned path for a number to reach a .tex "
            "file. Never type a numeral into the manuscript yourself; if a table "
            "needs a number you do not have, run the job that measures it.\n\n"
            "The table's shape lives in tables/<table_key>.spec.json, which holds "
            "headers and dotted paths into metrics — prose and structure, never "
            "values."
        )
    )
    async def write_result_table(table_key: str, job_ids: list[str]) -> str:
        try:
            path = results.write_result_table(cfg.paths.paper_repo, table_key, job_ids)
        except results.ResultsError as exc:
            return f"ERROR: {exc}"
        return (
            f"wrote {path.relative_to(cfg.paths.paper_repo)} from "
            f"{len(job_ids)} job(s). Reference it with \\input{{tables/{table_key}}}."
        )

    @server.tool(
        description=(
            "The sentence-level diff of your branch against the paper's main "
            "branch, per file. This is what the human will see in the merge pane; "
            "read it to check your own change before you hand it over."
        )
    )
    async def review_my_changes(session_id: str | None = None) -> str:
        from .services import git

        repo = cfg.paths.paper_repo
        rows = db.list_sessions()
        row = next(
            (r for r in rows if session_id in (None, r["id"]) and r["status"] != "removed"),
            None,
        )
        if row is None:
            return "no session found"
        files = git.changed_files(repo, cfg.paper.main_branch, row["branch"])
        return json.dumps({"branch": row["branch"], "files": files}, indent=2)

    return server


def artifacts_hint(cfg: Config) -> dict[str, Any]:
    return {"artifacts_cache": str(cfg.artifacts_dir), "scratch": str(cfg.cluster.scratch)}
