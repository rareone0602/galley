#!/usr/bin/env bash
# Galley while you are working on Galley.
#
# Two servers, one command. The backend restarts when a Python file changes;
# Vite serves the UI from source and swaps a changed component into the running
# page without a reload, so neither half needs `npm run build` any more.
#
# Open the Vite address it prints (5173 by default), not the backend's own
# port: Vite forwards every /api request through to it. `./run.sh` is still
# what you want for ordinary use — it serves the built UI from one port.
#
# Run it in its own terminal or tmux window; Ctrl-C stops both halves.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ui/node_modules ]; then
  echo "dev: installing the UI's dependencies (first run only)"
  (cd ui && npm install)
fi

# Kill the whole process group, so Vite's child does not outlive the script and
# hold port 5173 against the next run.
trap 'trap - EXIT INT TERM; kill 0' EXIT INT TERM

(cd ui && npm run dev) &
./run.sh --reload "$@" &
wait
