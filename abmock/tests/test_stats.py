import random

import pytest

from abmock.stats import phi, simulate, z_test


def test_phi_known():
    assert phi(0) == pytest.approx(0.5)
    assert phi(1.96) == pytest.approx(0.9750021, abs=1e-6)
    assert phi(-1.645) == pytest.approx(0.0499849, abs=1e-6)


def test_z_test_known_values():
    # p_hat=0.125, se=sqrt(.125*.875*.002)=0.014790, z=0.05/se=3.3806
    z, p = z_test(1000, 100, 1000, 150)
    assert z == pytest.approx(3.3806, abs=1e-3)
    assert p == pytest.approx(0.000362, abs=2e-5)


def test_z_test_no_difference_and_direction():
    z, p = z_test(500, 50, 500, 50)
    assert z == 0 and p == pytest.approx(0.5)
    _, p_worse = z_test(1000, 150, 1000, 100)
    assert p_worse > 0.99


def test_simulate_deterministic_and_reasonable():
    a1 = simulate(20000, 0.04, 0.3, random.Random(1))
    assert a1 == simulate(20000, 0.04, 0.3, random.Random(1))
    assert 600 < a1[0] < 1000 and a1[1] > a1[0]
