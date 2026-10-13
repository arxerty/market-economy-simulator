"""Theoretical benchmarks computed from the induced supply and demand schedules.

Buyers' private valuations form the demand schedule, producers' marginal costs form
the supply schedule (Vernon Smith style induced values). From these we derive the
competitive equilibrium (the "fundamental" value), the maximum attainable surplus, the
equilibrium under the current tax / price-control regime, and the joint-profit-maximising
(cartel / monopoly) price.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from .policy import GoodPolicy
from .state import AgentState


@dataclass
class Equilibrium:
    price: Optional[float]
    quantity: int
    surplus: float  # Σ (v - c) over traded units (tax revenue counts as surplus)
    buyer_price: Optional[float] = None  # tax-inclusive price paid
    seller_price: Optional[float] = None  # net price received

    def to_dict(self) -> dict:
        return asdict(self)


def demand_schedule(agents: Iterable[AgentState], good: str) -> list[float]:
    vals: list[float] = []
    for a in agents:
        if a.role == "buyer" and good in a.valuations:
            v = a.valuations[good]
            for k in range(a.units_per_round):
                vals.append(v * (1.0 - a.valuation_decay) ** k)
    return sorted(vals, reverse=True)


def supply_schedule(agents: Iterable[AgentState], good: str) -> list[float]:
    costs: list[float] = []
    for a in agents:
        if a.role == "seller" and good in a.costs:
            for k in range(a.capacity.get(good, 0)):
                costs.append(a.costs[good] + a.cost_slope * k)
    return sorted(costs)


def _surplus(demand: list[float], supply: list[float], q: int) -> float:
    return round(sum(demand[i] - supply[i] for i in range(q)), 6)


def equilibrium(demand: list[float], supply: list[float], policy: Optional[GoodPolicy] = None) -> Equilibrium:
    """Competitive equilibrium under ``policy`` (taxes, subsidies, price controls)."""
    pol = policy or GoodPolicy()
    pb = [pol.buyer_threshold(v) for v in demand]  # descending
    ps = [pol.seller_threshold(c) for c in supply]  # ascending
    n = min(len(pb), len(ps))
    q = 0
    while q < n and pb[q] >= ps[q]:
        q += 1
    if q == 0:
        price = None
    else:
        lo = max(ps[q - 1], pb[q] if q < len(pb) else float("-inf"))
        hi = min(pb[q - 1], ps[q] if q < len(ps) else float("inf"))
        if lo > hi:  # pragma: no cover - cannot happen with sorted schedules
            lo, hi = hi, lo
        price = (lo + hi) / 2.0

    # Binding price controls override the market-clearing price.
    for bound, binding in ((pol.price_ceiling, lambda p, b: p > b), (pol.price_floor, lambda p, b: p < b)):
        if bound is not None and (price is None or binding(price, bound)):
            d = sum(1 for x in pb if x >= bound)
            s = sum(1 for x in ps if x <= bound)
            q = min(d, s)
            price = bound if q > 0 else price
    if price is None:
        return Equilibrium(price=None, quantity=0, surplus=0.0)
    price = round(price, 4)
    return Equilibrium(
        price=price, quantity=q, surplus=_surplus(demand, supply, q),
        buyer_price=round(pol.buyer_cost(price), 4), seller_price=round(pol.seller_net(price), 4),
    )


def max_surplus(demand: list[float], supply: list[float]) -> float:
    """Surplus at the undistorted competitive equilibrium (efficient benchmark)."""
    return equilibrium(demand, supply).surplus


def monopoly_outcome(demand: list[float], supply: list[float], policy: Optional[GoodPolicy] = None) -> Equilibrium:
    """Price that maximises sellers' joint profit given the demand schedule (perfect cartel)."""
    pol = policy or GoodPolicy()
    if not demand or not supply:
        return Equilibrium(price=None, quantity=0, surplus=0.0)
    pb = [pol.buyer_threshold(v) for v in demand]
    best: tuple[float, float, int] = (float("-inf"), 0.0, 0)
    for p in sorted(set(pb)):
        q = min(sum(1 for x in pb if x >= p), len(supply))
        if q <= 0:
            continue
        net = pol.seller_net(p)
        profit = sum(net - supply[i] for i in range(q) if net >= supply[i])
        q_eff = sum(1 for i in range(q) if net >= supply[i])
        if profit > best[0] + 1e-9:
            best = (profit, p, q_eff)
    _, price, q = best
    return Equilibrium(price=round(price, 4), quantity=q, surplus=_surplus(demand, supply, q))
