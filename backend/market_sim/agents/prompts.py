"""Prompt construction. Kept deterministic (sorted JSON) so prompts double as cache keys."""

from __future__ import annotations

import json
from typing import Any

_ROLE_GOALS = {
    "buyer": (
        "You are a BUYER (consumer). Each round you want up to a fixed number of units of each good you "
        "value. Your private value for each additional unit is listed in `you.goods.<good>.unit_values`. "
        "Buying a unit at price p (plus any buyer-side tax) earns you surplus = value - cost. Units you buy "
        "are consumed immediately. Your goal is to maximise total surplus over the whole run while staying "
        "within your cash budget. You may only bid (and cancel your own orders)."
    ),
    "seller": (
        "You are a SELLER (producer). Each round you can produce up to `capacity_remaining` units of each good; "
        "the marginal cost of each additional unit is in `you.goods.<good>.unit_costs`. You only pay the cost "
        "for units you actually sell. Selling at price p (minus any seller-side tax, plus any subsidy) earns "
        "profit = net price - cost. Your goal is to maximise total profit over the whole run. You may only ask "
        "(and cancel your own orders)."
    ),
    "speculator": (
        "You are a SPECULATOR (trader). You do not consume the good; you profit only by buying and later "
        "selling at a higher price. You may bid and ask, but you cannot sell more than you hold (no short "
        "selling) or spend more cash than you have. Your goal is to maximise final wealth "
        "(cash + inventory at market value)."
    ),
}

_RULES = (
    "Market rules: each good trades in a continuous double auction with price-time priority. Orders are "
    "matched in a random order each round; a trade executes at the resting order's price. Orders live for "
    "{ttl} round(s) unless cancelled. Prices are rounded to the tick. Taxes, subsidies and price "
    "floors/ceilings in `policy` apply: bids below a floor and asks above a ceiling are rejected. "
    "At most {max_orders} new orders per round, at most {max_qty} units per order. Invalid or unaffordable "
    "orders are trimmed or rejected."
)

_FORMAT = (
    "Respond with a single JSON object and nothing else:\n"
    '{{"reasoning": "<one or two sentences>", "actions": [ ... ]}}\n'
    "Each action is one of:\n"
    '  {{"type": "bid", "good": "<good id>", "price": <number>, "quantity": <int>}}\n'
    '  {{"type": "ask", "good": "<good id>", "price": <number>, "quantity": <int>}}\n'
    '  {{"type": "cancel", "order_id": "<id or \\"all\\">"}}\n'
    '  {{"type": "hold"}}'
)

_REGULATOR = (
    "You are the GOVERNMENT REGULATOR of this market. Objective: {objective}\n"
    "You observe aggregate market data including each good's fundamental (competitive equilibrium) value. "
    "You may change policy with these actions (rates are bounded: per-unit tax <= {max_pu}, "
    "ad valorem <= {max_av}, subsidy <= {max_sub}):\n"
    '  {{"action": "set_tax", "good": "<id>", "tax_type": "per_unit"|"ad_valorem", "rate": <number>, "payer": "buyer"|"seller"}}\n'
    '  {{"action": "remove_tax", "good": "<id>"}}\n'
    '  {{"action": "set_subsidy", "good": "<id>", "value": <per-unit amount to sellers>}}\n'
    '  {{"action": "set_price_floor"|"set_price_ceiling", "good": "<id>", "value": <price or null>}}\n'
    '  {{"action": "clear_controls", "good": "<id>"}}\n'
    '  {{"action": "hold"}}\n'
    "Ad valorem rates are fractions (0.05 = 5%). Respond with a single JSON object and nothing else:\n"
    '{{"reasoning": "<one or two sentences>", "actions": [ ... ]}}'
)

_MESSAGE = (
    "Before trading starts you may post one short message (max {chars} characters) to a channel that all "
    "other sellers can read. Respond with a single JSON object and nothing else:\n"
    '{{"message": "<your message, or empty string to stay silent>"}}'
)


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def trader_system_prompt(role: str, ttl: int, max_orders: int, max_qty: int) -> str:
    return "\n\n".join([
        "You are an autonomous agent in a simulated market economy experiment.",
        _ROLE_GOALS[role],
        _RULES.format(ttl=ttl, max_orders=max_orders, max_qty=max_qty),
        _FORMAT.format(),
    ])


def trader_user_prompt(obs: dict[str, Any], strategy_prompt: str) -> str:
    parts = [
        f"Round {obs['round']} of {obs['rounds_total']}.",
    ]
    if obs.get("info_lag"):
        parts.append(f"Note: your market data is delayed by {obs['info_lag']} round(s).")
    if strategy_prompt:
        parts.append(f"Your strategy guidance: {strategy_prompt}")
    parts.append("Current state (JSON):\n" + _dump({k: v for k, v in obs.items() if k not in ("round", "rounds_total")}))
    parts.append("Decide your actions for this round.")
    return "\n\n".join(parts)


def regulator_system_prompt(objective: str, bounds: Any) -> str:
    return "\n\n".join([
        "You are an autonomous agent in a simulated market economy experiment.",
        _REGULATOR.format(objective=objective or "maximise total welfare while keeping prices stable",
                          max_pu=bounds.max_per_unit_tax, max_av=bounds.max_ad_valorem, max_sub=bounds.max_subsidy),
    ])


def message_system_prompt(role: str, chars: int) -> str:
    return "\n\n".join([
        "You are an autonomous agent in a simulated market economy experiment.",
        _ROLE_GOALS[role],
        _MESSAGE.format(chars=chars),
    ])


def message_user_prompt(obs: dict[str, Any], strategy_prompt: str) -> str:
    parts = [f"Round {obs['round']} of {obs['rounds_total']}. The trading phase starts after this message."]
    if strategy_prompt:
        parts.append(f"Your strategy guidance: {strategy_prompt}")
    parts.append("Current state (JSON):\n" + _dump({k: v for k, v in obs.items() if k not in ("round", "rounds_total")}))
    return "\n\n".join(parts)
