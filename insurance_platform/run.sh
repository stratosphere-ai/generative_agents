#!/usr/bin/env bash
# Launch the prediction-market-backed insurance platform.
#
# Usage:
#   ./run.sh                 # default: provider=polymarket (auto-falls back to mock offline)
#   INSURE_PROVIDER=mock ./run.sh
set -euo pipefail

cd "$(dirname "$0")"
PORT="${INSURE_PORT:-8100}"
exec uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT"
