"""End-to-end runs with the mock LLM (no network)."""

import asyncio
import json
from pathlib import Path

import pytest

from market_sim.analysis import load_events, summarize
from market_sim.config import PolicyAction, load_config
from market_sim.llm.base import LLMError, LLMProvider, LLMResponse
from market_sim.simulation import Simulation

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"


def run_sim(cfg, **kw):
    async def go():
        sim = Simulation(cfg, **kw)
        await sim.run()
        await sim.close()
        return sim
    return asyncio.run(go())


def strip(events):
    return [json.dumps(e, sort_keys=True) for e in events]


def test_short_run_completes_and_logs(make_config):
    sim = run_sim(make_config())
    types = {e["type"] for e in sim.store.events}
    assert {"run_started", "round_started", "decision", "trade", "round_summary", "run_finished"} <= types
    assert sim.finished and sim.round == 8
    lines = (sim.run_dir / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == len(sim.store.events)
    assert json.loads(lines[-1])["type"] == "run_finished"
    assert all(e["data"]["source"] == "llm" for e in sim.store.of_type("decision"))


def test_same_seed_is_reproducible(make_config):
    a = run_sim(make_config(), run_id="a")
    b = run_sim(make_config(), run_id="b")
    ea, eb = strip(a.store.events[1:]), strip(b.store.events[1:])  # run_started embeds run_id
    assert ea == eb


def test_different_seed_differs(make_config):
    a = run_sim(make_config(seed=1))
    b = run_sim(make_config(seed=2))
    pa = [s["goods"]["w"]["close"] for s in a.summaries]
    pb = [s["goods"]["w"]["close"] for s in b.summaries]
    assert pa != pb


def test_invariants_hold_every_round(make_config):
    sim = run_sim(make_config(rounds=15))
    assert not sim.store.of_type("settlement_error")
    for a in sim.agents.values():
        assert a.cash >= -1e-6
        assert all(q >= 0 for q in a.inventory.values())
    # cash conservation: initial cash + income == final cash + government balance
    cfg = sim.config
    start = {m["id"]: m["cash"] for m in sim.store.events[0]["data"]["agents"]}
    income = sum(g.income_per_round * g.count for g in cfg.agents) * sim.round
    production = sum(e["data"]["production_cost"] for e in sim.store.of_type("trade"))
    total_now = sum(a.cash for a in sim.agents.values()) + sim.gov.balance
    assert total_now == pytest.approx(sum(start.values()) + income - production)
    # inventory conservation for speculators: units only move between agents or are consumed/produced
    for s in sim.summaries:
        g = s["goods"]["w"]
        assert g["volume"] == sum(g["seller_volumes"].values())


def test_failing_llm_falls_back_to_heuristic(make_config):
    cfg = make_config(llm={"provider": "mock", "max_retries": 1, "retry_backoff_s": 0, "timeout_s": 1,
                           "options": {"failure_rate": 1.0}})
    sim = run_sim(cfg)
    decisions = sim.store.of_type("decision")
    assert decisions and all(d["data"]["source"] == "fallback" for d in decisions)
    assert all(d["data"]["attempts"] == 2 for d in decisions)
    assert sim.store.of_type("trade")  # market keeps working
    assert sim.decisions.stats.fallbacks == len(decisions)


def test_timeouts_and_malformed_responses_are_retried(make_config):
    cfg = make_config(rounds=3, llm={"provider": "mock", "max_retries": 2, "retry_backoff_s": 0, "timeout_s": 0.05,
                                     "options": {"hang_rate": 0.2, "malformed_rate": 0.3}})
    sim = run_sim(cfg)
    errors = [err for d in sim.store.of_type("decision") for err in d["data"]["errors"]]
    assert any("timeout" in e for e in errors)
    assert any("unparseable" in e for e in errors)
    sources = {d["data"]["source"] for d in sim.store.of_type("decision")}
    assert "llm" in sources


class CountingProvider(LLMProvider):
    """Wraps the mock to count real calls."""

    name = "mock"

    def __init__(self, config):
        super().__init__(config)
        from market_sim.llm.mock import MockProvider
        self.inner = MockProvider(config)
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return await self.inner.complete(request)


def test_replay_cache_serves_identical_run(make_config, tmp_path):
    rec_cfg = make_config(llm={"provider": "mock", "cache": {"mode": "record", "path": str(tmp_path / "c.sqlite")}})
    p1 = CountingProvider(rec_cfg.llm)
    a = run_sim(rec_cfg, provider=p1, run_id="rec")
    assert p1.calls > 0

    rep_cfg = make_config(llm={"provider": "mock", "cache": {"mode": "strict_replay", "path": str(tmp_path / "c.sqlite")}})
    p2 = CountingProvider(rep_cfg.llm)
    b = run_sim(rep_cfg, provider=p2, run_id="rep")
    assert p2.calls == 0
    assert all(d["data"]["source"] == "cache" for d in b.store.of_type("decision"))
    strip_src = lambda evs: [  # noqa: E731
        json.dumps({**e, "data": {k: v for k, v in e["data"].items() if k not in ("source", "llm", "attempts")}}, sort_keys=True)
        for e in evs if e["type"] in ("trade", "decision")
    ]
    assert strip_src(a.store.events) == strip_src(b.store.events)


def test_strict_replay_miss_uses_fallback(make_config, tmp_path):
    cfg = make_config(rounds=2, llm={"provider": "mock", "cache": {"mode": "strict_replay", "path": str(tmp_path / "empty.sqlite")}})
    sim = run_sim(cfg)
    assert all(d["data"]["source"] == "fallback" for d in sim.store.of_type("decision"))


class ScriptedProvider(LLMProvider):
    """Returns hostile / malformed actions to prove sanitisation protects the engine."""

    name = "scripted"

    async def complete(self, request):
        if request.metadata.get("kind") == "policy":
            return LLMResponse(text='{"reasoning": "", "actions": [{"action": "set_tax", "good": "w", "rate": 1e9}]}')
        return LLMResponse(text=json.dumps({"reasoning": "chaos", "actions": [
            {"type": "bid", "good": "w", "price": 1e12, "quantity": 10**9},
            {"type": "ask", "good": "w", "price": 0.001, "quantity": 10**9},
            {"type": "cancel", "order_id": "someone-else"},
            {"type": "bid", "good": "nope", "price": 1, "quantity": 1},
        ]}))


def test_hostile_llm_cannot_break_invariants(make_config):
    cfg = make_config(agents=[
        {"role": "buyer", "count": 4, "cash": 500, "valuations": {"w": 100}, "units_per_round": 2},
        {"role": "seller", "count": 2, "costs": {"w": 50}, "capacity": {"w": 3}},
        {"role": "speculator", "count": 2, "cash": 1000, "inventory": {"w": 3}},
        {"role": "regulator", "count": 1, "decision": "llm"},
    ], policy={"bounds": {"max_per_unit_tax": 5}})
    sim = run_sim(cfg, provider=ScriptedProvider(cfg.llm))
    assert not sim.store.of_type("settlement_error")
    assert sim.policy["w"].per_unit_tax == 5  # clamped to bounds
    for a in sim.agents.values():
        assert a.cash >= -1e-6 and all(q >= 0 for q in a.inventory.values())
    assert any(d["data"]["result"].get("rejected") for d in sim.store.of_type("decision") if d["data"]["kind"] == "trade")


def test_policy_schedule_and_user_policy(make_config):
    cfg = make_config(rounds=6, policy={"schedule": [
        {"round": 2, "action": "set_tax", "good": "w", "tax_type": "per_unit", "rate": 10},
        {"round": 4, "action": "remove_tax", "good": "w"},
    ]})

    async def go():
        sim = Simulation(cfg)
        await sim.run(3)
        sim.queue_policy(PolicyAction(action="set_price_ceiling", good="w", value=95), "user")
        await sim.run()
        await sim.close()
        return sim
    sim = asyncio.run(go())
    changes = [(e["round"], e["data"]["source"], e["data"]["action"]["action"]) for e in sim.store.of_type("policy_change")]
    assert changes == [(2, "schedule", "set_tax"), (4, "schedule", "remove_tax"), (4, "user", "set_price_ceiling")]
    taxed = [s for s in sim.summaries if s["round"] in (2, 3)]
    assert all(s["goods"]["w"]["tax"] == pytest.approx(10 * s["goods"]["w"]["volume"]) for s in taxed)
    for e in sim.store.of_type("trade"):
        if e["round"] >= 4:
            assert e["data"]["price"] <= 95


def test_communication_channel(make_config):
    cfg = make_config(rounds=4, market={"communication": {"enabled": True, "participants": ["seller"]}},
                      agents=[
                          {"role": "buyer", "count": 6, "cash": 2000, "valuations": {"w": 120}, "units_per_round": 2},
                          {"role": "seller", "count": 3, "strategy": {"name": "collusive_seller"},
                           "costs": {"w": 50}, "capacity": {"w": 4}},
                      ])
    sim = run_sim(cfg)
    msgs = sim.store.of_type("message")
    assert len(msgs) == 3 * 4
    assert {m["data"]["from"] for m in msgs} == {"S01", "S02", "S03"}
    # sellers see the channel in their prompt; buyers do not
    seller_obs = sim.observe(sim.agents["S01"])
    buyer_obs = sim.observe(sim.agents["B01"])
    assert seller_obs["channel"] and "channel" not in buyer_obs


def test_heuristic_agents_make_no_llm_calls(make_config):
    cfg = make_config(agents=[
        {"role": "buyer", "count": 4, "decision": "heuristic", "valuations": {"w": 100}},
        {"role": "seller", "count": 2, "decision": "heuristic", "costs": {"w": 50}, "capacity": {"w": 2}},
    ])
    p = CountingProvider(cfg.llm)
    sim = run_sim(cfg, provider=p)
    assert p.calls == 0
    assert {d["data"]["source"] for d in sim.store.of_type("decision")} == {"heuristic"}


def test_summary_from_disk_matches_memory(make_config):
    sim = run_sim(make_config())
    assert summarize(load_events(sim.run_dir)) == summarize(sim.store.events)


@pytest.mark.parametrize("name", ["bubble.yaml", "cartel.yaml", "cartel_tacit.yaml", "taxation.yaml", "baseline.yaml"])
def test_bundled_experiments_run(name, tmp_path):
    cfg = load_config(EXPERIMENTS / name, {"rounds": 5, "runs_dir": str(tmp_path / "runs"),
                                           "llm": {"provider": "mock", "cache": {"mode": "off"}}})
    sim = run_sim(cfg)
    s = summarize(sim.store.events)
    assert s["rounds_completed"] == 5
    for g in s["goods"].values():
        assert g["total_volume"] > 0


def test_bubble_experiment_produces_bubble(tmp_path):
    cfg = load_config(EXPERIMENTS / "bubble.yaml", {"rounds": 40, "runs_dir": str(tmp_path / "runs"),
                                                     "llm": {"cache": {"mode": "off"}}})
    s = summarize(run_sim(cfg).store.events)
    b = s["goods"]["widget"]["bubble"]
    assert b["bubble_detected"] and b["crash_round"] is not None and b["crash_round"] > b["peak_round"]


def test_bubble_needs_speculators(tmp_path):
    cfg = load_config(EXPERIMENTS / "bubble.yaml", {"rounds": 40, "runs_dir": str(tmp_path / "runs"),
                                                     "llm": {"cache": {"mode": "off"}}})
    cfg.agents = [g for g in cfg.agents if g.role != "speculator"]
    s = summarize(run_sim(cfg).store.events)
    assert s["goods"]["widget"]["bubble"]["max_deviation"] < 0.3


def test_cartel_vs_tacit_verdicts(tmp_path):
    out = {}
    for name in ("cartel.yaml", "cartel_tacit.yaml"):
        cfg = load_config(EXPERIMENTS / name, {"rounds": 40, "runs_dir": str(tmp_path / "runs"),
                                               "llm": {"cache": {"mode": "off"}}})
        out[name] = summarize(run_sim(cfg).store.events)["goods"]["cement"]["cartel"]
    assert out["cartel.yaml"]["explicit_coordination_messages"] > 0
    assert out["cartel_tacit.yaml"]["explicit_coordination_messages"] == 0
    assert out["cartel.yaml"]["markup_over_mc"] > 0.2


def test_tax_reduces_volume(tmp_path):
    cfg = load_config(EXPERIMENTS / "taxation.yaml", {"rounds": 40, "runs_dir": str(tmp_path / "runs"),
                                                       "llm": {"cache": {"mode": "off"}}})
    s = summarize(run_sim(cfg).store.events)
    effect = s["goods"]["grain"]["tax_effects"][0]
    assert effect["round"] == 20
    assert effect["after"]["avg_volume"] < effect["before"]["avg_volume"]
    assert effect["after"]["avg_tax_revenue"] > 0
    assert effect["after"]["theoretical_dwl"] > 0
