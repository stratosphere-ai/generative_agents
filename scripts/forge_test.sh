#!/usr/bin/env bash
# Install Foundry (idempotent) and run forge test in contracts/.
# Sandbox-aware: skips install if foundryup binary already present.

set -euo pipefail

cd "$(dirname "$0")/../contracts"

if ! command -v foundryup >/dev/null 2>&1; then
  echo "[forge_test] installing foundry…"
  if ! curl -sSfL https://foundry.paradigm.xyz | bash; then
    echo "[forge_test] foundry installer download failed."
    echo "[forge_test] install manually: https://book.getfoundry.sh/getting-started/installation"
    exit 1
  fi
  # Pick up the bin path the installer added.
  export PATH="$HOME/.foundry/bin:$PATH"
fi

if ! command -v forge >/dev/null 2>&1; then
  echo "[forge_test] running foundryup to install forge/cast/anvil…"
  foundryup
fi

# Install forge-std once (sub-repo). Re-runs are no-ops.
if [ ! -d "lib/forge-std" ]; then
  echo "[forge_test] installing forge-std…"
  forge install --no-commit foundry-rs/forge-std
fi

echo "[forge_test] forge --version:"
forge --version

echo "[forge_test] forge build:"
forge build

echo "[forge_test] forge test:"
exec forge test -vv
