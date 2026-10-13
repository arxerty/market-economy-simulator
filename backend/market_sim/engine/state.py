"""Persistent per-agent economic state (the ledger the engine settles against)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["buyer", "seller", "speculator", "regulator"]


@dataclass
class AgentState:
    id: str
    role: Role
    cash: float = 0.0
    inventory: dict[str, int] = field(default_factory=dict)
    cost_basis: dict[str, float] = field(default_factory=dict)  # average cost per held unit
    # Buyers
    valuations: dict[str, float] = field(default_factory=dict)
    units_per_round: int = 1
    valuation_decay: float = 0.0
    bought_this_round: dict[str, int] = field(default_factory=dict)
    # Sellers / producers
    costs: dict[str, float] = field(default_factory=dict)
    capacity: dict[str, int] = field(default_factory=dict)
    cost_slope: float = 0.0
    sold_this_round: dict[str, int] = field(default_factory=dict)
    # Common
    income_per_round: float = 0.0
    info_lag: int = 0
    group: str = ""
    decision: str = "llm"
    strategy_name: str = "default"
    strategy_prompt: str = ""
    strategy_params: dict[str, Any] = field(default_factory=dict)
    objective: str = ""
    decision_interval: int = 1
    memory: deque = field(default_factory=lambda: deque(maxlen=8))
    # Cumulative statistics
    consumer_surplus: float = 0.0
    producer_profit: float = 0.0
    trading_pnl: float = 0.0
    taxes_paid: float = 0.0
    units_bought: int = 0
    units_sold: int = 0
    initial_wealth: float = 0.0
    last_reasoning: str = ""

    # ------------------------------------------------------------ role helpers
    def next_unit_value(self, good: str, offset: int = 0) -> float:
        """Private valuation of the next unit of ``good`` this round (buyers)."""
        base = self.valuations.get(good, 0.0)
        k = self.bought_this_round.get(good, 0) + offset
        return round(base * (1.0 - self.valuation_decay) ** k, 4)

    def demand_remaining(self, good: str) -> int:
        if self.role != "buyer" or good not in self.valuations:
            return 0
        return max(0, self.units_per_round - self.bought_this_round.get(good, 0))

    def next_unit_cost(self, good: str, offset: int = 0) -> float:
        """Marginal production cost of the next unit (sellers)."""
        k = self.sold_this_round.get(good, 0) + offset
        return round(self.costs.get(good, 0.0) + self.cost_slope * k, 4)

    def capacity_remaining(self, good: str) -> int:
        if self.role != "seller":
            return 0
        return max(0, self.capacity.get(good, 0) - self.sold_this_round.get(good, 0))

    def can_buy(self, good: str) -> bool:
        return self.role == "speculator" or (self.role == "buyer" and good in self.valuations)

    def can_sell(self, good: str) -> bool:
        return self.role == "speculator" or (self.role == "seller" and good in self.costs)

    def sellable(self, good: str) -> int:
        if self.role == "seller":
            return self.capacity_remaining(good)
        if self.role == "speculator":
            return self.inventory.get(good, 0)
        return 0

    def buyable(self, good: str) -> int:
        """Quantity cap from role (cash is checked separately)."""
        if self.role == "buyer":
            return self.demand_remaining(good)
        if self.role == "speculator":
            return 10**9
        return 0

    def start_round(self) -> None:
        self.bought_this_round = {}
        self.sold_this_round = {}
        self.cash = round(self.cash + self.income_per_round, 6)

    def wealth(self, prices: dict[str, float]) -> float:
        held = sum(q * prices.get(g, 0.0) for g, q in self.inventory.items())
        return round(self.cash + held, 4)

    def remember(self, text: str) -> None:
        self.memory.append(text)

    def public_view(self, prices: dict[str, float]) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "group": self.group,
            "strategy": self.strategy_name,
            "decision": self.decision,
            "cash": round(self.cash, 2),
            "inventory": dict(self.inventory),
            "wealth": round(self.wealth(prices), 2),
            "consumer_surplus": round(self.consumer_surplus, 2),
            "producer_profit": round(self.producer_profit, 2),
            "trading_pnl": round(self.trading_pnl, 2),
            "units_bought": self.units_bought,
            "units_sold": self.units_sold,
        }
