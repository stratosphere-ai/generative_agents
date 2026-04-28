#!/usr/bin/env bash
# Start a local anvil node in the background. Idempotent.
set -euo pipefail

PORT="${ANVIL_PORT:-8545}"
LOG="${ANVIL_LOG:-/tmp/anvil.log}"

if pgrep -f "anvil.*--port ${PORT}" >/dev/null; then
  echo "[devchain] anvil already running on :${PORT}"
  exit 0
fi

if ! command -v anvil >/dev/null 2>&1; then
  echo "[devchain] anvil not found. Install Foundry: https://getfoundry.sh"
  exit 1
fi

nohup anvil --host 0.0.0.0 --port "${PORT}" >"${LOG}" 2>&1 &
echo "[devchain] anvil starting on :${PORT} (logs: ${LOG})"

# Wait until RPC responds.
for _ in $(seq 1 30); do
  if curl -sf -X POST -H 'content-type: application/json' \
       --data '{"jsonrpc":"2.0","method":"net_version","id":1}' \
       "http://127.0.0.1:${PORT}" >/dev/null; then
    echo "[devchain] anvil ready"
    exit 0
  fi
  sleep 0.2
done

echo "[devchain] anvil failed to come up; see ${LOG}"
exit 1
