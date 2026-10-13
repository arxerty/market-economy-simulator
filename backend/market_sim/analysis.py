"""Post-run analysis computed purely from the event log (works for live and replayed runs)."""

from __future__ import annotations

import math
import re
import statistics
from pathlib import Path
from typing import Any, Iterable, Optional

from .engine.events import read_events

_COORDINATION = re.compile(
    r"\b(agree|agreed|let'?s|we should|all of us|together|cartel|fix(ed)? (the )?price|no one|nobody|"
    r"don'?t undercut|undercut|stop undercutting|keep (prices|my|our|the)|hold (prices|at)|at least|or above|minimum|deal)\b",
    re.I,
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def load_events(run_dir: Path) -> list[dict[str, Any]]:
    return list(read_events(run_dir / "events.jsonl"))


def _mean(xs: Iterable[Optional[float]]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return statistics.fmean(vals) if vals else None


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, n)


def hhi(volumes: dict[str, float]) -> Optional[float]:
    """Herfindahl-Hirschman index on a 0-10,000 scale."""
    total = sum(volumes.values())
    if total <= 0:
        return None
    return round(sum((100.0 * v / total) ** 2 for v in volumes.values()), 2)


def volatility(prices: list[float]) -> Optional[float]:
    """Standard deviation of log returns between consecutive traded prices."""
    rets = [math.log(b / a) for a, b in zip(prices, prices[1:]) if a > 0 and b > 0]
    return round(statistics.pstdev(rets), 6) if len(rets) >= 2 else None


def gini(values: list[float]) -> Optional[float]:
    xs = sorted(max(0.0, v) for v in values)
    n, total = len(xs), sum(xs)
    if n == 0 or total == 0:
        return None
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return round((2 * cum) / (n * total) - (n + 1) / n, 4)


def bubble_metrics(rounds: list[int], prices: list[float], fundamentals: list[Optional[float]],
                   crash_drop: float = 0.2, bubble_threshold: float = 0.15) -> dict[str, Any]:
    devs = [(p - f) / f for p, f in zip(prices, fundamentals) if f]
    if not devs:
        return {}
    pairs = [(r, p, f) for r, p, f in zip(rounds, prices, fundamentals) if f]
    peak_i = max(range(len(pairs)), key=lambda i: pairs[i][1] / pairs[i][2])
    peak_round, peak_price, peak_f = pairs[peak_i]
    crash_round = None
    for r, p, _ in pairs[peak_i + 1:]:
        if p <= peak_price * (1 - crash_drop):
            crash_round = r
            break
    start = next((r for r, p, f in pairs if (p - f) / f > bubble_threshold), None)
    return {
        "relative_deviation": _r(statistics.fmean(devs)),  # RD (Stöckl et al.)
        "relative_absolute_deviation": _r(statistics.fmean(abs(d) for d in devs)),  # RAD
        "max_deviation": _r(max(devs)),
        "peak_round": peak_round,
        "peak_price": _r(peak_price, 2),
        "bubble_start_round": start if start is not None and (peak_price - peak_f) / peak_f > bubble_threshold else None,
        "crash_round": crash_round,
        "crash_depth": _r(min(p for _, p, _ in pairs[peak_i:]) / peak_price - 1) if peak_i < len(pairs) - 1 else None,
        "bubble_detected": (peak_price - peak_f) / peak_f > bubble_threshold,
    }


def cartel_metrics(summaries: list[dict[str, Any]], good: str, messages: list[dict[str, Any]],
                   competitive_price: Optional[float], monopoly_price: Optional[float]) -> dict[str, Any]:
    rev = sum(s["goods"][good]["producer_revenue"] for s in summaries)
    cost = sum(s["goods"][good]["production_cost"] for s in summaries)
    units = sum(s["goods"][good]["producer_units"] for s in summaries)
    avg_price = rev / units if units else None
    avg_mc = cost / units if units else None

    cvs = []
    for s in summaries:
        asks = list(s["goods"][good]["seller_asks"].values())
        if len(asks) >= 2 and statistics.fmean(asks) > 0:
            cvs.append(statistics.pstdev(asks) / statistics.fmean(asks))
        else:
            cvs.append(None)
    third = max(1, len(cvs) // 3)
    early, late = _mean(cvs[:third]), _mean(cvs[-third:])
    late_rounds = summaries[-third:]
    late_units = sum(s["goods"][good]["producer_units"] for s in late_rounds)
    late_price = (sum(s["goods"][good]["producer_revenue"] for s in late_rounds) / late_units) if late_units else None

    collusion_index = None
    if late_price is not None and competitive_price and monopoly_price and monopoly_price > competitive_price:
        collusion_index = (late_price - competitive_price) / (monopoly_price - competitive_price)

    flagged = [m for m in messages if _COORDINATION.search(m["text"]) and (_NUMBER.search(m["text"]) or "undercut" in m["text"].lower())]
    converged = late is not None and late < 0.03
    supra = collusion_index is not None and collusion_index > 0.25
    if supra and flagged:
        verdict = "explicit coordination"
    elif supra and converged:
        verdict = "tacit collusion (converged without explicit agreement)"
    elif supra:
        verdict = "supra-competitive pricing"
    else:
        verdict = "competitive"
    return {
        "avg_producer_price": _r(avg_price, 2),
        "avg_marginal_cost": _r(avg_mc, 2),
        "markup_over_mc": _r(avg_price / avg_mc - 1) if avg_price and avg_mc else None,
        "lerner_index": _r((avg_price - avg_mc) / avg_price) if avg_price and avg_mc else None,
        "markup_over_competitive": _r(late_price / competitive_price - 1) if late_price and competitive_price else None,
        "collusion_index": _r(collusion_index),
        "ask_dispersion_early": _r(early),
        "ask_dispersion_late": _r(late),
        "prices_converged": converged,
        "explicit_coordination_messages": len(flagged),
        "first_explicit_round": flagged[0]["round"] if flagged else None,
        "example_messages": [f"R{m['round']} {m['from']}: {m['text']}" for m in flagged[:5]],
        "verdict": verdict,
    }


def _window_stats(summaries: list[dict[str, Any]], good: str) -> dict[str, Any]:
    g = [s["goods"][good] for s in summaries]
    n = len(g) or 1
    return {
        "rounds": len(g),
        "avg_volume": _r(sum(x["volume"] for x in g) / n, 3),
        "avg_price": _r(_mean(x["vwap"] for x in g), 3),
        "avg_buyer_price": _r(_mean(x["buyer_price"] for x in g), 3),
        "avg_total_surplus": _r(sum(x["realized_surplus"] for x in g) / n, 3),
        "avg_consumer_surplus": _r(sum(x["buyer_surplus"] for x in g) / n, 3),
        "avg_producer_surplus": _r(sum(x["producer_profit"] for x in g) / n, 3),
        "avg_tax_revenue": _r(sum(x["tax"] - x["subsidy"] for x in g) / n, 3),
        "avg_dwl": _r(sum(x["dwl"] for x in g) / n, 3),
        "theoretical_dwl": _r(sum(x["efficient_surplus"] - x["policy_surplus"] for x in g) / n, 3),
    }


def tax_metrics(summaries: list[dict[str, Any]], policy_events: list[dict[str, Any]], good: str, window: int = 10) -> list[dict[str, Any]]:
    """Before/after comparison around every policy change affecting ``good``."""
    out = []
    changes = [e for e in policy_events if e["data"]["action"].get("good") in (None, good)]
    by_round = {s["round"]: s for s in summaries}
    for e in changes:
        r = e["round"]
        before = [by_round[k] for k in range(max(1, r - window), r) if k in by_round]
        after = [by_round[k] for k in range(r, r + window) if k in by_round]
        if not before or not after:
            continue
        b, a = _window_stats(before, good), _window_stats(after, good)
        out.append({
            "round": r, "source": e["data"]["source"], "action": e["data"]["action"],
            "before": b, "after": a,
            "delta": {k: _r(a[k] - b[k], 3) if isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)) else None
                      for k in a if k != "rounds"},
        })
    return out


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    start = next(e for e in events if e["type"] == "run_started")["data"]
    cfg = start["config"]
    summaries = [e["data"] for e in events if e["type"] == "round_summary"]
    messages = [e["data"] for e in events if e["type"] == "message"]
    policy_events = [e for e in events if e["type"] == "policy_change"]
    decisions = [e["data"] for e in events if e["type"] == "decision"]
    goods = [g["id"] for g in cfg["goods"]]

    per_good: dict[str, Any] = {}
    for g in goods:
        rows = [s["goods"][g] for s in summaries]
        rounds = [s["round"] for s in summaries]
        traded = [(s["round"], s["goods"][g]["vwap"], s["goods"][g]["fundamental"]) for s in summaries if s["goods"][g]["volume"]]
        prices = [p for _, p, _ in traded]
        bench = start["benchmarks"][g]
        efficient = sum(x["efficient_surplus"] for x in rows)
        realized = sum(x["realized_surplus"] for x in rows)
        seller_vol: dict[str, float] = {}
        producer_vol: dict[str, float] = {}
        for x in rows:
            for sid, q in x["seller_volumes"].items():
                seller_vol[sid] = seller_vol.get(sid, 0) + q
        producers = {a["id"] for a in start["agents"] if a["role"] == "seller"}
        for sid, q in seller_vol.items():
            if sid in producers:
                producer_vol[sid] = q
        hhis = [hhi(x["seller_volumes"]) for x in rows]
        per_good[g] = {
            "rounds": len(rows),
            "total_volume": sum(x["volume"] for x in rows),
            "avg_volume": _r(_mean(x["volume"] for x in rows), 3),
            "avg_price": _r(_mean(prices), 3),
            "final_price": rows[-1]["close"] if rows else None,
            "competitive_price": bench["competitive"]["price"],
            "competitive_qty": bench["competitive"]["quantity"],
            "monopoly_price": bench["monopoly"]["price"],
            "price_volatility": volatility(prices),
            "total_surplus": _r(realized, 2),
            "efficient_surplus": _r(efficient, 2),
            "deadweight_loss": _r(efficient - realized, 2),
            "efficiency": _r(realized / efficient) if efficient > 0 else None,
            "consumer_surplus": _r(sum(x["buyer_surplus"] for x in rows), 2),
            "producer_surplus": _r(sum(x["producer_profit"] for x in rows), 2),
            "speculator_pnl": _r(sum(x["trading_pnl"] for x in rows), 2),
            "tax_revenue": _r(sum(x["tax"] for x in rows), 2),
            "subsidies": _r(sum(x["subsidy"] for x in rows), 2),
            "hhi_sellers": hhi(seller_vol),
            "hhi_producers": hhi(producer_vol),
            "hhi_avg_per_round": _r(_mean(hhis), 2),
            "bubble": bubble_metrics([r for r, _, _ in traded], prices, [f for _, _, f in traded]),
            "cartel": cartel_metrics(summaries, g, messages, bench["competitive"]["price"], bench["monopoly"]["price"]),
            "tax_effects": tax_metrics(summaries, policy_events, g),
            "series": {
                "round": rounds,
                "close": [x["close"] for x in rows],
                "vwap": [x["vwap"] for x in rows],
                "fundamental": [x["fundamental"] for x in rows],
                "equilibrium": [x["equilibrium_price"] for x in rows],
                "volume": [x["volume"] for x in rows],
                "realized_surplus": [x["realized_surplus"] for x in rows],
                "dwl": [x["dwl"] for x in rows],
                "tax": [x["tax"] for x in rows],
            },
        }

    final_wealth = summaries[-1]["wealth"] if summaries else []
    by_role: dict[str, list[float]] = {}
    for w in final_wealth:
        by_role.setdefault(w["role"], []).append(w["wealth"])
    sources: dict[str, int] = {}
    for d in decisions:
        sources[d["source"]] = sources.get(d["source"], 0) + 1

    return {
        "run_id": start["run_id"],
        "name": cfg["name"],
        "experiment_type": cfg.get("experiment_type", "generic"),
        "description": cfg.get("description", ""),
        "seed": cfg["seed"],
        "rounds_completed": len(summaries),
        "rounds_planned": cfg["rounds"],
        "finished": any(e["type"] == "run_finished" for e in events),
        "goods": per_good,
        "wealth": {
            "gini": gini([w["wealth"] for w in final_wealth]),
            "by_role": {r: {"count": len(v), "total": _r(sum(v), 2), "mean": _r(statistics.fmean(v), 2)} for r, v in by_role.items()},
            "final": final_wealth,
        },
        "policy_changes": [{"round": e["round"], **e["data"]["action"], "source": e["data"]["source"]} for e in policy_events],
        "messages": len(messages),
        "decision_sources": sources,
        "llm": summaries[-1]["llm"] if summaries else {},
    }


_COMPARE_KEYS = [
    "avg_price", "price_volatility", "total_volume", "total_surplus", "deadweight_loss", "efficiency",
    "consumer_surplus", "producer_surplus", "speculator_pnl", "tax_revenue", "hhi_sellers",
]


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    goods = sorted(set(a["goods"]) & set(b["goods"]))
    rows = {}
    for g in goods:
        ga, gb = a["goods"][g], b["goods"][g]
        metrics = {}
        for k in _COMPARE_KEYS:
            va, vb = ga.get(k), gb.get(k)
            metrics[k] = {"a": va, "b": vb, "delta": _r(vb - va, 4) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None}
        for sect, keys in (("bubble", ["relative_absolute_deviation", "max_deviation", "peak_round", "crash_round"]),
                           ("cartel", ["markup_over_mc", "lerner_index", "collusion_index"])):
            for k in keys:
                va, vb = ga[sect].get(k), gb[sect].get(k)
                metrics[f"{sect}.{k}"] = {"a": va, "b": vb, "delta": _r(vb - va, 4) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None}
        rows[g] = {"metrics": metrics, "series": {"a": ga["series"], "b": gb["series"]}}
    return {
        "a": {"run_id": a["run_id"], "name": a["name"]},
        "b": {"run_id": b["run_id"], "name": b["name"]},
        "goods": rows,
        "gini": {"a": a["wealth"]["gini"], "b": b["wealth"]["gini"]},
    }
