"""Government policy state: taxes, subsidies and price controls per good."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from ..config import PolicyAction, PolicyBounds


@dataclass
class GoodPolicy:
    per_unit_tax: float = 0.0
    ad_valorem_tax: float = 0.0  # fraction of price, e.g. 0.1 = 10%
    tax_payer: str = "seller"  # who remits the tax: "buyer" or "seller"
    subsidy: float = 0.0  # paid to the seller per unit sold
    price_floor: Optional[float] = None
    price_ceiling: Optional[float] = None

    def tax_per_unit(self, price: float) -> float:
        return self.per_unit_tax + self.ad_valorem_tax * price

    def buyer_cost(self, price: float) -> float:
        """What a buyer pays per unit at pre-tax market price ``price``."""
        return price + (self.tax_per_unit(price) if self.tax_payer == "buyer" else 0.0)

    def seller_net(self, price: float) -> float:
        """What a seller receives per unit at pre-tax market price ``price``."""
        tax = self.tax_per_unit(price) if self.tax_payer == "seller" else 0.0
        return price - tax + self.subsidy

    def buyer_threshold(self, valuation: float) -> float:
        """Highest pre-tax price at which a buyer with ``valuation`` still gains."""
        if self.tax_payer == "buyer":
            return (valuation - self.per_unit_tax) / (1.0 + self.ad_valorem_tax)
        return valuation

    def seller_threshold(self, cost: float) -> float:
        """Lowest pre-tax price at which a seller with marginal ``cost`` still gains."""
        if self.tax_payer == "seller":
            denom = max(1e-9, 1.0 - self.ad_valorem_tax)
            return (cost + self.per_unit_tax - self.subsidy) / denom
        return cost - self.subsidy

    def clamp(self, price: float) -> float:
        if self.price_floor is not None:
            price = max(price, self.price_floor)
        if self.price_ceiling is not None:
            price = min(price, self.price_ceiling)
        return price

    def is_neutral(self) -> bool:
        return self == GoodPolicy()

    def to_dict(self) -> dict:
        return asdict(self)


class PolicyState:
    def __init__(self, goods: list[str], bounds: Optional[PolicyBounds] = None) -> None:
        self.goods = {g: GoodPolicy() for g in goods}
        self.bounds = bounds or PolicyBounds()

    def __getitem__(self, good: str) -> GoodPolicy:
        return self.goods[good]

    def snapshot(self) -> dict[str, dict]:
        return {g: p.to_dict() for g, p in self.goods.items()}

    def sanitize(self, action: PolicyAction) -> tuple[Optional[PolicyAction], Optional[str]]:
        """Clamp a policy action into the configured bounds. Returns (action, error)."""
        if action.action in ("hold",):
            return action, None
        if action.action != "clear_controls" and action.good is not None and action.good not in self.goods:
            return None, f"unknown good '{action.good}'"
        a = action.model_copy()
        b = self.bounds
        if a.action == "set_tax":
            limit = b.max_per_unit_tax if a.tax_type == "per_unit" else b.max_ad_valorem
            a.rate = round(min(max(a.rate, 0.0), limit), 4)
        elif a.action == "set_subsidy":
            a.value = round(min(max(a.value if a.value is not None else a.rate, 0.0), b.max_subsidy), 4)
        elif a.action in ("set_price_floor", "set_price_ceiling"):
            if a.value is not None:
                if a.value <= 0:
                    return None, "price control must be positive"
                a.value = round(a.value, 2)
        return a, None

    def apply(self, action: PolicyAction) -> list[str]:
        """Apply an already-sanitized action. Returns the list of goods affected."""
        targets = list(self.goods) if action.good is None else [action.good]
        if action.action == "hold":
            return []
        for g in targets:
            p = self.goods[g]
            if action.action == "set_tax":
                if action.tax_type == "per_unit":
                    p.per_unit_tax = action.rate
                else:
                    p.ad_valorem_tax = action.rate
                p.tax_payer = action.payer
            elif action.action == "remove_tax":
                p.per_unit_tax = 0.0
                p.ad_valorem_tax = 0.0
                p.tax_payer = "seller"
            elif action.action == "set_subsidy":
                p.subsidy = float(action.value or 0.0)
            elif action.action == "set_price_floor":
                p.price_floor = action.value
                if p.price_ceiling is not None and p.price_floor is not None and p.price_floor > p.price_ceiling:
                    p.price_ceiling = p.price_floor
            elif action.action == "set_price_ceiling":
                p.price_ceiling = action.value
                if p.price_floor is not None and p.price_ceiling is not None and p.price_ceiling < p.price_floor:
                    p.price_floor = p.price_ceiling
            elif action.action == "clear_controls":
                p.price_floor = None
                p.price_ceiling = None
        return targets
