#!/usr/bin/env bash
# The browser probe: build the UI, serve a throwaway project, drive Chromium
# through it, and say what is on the screen.
#
# `./check.sh` proves the types agree and the bundle builds. This proves a
# feature works. Run it after any change to `ui/`.
#
#   ./probe/run.sh              headless, prints one line per check
#   GALLEY_PROBE_CHROME=... ./probe/run.sh   with another browser
#
# Screenshots land beside the fixture; the path is printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f run.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./run.env
  set +a
fi

PORT="${GALLEY_PROBE_PORT:-8127}"
export GALLEY_PROBE_DIR="${GALLEY_PROBE_DIR:-${TMPDIR:-/tmp}/galley-probe}"
export GALLEY_PROBE_APP="http://127.0.0.1:$PORT/"
mkdir -p "$GALLEY_PROBE_DIR"

step() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }

step "UI build"
(cd ui && npx vite build >/dev/null)

step "throwaway project"
CONFIG=$(uv run python probe/fixture.py "$GALLEY_PROBE_DIR/project" "$PORT")
echo "$CONFIG"

step "server on $PORT"
# The agent never starts here, but the same rule holds: no key in the child.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
GALLEY_CONFIG="$CONFIG" uv run galley > "$GALLEY_PROBE_DIR/server.log" 2>&1 &
SERVER=$!
# By PID. `pkill -f galley` matches this script's own command line and kills
# the shell running it — which has happened, twice.
trap 'kill "$SERVER" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 40); do
  if curl -sf "http://127.0.0.1:$PORT/api/config" >/dev/null; then break; fi
  sleep 0.25
done

step "the browser"
code=0
uv run python probe/drive.py || code=$?

echo "screenshots: $GALLEY_PROBE_DIR"
exit "$code"
