"""The round loop: observe -> (talk) -> decide -> validate -> match -> settle -> record.

``Simulation`` wires the engine (books, settlement, policy), the agents (strategies,
prompts, decision runner) and the event store together. It is UI-agnostic: the API
layer drives it through :meth:`step` and :meth:`queue_policy`.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .agents.actions import (
    DECISION_SCHEMA, POLICY_SCHEMA, coerce_policy_action, committed_cash, sanitize_trade_actions,
)
from .agents.decision import MESSAGE_SCHEMA, DecisionEngine, DecisionOutcome, DecisionTask
from .agents import prompts
from .agents.strategies import get_strategy
from .config import ExperimentConfig, PolicyAction
from .engine.economics import Equilibrium, demand_schedule, equilibrium, monopoly_outcome, supply_schedule
from .engine.events import EventStore
from .engine.orderbook import OrderBook, Trade
from .engine.policy import PolicyState
from .engine.settlement import Government, SettlementError, SettlementRecord, settle
from .engine.state import AgentState
from .llm.base import LLMProvider, create_provider
from .llm.cache import ResponseCache
from .rng import derive_rng, derive_seed, draw

_ROLE_PREFIX = {"buyer": "B", "seller": "S", "speculator": "X", "regulator": "G"}
_HISTORY = 12  # price points shown to agents


def new_run_id(name: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{name}-{uuid.uuid4().hex[:6]}"


class Simulation:
    def __init__(
        self,
        config: ExperimentConfig,
        run_id: Optional[str] = None,
        provider: Optional[LLMProvider] = None,
        persist: bool = True,
        cache: Optional[ResponseCache] = None,
    ) -> None:
        self.config = config
        self.run_id = run_id or new_run_id(config.name)
        self.round = 0
        self.finished = False
        self.goods = [g.id for g in config.goods]
        self.ticks = {g.id: g.tick for g in config.goods}
        self.books = {g: OrderBook(g) for g in self.goods}
        self.policy = PolicyState(self.goods, config.policy.bounds)
        self.gov = Government()
        self.agents: dict[str, AgentState] = {}
        self.channel: list[dict[str, Any]] = []
        self.pending_policy: list[tuple[PolicyAction, str]] = []
        self.summaries: list[dict[str, Any]] = []
        self._setup_agents()

        self.run_dir: Optional[Path] = Path(config.runs_dir) / self.run_id if persist else None
        self.store = EventStore(self.run_dir / "events.jsonl" if self.run_dir else None)
        if self.run_dir:
            (self.run_dir / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
            self._write_meta(status="created")

        if cache is None and config.llm.cache.mode != "off":
            cache = ResponseCache(config.llm.cache.path)
        self._owns_cache = cache is not None
        self.cache = cache
        self.provider = provider or create_provider(config.llm)
        self.decisions = DecisionEngine(config.llm, self.provider, cache)

        # Static schedules -> theoretical benchmarks
        traders = list(self.agents.values())
        self.demand = {g: demand_schedule(traders, g) for g in self.goods}
        self.supply = {g: supply_schedule(traders, g) for g in self.goods}
        self.competitive = {g: equilibrium(self.demand[g], self.supply[g]) for g in self.goods}
        self.monopoly = {g: monopoly_outcome(self.demand[g], self.supply[g]) for g in self.goods}

        init = {}
        for gc in config.goods:
            init[gc.id] = {
                "last_price": gc.initial_price, "prices": [gc.initial_price], "volumes": [0], "vwap": None,
                "best_bid": None, "best_ask": None, "depth": {"bids": [], "asks": []}, "volume": 0,
            }
        self.snapshots: list[dict[str, dict[str, Any]]] = [init]

        self.store.append("run_started", 0, {
            "run_id": self.run_id,
            "config": json.loads(config.model_dump_json()),
            "agents": [self._agent_meta(a) for a in self.agents.values()],
            "benchmarks": {g: {
                "competitive": self.competitive[g].to_dict(),
                "monopoly": self.monopoly[g].to_dict(),
                "demand": self.demand[g], "supply": self.supply[g],
            } for g in self.goods},
        })

    # ------------------------------------------------------------------ setup
    def _setup_agents(self) -> None:
        cfg = self.config
        counters: dict[str, int] = {}
        for gi, grp in enumerate(cfg.agents):
            prefix = grp.id_prefix or _ROLE_PREFIX[grp.role]
            strat = get_strategy(grp.strategy.name, grp.role)
            for _ in range(grp.count):
                counters[prefix] = counters.get(prefix, 0) + 1
                aid = f"{prefix}{counters[prefix]:02d}"
                rng = derive_rng(cfg.seed, "setup", aid)
                a = AgentState(
                    id=aid, role=grp.role, cash=draw(grp.cash, rng),
                    income_per_round=grp.income_per_round, info_lag=grp.info_lag,
                    group=f"{grp.role}:{gi}", decision=grp.decision, strategy_name=strat.name,
                    strategy_prompt=grp.strategy.prompt or strat.prompt,
                    strategy_params=dict(grp.strategy.params), objective=grp.objective,
                    decision_interval=max(1, grp.decision_interval),
                    units_per_round=grp.units_per_round, valuation_decay=grp.valuation_decay,
                    cost_slope=grp.cost_slope,
                )
                a.memory = type(a.memory)(maxlen=max(1, grp.memory_size))
                for g, d in grp.valuations.items():
                    a.valuations[g] = draw(d, rng)
                for g, d in grp.costs.items():
                    a.costs[g] = draw(d, rng)
                for g, cap in grp.capacity.items():
                    a.capacity[g] = int(cap)
                for g, d in grp.inventory.items():
                    a.inventory[g] = int(draw(d, rng))
                if grp.role == "speculator":
                    init_price = {gc.id: gc.initial_price for gc in cfg.goods}
                    for g, q in a.inventory.items():
                        a.cost_basis[g] = init_price[g] if q else 0.0
                a.initial_wealth = a.wealth({gc.id: gc.initial_price for gc in cfg.goods})
                self.agents[aid] = a

    @staticmethod
    def _agent_meta(a: AgentState) -> dict[str, Any]:
        return {
            "id": a.id, "role": a.role, "group": a.group, "decision": a.decision, "strategy": a.strategy_name,
            "cash": a.cash, "inventory": dict(a.inventory), "valuations": dict(a.valuations),
            "costs": dict(a.costs), "capacity": dict(a.capacity), "units_per_round": a.units_per_round,
            "info_lag": a.info_lag,
        }

    # ------------------------------------------------------------- accessors
    @property
    def traders(self) -> list[AgentState]:
        return [a for a in self.agents.values() if a.role != "regulator"]

    @property
    def regulators(self) -> list[AgentState]:
        return [a for a in self.agents.values() if a.role == "regulator"]

    def fundamental(self, good: str, round_no: Optional[int] = None) -> Optional[float]:
        r = self.round if round_no is None else round_no
        gc = next(g for g in self.config.goods if g.id == good)
        value: Optional[float] = None if gc.fundamental_value == "auto" else float(gc.fundamental_value)
        for entry in sorted(gc.fundamental_schedule, key=lambda e: e["round"]):
            if entry["round"] <= r:
                value = float(entry["value"])
        if value is None:
            value = self.competitive[good].price
        return value

    def last_prices(self) -> dict[str, float]:
        return {g: self.snapshots[-1][g]["last_price"] for g in self.goods}

    # ------------------------------------------------------------ observation
    def _market_view(self, lag: int, with_fundamental: bool = False) -> dict[str, Any]:
        idx = max(0, len(self.snapshots) - 1 - lag)
        snap = self.snapshots[idx]
        view = {}
        for g in self.goods:
            s = snap[g]
            view[g] = {
                "last_price": s["last_price"], "vwap": s["vwap"], "best_bid": s["best_bid"], "best_ask": s["best_ask"],
                "prices": s["prices"][-_HISTORY:], "volumes": s["volumes"][-_HISTORY:],
                "depth": {"bids": s["depth"]["bids"][:5], "asks": s["depth"]["asks"][:5]},
                "as_of_round": idx,
            }
            if with_fundamental:
                view[g]["fundamental"] = self.fundamental(g)
        return view

    def observe(self, agent: AgentState) -> dict[str, Any]:
        r = self.round
        goods_info: dict[str, Any] = {}
        if agent.role == "buyer":
            for g in agent.valuations:
                need = agent.demand_remaining(g)
                goods_info[g] = {"remaining_demand": need,
                                 "unit_values": [agent.next_unit_value(g, k) for k in range(need)]}
        elif agent.role == "seller":
            last = self.summaries[-1]["goods"] if self.summaries else {}
            for g in agent.costs:
                cap = agent.capacity_remaining(g)
                goods_info[g] = {
                    "capacity_remaining": cap,
                    "unit_costs": [agent.next_unit_cost(g, k) for k in range(cap)],
                    "sold_last_round": last.get(g, {}).get("seller_volumes", {}).get(agent.id, 0),
                }
        open_orders = [
            {"id": o.id, "good": o.good, "side": o.side, "price": o.price, "remaining": o.remaining}
            for b in self.books.values() for o in b.orders_for(agent.id)
        ]
        obs: dict[str, Any] = {
            "round": r,
            "rounds_total": self.config.rounds,
            "info_lag": agent.info_lag,
            "you": {
                "id": agent.id, "role": agent.role, "cash": round(agent.cash, 2),
                "available_cash": round(agent.cash - committed_cash(agent, self.books, self.policy), 2),
                "inventory": dict(agent.inventory),
                "cost_basis": {g: round(v, 2) for g, v in agent.cost_basis.items()},
                "goods": goods_info, "open_orders": open_orders,
                "results_so_far": {
                    "consumer_surplus": round(agent.consumer_surplus, 2),
                    "producer_profit": round(agent.producer_profit, 2),
                    "trading_pnl": round(agent.trading_pnl, 2),
                },
            },
            "market": self._market_view(agent.info_lag),
            "policy": self.policy.snapshot(),
            "memory": list(agent.memory),
        }
        comm = self.config.market.communication
        if comm.enabled and agent.role in comm.participants:
            lo = r - comm.history_rounds + 1
            obs["channel"] = [m for m in self.channel if m["round"] >= lo]
        return obs

    def observe_regulator(self, agent: AgentState) -> dict[str, Any]:
        recent = self.summaries[-5:]
        return {
            "round": self.round,
            "rounds_total": self.config.rounds,
            "market": self._market_view(0, with_fundamental=True),
            "policy": self.policy.snapshot(),
            "recent_rounds": [
                {"round": s["round"], **{g: {k: s["goods"][g][k] for k in (
                    "close", "volume", "realized_surplus", "dwl", "tax")} for g in self.goods}}
                for s in recent
            ],
            "government": {"tax_revenue": round(self.gov.tax_revenue, 2), "subsidies_paid": round(self.gov.subsidies_paid, 2)},
            "memory": list(agent.memory),
        }

    # ----------------------------------------------------------------- policy
    def queue_policy(self, action: PolicyAction, source: str = "user") -> None:
        self.pending_policy.append((action, source))

    def _apply_policy(self, action: PolicyAction, source: str) -> Optional[dict[str, Any]]:
        clean, err = self.policy.sanitize(action)
        if clean is None:
            self.store.append("policy_rejected", self.round, {"source": source, "action": action.model_dump(), "reason": err})
            return None
        if clean.action == "hold":
            return None
        self.policy.apply(clean)
        data = {"source": source, "action": clean.model_dump(), "policy": self.policy.snapshot()}
        self.store.append("policy_change", self.round, data)
        desc = _describe_policy(clean)
        for a in self.agents.values():
            a.remember(f"R{self.round}: government {desc}")
        return data

    async def _policy_phase(self) -> None:
        r = self.round
        for p in self.config.policy.schedule:
            if p.round == r:
                self._apply_policy(PolicyAction(**p.model_dump(exclude={"round"})), "schedule")
        while self.pending_policy:
            action, source = self.pending_policy.pop(0)
            self._apply_policy(action, source)

        tasks = []
        for reg in self.regulators:
            if reg.decision == "schedule" or (r - 1) % reg.decision_interval != 0:
                continue
            obs = self.observe_regulator(reg)
            strat = get_strategy(reg.strategy_name, "regulator")
            params = {"threshold": self.config.policy.stabilizer_threshold, "tax": self.config.policy.stabilizer_tax,
                      **reg.strategy_params}
            tasks.append(DecisionTask(
                agent_id=reg.id, kind="policy", use_llm=reg.decision == "llm",
                system=prompts.regulator_system_prompt(reg.objective, self.config.policy.bounds),
                user=prompts.trader_user_prompt(obs, reg.strategy_prompt),
                heuristic=_bind(strat.decide, obs, params), rng_seed=derive_seed(self.config.seed, r, reg.id, "policy"),
                schema=POLICY_SCHEMA,
            ))
        for out in await self.decisions.run(tasks):
            applied, rejected = [], []
            for raw in out.payload.get("actions", [])[:4]:
                try:
                    act = coerce_policy_action(raw)
                except Exception as exc:  # noqa: BLE001
                    rejected.append({"action": raw, "reason": str(exc).splitlines()[0]})
                    continue
                if act.action != "hold" and self._apply_policy(act, f"regulator:{out.agent_id}"):
                    applied.append(act.model_dump())
            self._log_decision(out, {"applied": applied, "rejected": rejected})

    # ----------------------------------------------------------- revalidation
    def _revalidate_orders(self) -> None:
        """Trim resting orders that are no longer legal or affordable (TTL > 1, policy changes)."""
        for a in self.traders:
            for g, book in self.books.items():
                pol = self.policy[g]
                for o in list(book.orders_for(a.id)):
                    if (o.side == "buy" and pol.price_floor is not None and o.price < pol.price_floor) or \
                       (o.side == "sell" and pol.price_ceiling is not None and o.price > pol.price_ceiling):
                        book.cancel(o.id)
                sells = sorted((o for o in book.orders_for(a.id) if o.side == "sell"), key=lambda o: -o.seq)
                excess = sum(o.remaining for o in sells) - a.sellable(g)
                for o in sells:
                    if excess <= 0:
                        break
                    cut = min(excess, o.remaining)
                    book.reduce(o.id, o.remaining - cut)
                    excess -= cut
                if a.role == "buyer":
                    buys = sorted((o for o in book.orders_for(a.id) if o.side == "buy"), key=lambda o: -o.seq)
                    excess = sum(o.remaining for o in buys) - a.demand_remaining(g)
                    for o in buys:
                        if excess <= 0:
                            break
                        cut = min(excess, o.remaining)
                        book.reduce(o.id, o.remaining - cut)
                        excess -= cut
            # cash: drop newest bids until commitments fit
            while committed_cash(a, self.books, self.policy) > a.cash + 1e-9:
                bids = [o for b in self.books.values() for o in b.orders_for(a.id) if o.side == "buy"]
                if not bids:
                    break
                newest = max(bids, key=lambda o: o.seq)
                self.books[newest.good].cancel(newest.id)

    # ----------------------------------------------------------- talk phase
    async def _communication_phase(self) -> None:
        comm = self.config.market.communication
        if not comm.enabled:
            return
        tasks = []
        for a in self.traders:
            if a.role not in comm.participants:
                continue
            obs = self.observe(a)
            strat = get_strategy(a.strategy_name, a.role)
            msg_fn = strat.message
            params = a.strategy_params

            def heuristic(rng, obs=obs, msg_fn=msg_fn, params=params):  # type: ignore[no-untyped-def]
                return {"message": msg_fn(obs, params, rng) if msg_fn else "", "reasoning": ""}

            tasks.append(DecisionTask(
                agent_id=a.id, kind="message", use_llm=a.decision == "llm",
                system=prompts.message_system_prompt(a.role, comm.max_message_chars),
                user=prompts.message_user_prompt(obs, a.strategy_prompt),
                heuristic=heuristic, rng_seed=derive_seed(self.config.seed, self.round, a.id, "message"),
                schema=MESSAGE_SCHEMA,
            ))
        for out in await self.decisions.run(tasks):
            text = " ".join(str(out.payload.get("message") or "").split())[: comm.max_message_chars]
            if text:
                msg = {"round": self.round, "from": out.agent_id, "text": text}
                self.channel.append(msg)
                self.store.append("message", self.round, {**msg, "source": out.source})

    # ----------------------------------------------------------- trade phase
    async def _decision_phase(self) -> list[tuple[AgentState, DecisionOutcome]]:
        cfg = self.config
        tasks = []
        order = []
        for a in self.traders:
            obs = self.observe(a)
            strat = get_strategy(a.strategy_name, a.role)
            tasks.append(DecisionTask(
                agent_id=a.id, kind="trade", use_llm=a.decision == "llm",
                system=prompts.trader_system_prompt(a.role, cfg.market.order_ttl, cfg.market.max_orders_per_agent,
                                                    cfg.market.max_quantity_per_order),
                user=prompts.trader_user_prompt(obs, a.strategy_prompt),
                heuristic=_bind(strat.decide, obs, a.strategy_params),
                rng_seed=derive_seed(cfg.seed, self.round, a.id, "trade"),
                schema=DECISION_SCHEMA,
            ))
            order.append(a)
        outcomes = await self.decisions.run(tasks)
        return list(zip(order, outcomes))

    # ------------------------------------------------------------------ step
    async def step(self) -> dict[str, Any]:
        if self.finished:
            raise RuntimeError("simulation already finished")
        self.round += 1
        r = self.round
        cfg = self.config
        self.store.append("round_started", r, {})
        for a in self.agents.values():
            a.start_round()

        await self._policy_phase()
        self._revalidate_orders()
        await self._communication_phase()
        decided = await self._decision_phase()

        # Validate & sanitise. Cancels apply before any new order is matched.
        new_orders = []
        fills: dict[str, list[str]] = {}
        for a, out in decided:
            res = sanitize_trade_actions(
                out.payload.get("actions", []), a, self.books, self.policy, cfg.market, self.ticks, r,
                id_prefix=f"R{r}-{a.id}",
            )
            for oid in res.cancels:
                for b in self.books.values():
                    b.cancel(oid)
            new_orders.extend(res.orders)
            a.last_reasoning = out.payload.get("reasoning", "")
            self._log_decision(out, res.summary())
            for rej in res.rejected[:2]:
                a.remember(f"R{r}: order rejected ({rej['reason']})")

        # Deterministic random arrival order, then continuous matching.
        derive_rng(cfg.seed, r, "arrival").shuffle(new_orders)
        acc = {g: _Acc() for g in self.goods}
        for o in new_orders:
            for t in self.books[o.good].submit(o, r):
                rec = self._settle(t)
                if rec is None:
                    continue
                acc[t.good].add(t, rec, self.agents[t.seller_id].role)
                fills.setdefault(t.buyer_id, []).append(f"bought {t.quantity} {t.good} @ {t.price:.2f}")
                fills.setdefault(t.seller_id, []).append(f"sold {t.quantity} {t.good} @ {t.price:.2f}")
        for o in new_orders:
            if o.side == "sell" and self.agents[o.agent_id].role == "seller":
                acc[o.good].seller_asks.setdefault(o.agent_id, []).append(o.price)

        for aid, lines in fills.items():
            self.agents[aid].remember(f"R{r}: " + "; ".join(lines[:4]))

        # Snapshot before expiry so the book depth reflects end-of-round liquidity.
        snapshot = {}
        prev = self.snapshots[-1]
        for g in self.goods:
            book, a = self.books[g], acc[g]
            close = a.close if a.volume else prev[g]["last_price"]
            snapshot[g] = {
                "last_price": close, "vwap": a.vwap, "best_bid": book.best_bid(), "best_ask": book.best_ask(),
                "depth": book.depth(15), "volume": a.volume,
                "prices": (prev[g]["prices"] + [close])[-50:], "volumes": (prev[g]["volumes"] + [a.volume])[-50:],
            }
        self.snapshots.append(snapshot)
        for b in self.books.values():
            b.expire(r)

        summary = self._round_summary(acc, snapshot)
        self.summaries.append(summary)
        self.store.append("round_summary", r, summary)
        if r >= cfg.rounds:
            self.finish()
        return summary

    def _settle(self, t: Trade) -> Optional[SettlementRecord]:
        try:
            rec = settle(t, self.agents[t.buyer_id], self.agents[t.seller_id], self.policy[t.good], self.gov)
        except SettlementError as exc:  # pragma: no cover - guarded by sanitisation; logged if it ever happens
            self.store.append("settlement_error", self.round, {"trade": t.to_dict(), "error": str(exc)})
            return None
        self.store.append("trade", self.round, {**rec.to_dict(), "aggressor": t.aggressor,
                                                "buy_order_id": t.buy_order_id, "sell_order_id": t.sell_order_id})
        return rec

    def _log_decision(self, out: DecisionOutcome, result: dict[str, Any]) -> None:
        a = self.agents[out.agent_id]
        self.store.append("decision", self.round, {
            "agent": a.id, "role": a.role, "kind": out.kind, "source": out.source, "attempts": out.attempts,
            "errors": out.errors, "reasoning": str(out.payload.get("reasoning", ""))[:600],
            "actions": out.payload.get("actions", []), "result": result,
            "raw": (out.raw or "")[:2000] if out.source in ("llm", "cache") else None,
        })

    def _round_summary(self, acc: dict[str, "_Acc"], snapshot: dict[str, Any]) -> dict[str, Any]:
        goods: dict[str, Any] = {}
        prices = {g: snapshot[g]["last_price"] for g in self.goods}
        for g in self.goods:
            a, s, pol = acc[g], snapshot[g], self.policy[g]
            eq: Equilibrium = equilibrium(self.demand[g], self.supply[g], pol)
            efficient = self.competitive[g].surplus
            realized = a.surplus
            ask_means = {sid: round(sum(v) / len(v), 4) for sid, v in a.seller_asks.items()}
            goods[g] = {
                "open": a.open, "high": a.high, "low": a.low, "close": s["last_price"], "vwap": a.vwap,
                "volume": a.volume, "trades": a.trades,
                "best_bid": s["best_bid"], "best_ask": s["best_ask"],
                "spread": round(s["best_ask"] - s["best_bid"], 4) if s["best_bid"] and s["best_ask"] else None,
                "depth": s["depth"],
                "fundamental": self.fundamental(g),
                "equilibrium_price": eq.price, "equilibrium_qty": eq.quantity,
                "competitive_price": self.competitive[g].price, "competitive_qty": self.competitive[g].quantity,
                "monopoly_price": self.monopoly[g].price,
                "efficient_surplus": round(efficient, 4), "policy_surplus": round(eq.surplus, 4),
                "realized_surplus": round(realized, 4), "dwl": round(efficient - realized, 4),
                "efficiency": round(realized / efficient, 4) if efficient > 0 else None,
                "buyer_surplus": round(a.buyer_surplus, 4), "producer_profit": round(a.producer_profit, 4),
                "trading_pnl": round(a.trading_pnl, 4), "tax": round(a.tax, 4), "subsidy": round(a.subsidy, 4),
                "producer_revenue": round(a.producer_revenue, 4), "production_cost": round(a.production_cost, 4),
                "producer_units": a.producer_units, "consumed_units": a.consumed_units,
                "buyer_price": round(a.buyer_paid / a.consumed_units, 4) if a.consumed_units else None,
                "seller_volumes": a.seller_volumes, "seller_asks": ask_means,
                "policy": pol.to_dict(),
            }
        wealth = [{"id": ag.id, "role": ag.role, "wealth": round(ag.wealth(prices), 2), "cash": round(ag.cash, 2),
                   "inventory": sum(ag.inventory.values())} for ag in self.traders]
        return {
            "round": self.round, "goods": goods, "wealth": wealth,
            "government": {"tax_revenue": round(self.gov.tax_revenue, 4), "subsidies_paid": round(self.gov.subsidies_paid, 4),
                           "balance": self.gov.balance},
            "llm": {k: v for k, v in self.decisions.stats.to_dict().items() if k != "avg_latency_ms"},
        }

    # ------------------------------------------------------------------- run
    async def run(self, rounds: Optional[int] = None) -> list[dict[str, Any]]:
        target = self.config.rounds if rounds is None else min(self.config.rounds, self.round + rounds)
        out = []
        while not self.finished and self.round < target:
            out.append(await self.step())
        return out

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        prices = self.last_prices()
        self.store.append("run_finished", self.round, {
            "rounds": self.round,
            "agents": [a.public_view(prices) for a in self.agents.values()],
            "government": {"tax_revenue": self.gov.tax_revenue, "subsidies_paid": self.gov.subsidies_paid},
        })
        self._write_meta(status="finished")

    async def close(self) -> None:
        self.store.close()
        await self.provider.aclose()
        if self._owns_cache and self.cache is not None:
            self.cache.close()

    def _write_meta(self, status: str) -> None:
        if not self.run_dir:
            return
        meta = {
            "run_id": self.run_id, "name": self.config.name, "experiment_type": self.config.experiment_type,
            "description": self.config.description, "seed": self.config.seed, "rounds": self.config.rounds,
            "rounds_completed": self.round, "status": status, "updated_at": time.time(),
            "llm_provider": self.config.llm.provider, "llm_model": self.config.llm.model,
            "llm_stats": self.decisions.stats.to_dict() if hasattr(self, "decisions") else {},
        }
        (self.run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def state(self) -> dict[str, Any]:
        prices = self.last_prices()
        return {
            "run_id": self.run_id, "round": self.round, "rounds": self.config.rounds, "finished": self.finished,
            "goods": [g.model_dump() for g in self.config.goods],
            "policy": self.policy.snapshot(),
            "agents": [a.public_view(prices) for a in self.agents.values()],
            "books": {g: b.depth(15) for g, b in self.books.items()},
            "channel": self.channel[-30:],
            "llm": self.decisions.stats.to_dict(),
        }


class _Acc:
    """Per-good, per-round accumulator for trades and settlements."""

    def __init__(self) -> None:
        self.open = self.high = self.low = self.close = None  # type: Optional[float]
        self.volume = self.trades = 0
        self.value = 0.0
        self.buyer_surplus = self.producer_profit = self.trading_pnl = 0.0
        self.tax = self.subsidy = 0.0
        self.producer_revenue = self.production_cost = 0.0
        self.producer_units = self.consumed_units = 0
        self.buyer_paid = 0.0
        self.seller_volumes: dict[str, int] = {}
        self.seller_asks: dict[str, list[float]] = {}

    def add(self, t: Trade, rec: SettlementRecord, seller_role: str) -> None:
        p = t.price
        self.open = p if self.open is None else self.open
        self.high = p if self.high is None else max(self.high, p)
        self.low = p if self.low is None else min(self.low, p)
        self.close = p
        self.volume += t.quantity
        self.trades += 1
        self.value += p * t.quantity
        self.buyer_surplus += rec.buyer_surplus
        self.producer_profit += rec.producer_profit
        self.trading_pnl += rec.trading_pnl
        self.tax += rec.tax
        self.subsidy += rec.subsidy
        if rec.consumed_value:
            self.consumed_units += t.quantity
            self.buyer_paid += rec.buyer_paid
        if seller_role == "seller":
            self.producer_revenue += p * t.quantity
            self.production_cost += rec.production_cost
            self.producer_units += t.quantity
        self.seller_volumes[t.seller_id] = self.seller_volumes.get(t.seller_id, 0) + t.quantity

    @property
    def vwap(self) -> Optional[float]:
        return round(self.value / self.volume, 4) if self.volume else None

    @property
    def surplus(self) -> float:
        return self.buyer_surplus + self.producer_profit + self.trading_pnl + self.tax - self.subsidy


def _bind(fn, obs, params):  # type: ignore[no-untyped-def]
    return lambda rng: fn(obs, params, rng)


def _describe_policy(a: PolicyAction) -> str:
    g = a.good or "all goods"
    if a.action == "set_tax":
        rate = f"{a.rate:.0%}" if a.tax_type == "ad_valorem" else f"{a.rate:.2f}/unit"
        return f"set {a.tax_type.replace('_', ' ')} tax {rate} on {g} ({a.payer} pays)"
    if a.action == "remove_tax":
        return f"removed taxes on {g}"
    if a.action == "set_subsidy":
        return f"set subsidy {a.value:.2f}/unit to sellers of {g}"
    if a.action == "set_price_floor":
        return f"set price floor {a.value} on {g}"
    if a.action == "set_price_ceiling":
        return f"set price ceiling {a.value} on {g}"
    if a.action == "clear_controls":
        return f"cleared price controls on {g}"
    return a.action
