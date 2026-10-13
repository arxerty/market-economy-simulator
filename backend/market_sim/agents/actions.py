"""Structured actions: parsing LLM output, validation and sanitisation.

Pipeline: raw text -> :func:`parse_decision` (lenient JSON extraction + schema
validation per action) -> :func:`sanitize_trade_actions` (enforce market rules and
the agent's budget / inventory / capacity) -> orders the engine can trust.
Everything rejected or modified is reported so it can be logged.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..config import MarketConfig, PolicyAction
from ..engine.orderbook import Order, OrderBook
from ..engine.policy import PolicyState
from ..engine.state import AgentState

_SYNONYMS = {
    "buy": "bid", "bid": "bid", "ask": "ask", "sell": "ask", "offer": "ask",
    "cancel": "cancel", "hold": "hold", "wait": "hold", "pass": "hold", "none": "hold",
}


class TradeAction(BaseModel):
    type: Literal["bid", "ask", "cancel", "hold"]
    good: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    order_id: Optional[str] = None

    @field_validator("price")
    @classmethod
    def _finite(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not math.isfinite(v):
            raise ValueError("price must be finite")
        return v


class Decision(BaseModel):
    reasoning: str = ""
    actions: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "message": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["bid", "ask", "cancel", "hold"]},
                    "good": {"type": "string"},
                    "price": {"type": "number"},
                    "quantity": {"type": "integer"},
                    "order_id": {"type": "string"},
                },
                "required": ["type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reasoning", "actions"],
    "additionalProperties": False,
}

POLICY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": [
                        "set_tax", "remove_tax", "set_price_floor", "set_price_ceiling",
                        "set_subsidy", "clear_controls", "hold"]},
                    "good": {"type": "string"},
                    "tax_type": {"type": "string", "enum": ["per_unit", "ad_valorem"]},
                    "rate": {"type": "number"},
                    "payer": {"type": "string", "enum": ["buyer", "seller"]},
                    "value": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reasoning", "actions"],
    "additionalProperties": False,
}


class ParseError(ValueError):
    pass


def extract_json(text: str) -> Any:
    """Find the first balanced JSON object/array in ``text`` (handles ```json fences and chatter)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(opener, start + 1)
    raise ParseError("no JSON object found in response")


def parse_decision(text: str) -> Decision:
    data = extract_json(text)
    if isinstance(data, list):
        data = {"actions": data}
    if not isinstance(data, dict):
        raise ParseError("response is not a JSON object")
    if "actions" not in data:
        if "action" in data and isinstance(data["action"], list):
            data = {**data, "actions": data["action"]}
        elif "type" in data or "action" in data:
            data = {"reasoning": data.get("reasoning", ""), "actions": [data], "message": data.get("message")}
        else:
            data = {**data, "actions": []}
    if not isinstance(data.get("actions"), list):
        raise ParseError("'actions' must be a list")
    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning)
    message = data.get("message")
    return Decision(reasoning=reasoning[:1000], actions=data["actions"],
                    message=message if isinstance(message, str) else None)


def coerce_trade_action(raw: Any) -> TradeAction:
    if not isinstance(raw, dict):
        raise ValueError("action must be an object")
    raw = dict(raw)
    kind = str(raw.pop("type", raw.pop("action", ""))).strip().lower()
    if kind not in _SYNONYMS:
        raise ValueError(f"unknown action type '{kind}'")
    raw["type"] = _SYNONYMS[kind]
    if "qty" in raw and "quantity" not in raw:
        raw["quantity"] = raw.pop("qty")
    if "quantity" in raw and isinstance(raw["quantity"], float):
        raw["quantity"] = int(raw["quantity"])
    allowed = {"type", "good", "price", "quantity", "order_id"}
    return TradeAction.model_validate({k: v for k, v in raw.items() if k in allowed})


def coerce_policy_action(raw: Any) -> PolicyAction:
    if not isinstance(raw, dict):
        raise ValueError("policy action must be an object")
    raw = dict(raw)
    if "action" not in raw and "type" in raw:
        raw["action"] = raw.pop("type")
    allowed = set(PolicyAction.model_fields)
    return PolicyAction.model_validate({k: v for k, v in raw.items() if k in allowed})


@dataclass
class SanitizeResult:
    orders: list[Order] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    adjusted: list[dict[str, Any]] = field(default_factory=list)
    holds: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "orders": [
                {"id": o.id, "good": o.good, "side": o.side, "price": o.price, "quantity": o.quantity}
                for o in self.orders
            ],
            "cancels": self.cancels,
            "rejected": self.rejected,
            "adjusted": self.adjusted,
        }


def _round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 6)


def committed_cash(agent: AgentState, books: dict[str, OrderBook], policy: PolicyState) -> float:
    total = 0.0
    for good, book in books.items():
        for o in book.orders_for(agent.id):
            if o.side == "buy":
                total += policy[good].buyer_cost(o.price) * o.remaining
    return total


def committed_qty(agent: AgentState, books: dict[str, OrderBook], good: str, side: str,
                  exclude: Optional[list[str]] = None) -> int:
    skip = set(exclude or ())
    return sum(o.remaining for o in books[good].orders_for(agent.id) if o.side == side and o.id not in skip)


def sanitize_trade_actions(
    raw_actions: list[Any],
    agent: AgentState,
    books: dict[str, OrderBook],
    policy: PolicyState,
    market: MarketConfig,
    ticks: dict[str, float],
    round_no: int,
    id_prefix: str,
) -> SanitizeResult:
    res = SanitizeResult()
    available_cash = agent.cash - committed_cash(agent, books, policy)
    pending_buy: dict[str, int] = {}
    pending_sell: dict[str, int] = {}
    n_orders = 0

    for idx, raw in enumerate(raw_actions):
        try:
            act = coerce_trade_action(raw)
        except (ValueError, ValidationError) as exc:
            res.rejected.append({"action": raw, "reason": f"invalid: {str(exc).splitlines()[0]}"})
            continue

        if act.type == "hold":
            res.holds += 1
            continue

        if act.type == "cancel":
            oid = act.order_id
            if oid in (None, "", "all", "*"):
                targets = [o.id for g, b in books.items() if act.good in (None, g) for o in b.orders_for(agent.id)]
            else:
                targets = [oid] if any((o := b.get(oid)) is not None and o.agent_id == agent.id for b in books.values()) else []
                if not targets:
                    res.rejected.append({"action": raw, "reason": "unknown order id or not owned by agent"})
                    continue
            for oid in targets:
                if oid not in res.cancels:
                    res.cancels.append(oid)
                    # Freed commitments become available within the same decision.
                    for g, b in books.items():
                        o = b.get(oid)
                        if o is not None and o.side == "buy":
                            available_cash += policy[g].buyer_cost(o.price) * o.remaining
            continue

        # bid / ask
        if n_orders >= market.max_orders_per_agent:
            res.rejected.append({"action": raw, "reason": f"max {market.max_orders_per_agent} orders per round"})
            continue
        good = act.good
        if good is None and len(books) == 1:
            good = next(iter(books))
        if good not in books:
            res.rejected.append({"action": raw, "reason": f"unknown good '{act.good}'"})
            continue
        if act.price is None or act.price <= 0:
            res.rejected.append({"action": raw, "reason": "price must be a positive number"})
            continue
        if act.quantity is None or act.quantity <= 0:
            res.rejected.append({"action": raw, "reason": "quantity must be a positive integer"})
            continue
        side = "buy" if act.type == "bid" else "sell"
        if side == "buy" and not agent.can_buy(good):
            res.rejected.append({"action": raw, "reason": f"{agent.role} cannot bid on {good}"})
            continue
        if side == "sell" and not agent.can_sell(good):
            res.rejected.append({"action": raw, "reason": f"{agent.role} cannot ask on {good}"})
            continue

        notes: list[str] = []
        pol = policy[good]
        price = _round_tick(min(act.price, market.max_price), ticks[good])
        if price != act.price:
            notes.append("price rounded to tick / capped")
        # Price controls: clamp in the agent-favourable direction, reject the illegal one.
        if side == "buy":
            if pol.price_floor is not None and price < pol.price_floor:
                res.rejected.append({"action": raw, "reason": f"bid below price floor {pol.price_floor}"})
                continue
            if pol.price_ceiling is not None and price > pol.price_ceiling:
                price = pol.price_ceiling
                notes.append("bid clamped to price ceiling")
        else:
            if pol.price_ceiling is not None and price > pol.price_ceiling:
                res.rejected.append({"action": raw, "reason": f"ask above price ceiling {pol.price_ceiling}"})
                continue
            if pol.price_floor is not None and price < pol.price_floor:
                price = pol.price_floor
                notes.append("ask clamped to price floor")
        if price <= 0:
            res.rejected.append({"action": raw, "reason": "price rounds to zero"})
            continue

        qty = min(act.quantity, market.max_quantity_per_order)
        if qty < act.quantity:
            notes.append(f"quantity capped at {market.max_quantity_per_order}")
        if side == "buy":
            role_cap = agent.buyable(good) - committed_qty(agent, books, good, "buy", res.cancels) - pending_buy.get(good, 0)
            unit_cost = pol.buyer_cost(price)
            cash_cap = int(max(0.0, available_cash) // unit_cost) if unit_cost > 0 else 0
            cap = min(role_cap, cash_cap)
            reason = "no remaining demand" if role_cap <= 0 else "insufficient cash"
        else:
            already = committed_qty(agent, books, good, "sell", res.cancels) + pending_sell.get(good, 0)
            cap = agent.sellable(good) - already
            reason = "no remaining capacity" if agent.role == "seller" else "insufficient inventory"
            if agent.role == "seller" and cap > 0:
                # Producers must be able to fund any loss on units sold below marginal cost.
                net = pol.seller_net(price)
                budget = max(0.0, agent.cash - committed_cash(agent, books, policy))
                funded = 0
                for k in range(min(cap, qty)):
                    budget -= max(0.0, agent.next_unit_cost(good, already + k) - net)
                    if budget < -1e-9:
                        break
                    funded += 1
                if funded < min(cap, qty):
                    cap, reason = funded, "cannot fund production at this price"
        if cap <= 0:
            res.rejected.append({"action": raw, "reason": reason})
            continue
        if qty > cap:
            notes.append(f"quantity reduced {qty}->{cap} ({reason})")
            qty = cap

        if side == "buy":
            available_cash -= pol.buyer_cost(price) * qty
            pending_buy[good] = pending_buy.get(good, 0) + qty
        else:
            pending_sell[good] = pending_sell.get(good, 0) + qty
        order = Order(
            id=f"{id_prefix}-{idx}", agent_id=agent.id, good=good, side=side, price=price,
            quantity=qty, round_placed=round_no, expires_round=round_no + market.order_ttl - 1,
        )
        res.orders.append(order)
        n_orders += 1
        if notes:
            res.adjusted.append({"order_id": order.id, "notes": notes})
    return res
