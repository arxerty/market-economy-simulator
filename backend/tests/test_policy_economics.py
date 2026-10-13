import pytest

from market_sim.config import PolicyAction, PolicyBounds
from market_sim.engine.economics import equilibrium, max_surplus, monopoly_outcome
from market_sim.engine.policy import GoodPolicy, PolicyState

DEMAND = [100, 90, 80, 70, 60, 50]
SUPPLY = [20, 30, 40, 50, 60, 70]


def test_competitive_equilibrium():
    eq = equilibrium(DEMAND, SUPPLY)
    # Units 1-4 trade (70 >= 50), unit 5 is marginal (60 >= 60) and also trades.
    assert eq.quantity == 5
    assert eq.price == 60
    assert eq.surplus == (100 - 20) + (90 - 30) + (80 - 40) + (70 - 50) + 0
    assert max_surplus(DEMAND, SUPPLY) == eq.surplus


def test_per_unit_tax_reduces_quantity_and_creates_deadweight_loss():
    eq = equilibrium(DEMAND, SUPPLY, GoodPolicy(per_unit_tax=25, tax_payer="seller"))
    assert eq.quantity == 3  # 80 - 40 >= 25, 70 - 50 < 25
    dwl = max_surplus(DEMAND, SUPPLY) - eq.surplus
    assert dwl == 20
    assert eq.buyer_price == eq.price and eq.seller_price == pytest.approx(eq.price - 25)


def test_tax_incidence_does_not_change_quantity():
    a = equilibrium(DEMAND, SUPPLY, GoodPolicy(per_unit_tax=25, tax_payer="seller"))
    b = equilibrium(DEMAND, SUPPLY, GoodPolicy(per_unit_tax=25, tax_payer="buyer"))
    assert a.quantity == b.quantity
    assert a.buyer_price == pytest.approx(b.buyer_price)


def test_binding_price_ceiling_and_floor():
    ceil = equilibrium(DEMAND, SUPPLY, GoodPolicy(price_ceiling=35))
    assert ceil.price == 35 and ceil.quantity == 2
    floor = equilibrium(DEMAND, SUPPLY, GoodPolicy(price_floor=85))
    assert floor.price == 85 and floor.quantity == 2
    slack = equilibrium(DEMAND, SUPPLY, GoodPolicy(price_ceiling=95))
    assert slack.quantity == 5


def test_monopoly_price_above_competitive():
    mono = monopoly_outcome(DEMAND, SUPPLY)
    comp = equilibrium(DEMAND, SUPPLY)
    assert mono.price > comp.price and mono.quantity < comp.quantity


def test_empty_market():
    assert equilibrium([], SUPPLY).quantity == 0
    assert equilibrium([10], [20]).price is None


def test_policy_state_sanitize_clamps_to_bounds():
    ps = PolicyState(["w"], PolicyBounds(max_per_unit_tax=10, max_ad_valorem=0.2))
    a, err = ps.sanitize(PolicyAction(action="set_tax", good="w", rate=50))
    assert err is None and a.rate == 10
    a, _ = ps.sanitize(PolicyAction(action="set_tax", good="w", tax_type="ad_valorem", rate=-1))
    assert a.rate == 0
    _, err = ps.sanitize(PolicyAction(action="set_tax", good="nope", rate=1))
    assert err
    _, err = ps.sanitize(PolicyAction(action="set_price_floor", good="w", value=-3))
    assert err


def test_policy_apply_and_remove():
    ps = PolicyState(["w", "v"])
    ps.apply(PolicyAction(action="set_tax", good="w", rate=5, payer="buyer"))
    assert ps["w"].per_unit_tax == 5 and ps["w"].tax_payer == "buyer" and ps["v"].per_unit_tax == 0
    ps.apply(PolicyAction(action="set_price_floor", good=None, value=3))
    assert ps["w"].price_floor == 3 and ps["v"].price_floor == 3
    ps.apply(PolicyAction(action="set_price_ceiling", good="w", value=2))
    assert ps["w"].price_floor == 2  # floor pulled down to stay consistent
    ps.apply(PolicyAction(action="remove_tax", good="w"))
    ps.apply(PolicyAction(action="clear_controls", good=None))
    assert ps["w"].is_neutral() and ps["v"].is_neutral()


def test_thresholds_match_cost_functions():
    pol = GoodPolicy(per_unit_tax=3, ad_valorem_tax=0.1, tax_payer="seller", subsidy=1)
    p = pol.seller_threshold(50)
    assert pol.seller_net(p) == pytest.approx(50)
    pol_b = GoodPolicy(per_unit_tax=3, ad_valorem_tax=0.1, tax_payer="buyer")
    assert pol_b.buyer_cost(pol_b.buyer_threshold(80)) == pytest.approx(80)
