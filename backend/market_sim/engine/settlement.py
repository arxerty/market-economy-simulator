"""Settlement: turn a matched trade into cash / inventory / tax transfers.

Accounting identities maintained here (and tested):

* cash is conserved: buyer outflow == seller inflow + net government revenue
  (producers then pay their marginal cost out of the economy, make-to-order)
* buyers consume what they buy and realise ``valuation - price paid`` per unit
* producers realise ``net receipt - marginal cost`` per unit
* speculators carry inventory at average cost and realise P&L on sale
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .orderbook import Trade
from .policy import GoodPolicy
from .state import AgentState


class SettlementError(RuntimeError):
    pass


@dataclass
class Government:
    tax_revenue: float = 0.0
    subsidies_paid: float = 0.0

    @property
    def balance(self) -> float:
        return round(self.tax_revenue - self.subsidies_paid, 6)


@dataclass
class SettlementRecord:
    good: str
    price: float
    quantity: int
    buyer_id: str
    seller_id: str
    buyer_paid: float
    seller_received: float
    tax: float
    subsidy: float
    buyer_surplus: float  # consumption surplus (buyers only)
    producer_profit: float  # production profit (sellers only)
    trading_pnl: float  # realised speculator P&L (seller side)
    consumed_value: float  # Σ valuations of consumed units
    production_cost: float  # Σ marginal costs of produced units

    @property
    def total_surplus(self) -> float:
        return self.buyer_surplus + self.producer_profit + self.trading_pnl + self.tax - self.subsidy

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_surplus"] = round(self.total_surplus, 6)
        return d


def settle(trade: Trade, buyer: AgentState, seller: AgentState, policy: GoodPolicy, gov: Government) -> SettlementRecord:
    g, q, p = trade.good, trade.quantity, trade.price
    if q <= 0:
        raise SettlementError("trade quantity must be positive")
    if buyer.id == seller.id:
        raise SettlementError("self trade")

    tax_unit = policy.tax_per_unit(p)
    buyer_unit = policy.buyer_cost(p)
    seller_unit = policy.seller_net(p)
    buyer_paid = round(buyer_unit * q, 6)
    seller_received = round(seller_unit * q, 6)
    tax = round(tax_unit * q, 6)
    subsidy = round(policy.subsidy * q, 6)

    if buyer.cash + 1e-9 < buyer_paid:
        raise SettlementError(f"{buyer.id} cannot afford {buyer_paid:.2f} (cash {buyer.cash:.2f})")
    if seller.sellable(g) < q:
        raise SettlementError(f"{seller.id} cannot deliver {q} {g}")
    if buyer.role == "buyer" and buyer.demand_remaining(g) < q:
        raise SettlementError(f"{buyer.id} has no remaining demand for {q} {g}")

    # --- buyer side
    buyer_surplus = 0.0
    consumed_value = 0.0
    buyer.cash = round(buyer.cash - buyer_paid, 6)
    if buyer.role == "buyer":
        for k in range(q):
            v = buyer.next_unit_value(g, offset=k)
            consumed_value += v
            buyer_surplus += v - buyer_unit
        buyer.bought_this_round[g] = buyer.bought_this_round.get(g, 0) + q
        buyer.consumer_surplus += buyer_surplus
    else:  # speculator accumulates inventory at average cost
        held = buyer.inventory.get(g, 0)
        basis = buyer.cost_basis.get(g, 0.0)
        new_held = held + q
        buyer.cost_basis[g] = round((basis * held + buyer_paid) / new_held, 6)
        buyer.inventory[g] = new_held
    buyer.units_bought += q
    if policy.tax_payer == "buyer":
        buyer.taxes_paid += tax

    # --- seller side
    producer_profit = 0.0
    trading_pnl = 0.0
    production_cost = 0.0
    if seller.role == "seller":
        # Make-to-order: the marginal cost of each unit is paid out of the economy on sale.
        for k in range(q):
            c = seller.next_unit_cost(g, offset=k)
            production_cost += c
            producer_profit += seller_unit - c
        seller.cash = round(seller.cash + seller_received - production_cost, 6)
        seller.sold_this_round[g] = seller.sold_this_round.get(g, 0) + q
        seller.producer_profit += producer_profit
    else:
        seller.cash = round(seller.cash + seller_received, 6)
        basis = seller.cost_basis.get(g, 0.0)
        trading_pnl = (seller_unit - basis) * q
        seller.inventory[g] = seller.inventory.get(g, 0) - q
        if seller.inventory[g] == 0:
            seller.cost_basis[g] = 0.0
        seller.trading_pnl += trading_pnl
    seller.units_sold += q
    if policy.tax_payer == "seller":
        seller.taxes_paid += tax

    gov.tax_revenue = round(gov.tax_revenue + tax, 6)
    gov.subsidies_paid = round(gov.subsidies_paid + subsidy, 6)

    return SettlementRecord(
        good=g, price=p, quantity=q, buyer_id=buyer.id, seller_id=seller.id,
        buyer_paid=buyer_paid, seller_received=seller_received, tax=tax, subsidy=subsidy,
        buyer_surplus=round(buyer_surplus, 6), producer_profit=round(producer_profit, 6),
        trading_pnl=round(trading_pnl, 6), consumed_value=round(consumed_value, 6),
        production_cost=round(production_cost, 6),
    )
