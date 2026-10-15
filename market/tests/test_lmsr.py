import math

import pytest

from market import lmsr


def test_prices_sum_to_one():
    for qy, qn in [(0, 0), (50, -20), (1000, 0), (-300, 400)]:
        assert lmsr.price_yes(qy, qn, 200) + lmsr.price_no(qy, qn, 200) == pytest.approx(1.0)


def test_initial_price_equals_p0():
    for p0 in (0.1, 0.25, 0.5, 0.9):
        qy, qn = lmsr.initial_q(p0, 200)
        assert lmsr.price_yes(qy, qn, 200) == pytest.approx(p0)


@pytest.mark.parametrize("side", ["yes", "no"])
def test_closed_form_matches_cost_difference(side):
    qy, qn = lmsr.initial_q(0.25, 200)
    for amount in (1, 50, 100, 300):
        d = lmsr.shares_for_amount(side, amount, qy, qn, 200)
        assert lmsr.trade_cost(side, d, qy, qn, 200) == pytest.approx(amount, rel=1e-9)


def test_reference_bet_moves_price_to_about_070():
    d = lmsr.shares_for_amount("yes", 100, 0, 0, 200)
    assert lmsr.price_yes(d, 0, 200) == pytest.approx(0.70, abs=0.02)


def test_buy_then_sell_same_quantity_is_free():
    qy, qn = lmsr.initial_q(0.3, 200)
    d = 37.5
    buy = lmsr.trade_cost("yes", d, qy, qn, 200)
    sell = lmsr.trade_cost("yes", -d, qy + d, qn, 200)
    assert buy + sell == pytest.approx(0.0, abs=1e-9)


def test_max_loss_bound():
    for p0 in (0.25, 0.5, 0.8):
        b = 200
        qy, qn = lmsr.initial_q(p0, b)
        bound = lmsr.max_loss(p0, b)
        # worst case: market is pushed far towards one side, then that side wins
        for side in ("yes", "no"):
            delta = 50000.0
            paid = lmsr.trade_cost(side, delta, qy, qn, b)
            loss = delta - paid
            assert loss <= bound + 1e-6
    assert lmsr.max_loss(0.5, 200) == pytest.approx(200 * math.log(2))


def test_no_overflow_for_large_q():
    assert lmsr.price_yes(1e6, 0, 200) == 1.0
    assert lmsr.price_yes(-1e6, 0, 200) == 0.0
    assert math.isfinite(lmsr.cost(1e6, -1e6, 200))
    assert lmsr.cost(1e6, 0, 200) == pytest.approx(1e6)
