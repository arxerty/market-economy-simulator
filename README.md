# Multi-Agent Market Economy Simulator

LLM-driven agents (consumers, producers, speculators, a regulator) trade in a shared
order-book market. The goal is to study emergent phenomena such as **speculative
bubbles**, **cartels / tacit collusion**, and the **effects of taxation**. The
simulation runs in real time and is shown in a React dashboard that streams updates
over WebSockets.

```
docker compose up --build          # UI on http://localhost:3000, API on http://localhost:8000
```

Every bundled experiment uses the deterministic **mock LLM** by default, so it runs
without an API key. To use Claude, set `ANTHROPIC_API_KEY` (copy `.env.example` to
`.env`) and pick the `anthropic` provider in the UI, or set `llm.provider: anthropic`
in the experiment file.

---

## Contents

- [Running locally](#running-locally)
- [Architecture](#architecture)
- [The round loop](#the-round-loop)
- [Economic model and accounting](#economic-model-and-accounting)
- [Experiments](#experiments)
- [How to define a new agent strategy](#how-to-define-a-new-agent-strategy)
- [How to define a new good](#how-to-define-a-new-good)
- [How to define a new experiment](#how-to-define-a-new-experiment)
- [LLM providers, retries, fallbacks and replay](#llm-providers-retries-fallbacks-and-replay)
- [Metrics](#metrics)
- [API](#api)
- [Tests](#tests)

---

## Running locally

**Backend** (Python 3.11 or later):

```bash
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn market_sim.api.app:app --reload --port 8000
```

**Frontend** (Node 20 or later):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api (incl. WebSockets) to 127.0.0.1:8000
```

**Headless CLI** (no UI needed):

```bash
cd backend
python -m market_sim run experiments/bubble.yaml              # prints each round, then the summary
python -m market_sim run experiments/taxation.yaml --provider anthropic --rounds 20
python -m market_sim analyze data/runs/<run_id>
python -m market_sim compare data/runs/<run_a> data/runs/<run_b>
```

## Architecture

```
backend/market_sim/
├── config.py            Experiment schema (pydantic). YAML/JSON → ExperimentConfig
├── rng.py               Seed derivation: every random draw is keyed by (seed, label…)
├── engine/              Pure market mechanics. No LLM or agent-logic imports.
│   ├── orderbook.py     Limit order book, price-time priority, self-trade prevention
│   ├── settlement.py    Cash/inventory/tax transfers for each trade
│   ├── policy.py        Taxes, subsidies, price floors/ceilings per good
│   ├── economics.py     Supply/demand schedules, equilibrium, max surplus, monopoly price
│   ├── state.py         AgentState: cash, inventory, cost basis, valuations, memory…
│   └── events.py        Append-only JSONL event store (+ in-process subscribers)
├── agents/              Decision-making
│   ├── strategies.py    Strategy registry: heuristics + prompt guidance (+ chat messages)
│   ├── prompts.py       System/user prompt builders (deterministic → cacheable)
│   ├── actions.py       Parse LLM output → validate → sanitise into safe orders
│   └── decision.py      Concurrent runner: semaphore, timeout, retries, cache, fallback
├── llm/                 Pluggable provider layer
│   ├── base.py          LLMProvider interface + registry
│   ├── anthropic_provider.py   Claude via the official Anthropic SDK
│   ├── mock.py          Deterministic mock (runs the heuristic, optional fault injection)
│   └── cache.py         SQLite response cache keyed by the full decision context
├── simulation.py        The round loop that wires engine + agents + event store
├── analysis.py          Post-run metrics computed only from the event log
└── api/                 FastAPI app (REST + WebSocket) and run controller
frontend/src/            React + TypeScript + Recharts dashboard
```

The engine never imports agents or LLM code, and the LLM layer never imports the
engine. `simulation.py` is the only module that knows about all three, and the API
drives a `Simulation` only through `step()` and `queue_policy()`.

## The round loop

Each call to `Simulation.step()` runs one round:

1. **Start.** Per-round demand and capacity reset, and income is paid.
2. **Policy.** Scheduled policy actions for this round run first, then actions queued
   from the UI, then regulator agents (heuristic or LLM). Every action is clamped to
   `policy.bounds`.
3. **Revalidation.** Resting orders that are no longer legal or affordable are trimmed.
   This only matters when `order_ttl > 1` or a policy changed.
4. **Talk** (optional). Participants of `market.communication` post one message each to
   a shared channel. Messages are visible to participants from then on.
5. **Decide.** All traders decide in parallel, limited by `llm.max_concurrency`. Each
   decision is an LLM call (or a heuristic) that returns
   `{"reasoning": "...", "actions": [{"type": "bid|ask|cancel|hold", ...}]}`.
6. **Validate and sanitise.** Bad JSON, unknown goods, non-positive prices or
   quantities, unaffordable bids, asks beyond inventory or capacity, price-control
   violations and order-count limits are rejected or trimmed. Each fix is logged.
7. **Match.** Cancels run first. New orders are shuffled with a seeded RNG and fed into
   a continuous double auction. Trades execute at the resting order's price.
8. **Settle.** Cash, inventory, cost basis, taxes and subsidies move. Surplus is
   recorded.
9. **Record.** The book depth is snapshotted, expired orders are dropped, and a
   `round_summary` event is appended (prices, volume, depth, surplus, DWL, wealth…).

**Determinism.** Agent setup, order arrival order and heuristic fallbacks all use RNGs
derived from `(seed, round, agent_id, purpose)`. They never depend on the order in which
concurrent LLM calls complete. Event logs have no wall-clock timestamps, so two runs
with the same seed and the same LLM answers produce byte-identical `events.jsonl`. A
test checks this.

## Economic model and accounting

The model uses induced values, as in Vernon Smith's market experiments:

| Role | Private information | Constraint | Objective |
|---|---|---|---|
| **Buyer** | valuation per good (optionally declining per unit by `valuation_decay`) | cash budget, `units_per_round` | consumer surplus = value − price paid |
| **Seller** | marginal cost per good (`cost + cost_slope·k` for the k-th unit) | `capacity` per round, must be able to fund production | profit = net price − cost |
| **Speculator** | none | cash, inventory (no short selling) | wealth (cash + inventory at market) |
| **Regulator** | sees fundamentals and aggregates | `policy.bounds` | its `objective` prompt |

- Buyers consume what they buy. Sellers produce to order and pay the marginal cost out
  of the economy at sale. Speculators carry inventory at average cost.
- Taxes are **per-unit** or **ad valorem**, remitted by the `buyer` or the `seller`.
  Subsidies are paid to sellers per unit. Revenue and outlays go to a government account.
- The **fundamental value** of a good is the competitive equilibrium price of the
  induced supply and demand schedules (`fundamental_value: auto`). You can also give a
  constant or a `fundamental_schedule`.
- **Realised total surplus** = consumer surplus + producer profit + realised speculator
  P&L + tax revenue − subsidies. **Deadweight loss** = efficient (untaxed competitive)
  surplus − realised surplus. The summary also reports the *theoretical* DWL implied by
  the current policy, so you can separate the tax wedge from trading inefficiency.

## Experiments

| File | Studies | Main knobs |
|---|---|---|
| `bubble.yaml` | Momentum speculators with delayed information push the price above fundamental, then it crashes | speculator `count` (density), `info_lag`, `strategy.params` (window, aggressiveness) |
| `cartel.yaml` | Four producers with a pre-round chat channel; explicit price signalling | `market.communication`, `strategy.params.explicit` |
| `cartel_tacit.yaml` | Control: same market, but messages never mention prices | compare with `cartel.yaml` on the Compare page |
| `taxation.yaml` | Untaxed, then a per-unit excise, then an ad valorem sales tax, then repeal | `policy.schedule` |
| `baseline.yaml` | Two goods, fundamentalist traders, LLM regulator | starting point for new designs |

With the mock LLM, the bundled experiments reproduce the target phenomena. The bubble
config peaks around round 14 at about +75% over fundamental and crashes by round 17.
The cartel config reaches a collusion index of about 0.84; the tacit control reaches
about 0.40 with ask prices converging. The excise cuts volume from about 30 to 22
units per round, and DWL rises. With real LLMs the outcomes come from the models'
own reasoning, which is the point of the simulator.

**Cost note.** Every LLM agent makes one call per round, and the default model is
`claude-opus-5-5` at `effort: low`. For large populations, either keep the crowd on
`decision: heuristic` and make only the agents you study LLM-driven, or set
`llm.model` to a cheaper model.

## How to define a new agent strategy

A strategy is a heuristic plus the prompt guidance an LLM agent receives. The heuristic
is used three ways: as the policy for `decision: heuristic` agents, as the fallback
when an LLM call fails, and as the brain of the mock provider.

1. Add a function to `backend/market_sim/agents/strategies.py`, or to any module
   imported at startup:

```python
from market_sim.agents.strategies import strategy, message_for

@strategy(
    "contrarian", roles=("speculator",),
    prompt="Bet against the crowd: buy after sharp drops and sell into rallies.",
)
def contrarian(obs, params, rng):
    actions = []
    for good, m in obs["market"].items():
        prices = [p for p in m["prices"] if p]
        if len(prices) < 3:
            continue
        change = prices[-1] / prices[-3] - 1
        if change < -params.get("trigger", 0.05):
            qty = int(obs["you"]["available_cash"] * 0.2 // prices[-1])
            if qty:
                actions.append({"type": "bid", "good": good, "price": round(prices[-1] * 1.01, 2), "quantity": qty})
        elif change > params.get("trigger", 0.05) and obs["you"]["inventory"].get(good, 0):
            actions.append({"type": "ask", "good": good, "price": round(prices[-1] * 0.99, 2),
                            "quantity": obs["you"]["inventory"][good]})
    return {"reasoning": "Fading the last move.", "actions": actions or [{"type": "hold"}]}

# Optional: what this agent says in the communication channel
@message_for("contrarian")
def contrarian_msg(obs, params, rng):
    return "Everyone is too bullish."
```

2. Use it in an experiment. `strategy.prompt` overrides the default guidance, and
   `params` are passed to the heuristic:

```yaml
- role: speculator
  count: 5
  decision: llm            # or heuristic
  strategy:
    name: contrarian
    prompt: "You are a deep-value contrarian investor."
    params: {trigger: 0.08}
```

The observation `obs` is exactly what the LLM sees: `you` (cash, available_cash,
inventory, cost_basis, per-good `unit_values` / `unit_costs`, open orders), `market`
(lagged by `info_lag`: last price, VWAP, price/volume history, best bid/ask, top-5
depth), `policy`, `memory` and, for channel participants, `channel`. Whatever a
strategy returns goes through the same validation and sanitisation as LLM output, so
a buggy heuristic cannot break market invariants.

## How to define a new good

Add an entry under `goods`, then give agents values and costs for it:

```yaml
goods:
  - id: lumber
    name: Lumber
    tick: 0.05                 # price increment
    initial_price: 40          # first reference price shown to agents
    fundamental_value: auto    # or a number, e.g. 42.5
    fundamental_schedule:      # optional: piecewise-constant override, e.g. a demand shock
      - {round: 30, value: 55}

agents:
  - role: buyer
    valuations: {lumber: {dist: uniform, low: 20, high: 70}}
  - role: seller
    costs: {lumber: {dist: normal, mean: 30, std: 5, min: 5}}
    capacity: {lumber: 6}
  - role: speculator
    inventory: {lumber: 10}
```

Each good gets its own order book, policy slot, equilibrium benchmark and dashboard
charts (use the good selector on the Live page). Distributions can be `fixed`
(or a bare number), `uniform`, `normal` (with an optional `min`) or `choice`. Values
are drawn once per agent from the seeded RNG. The config validator rejects references
to unknown goods.

## How to define a new experiment

Drop a `.yaml` (or `.json`) file into `backend/experiments/`. It appears in the UI's
experiment picker straight away. Full schema: `backend/market_sim/config.py`.

```yaml
name: price_ceiling_shock
description: A binding rent-control style ceiling at round 25.
experiment_type: taxation         # generic | bubble | cartel | taxation (labelling only)
seed: 2024
rounds: 50
round_delay_ms: 300               # live-UI pacing (changeable at runtime)

goods:
  - {id: housing, initial_price: 100}

market:
  order_ttl: 1                    # rounds an order rests in the book
  max_orders_per_agent: 4
  max_quantity_per_order: 20
  communication: {enabled: false}

llm:
  provider: mock                  # mock | anthropic | <your provider>
  model: claude-opus-5-5
  effort: low
  max_concurrency: 8
  timeout_s: 30
  max_retries: 2
  cache: {mode: record}           # off | record | replay | strict_replay

agents:
  - {role: buyer, count: 25, cash: 3000, income_per_round: 200, units_per_round: 1,
     valuations: {housing: {dist: uniform, low: 60, high: 180}}}
  - {role: seller, count: 8, decision: heuristic,
     costs: {housing: {dist: uniform, low: 50, high: 140}}, capacity: {housing: 3}}
  - {role: regulator, count: 1, decision: schedule}   # schedule | heuristic | llm

policy:
  schedule:
    - {round: 25, action: set_price_ceiling, good: housing, value: 90}
  bounds: {max_per_unit_tax: 50, max_ad_valorem: 0.5, max_subsidy: 30}
```

Policy actions are `set_tax` (`tax_type`, `rate`, `payer`), `remove_tax`,
`set_subsidy` (`value`), `set_price_floor` / `set_price_ceiling` (`value`),
`clear_controls` and `hold`. Leave out `good` to target every good. The same
actions are available from the UI mid-run (they apply at the next round) and to LLM
regulators.

## LLM providers, retries, fallbacks and replay

- **Interface.** `llm/base.py` defines `LLMProvider.complete(LLMRequest) -> LLMResponse`.
  To add a provider (for example a local model server):

  ```python
  from market_sim.llm.base import LLMProvider, LLMRequest, LLMResponse, LLMError, register_provider

  @register_provider("my_provider")
  class MyProvider(LLMProvider):
      async def complete(self, request: LLMRequest) -> LLMResponse:
          text = await call_my_model(request.system, request.user)   # raise LLMError on failure
          return LLMResponse(text=text, model=request.model)
  ```

  Import the module in `llm/base.py:create_provider` (or anywhere at startup), then set
  `llm.provider: my_provider`. Providers only return text. Parsing, validation,
  retries, caching and fallbacks are shared.
- **Claude.** `anthropic_provider.py` uses the official `anthropic` SDK. It sends a JSON
  schema for structured output, sets `output_config.effort`, and turns on the
  server-side refusal fallback (`fallbacks: "default"`) on models that support it. Turn
  that off with `llm.options.fallbacks: null`. A refusal or empty answer counts as a
  failed attempt.
- **Concurrency, timeouts, retries.** Calls run concurrently, limited by
  `llm.max_concurrency`. Each attempt is bounded by `timeout_s`. Errors, timeouts and
  unparseable answers are retried `max_retries` times with exponential backoff. After
  that, the agent's heuristic decides instead (`source: fallback` in the log), so the
  simulation keeps going.
- **Cache and replay.** Responses are cached in SQLite under a SHA-256 of
  provider + model + sampling settings + system prompt + user prompt. The user prompt
  holds the agent's whole observation, so a cache hit means the agent saw the same world.
  - `record` (default): call the LLM and store the answer.
  - `replay`: serve from the cache and call the LLM only on a miss.
  - `strict_replay`: serve from the cache and use the heuristic on a miss. No network calls.

  The **Replay** button on a finished run (or `POST /api/runs/{id}/replay`) re-runs the
  same config and seed in `replay` mode and reproduces the original run exactly.
- **Mock provider.** Runs the strategy heuristic with an RNG seeded from the prompt.
  For testing it can inject failures:
  `llm.options: {failure_rate: 0.1, malformed_rate: 0.05, hang_rate: 0.02, latency_ms: 20}`.

## Metrics

All metrics are computed from the event log (`analysis.py`), so live runs, finished
runs and replays are analysed the same way.

| Metric | Definition |
|---|---|
| Total surplus | Σ consumer surplus + producer profit + realised speculator P&L + taxes − subsidies |
| Deadweight loss | efficient competitive surplus − realised surplus (per round, summed) |
| Theoretical DWL | efficient surplus − surplus at the equilibrium under the active policy |
| Efficiency | realised / efficient surplus |
| HHI | Σ (100 · seller share of units sold)², 0–10,000; reported for all sellers and for producers only |
| Volatility | σ of log returns of round VWAPs |
| Bubble | RD, RAD (mean signed / absolute relative deviation from fundamental), max deviation, start round (> +15%), peak round, crash round (first close ≤ 80% of peak), crash depth |
| Cartel | markup over marginal cost, Lerner index, markup over competitive price, **collusion index** = (P − P_competitive)/(P_monopoly − P_competitive) over the last third of the run, ask-price dispersion early vs late, count of explicit coordination messages (regex for coordination language plus a price or "undercut"), verdict: competitive / supra-competitive / tacit collusion / explicit coordination |
| Taxation | for every policy change: 10-round before/after windows of volume, price, consumer price, total, consumer and producer surplus, revenue, realised and theoretical DWL |
| Wealth | final Gini, mean wealth by role |

## API

| Method | Path | |
|---|---|---|
| GET | `/api/experiments` | list experiment files |
| POST | `/api/runs` | `{experiment, overrides?, autostart?}` or `{config}` → run status |
| GET | `/api/runs` | runs on disk + live status |
| POST | `/api/runs/{id}/start` · `pause` · `step` · `stop` | lifecycle |
| POST | `/api/runs/{id}/speed` | `{delay_ms}` |
| POST | `/api/runs/{id}/policy` | a policy action, applied at the next round |
| POST | `/api/runs/{id}/replay` | re-run from the LLM cache |
| GET | `/api/runs/{id}/history` | config, benchmarks, round summaries, recent log |
| GET | `/api/runs/{id}/events?after=&types=` | raw events |
| GET | `/api/runs/{id}/summary` | post-run metrics |
| GET | `/api/compare?a=&b=` | side-by-side metrics and series |
| WS | `/api/runs/{id}/ws` | `init` (history) followed by streamed `event` and `status` messages |

Interactive docs: http://localhost:8000/docs.

## Tests

```bash
cd backend && pytest
```

- `test_orderbook.py`: price-time priority, partial fills, resting-price execution,
  self-trade prevention, cancel/expiry/depth, determinism.
- `test_settlement.py`: transfers, per-unit and ad valorem taxes with either payer,
  subsidies, declining values and rising costs, speculator cost basis and P&L,
  guards against overdraft, over-delivery and short selling.
- `test_policy_economics.py`: equilibrium, tax wedge and DWL, incidence invariance,
  price floors and ceilings, monopoly price, policy bounds.
- `test_actions.py`: JSON extraction, action coercion, sanitisation (cash, demand,
  capacity, inventory, tax-inclusive affordability, price controls, cancels).
- `test_integration.py`: short simulations with the mock LLM: reproducibility,
  invariants (no negative cash or inventory, cash conservation), fallback on failure,
  retry on timeout or malformed output, record → strict-replay equivalence, hostile LLM
  output, policy schedule plus mid-run policy, communication channel, and the bundled
  experiments producing a bubble, cartel signalling and tax effects.
- `test_api.py`: REST lifecycle, WebSocket stream, compare, replay.

The engine and settlement tests never touch an LLM. The integration tests use only
the mock provider.
