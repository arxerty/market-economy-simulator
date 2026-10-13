import pytest

from market_sim.engine.orderbook import Order, OrderBook


def mk(oid, agent, side, price, qty, expires=1):
    return Order(id=oid, agent_id=agent, good="w", side=side, price=price, quantity=qty, expires_round=expires)


def test_resting_orders_sorted_by_price_then_time():
    b = OrderBook("w")
    b.submit(mk("b1", "A", "buy", 10, 1))
    b.submit(mk("b2", "B", "buy", 12, 1))
    b.submit(mk("b3", "C", "buy", 12, 1))
    b.submit(mk("a1", "D", "sell", 15, 1))
    b.submit(mk("a2", "E", "sell", 14, 1))
    assert [o.id for o in b.bids] == ["b2", "b3", "b1"]
    assert [o.id for o in b.asks] == ["a2", "a1"]
    assert b.best_bid() == 12 and b.best_ask() == 14


def test_trade_executes_at_resting_price_with_time_priority():
    b = OrderBook("w")
    b.submit(mk("a1", "S1", "sell", 10, 1))
    b.submit(mk("a2", "S2", "sell", 10, 1))
    trades = b.submit(mk("b1", "B1", "buy", 12, 1), round_no=3)
    assert len(trades) == 1
    t = trades[0]
    assert (t.price, t.quantity, t.seller_id, t.buyer_id, t.aggressor, t.round) == (10, 1, "S1", "B1", "buy", 3)
    assert [o.id for o in b.asks] == ["a2"]


def test_partial_fill_walks_the_book_and_rests_remainder():
    b = OrderBook("w")
    b.submit(mk("a1", "S1", "sell", 10, 2))
    b.submit(mk("a2", "S2", "sell", 11, 3))
    b.submit(mk("a3", "S3", "sell", 13, 5))
    trades = b.submit(mk("b1", "B1", "buy", 12, 7))
    assert [(t.price, t.quantity) for t in trades] == [(10, 2), (11, 3)]
    assert b.best_bid() == 12 and b.get("b1").remaining == 2
    assert b.best_ask() == 13


def test_incoming_sell_matches_highest_bid():
    b = OrderBook("w")
    b.submit(mk("b1", "B1", "buy", 9, 1))
    b.submit(mk("b2", "B2", "buy", 11, 1))
    trades = b.submit(mk("a1", "S1", "sell", 8, 2))
    assert [(t.buyer_id, t.price) for t in trades] == [("B2", 11), ("B1", 9)]
    assert len(b) == 0


def test_no_trade_when_not_crossing():
    b = OrderBook("w")
    b.submit(mk("a1", "S1", "sell", 10, 1))
    assert b.submit(mk("b1", "B1", "buy", 9.99, 1)) == []
    assert len(b) == 2


def test_self_trade_prevention_skips_own_orders():
    b = OrderBook("w")
    b.submit(mk("a1", "X", "sell", 10, 1))
    b.submit(mk("a2", "Y", "sell", 11, 1))
    trades = b.submit(mk("b1", "X", "buy", 12, 2))
    assert [(t.seller_id, t.price) for t in trades] == [("Y", 11)]
    assert b.get("a1") is not None  # own resting order untouched
    assert b.get("b1").remaining == 1


def test_cancel_and_cancel_agent():
    b = OrderBook("w")
    b.submit(mk("b1", "A", "buy", 10, 1))
    b.submit(mk("b2", "A", "buy", 10, 1))
    b.submit(mk("b3", "B", "buy", 10, 1))
    assert b.cancel("b1").id == "b1"
    assert b.cancel("b1") is None
    assert [o.id for o in b.cancel_agent("A")] == ["b2"]
    assert [o.id for o in b.bids] == ["b3"]


def test_expiry_removes_orders_at_their_round():
    b = OrderBook("w")
    b.submit(mk("b1", "A", "buy", 10, 1, expires=1))
    b.submit(mk("b2", "B", "buy", 10, 1, expires=3))
    assert [o.id for o in b.expire(1)] == ["b1"]
    assert [o.id for o in b.expire(2)] == []
    assert [o.id for o in b.expire(3)] == ["b2"]


def test_depth_aggregates_levels():
    b = OrderBook("w")
    for i, p in enumerate([10, 10, 9, 8]):
        b.submit(mk(f"b{i}", f"A{i}", "buy", p, 2))
    b.submit(mk("a1", "S", "sell", 12, 4))
    d = b.depth(2)
    assert d["bids"] == [[10, 4], [9, 2]]
    assert d["asks"] == [[12, 4]]


def test_invalid_orders_rejected():
    b = OrderBook("w")
    with pytest.raises(ValueError):
        b.submit(mk("x", "A", "buy", 0, 1))
    with pytest.raises(ValueError):
        b.submit(mk("x", "A", "buy", 10, 0))
    with pytest.raises(ValueError):
        b.submit(Order(id="x", agent_id="A", good="other", side="buy", price=1, quantity=1))
    b.submit(mk("dup", "A", "buy", 5, 1))
    with pytest.raises(ValueError):
        b.submit(mk("dup", "A", "buy", 5, 1))


def test_matching_is_deterministic():
    def run():
        b = OrderBook("w")
        out = []
        orders = [mk(f"o{i}", f"A{i % 5}", "buy" if i % 2 else "sell", 10 + (i * 7) % 5, 1 + i % 3) for i in range(40)]
        for o in orders:
            out += [(t.buyer_id, t.seller_id, t.price, t.quantity) for t in b.submit(o)]
        return out, b.depth()
    assert run() == run()


def test_reduce_shrinks_or_cancels():
    b = OrderBook("w")
    b.submit(mk("a1", "S", "sell", 10, 5))
    b.reduce("a1", 2)
    assert b.get("a1").remaining == 2
    b.reduce("a1", 0)
    assert b.get("a1") is None
