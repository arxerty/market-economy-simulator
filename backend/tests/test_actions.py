import pytest

from market_sim.agents.actions import ParseError, extract_json, parse_decision, sanitize_trade_actions
from market_sim.config import MarketConfig, PolicyAction
from market_sim.engine.orderbook import Order, OrderBook
from market_sim.engine.policy import PolicyState
from market_sim.engine.state import AgentState

MARKET = MarketConfig(max_orders_per_agent=3, max_quantity_per_order=10)


def ctx():
    books = {"w": OrderBook("w")}
    return books, PolicyState(["w"])


def run(actions, agent, books=None, policy=None, round_no=1):
    b, p = ctx()
    return sanitize_trade_actions(actions, agent, books or b, policy or p, MARKET, {"w": 0.5}, round_no, "R1-A")


def buyer(cash=100.0):
    return AgentState(id="B", role="buyer", cash=cash, valuations={"w": 50}, units_per_round=3)


def seller():
    return AgentState(id="S", role="seller", costs={"w": 10}, capacity={"w": 4})


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('I think {"a": {"b": "}"}} is right') == {"a": {"b": "}"}}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(ParseError):
        extract_json("no json here")


def test_parse_decision_accepts_single_action_and_synonyms():
    d = parse_decision('{"type": "buy", "good": "w", "price": 10, "quantity": 1}')
    assert len(d.actions) == 1
    d = parse_decision('{"reasoning": "x", "actions": [{"action": "sell"}]}')
    assert d.reasoning == "x"
    with pytest.raises(ParseError):
        parse_decision('{"actions": "bid"}')


def test_valid_bid_passes_and_rounds_to_tick():
    r = run([{"type": "bid", "good": "w", "price": 20.3, "quantity": 2}], buyer())
    assert len(r.orders) == 1
    o = r.orders[0]
    assert (o.side, o.price, o.quantity, o.expires_round) == ("buy", 20.5, 2, 1)
    assert r.adjusted


def test_bid_quantity_trimmed_to_cash_and_demand():
    r = run([{"type": "bid", "good": "w", "price": 40, "quantity": 9}], buyer(cash=100))
    assert r.orders[0].quantity == 2  # cash allows 2
    r = run([{"type": "bid", "good": "w", "price": 1, "quantity": 9}], buyer(cash=1000))
    assert r.orders[0].quantity == 3  # demand allows 3


def test_cash_reserved_across_multiple_bids():
    r = run([{"type": "bid", "good": "w", "price": 40, "quantity": 2},
             {"type": "bid", "good": "w", "price": 30, "quantity": 1}], buyer(cash=100))
    assert [o.quantity for o in r.orders] == [2]
    assert r.rejected[0]["reason"] == "insufficient cash"


def test_role_permissions():
    r = run([{"type": "ask", "good": "w", "price": 10, "quantity": 1}], buyer())
    assert not r.orders and "cannot ask" in r.rejected[0]["reason"]
    r = run([{"type": "bid", "good": "w", "price": 10, "quantity": 1}], seller())
    assert not r.orders and "cannot bid" in r.rejected[0]["reason"]


def test_seller_capacity_and_order_limit():
    r = run([{"type": "ask", "good": "w", "price": 12, "quantity": 3},
             {"type": "ask", "good": "w", "price": 13, "quantity": 3}], seller())
    assert [o.quantity for o in r.orders] == [3, 1]


def test_garbage_actions_rejected_individually():
    r = run([
        {"type": "bid", "good": "w", "price": "abc", "quantity": 1},
        {"type": "launch_rocket"},
        "not an object",
        {"type": "bid", "good": "zzz", "price": 5, "quantity": 1},
        {"type": "bid", "good": "w", "price": -5, "quantity": 1},
        {"type": "bid", "good": "w", "price": 5, "quantity": 0},
        {"type": "bid", "good": "w", "price": float("inf"), "quantity": 1},
        {"type": "hold"},
        {"type": "bid", "good": "w", "price": 5, "quantity": 1},
    ], buyer())
    assert len(r.orders) == 1 and len(r.rejected) == 7 and r.holds == 1


def test_max_orders_per_agent():
    acts = [{"type": "ask", "good": "w", "price": 10 + i, "quantity": 1} for i in range(5)]
    s = AgentState(id="S", role="seller", costs={"w": 1}, capacity={"w": 10})
    r = run(acts, s)
    assert len(r.orders) == 3 and len(r.rejected) == 2


def test_price_controls():
    books, pol = ctx()
    pol.apply(PolicyAction(action="set_price_floor", good="w", value=20))
    pol.apply(PolicyAction(action="set_price_ceiling", good="w", value=30))
    r = run([{"type": "bid", "good": "w", "price": 15, "quantity": 1},
             {"type": "bid", "good": "w", "price": 35, "quantity": 1}], buyer(), books, pol)
    assert len(r.rejected) == 1 and r.orders[0].price == 30
    r = run([{"type": "ask", "good": "w", "price": 40, "quantity": 1},
             {"type": "ask", "good": "w", "price": 5, "quantity": 1}], seller(), books, pol)
    assert len(r.rejected) == 1 and r.orders[0].price == 20


def test_buyer_tax_included_in_affordability():
    books, pol = ctx()
    pol.apply(PolicyAction(action="set_tax", good="w", rate=10, payer="buyer"))
    r = run([{"type": "bid", "good": "w", "price": 40, "quantity": 3}], buyer(cash=100), books, pol)
    assert r.orders[0].quantity == 2  # 2 * 50 = 100


def test_cancel_own_orders_only_and_frees_commitments():
    books, pol = ctx()
    books["w"].submit(Order(id="mine", agent_id="B", good="w", side="buy", price=40, quantity=2, expires_round=5))
    books["w"].submit(Order(id="theirs", agent_id="Z", good="w", side="buy", price=40, quantity=2, expires_round=5))
    r = run([{"type": "cancel", "order_id": "theirs"}], buyer(cash=100), books, pol)
    assert not r.cancels and r.rejected
    # 80 already committed to the resting bid: only 20 cash left
    r = run([{"type": "bid", "good": "w", "price": 40, "quantity": 1}], buyer(cash=100), books, pol)
    assert not r.orders and r.rejected[0]["reason"] == "insufficient cash"
    # plenty of cash, but 2 of 3 units of demand are already committed
    r = run([{"type": "bid", "good": "w", "price": 40, "quantity": 3}], buyer(cash=1000), books, pol)
    assert r.orders[0].quantity == 1 and r.adjusted
    # cancelling frees both cash and demand within the same decision
    r = run([{"type": "cancel", "order_id": "all"}, {"type": "bid", "good": "w", "price": 40, "quantity": 3}],
            buyer(cash=100), books, pol)
    assert r.cancels == ["mine"] and r.orders[0].quantity == 2


def test_speculator_inventory_limit():
    x = AgentState(id="X", role="speculator", cash=0, inventory={"w": 2})
    r = run([{"type": "ask", "good": "w", "price": 10, "quantity": 5}], x)
    assert r.orders[0].quantity == 2
    r = run([{"type": "bid", "good": "w", "price": 10, "quantity": 5}], x)
    assert not r.orders
