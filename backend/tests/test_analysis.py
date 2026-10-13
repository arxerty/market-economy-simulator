from market_sim.analysis import bubble_metrics, cartel_metrics, gini, hhi, volatility


def test_hhi():
    assert hhi({"a": 10}) == 10000
    assert hhi({"a": 1, "b": 1, "c": 1, "d": 1}) == 2500
    assert hhi({}) is None


def test_volatility_and_gini():
    assert volatility([100, 100, 100]) == 0
    assert volatility([100]) is None
    assert volatility([100, 110, 99]) > 0
    assert gini([1, 1, 1, 1]) == 0
    assert gini([0, 0, 0, 10]) == 0.75


def test_bubble_metrics_peak_and_crash():
    rounds = list(range(1, 11))
    prices = [100, 110, 130, 160, 200, 190, 150, 120, 100, 95]
    m = bubble_metrics(rounds, prices, [100] * 10)
    assert m["peak_round"] == 5 and m["max_deviation"] == 1.0
    assert m["crash_round"] == 7  # first price <= 80% of peak
    assert m["bubble_start_round"] == 3 and m["bubble_detected"]


def test_cartel_metrics_detects_explicit_messages():
    summaries = [{"goods": {"g": {"producer_revenue": 100.0 * 10, "production_cost": 50.0 * 10, "producer_units": 10,
                                  "seller_asks": {"S1": 100, "S2": 100}}}} for _ in range(9)]
    msgs = [{"round": 2, "from": "S1", "text": "Let's all keep the price at 100 or above."},
            {"round": 3, "from": "S2", "text": "Nice weather."}]
    m = cartel_metrics(summaries, "g", msgs, competitive_price=60, monopoly_price=110)
    assert m["markup_over_mc"] == 1.0 and m["lerner_index"] == 0.5
    assert m["collusion_index"] == 0.8
    assert m["explicit_coordination_messages"] == 1 and m["first_explicit_round"] == 2
    assert m["prices_converged"] and m["verdict"] == "explicit coordination"
    m2 = cartel_metrics(summaries, "g", msgs[1:], competitive_price=60, monopoly_price=110)
    assert m2["verdict"].startswith("tacit")
