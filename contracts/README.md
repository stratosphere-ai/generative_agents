# TaskRegistry Contracts

On-chain task ledger for the Vending-Machine AI Agent sandbox.

## Quickstart

```bash
# install foundry if needed
curl -L https://foundry.paradigm.xyz | bash && foundryup

# install forge-std
forge install foundry-rs/forge-std --no-commit

# build & test
forge build
forge test -vv
```

## Local deploy (anvil)

```bash
anvil &                       # http://127.0.0.1:8545, prints test keys
export PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
forge script script/Deploy.s.sol \
  --rpc-url http://127.0.0.1:8545 \
  --broadcast
```

The deployed address is printed in `console2`. Copy it to the project-root `.env.local`
as `CONTRACT_ADDRESS=...`.

## ABI export for the Python client

```bash
forge inspect TaskRegistry abi > ../reverie/backend_server/blockchain/abi/TaskRegistry.json
```
