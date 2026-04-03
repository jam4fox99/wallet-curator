#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

export DISABLE_SCHEDULER="${DISABLE_SCHEDULER:-1}"
export READ_ONLY_UI="${READ_ONLY_UI:-1}"
export PORT="${PORT:-8050}"

exec python app.py
