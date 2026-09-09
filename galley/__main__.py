"""`galley` — start the backend, or read back how you have been using it."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import uvicorn

from .config import ConfigError, load


def main() -> int:
    parser = argparse.ArgumentParser(prog="galley", description="Galley backend")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--bind", type=str, default=None)
    parser.add_argument("--reload", action="store_true")

    sub = parser.add_subparsers(dest="command")
    look = sub.add_parser(
        "usage",
        help="what you have been doing with the workbench",
        description=(
            "A local record of how you use Galley, kept so it can be made "
            "better. It holds no sentence of the paper and never leaves this "
            "machine."
        ),
    )
    look.add_argument("--days", type=float, default=30, help="how far back to look")
    look.add_argument("--json", action="store_true", help="the report as data")
    look.add_argument(
        "--forget",
        nargs="?",
        const="all",
        metavar="DAYS",
        help="delete the record: everything, or everything older than DAYS",
    )
    args = parser.parse_args()

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"galley: {exc}", file=sys.stderr)
        return 2

    if args.command == "usage":
        return _usage(cfg, args)

    host = args.bind or cfg.server.bind
    port = args.port or cfg.server.port
    print(f"galley: paper  {cfg.paths.paper_repo}")
    if cfg.paths.code_mirror is not None:
        print(f"galley: code   {cfg.paths.code_mirror}")
    if not cfg.builds_a_pdf:
        print(
            f"galley: no {cfg.paper.main_tex} in the paper — the PDF, SyncTeX "
            "and LaTeX completion are off for this project"
        )
    print(f"galley: config {cfg.source}")
    print(f"galley: serving on http://{host}:{port}")

    from .app import create_app

    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")
    return 0


def _usage(cfg, args) -> int:
    """Print the usage log, or delete it. Never both."""
    from .db import Database
    from .services import usage

    db = Database(cfg.db_path)
    try:
        if args.forget is not None:
            if args.forget == "all":
                gone = db.forget_usage()
                print(f"galley: deleted {gone} usage entries. The record starts again now.")
            else:
                before = time.time() - float(args.forget) * 86400
                gone = db.forget_usage(before)
                print(f"galley: deleted {gone} entries older than {args.forget} days.")
            return 0

        data = usage.report(db, args.days)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(usage.render(data))
            if not cfg.usage.enabled:
                print("\nRecording is off ([usage] enabled = false), so this stops here.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
