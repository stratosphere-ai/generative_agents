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
| `INSURE_SEED_CAPITAL` | `2000000` | starting pool cash (large enough for cargo baskets) |
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

## Cargo basket policies (P1) — one policy, many hedged factors

Beyond single-event cover, the platform insures a **shipment** against a *basket*
of correlated risk factors. The buyer's experience is unchanged — submit the
cargo details, see one premium, click **一键投保** — but behind the scenes a rule
engine fans the shipment out into a hedged portfolio.

**Flow.** `POST /api/shipments/quote` → the risk engine (`app/risk_engine.py`)
picks the relevant factors (route/geopolitical/macro/weather) by route keywords,
prices each as a mini single-event cover, and returns the total premium plus the
factor breakdown. `POST /api/shipments/policies` issues one `BasketPolicy` with a
`HedgeLeg` per factor, each hedged on its own market book. Resolving a factor
book in Admin settles that leg across every policy referencing it.

**Economics (per factor i).** `covered_loss_i = impact_i · cargo_value`;
`premium_i = covered_loss_i · p_i · (1 + loading)`; hedge buys `covered_loss_i`
YES-shares at `p_i`. Policy totals are the sums. **P1 assumes factors are
independent and losses additive** — correlation, a value cap, basis risk and
order-book depth/slippage are deferred to later phases (see the roadmap).

**Endpoints:** `POST /api/shipments/quote`, `POST /api/shipments/policies`,
`GET /api/shipments/policies`. **Frontend:** `static/cargo.html`.

> Because a $400k cargo reserves a large liability, the default seed capital is
> `INSURE_SEED_CAPITAL=2_000_000`. This is P1 *gross* reserving; capital-efficient
> net reserving (crediting the hedge assets against liabilities) is a later item.

### Roadmap
- **P1 (done):** basket data model, rule-based engine, portfolio premium/solvency, cargo UI.
- **P2:** LLM-based factor *discovery* + real order-book mapping (multi-provider).
- **P3:** factor correlation & joint-loss model, basis-risk capital, CLOB depth/slippage, parametric delivery oracle for claims.

## Intelligent engine (P2) — LLM factor discovery + real order-book matching

P1's engine used a fixed factor template and synthetic books. P2 makes it
intelligent and connects it to real markets — the buyer experience is unchanged.

- **Factor discovery** (`app/discovery.py`, `app/llm.py`): `INSURE_DISCOVERY=llm`
  uses **Claude** (official `anthropic` SDK, `claude-opus-4-8`, structured JSON
  output) to read the shipment and propose material risk factors. Any failure
  (no key, blocked network, bad output) **auto-falls back** to the P1 rule
  engine (`FallbackDiscovery`), so the app never breaks. Default `rule`.
- **Real multi-provider matching** (`app/matching.py`, `app/providers/kalshi.py`):
  `INSURE_MATCH=on` maps each factor to a real prediction-market book across
  **Polymarket + Kalshi** (`search_markets` + keyword scoring), and prices the
  hedge at the real book's probability. Unmatched factors keep a synthetic
  `engine` book. Providers are called defensively, so a blocked provider just
  yields no match. Default `off`.
- **Orchestration** (`app/engine.py`): `assess()` = discover → match → price.
  The API and cargo UI surface each factor's **discovery source** (LLM / 规则)
  and **盘口来源** (Polymarket / Kalshi / 合成).

| Var | Default | Meaning |
|---|---|---|
| `INSURE_DISCOVERY` | `rule` | `rule` or `llm` (Claude, auto-fallback) |
| `INSURE_LLM_MODEL` | `claude-opus-4-8` | model for LLM discovery |
| `INSURE_MATCH` | `off` | `off` (synthetic books) or `on` (real Polymarket/Kalshi) |
| `INSURE_MATCH_PROVIDERS` | `polymarket,kalshi` | providers to search when matching |

Enable the intelligent path where it can reach the services:
```bash
export ANTHROPIC_API_KEY=sk-...        # for INSURE_DISCOVERY=llm
INSURE_DISCOVERY=llm INSURE_MATCH=on ./run.sh
```
Both paths degrade gracefully, so this sandbox (no key, Polymarket/Kalshi
blocked) runs `rule` + synthetic; the LLM/real-match code is exercised in tests
via mocks (`tests/test_discovery.py`, `test_matching.py`, `test_engine.py`).
