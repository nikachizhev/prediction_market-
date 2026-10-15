"""Own z-test / normal CDF (no scipy) and the experiment simulation."""
from __future__ import annotations

import math
import random


def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def z_test(n_a: int, x_a: int, n_b: int, x_b: int) -> tuple[float, float]:
    """One-sided two-proportion z-test (B > A). Returns (z, p_value)."""
    p_hat = (x_a + x_b) / (n_a + n_b)
    se = math.sqrt(p_hat * (1 - p_hat) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 0.0, 0.5
    z = (x_b / n_b - x_a / n_a) / se
    return z, 1.0 - phi(z)


def binomial(rng: random.Random, n: int, p: float) -> int:
    p = min(max(p, 0.0), 1.0)
    if hasattr(rng, "binomialvariate"):  # Python 3.12+
        return rng.binomialvariate(n, p)
    mean, sd = n * p, math.sqrt(n * p * (1 - p))
    return int(min(n, max(0, round(rng.gauss(mean, sd)))))


def simulate(n: int, baseline: float, true_lift: float, rng: random.Random | None = None) -> tuple[int, int]:
    rng = rng or random.Random()
    return binomial(rng, n, baseline), binomial(rng, n, baseline * (1 + true_lift))
