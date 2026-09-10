#!/usr/bin/env bash
# Everything that can be checked without a browser, in the order that fails
# fastest. Run it before you commit.
#
# What it does not cover: the UI has no test runner, so a green run here means
# the types agree and the bundle builds — not that a feature works. Drive the
# real thing for that; see docs/development.md.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f run.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./run.env
  set +a
fi

step() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }

step "backend tests"
uv run pytest -q

step "UI types"
(cd ui && npx tsc --noEmit)

step "UI build"
(cd ui && npx vite build)

printf '\n\033[32mAll green.\033[0m\n'
