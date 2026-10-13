"""Limit order book with price-time priority (continuous double auction).

The book is purely mechanical: it knows nothing about agents' cash or inventory.
Affordability is checked before an order reaches the book (see ``agents.actions``).
Trades execute at the resting order's price. An incoming order never matches a
resting order from the same agent (self-trade prevention: the resting order is skipped).
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal, Optional

Side = Literal["buy", "sell"]


@dataclass
class Order:
    id: str
    agent_id: str
    good: str
    side: Side
    price: float
    quantity: int
    round_placed: int = 0
    expires_round: int = 0  # order is removed at the end of this round
    seq: int = 0  # assigned by the book; lower = earlier
    remaining: int = field(default=-1)

    def __post_init__(self) -> None:
        if self.remaining < 0:
            self.remaining = self.quantity

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    good: str
    price: float
    quantity: int
    buyer_id: str
    seller_id: str
    buy_order_id: str
    sell_order_id: str
    aggressor: Side
    round: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class OrderBook:
    def __init__(self, good: str) -> None:
        self.good = good
        self._bids: list[tuple[tuple[float, int], Order]] = []  # key: (-price, seq)
        self._asks: list[tuple[tuple[float, int], Order]] = []  # key: (price, seq)
        self._orders: dict[str, Order] = {}
        self._seq = 0

    # ----------------------------------------------------------------- queries
    def __len__(self) -> int:
        return len(self._orders)

    def __contains__(self, order_id: str) -> bool:
        return order_id in self._orders

    def get(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    @property
    def bids(self) -> list[Order]:
        return [o for _, o in self._bids]

    @property
    def asks(self) -> list[Order]:
        return [o for _, o in self._asks]

    def best_bid(self) -> Optional[float]:
        return self._bids[0][1].price if self._bids else None

    def best_ask(self) -> Optional[float]:
        return self._asks[0][1].price if self._asks else None

    def orders_for(self, agent_id: str) -> list[Order]:
        return [o for o in self._orders.values() if o.agent_id == agent_id]

    def depth(self, levels: int = 15) -> dict[str, list[list[float]]]:
        """Aggregated quantity per price level, best first."""
        return {
            "bids": _aggregate(self.bids, levels),
            "asks": _aggregate(self.asks, levels),
        }

    # ---------------------------------------------------------------- mutation
    def submit(self, order: Order, round_no: int = 0) -> list[Trade]:
        if order.good != self.good:
            raise ValueError(f"order for {order.good} submitted to {self.good} book")
        if order.quantity <= 0 or order.price <= 0:
            raise ValueError("order quantity and price must be positive")
        if order.id in self._orders:
            raise ValueError(f"duplicate order id {order.id}")
        self._seq += 1
        order.seq = self._seq
        trades: list[Trade] = []
        opposite = self._asks if order.side == "buy" else self._bids

        i = 0
        while order.remaining > 0 and i < len(opposite):
            resting = opposite[i][1]
            crosses = order.price >= resting.price if order.side == "buy" else order.price <= resting.price
            if not crosses:
                break
            if resting.agent_id == order.agent_id:
                i += 1  # self-trade prevention: skip, keep the resting order
                continue
            qty = min(order.remaining, resting.remaining)
            order.remaining -= qty
            resting.remaining -= qty
            buy, sell = (order, resting) if order.side == "buy" else (resting, order)
            trades.append(Trade(
                good=self.good, price=resting.price, quantity=qty,
                buyer_id=buy.agent_id, seller_id=sell.agent_id,
                buy_order_id=buy.id, sell_order_id=sell.id,
                aggressor=order.side, round=round_no,
            ))
            if resting.remaining == 0:
                opposite.pop(i)
                del self._orders[resting.id]

        if order.remaining > 0:
            self._insert(order)
        return trades

    def cancel(self, order_id: str) -> Optional[Order]:
        order = self._orders.pop(order_id, None)
        if order is None:
            return None
        book = self._bids if order.side == "buy" else self._asks
        key = self._key(order)
        idx = bisect.bisect_left(book, key, key=lambda e: e[0])
        if idx < len(book) and book[idx][1] is order:
            book.pop(idx)
        else:  # pragma: no cover - defensive, keys are unique
            book[:] = [e for e in book if e[1] is not order]
        return order

    def cancel_many(self, order_ids: Iterable[str]) -> list[Order]:
        return [o for oid in list(order_ids) if (o := self.cancel(oid)) is not None]

    def cancel_agent(self, agent_id: str) -> list[Order]:
        return self.cancel_many([o.id for o in self.orders_for(agent_id)])

    def expire(self, round_no: int) -> list[Order]:
        """Remove every order whose ``expires_round`` is <= ``round_no``."""
        return self.cancel_many([o.id for o in self._orders.values() if o.expires_round <= round_no])

    def reduce(self, order_id: str, new_remaining: int) -> None:
        """Shrink a resting order (used when an agent's means fall below its commitments)."""
        order = self._orders.get(order_id)
        if order is None:
            return
        if new_remaining <= 0:
            self.cancel(order_id)
        else:
            order.remaining = min(order.remaining, new_remaining)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _key(order: Order) -> tuple[float, int]:
        return (-order.price, order.seq) if order.side == "buy" else (order.price, order.seq)

    def _insert(self, order: Order) -> None:
        book = self._bids if order.side == "buy" else self._asks
        key = self._key(order)
        bisect.insort(book, (key, order), key=lambda e: e[0])
        self._orders[order.id] = order


def _aggregate(orders: list[Order], levels: int) -> list[list[float]]:
    out: list[list[float]] = []
    for o in orders:
        if out and out[-1][0] == o.price:
            out[-1][1] += o.remaining
        else:
            if len(out) >= levels:
                break
            out.append([o.price, o.remaining])
    return out
