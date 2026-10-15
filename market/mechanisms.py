"""Binary pricing; LS-LMSR uses a positive seed and the full cost gradient."""
import math
from . import lmsr


def ls_state(q, y, n):
    seed = q.b / (2 * q.alpha)
    return y + seed, n + seed


def ls_cost(q, y, n):
    y, n = ls_state(q, y, n)
    if min(y, n) <= 0:
        raise ValueError("Недопустимое состояние LS-LMSR.")
    return lmsr.cost(y, n, q.alpha * (y + n))


def ls_prices(q, y, n):
    y, n = ls_state(q, y, n)
    b = q.alpha * (y + n)
    s = lmsr.price_yes(y, n, b)
    entropy = -sum(p * math.log(p) for p in (s, 1-s) if p > 0)
    fee = q.alpha * entropy
    return s + fee, 1-s + fee


def probability(q, y, n):
    if q.kind == "cpmm":
        return q.pool_no / (q.pool_yes + q.pool_no)
    if q.kind == "ls_lmsr":
        yes, no = ls_prices(q, y, n)
        return yes / (yes + no)
    return lmsr.price_yes(y, n, q.b)


def trade_cost(q, side, shares):
    if q.kind == "ls_lmsr":
        return ls_cost(q, q.q_yes + (shares if side == "yes" else 0), q.q_no + (shares if side == "no" else 0)) - ls_cost(q, q.q_yes, q.q_no)
    if q.kind == "cpmm":
        x, y = (q.pool_yes, q.pool_no) if side == "yes" else (q.pool_no, q.pool_yes)
        # Stable root of (x+c-shares)(y+c)=xy, valid for buys and sales.
        a = x + y - shares
        root = math.sqrt(a*a + 4*y*shares)
        return 2*y*shares / (root + a)
    return lmsr.trade_cost(side, shares, q.q_yes, q.q_no, q.b)


def shares_for_amount(q, side, amount):
    if q.kind == "cpmm":
        x, y = (q.pool_yes, q.pool_no) if side == "yes" else (q.pool_no, q.pool_yes)
        return amount + x * amount / (y + amount)
    if q.kind != "ls_lmsr":
        return lmsr.shares_for_amount(side, amount, q.q_yes, q.q_no, q.b)
    low, high = 0., max(1., amount * 2)
    while trade_cost(q, side, high) < amount:
        high *= 2
    for _ in range(80):
        mid = (low + high)/2
        if trade_cost(q, side, mid) <= amount:
            low = mid
        else:
            high = mid
    return low


def move_pool(q, side, shares, cost):
    if q.kind == "cpmm":
        q.pool_yes += cost - (shares if side == "yes" else 0)
        q.pool_no += cost - (shares if side == "no" else 0)
