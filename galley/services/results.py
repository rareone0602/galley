"""The numbers invariant: no numeral is ever typed into a .tex file.

The path is one-way, and each arrow is a different thing you can check::

    artifacts/metrics.json   pulled from the run, carries the code SHA
      -> results/<job>.json  committed to the paper repo
      -> tables/<key>.tex    generated from a spec; never hand-edited
      -> \\input{tables/<key>}  the only reference in the manuscript

Retrofitting this means auditing every number already in the paper, so it is
here from the first commit. A hallucinated figure has nowhere to enter: it would
have to appear as a results/*.json with no job id behind it, which the UI flags
without anyone having to exercise judgement.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESULTS_DIR = "results"
TABLES_DIR = "tables"

PRECOMMIT_HOOK = r"""#!/usr/bin/env bash
# Installed by galley. The numbers invariant, made structural.
#
# A commit that changes a generated table without changing the results it was
# generated from means a number was edited by hand. Refuse it.
set -euo pipefail

staged=$(git diff --cached --name-only)
touches_tables=$(printf '%s\n' "$staged" | grep -c '^tables/' || true)
touches_results=$(printf '%s\n' "$staged" | grep -c '^results/' || true)

if [ "$touches_tables" -gt 0 ] && [ "$touches_results" -eq 0 ]; then
  echo "galley: this commit changes tables/ but not results/." >&2
  echo "        Tables are generated. Regenerate from a job's results with" >&2
  echo "        write_result_table, or stage the results/*.json it came from." >&2
  echo "        Override once with --no-verify if you know why." >&2
  exit 1
fi
"""


class ResultsError(RuntimeError):
    pass


@dataclass(frozen=True)
class TableSpec:
    """How a table is built. Prose and structure only — never numbers."""

    key: str
    caption: str
    label: str
    columns: list[dict]
    align: str | None = None

    @classmethod
    def load(cls, repo: Path, key: str) -> "TableSpec":
        path = repo / TABLES_DIR / f"{key}.spec.json"
        if not path.is_file():
            raise ResultsError(
                f"no table spec at {path}. A table's shape is declared once, in a "
                "spec file; galley fills in the numbers from results/."
            )
        raw = json.loads(path.read_text())
        return cls(
            key=key,
            caption=raw.get("caption", key),
            label=raw.get("label", f"tab:{key}"),
            columns=raw["columns"],
            align=raw.get("align"),
        )


HOOK_MARKER = "# Installed by galley."


def install_precommit_hook(repo: Path, force: bool = False) -> Path | None:
    """Install the hook, but never over one that is not ours.

    Returns the path if it wrote, None if it left an existing hook alone.
    """
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    path = hooks / "pre-commit"
    if path.exists() and not force:
        existing = path.read_text(errors="replace")
        if HOOK_MARKER not in existing:
            return None  # somebody else's hook; leave it be
        if existing == PRECOMMIT_HOOK:
            return path
    path.write_text(PRECOMMIT_HOOK)
    path.chmod(0o755)
    return path


def record_result(repo: Path, job: dict, metrics: dict) -> Path:
    """Commit-ready results file: the metrics plus the provenance behind them."""
    out_dir = repo / RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{job['id']}.json"
    path.write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "code_sha": job.get("code_sha"),
                "scheduler_id": job.get("scheduler_id"),
                "note": job.get("note"),
                "state": job.get("state"),
                "exit_code": job.get("exit_code"),
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "metrics": metrics,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path


def load_result(repo: Path, job_id: str) -> dict:
    path = repo / RESULTS_DIR / f"{job_id}.json"
    if not path.is_file():
        raise ResultsError(
            f"job {job_id} has no results/{job_id}.json. Record the job's metrics "
            "before asking for a table built from it."
        )
    return json.loads(path.read_text())


def dig(data: Any, dotted: str) -> Any:
    """Follow a dotted path into a metrics object; `None` if it is not there."""
    node = data
    for part in dotted.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit():
            node = node[int(part)] if int(part) < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


def render_table(repo: Path, spec: TableSpec, job_ids: list[str]) -> str:
    rows = [load_result(repo, jid) for jid in job_ids]
    align = spec.align or "l" + "r" * (len(spec.columns) - 1)
    lines = [
        "% Generated by galley. Do not edit by hand.",
        "% Every number below comes from results/<job>.json; edit those, or the",
        "% spec beside this file, and regenerate.",
        f"% jobs: {', '.join(job_ids)}",
        "\\begin{table}[t]",
        "  \\centering",
        f"  \\begin{{tabular}}{{{align}}}",
        "    \\toprule",
        "    " + " & ".join(_escape(c["header"]) for c in spec.columns) + " \\\\",
        "    \\midrule",
    ]
    for result in rows:
        cells = [_cell(result, column) for column in spec.columns]
        lines.append("    " + " & ".join(cells) + " \\\\")
    lines += [
        "    \\bottomrule",
        "  \\end{tabular}",
        f"  \\caption{{{spec.caption}}}",
        f"  \\label{{{spec.label}}}",
        "\\end{table}",
        "",
    ]
    return "\n".join(lines)


def _cell(result: dict, column: dict) -> str:
    source = result if column.get("path", "").startswith("$.") else result.get("metrics", {})
    path = column["path"].removeprefix("$.")
    value = dig(source, path)
    if value is None:
        return column.get("missing", "---")
    fmt = column.get("format")
    if fmt and isinstance(value, (int, float)):
        return format(value, fmt)
    return _escape(str(value))


def _escape(text: str) -> str:
    """Escape only what would break the table; leave real LaTeX alone."""
    if any(marker in text for marker in ("\\", "$", "^", "_{")):
        return text
    return text.replace("&", "\\&").replace("%", "\\%").replace("#", "\\#")


def write_result_table(repo: Path, table_key: str, job_ids: list[str]) -> Path:
    if not job_ids:
        raise ResultsError("a table needs at least one job to be built from")
    spec = TableSpec.load(repo, table_key)
    path = repo / TABLES_DIR / f"{table_key}.tex"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_table(repo, spec, job_ids))
    return path


def unbacked_results(repo: Path, known_job_ids: set[str]) -> list[str]:
    """results/*.json files with no job behind them — a number that appeared."""
    out_dir = repo / RESULTS_DIR
    if not out_dir.is_dir():
        return []
    orphans = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            job_id = json.loads(path.read_text()).get("job_id")
        except (OSError, json.JSONDecodeError):
            job_id = None
        if not job_id or job_id not in known_job_ids:
            orphans.append(path.name)
    return orphans
