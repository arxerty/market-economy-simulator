import pytest

from market_sim.engine.orderbook import Trade
from market_sim.engine.policy import GoodPolicy
from market_sim.engine.settlement import Government, SettlementError, settle
from market_sim.engine.state import AgentState


def buyer(cash=1000.0, v=100.0, units=2, decay=0.0):
    return AgentState(id="B", role="buyer", cash=cash, valuations={"w": v}, units_per_round=units, valuation_decay=decay)


def seller(cost=40.0, cap=5, slope=0.0):
    return AgentState(id="S", role="seller", cash=0.0, costs={"w": cost}, capacity={"w": cap}, cost_slope=slope)


def spec(aid="X", cash=1000.0, inv=0):
    return AgentState(id=aid, role="speculator", cash=cash, inventory={"w": inv})


def trade(b, s, price, qty):
    return Trade(good="w", price=price, quantity=qty, buyer_id=b.id, seller_id=s.id,
                 buy_order_id="bo", sell_order_id="so", aggressor="buy")


def total_cash(*agents, gov):
    return sum(a.cash for a in agents) + gov.balance


def test_untaxed_trade_transfers_and_surplus():
    b, s, g = buyer(), seller(), Government()
    before = total_cash(b, s, gov=g)
    rec = settle(trade(b, s, 70, 2), b, s, GoodPolicy(), g)
    assert b.cash == 860 and s.cash == 60  # 140 revenue - 80 production cost
    assert rec.buyer_surplus == 60 and rec.producer_profit == 60
    assert rec.total_surplus == 120
    assert b.bought_this_round["w"] == 2 and s.sold_this_round["w"] == 2
    assert b.demand_remaining("w") == 0 and s.capacity_remaining("w") == 3
    # cash is conserved except for production cost paid out of the economy
    assert total_cash(b, s, gov=g) == pytest.approx(before - rec.production_cost)


def test_per_unit_tax_paid_by_seller():
    b, s, g = buyer(), seller(), Government()
    pol = GoodPolicy(per_unit_tax=10, tax_payer="seller")
    rec = settle(trade(b, s, 70, 1), b, s, pol, g)
    assert b.cash == 930 and s.cash == 20  # 70 - 10 tax - 40 cost
    assert rec.tax == 10 and g.tax_revenue == 10
    assert rec.producer_profit == 20 and rec.buyer_surplus == 30
    # Tax is a transfer: total surplus = v - c
    assert rec.total_surplus == pytest.approx(60)
    assert s.taxes_paid == 10


def test_ad_valorem_tax_paid_by_buyer():
    b, s, g = buyer(), seller(), Government()
    pol = GoodPolicy(ad_valorem_tax=0.1, tax_payer="buyer")
    before = total_cash(b, s, gov=g)
    rec = settle(trade(b, s, 50, 2), b, s, pol, g)
    assert rec.buyer_paid == pytest.approx(110) and rec.seller_received == pytest.approx(100)
    assert g.tax_revenue == pytest.approx(10)
    assert rec.buyer_surplus == pytest.approx(2 * (100 - 55))
    assert total_cash(b, s, gov=g) == pytest.approx(before - rec.production_cost)


def test_subsidy_paid_to_seller_by_government():
    b, s, g = buyer(), seller(), Government()
    rec = settle(trade(b, s, 50, 1), b, s, GoodPolicy(subsidy=5), g)
    assert s.cash == 15 and g.subsidies_paid == 5 and g.balance == -5  # 50 + 5 - 40
    assert rec.total_surplus == pytest.approx(60)


def test_increasing_marginal_cost_and_decaying_value():
    b, s, g = buyer(v=100, decay=0.5), seller(cost=10, slope=5), Government()
    rec = settle(trade(b, s, 40, 2), b, s, GoodPolicy(), g)
    assert rec.consumed_value == pytest.approx(150)  # 100 + 50
    assert rec.production_cost == pytest.approx(25)  # 10 + 15
    assert rec.buyer_surplus == pytest.approx(70) and rec.producer_profit == pytest.approx(55)


def test_speculator_cost_basis_and_realised_pnl():
    s, x, b, g = seller(cost=10), spec(cash=500), buyer(), Government()
    settle(trade(x, s, 20, 2), x, s, GoodPolicy(), g)
    settle(trade(x, s, 26, 1), x, s, GoodPolicy(), g)
    assert x.inventory["w"] == 3 and x.cost_basis["w"] == pytest.approx(22)
    rec = settle(trade(b, x, 30, 2), b, x, GoodPolicy(), g)
    assert rec.trading_pnl == pytest.approx(16)
    assert x.inventory["w"] == 1 and x.cash == pytest.approx(500 - 66 + 60)


def test_speculator_to_speculator():
    x, y, g = spec("X", 0, inv=2), spec("Y", 100), Government()
    x.cost_basis["w"] = 10
    settle(trade(y, x, 15, 2), y, x, GoodPolicy(), g)
    assert x.inventory["w"] == 0 and x.cost_basis["w"] == 0 and x.trading_pnl == 10
    assert y.inventory["w"] == 2 and y.cost_basis["w"] == 15


@pytest.mark.parametrize("make,err", [
    (lambda: (buyer(cash=10), seller()), "afford"),
    (lambda: (buyer(), seller(cap=1)), "deliver"),
    (lambda: (buyer(units=1), seller()), "demand"),
])
def test_settlement_guards(make, err):
    b, s = make()
    with pytest.raises(SettlementError, match=err):
        settle(trade(b, s, 50, 2), b, s, GoodPolicy(), Government())


def test_no_short_selling():
    x, b = spec(inv=1), buyer()
    with pytest.raises(SettlementError):
        settle(trade(b, x, 50, 2), b, x, GoodPolicy(), Government())
