#!/usr/bin/env bash
set -euo pipefail

# Load .env if present
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export DATA_PILOT_PORT="${DATA_PILOT_PORT:-7860}"
export DATA_PILOT_HOST="${DATA_PILOT_HOST:-0.0.0.0}"

mkdir -p data logs

exec uvicorn app.main:app --host "$DATA_PILOT_HOST" --port "$DATA_PILOT_PORT" --workers 1
