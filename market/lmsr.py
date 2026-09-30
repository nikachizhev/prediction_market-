"""Pure float LMSR math for a binary market. No Django imports."""
from __future__ import annotations

import math


def cost(q_yes: float, q_no: float, b: float) -> float:
    """C(q) = b * ln(exp(q_yes/b) + exp(q_no/b)), via log-sum-exp."""
    m = max(q_yes, q_no)
    return m + b * math.log(math.exp((q_yes - m) / b) + math.exp((q_no - m) / b))


def price_yes(q_yes: float, q_no: float, b: float) -> float:
    """Current YES price (probability). Overflow-safe."""
    d = (q_no - q_yes) / b
    if d > 700:
        return 0.0
    if d < -700:
        return 1.0
    return 1.0 / (1.0 + math.exp(d))


def price_no(q_yes: float, q_no: float, b: float) -> float:
    return 1.0 - price_yes(q_yes, q_no, b)


def trade_cost(side: str, delta: float, q_yes: float, q_no: float, b: float) -> float:
    """Cost of buying `delta` shares of `side` (negative delta = sell, result negative)."""
    if side == "yes":
        return cost(q_yes + delta, q_no, b) - cost(q_yes, q_no, b)
    if side == "no":
        return cost(q_yes, q_no + delta, b) - cost(q_yes, q_no, b)
    raise ValueError(f"unknown side: {side!r}")


def shares_for_amount(side: str, amount: float, q_yes: float, q_no: float, b: float) -> float:
    """Shares of `side` bought for `amount` coins: b * (ln(expm1(m/b) + p) - ln(p))."""
    p = price_yes(q_yes, q_no, b)
    if side == "no":
        p = 1.0 - p
    elif side != "yes":
        raise ValueError(f"unknown side: {side!r}")
    if p <= 0.0:
        raise ValueError("price is zero")
    return b * (math.log(math.expm1(amount / b) + p) - math.log(p))


def initial_q(p0: float, b: float) -> tuple[float, float]:
    """(q_yes, q_no) giving initial YES price p0."""
    if not 0.0 < p0 < 1.0:
        raise ValueError("p0 must be in (0, 1)")
    return b * math.log(p0 / (1.0 - p0)), 0.0


def max_loss(p0: float, b: float) -> float:
    """Worst-case market-maker loss: b * ln(1 / min(p0, 1-p0))."""
    return b * math.log(1.0 / min(p0, 1.0 - p0))
