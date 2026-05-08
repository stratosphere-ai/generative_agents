#!/usr/bin/env bash
# Deploy TaskRegistry(V1 or V2) to local anvil and write CONTRACT_ADDRESS to .env.local.
#
# Pass `--v1` to deploy the legacy contract; default is V2 (atomic action +
# batch interfaces). The Python client speaks V2 by default and falls back
# gracefully if V1 is deployed (atomic shortcut method just isn't called).
set -euo pipefail

cd "$(dirname "$0")/../contracts"

: "${PRIVATE_KEY:=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80}"
: "${RPC_URL:=http://127.0.0.1:8545}"

VERSION="v2"
if [[ "${1:-}" == "--v1" ]]; then VERSION="v1"; fi

if [[ "${VERSION}" == "v2" ]]; then
  CONTRACT_NAME="TaskRegistryV2"
  SCRIPT_PATH="script/DeployV2.s.sol"
else
  CONTRACT_NAME="TaskRegistry"
  SCRIPT_PATH="script/Deploy.s.sol"
fi

forge install --no-commit foundry-rs/forge-std 2>/dev/null || true
forge build

PRIVATE_KEY="${PRIVATE_KEY}" forge script "${SCRIPT_PATH}" \
  --rpc-url "${RPC_URL}" --broadcast --json 2>&1 | tee /tmp/deploy.out

ADDR=$(grep -oE '0x[a-fA-F0-9]{40}' /tmp/deploy.out | tail -1)
if [[ -z "${ADDR}" ]]; then
  echo "[deploy] failed to extract address. Full output:"
  cat /tmp/deploy.out
  exit 1
fi

ENV_LOCAL="../.env.local"
{
  echo "BLOCKCHAIN_MODE=live"
  echo "RPC_URL=${RPC_URL}"
  echo "CHAIN_ID=31337"
  echo "PRIVATE_KEY=${PRIVATE_KEY}"
  echo "CONTRACT_ADDRESS=${ADDR}"
  echo "CONTRACT_VERSION=${VERSION}"
} > "${ENV_LOCAL}"

# Export ABI for the Python client.
mkdir -p ../reverie/backend_server/blockchain/abi
forge inspect "${CONTRACT_NAME}" abi > ../reverie/backend_server/blockchain/abi/TaskRegistry.json

echo "[deploy] ${CONTRACT_NAME} @ ${ADDR}"
echo "[deploy] wrote ${ENV_LOCAL}"
echo "[deploy] exported ABI to reverie/backend_server/blockchain/abi/TaskRegistry.json"
