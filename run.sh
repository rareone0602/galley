#!/usr/bin/env bash
# Start the Galley backend. Run it from tmux so it outlives your shell.
set -euo pipefail
cd "$(dirname "$0")"

# The Agent SDK bills the API instead of your subscription if it inherits a
# key. Galley strips it from the child, and refuses to start if it is here.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

# Anything this machine needs and no other does — where the virtualenv lives,
# where big temporary files go — belongs in run.env, which is gitignored.
# See run.env.example. Nothing here assumes a particular box.
if [ -f run.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./run.env
  set +a
fi
[ -n "${TMPDIR:-}" ] && mkdir -p "$TMPDIR"

exec uv run galley "$@"
