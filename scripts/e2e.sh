#!/usr/bin/env bash
# End-to-end smoke test: anvil up -> deploy -> launch reverie -> verify events.
set -euo pipefail

cd "$(dirname "$0")/.."

bash scripts/devchain_up.sh
bash scripts/deploy_local.sh

# Source .env.local for the python process.
set -a
# shellcheck disable=SC1091
source .env.local
set +a

export SIM_TEMPLATE="${SIM_TEMPLATE:-base_vending_min}"
export SIM_CODE="${SIM_CODE:-vending_e2e_$(date +%s)}"

echo "[e2e] launching reverie with template=${SIM_TEMPLATE} sim=${SIM_CODE}"
echo "[e2e] (interactive: enter '${SIM_TEMPLATE}' then '${SIM_CODE}' at the prompts)"

cd reverie/backend_server
python reverie.py
