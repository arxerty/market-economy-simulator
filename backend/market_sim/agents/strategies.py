"""Strategy registry: heuristic policies + the prompt text that describes them to an LLM.

Every strategy has
* ``decide(obs, params, rng) -> {"reasoning": str, "actions": [...]}``: a heuristic that
  works on the same observation the LLM sees. It is used when an agent's ``decision`` is
  ``heuristic``, as the fallback when an LLM call fails, and by the mock provider.
* ``prompt``: guidance appended to the LLM prompt (overridable per agent group).
* optionally ``message(obs, params, rng) -> str`` for the pre-round communication channel.

Add your own with the :func:`strategy` decorator (see README "Defining a new strategy").
"""

from __future__ import annotations

import math
import random
import re
import statistics
from dataclasses import dataclass
from typing import Any, Callable, Optional

Obs = dict[str, Any]
DecideFn = Callable[[Obs, dict[str, Any], random.Random], dict[str, Any]]
MessageFn = Callable[[Obs, dict[str, Any], random.Random], str]


@dataclass
class Strategy:
    name: str
    roles: tuple[str, ...]
    decide: DecideFn
    prompt: str = ""
    message: Optional[MessageFn] = None
    description: str = ""


STRATEGIES: dict[str, Strategy] = {}
DEFAULT_FOR_ROLE = {
    "buyer": "adaptive_buyer",
    "seller": "adaptive_seller",
    "speculator": "trend_follower",
    "regulator": "stabilizer",
}


def strategy(name: str, roles: tuple[str, ...], prompt: str = "", description: str = "") -> Callable[[DecideFn], DecideFn]:
    def deco(fn: DecideFn) -> DecideFn:
        STRATEGIES[name] = Strategy(name=name, roles=roles, decide=fn, prompt=prompt, description=description or (fn.__doc__ or "").strip())
        return fn
    return deco


def message_for(name: str) -> Callable[[MessageFn], MessageFn]:
    def deco(fn: MessageFn) -> MessageFn:
        STRATEGIES[name].message = fn
        return fn
    return deco


def get_strategy(name: str, role: str) -> Strategy:
    if name in ("", "default") or name not in STRATEGIES:
        name = DEFAULT_FOR_ROLE[role]
    return STRATEGIES[name]


# ----------------------------------------------------------------------------- helpers
def _hold(reason: str) -> dict[str, Any]:
    return {"reasoning": reason, "actions": [{"type": "hold"}]}


def _ref_price(m: dict[str, Any], fallback: float) -> float:
    for key in ("last_price", "vwap"):
        if m.get(key):
            return float(m[key])
    bb, ba = m.get("best_bid"), m.get("best_ask")
    if bb and ba:
        return (bb + ba) / 2
    return fallback


def _max_bid(obs: Obs, good: str, value: float) -> float:
    """Highest pre-tax price at which buying a unit worth ``value`` is not a loss."""
    p = obs["policy"][good]
    if p["tax_payer"] == "buyer":
        return (value - p["per_unit_tax"]) / (1 + p["ad_valorem_tax"])
    return value


def _min_ask(obs: Obs, good: str, cost: float) -> float:
    """Lowest pre-tax price that covers marginal ``cost`` after seller-side tax and subsidy."""
    p = obs["policy"][good]
    if p["tax_payer"] == "seller":
        return (cost + p["per_unit_tax"] - p["subsidy"]) / max(1e-9, 1 - p["ad_valorem_tax"])
    return cost - p["subsidy"]


def _cancel_stale(obs: Obs, good: str) -> list[dict[str, Any]]:
    """Cancel own resting orders on ``good`` so the new quote replaces them."""
    return [{"type": "cancel", "order_id": o["id"]} for o in obs["you"]["open_orders"] if o["good"] == good]


def _momentum(prices: list[float], window: int) -> float:
    prices = [p for p in prices if p]
    if len(prices) < window + 1:
        return 0.0
    base = prices[-1 - window]
    return (prices[-1] - base) / base if base else 0.0


# ----------------------------------------------------------------------------- buyers
@strategy(
    "adaptive_buyer", ("buyer",),
    prompt="Buy units whose private value exceeds the price. Anchor bids on recent trade prices, "
           "never bid above your value for that unit, and accept a good ask when one is available.",
)
def adaptive_buyer(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Bids near the recent price, never above private value; lifts cheap asks."""
    min_surplus = params.get("min_surplus", 0.02)
    actions: list[dict[str, Any]] = []
    notes = []
    for good, info in obs["you"]["goods"].items():
        need = info.get("remaining_demand", 0)
        if need <= 0:
            continue
        values = info["unit_values"][:need]
        caps = [_max_bid(obs, good, v) * (1 - min_surplus) for v in values]  # tax-aware reservation prices
        m = obs["market"][good]
        ref = _ref_price(m, values[0] * 0.8)
        actions += _cancel_stale(obs, good)
        ba = m.get("best_ask")
        v0 = values[0]
        if ba and ba <= caps[0] and rng.random() < params.get("take_prob", 0.6):
            price = ba
        else:
            price = min(caps[0], ref * (1 + rng.uniform(-0.06, 0.04)))
        # One order covering every unit still worth buying at this price, one lower bid for the rest.
        n_at = sum(1 for c in caps if c >= price)
        if price > 0 and n_at:
            actions.append({"type": "bid", "good": good, "price": round(price, 2), "quantity": n_at})
        if n_at < len(values) and caps[-1] > 0:
            actions.append({"type": "bid", "good": good, "price": round(min(caps[-1], price), 2), "quantity": len(values) - n_at})
        notes.append(f"{good}: value {v0:.2f}, ref {ref:.2f}")
    if not actions:
        return _hold("Demand satisfied this round.")
    return {"reasoning": "Bidding below private value; " + "; ".join(notes), "actions": actions}


@strategy("zi_buyer", ("buyer",), prompt="Bid a random price between zero and your private value.")
def zi_buyer(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Gode-Sunder zero-intelligence constrained trader."""
    actions = []
    for good, info in obs["you"]["goods"].items():
        if info.get("remaining_demand", 0) > 0:
            v = _max_bid(obs, good, info["unit_values"][0])
            if v > 1:
                actions += _cancel_stale(obs, good)
                actions.append({"type": "bid", "good": good, "price": round(rng.uniform(1, v), 2), "quantity": 1})
    return {"reasoning": "Random bid below value.", "actions": actions} if actions else _hold("Nothing to buy.")


# ----------------------------------------------------------------------------- sellers
@strategy(
    "adaptive_seller", ("seller",),
    prompt="Sell units whose price exceeds your marginal cost. Quote near recent trade prices, "
           "never below cost, and hit attractive bids.",
)
def adaptive_seller(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Quotes near the recent price, never below marginal cost; hits rich bids."""
    min_markup = params.get("min_markup", 0.02)
    actions: list[dict[str, Any]] = []
    notes = []
    for good, info in obs["you"]["goods"].items():
        cap = info.get("capacity_remaining", 0)
        if cap <= 0:
            continue
        costs = info["unit_costs"][:cap]
        floors = [_min_ask(obs, good, c) * (1 + min_markup) for c in costs]  # tax-aware reservation prices
        m = obs["market"][good]
        ref = _ref_price(m, costs[0] * 1.3)
        bb = m.get("best_bid")
        actions += _cancel_stale(obs, good)
        if bb and bb >= floors[0] and rng.random() < params.get("take_prob", 0.6):
            price = bb
        else:
            price = max(floors[0], ref * (1 + rng.uniform(-0.04, 0.06)))
        price = max(price, 0.01)
        n_at = sum(1 for f in floors if f <= price)
        actions.append({"type": "ask", "good": good, "price": round(price, 2), "quantity": n_at})
        if n_at < len(costs):
            actions.append({"type": "ask", "good": good, "price": round(max(floors[-1], price), 2), "quantity": len(costs) - n_at})
        notes.append(f"{good}: cost {costs[0]:.2f}, ref {ref:.2f}")
    if not actions:
        return _hold("No capacity left.")
    return {"reasoning": "Quoting above marginal cost; " + "; ".join(notes), "actions": actions}


@strategy("markup_seller", ("seller",), prompt="Always ask a fixed markup over marginal cost.")
def markup_seller(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Cost-plus pricing with a fixed markup."""
    markup = params.get("markup", 0.25)
    actions = []
    for good, info in obs["you"]["goods"].items():
        cap = info.get("capacity_remaining", 0)
        if cap > 0:
            actions += _cancel_stale(obs, good)
            floor = _min_ask(obs, good, info["unit_costs"][0])
            actions.append({"type": "ask", "good": good, "price": round(max(0.01, floor * (1 + markup)), 2), "quantity": cap})
    return {"reasoning": f"Cost plus {markup:.0%}.", "actions": actions} if actions else _hold("No capacity.")


_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


@strategy(
    "collusive_seller", ("seller",),
    prompt="You may talk to the other sellers before each round. Think about whether competing on "
           "price or keeping prices high is better for you over many rounds.",
)
def collusive_seller(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Follows the highest credible price signal from the channel / book and avoids undercutting."""
    min_markup = params.get("min_markup", 0.05)
    drift = params.get("drift", 0.015)
    actions: list[dict[str, Any]] = []
    for good, info in obs["you"]["goods"].items():
        cap = info.get("capacity_remaining", 0)
        if cap <= 0:
            continue
        cost = info["unit_costs"][0]
        m = obs["market"][good]
        ref = _ref_price(m, cost * 1.4)
        signals = [float(x) for msg in obs.get("channel", []) if msg["from"] != obs["you"]["id"]
                   for x in _PRICE_RE.findall(msg["text"]) if float(x) > cost]
        anchor = statistics.median(signals) if signals else ref
        sold_last = info.get("sold_last_round", 0)
        step = drift if sold_last > 0 else -drift / 2
        price = max(_min_ask(obs, good, cost) * (1 + min_markup), max(anchor, ref) * (1 + step), 0.01)
        actions += _cancel_stale(obs, good)
        actions.append({"type": "ask", "good": good, "price": round(price, 2), "quantity": cap})
    if not actions:
        return _hold("No capacity left.")
    return {"reasoning": "Matching the group's price level rather than undercutting.", "actions": actions}


@message_for("collusive_seller")
def collusive_message(obs: Obs, params: dict[str, Any], rng: random.Random) -> str:
    if not params.get("explicit", True):
        return rng.choice([
            "Demand looks steady to me.",
            "Costs are not getting any cheaper.",
            "Interesting round. Let's see how it goes.",
        ])
    good, info = next(iter(obs["you"]["goods"].items()))
    m = obs["market"][good]
    ref = _ref_price(m, info["unit_costs"][0] * 1.4)
    target = max(ref, info["unit_costs"][0] * 1.3) * 1.02
    return f"I'm keeping my {good} ask at {target:.0f} or above. Undercutting each other only hurts all of us."


# ----------------------------------------------------------------------------- speculators
@strategy(
    "trend_follower", ("speculator",),
    prompt="You have no use for the good; you profit only from buying low and selling high. "
           "Ride price trends: buy when prices are rising, sell when they start falling.",
)
def trend_follower(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Momentum trader: chases rising prices and dumps on falling ones (bubble fuel)."""
    window = int(params.get("window", 3))
    thresh = params.get("threshold", 0.01)
    aggr = params.get("aggressiveness", 0.03)
    frac = params.get("cash_fraction", 0.3)
    you = obs["you"]
    actions: list[dict[str, Any]] = []
    notes = []
    goods = list(obs["market"])
    for good in goods:
        m = obs["market"][good]
        prices = m.get("prices", [])
        last = _ref_price(m, 0.0)
        if not last:
            continue
        mom = _momentum(prices, window) + rng.gauss(0, params.get("noise", 0.01))
        held = you["inventory"].get(good, 0)
        actions += _cancel_stale(obs, good)
        if mom > thresh:
            price = last * (1 + aggr + min(mom, 0.2))
            qty = int((you["available_cash"] * frac / len(goods)) // price)
            if qty > 0:
                actions.append({"type": "bid", "good": good, "price": round(price, 2), "quantity": qty})
                notes.append(f"{good} momentum {mom:+.1%}: buying {qty}")
        elif mom < -thresh and held > 0:
            qty = max(1, math.ceil(held * params.get("sell_fraction", 0.5)))
            price = last * (1 - aggr + max(mom, -0.2) / 2)
            actions.append({"type": "ask", "good": good, "price": round(max(price, 0.01), 2), "quantity": qty})
            notes.append(f"{good} momentum {mom:+.1%}: selling {qty}")
        else:
            basis = you["cost_basis"].get(good, 0.0)
            if held > 0 and basis and last > basis * (1 + params.get("take_profit", 0.15)):
                actions.append({"type": "ask", "good": good, "price": round(last, 2), "quantity": max(1, held // 4)})
                notes.append(f"{good}: taking profit")
    if not [a for a in actions if a["type"] != "cancel"]:
        return {"reasoning": "No clear trend; staying out.", "actions": actions or [{"type": "hold"}]}
    return {"reasoning": "; ".join(notes), "actions": actions}


@strategy(
    "fundamentalist", ("speculator",),
    prompt="Estimate the long-run value of the good from its price history. Buy when the price is "
           "well below that value and sell when it is well above.",
)
def fundamentalist(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Mean-reversion trader anchored on the long-run average price."""
    band = params.get("band", 0.1)
    you = obs["you"]
    actions: list[dict[str, Any]] = []
    for good, m in obs["market"].items():
        prices = [p for p in m.get("prices", []) if p]
        if len(prices) < 4:
            continue
        anchor = statistics.mean(prices)
        last = prices[-1]
        actions += _cancel_stale(obs, good)
        if last < anchor * (1 - band):
            qty = int((you["available_cash"] * 0.2) // last)
            if qty > 0:
                actions.append({"type": "bid", "good": good, "price": round(last * 1.01, 2), "quantity": qty})
        elif last > anchor * (1 + band) and you["inventory"].get(good, 0) > 0:
            actions.append({"type": "ask", "good": good, "price": round(last * 0.99, 2),
                            "quantity": max(1, you["inventory"][good] // 2)})
    return {"reasoning": "Trading against deviations from the long-run average.", "actions": actions or [{"type": "hold"}]}


# ----------------------------------------------------------------------------- regulator
@strategy(
    "stabilizer", ("regulator",),
    prompt="Keep prices close to their fundamental value. Use a small transaction (ad valorem) tax to "
           "cool speculative bubbles and remove it once the market has calmed down.",
)
def stabilizer(obs: Obs, params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Imposes a transaction tax when price deviates from fundamental, lifts it when calm."""
    threshold = params.get("threshold", 0.3)
    rate = params.get("tax", 0.05)
    actions = []
    notes = []
    for good, m in obs["market"].items():
        f, p = m.get("fundamental"), m.get("last_price")
        if not f or not p:
            continue
        dev = (p - f) / f
        current = obs["policy"][good]["ad_valorem_tax"]
        if abs(dev) > threshold and current < rate:
            actions.append({"action": "set_tax", "good": good, "tax_type": "ad_valorem", "rate": rate,
                            "payer": "buyer", "reason": f"price {dev:+.0%} from fundamental"})
            notes.append(f"{good}: deviation {dev:+.0%}, taxing")
        elif abs(dev) < threshold / 2 and current > 0:
            actions.append({"action": "remove_tax", "good": good, "reason": "market calm"})
            notes.append(f"{good}: calm, removing tax")
    return {"reasoning": "; ".join(notes) or "Market within tolerance.", "actions": actions or [{"action": "hold"}]}
