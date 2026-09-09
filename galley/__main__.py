"""`galley` — start the backend."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .config import ConfigError, load


def main() -> int:
    parser = argparse.ArgumentParser(prog="galley", description="Galley backend")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--bind", type=str, default=None)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"galley: {exc}", file=sys.stderr)
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
