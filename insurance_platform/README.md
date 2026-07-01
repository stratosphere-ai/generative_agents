# PredInsure — Prediction-Market-Backed Insurance Platform (一键投保)

An insurance platform where **prediction markets are the underlying/backing asset**
and buying a policy takes **one click (一键投保)**.

## The idea

Insuring against an event *E* for coverage *C* is economically identical to taking a
**YES position** on *E* in a prediction market:

| Insurance concept | Prediction-market equivalent |
|---|---|
| Probability of the insured event | Market-implied YES price `p ∈ (0,1)` |
| Premium | `premium = C · p · (1 + loading_factor)` |
| Payout if the event happens | `C` |
| Reserve / liability | `C` |
| Backing asset (底层资产) | `C` YES-shares bought at price `p`, cost `C · p` |

- If *E* resolves **YES**: the hedge redeems for `C`, which covers the `C` claim
  payout. The insurer keeps the loading margin `C · p · loading`.
- If *E* resolves **NO**: the hedge expires worthless (loss `C · p`) but no claim is
  paid and the premium `C · p · (1 + loading)` was collected → net `+C · p · loading`.

So the book is roughly self-funding and the profit is the loading margin. See
`app/pricing.py` for the exact formulas.

> ⚠️ **This is an off-chain simulation.** Real market *prices* drive pricing, but the
> capital pool, hedging, premiums, and payouts are a simulated ledger. There is **no
> real on-chain trading** and this is **not** a real financial product.

## Architecture

```
app/
  config.py        env-driven settings (provider, loading factor, seed capital, …)
  db.py            SQLAlchemy engine/session + init_db()
  models.py        ORM: User, Market, Policy, CapitalPool, LedgerEntry
  schemas.py       Pydantic request/response models
  pricing.py       PURE pricing/solvency math (unit-tested, no I/O)
  ledger.py        append-only journal; pool recomputed from it
  settlement.py    underwrite_policy() + resolve_market()
  deps.py          FastAPI deps (session, demo user, pool snapshot, market upsert)
  routers/         markets, policies, admin, pool
  providers/       base ABC, polymarket (Gamma API), mock, fallback factory
static/            index.html, policies.html, admin.html, app.js, styles.css
tests/             pricing, settlement, providers (no network needed)
```

**Data source.** The default provider is **Polymarket's public Gamma API**
(`https://gamma-api.polymarket.com/markets`, no API key). It is wrapped in a
`FallbackProvider` that transparently degrades to a built-in **mock provider** on any
network/timeout/parse error — so the app always runs, online or offline. Only binary
Yes/No markets are surfaced (the insurance model needs a single YES probability).
The adapter interface (`app/providers/base.py`) lets Kalshi or any other source drop in.

**Persistence.** SQLite via SQLAlchemy. The append-only `LedgerEntry` journal is the
source of truth for pool balances; `CapitalPool` is a cache recomputed from it.

## Run

```bash
pip install -r requirements.txt
./run.sh                      # or: uvicorn app.main:app --reload --port 8100
# open http://localhost:8100/
```

Config via env vars:

| Var | Default | Meaning |
|---|---|---|
| `INSURE_PROVIDER` | `polymarket` | `polymarket` (real, auto-fallback) or `mock` |
| `INSURE_LOADING_FACTOR` | `0.15` | insurer margin on top of fair premium |
| `INSURE_MIN_SOLVENCY` | `1.0` | min cash/reserved ratio to underwrite |
| `INSURE_SEED_CAPITAL` | `10000` | starting pool cash |
| `INSURE_PORT` | `8100` | server port (avoids the repo's Django app on 8000) |

## API

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/markets?limit=50` | sync provider → cache → list insurable markets |
| GET  | `/api/markets/{id}` | single market |
| POST | `/api/quote` | `{market_id, coverage}` → premium/reserve (no write) |
| POST | `/api/policies` | one-click buy → policy + pool after (409 if insolvent) |
| GET  | `/api/policies` | the demo user's policies |
| GET  | `/api/pool` | capital-pool status + solvency ratio |
| GET  | `/api/admin/markets` | markets with exposure + resolvable flag |
| POST | `/api/admin/resolve` | `{market_id, outcome:"yes"\|"no"}` → settle claims |

## Test

```bash
pytest -q                     # pricing, settlement, provider mapping (no network)
```

Manual smoke test:

```bash
curl localhost:8100/api/markets
curl -X POST localhost:8100/api/quote    -H 'content-type: application/json' -d '{"market_id":1,"coverage":1000}'
curl -X POST localhost:8100/api/policies -H 'content-type: application/json' -d '{"market_id":1,"coverage":1000}'
curl localhost:8100/api/pool
curl -X POST localhost:8100/api/admin/resolve -H 'content-type: application/json' -d '{"market_id":1,"outcome":"yes"}'
```

End-to-end in the UI: **Browse** → 一键投保 → **My Policies** → **Admin** resolve → see
the payout reflected in the pool.
