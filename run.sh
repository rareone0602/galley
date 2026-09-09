#!/usr/bin/env bash
# Start the Galley backend. Run it from tmux so it outlives your shell.
set -euo pipefail
cd "$(dirname "$0")"

# The Agent SDK bills the API instead of your subscription if it inherits a
# key. Galley strips it from the child, and refuses to start if it is here.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-/scratch/users/$USER/venvs/galley}"
export TMPDIR="${TMPDIR:-/scratch/temp/$USER}"
mkdir -p "$TMPDIR"

exec uv run galley "$@"
