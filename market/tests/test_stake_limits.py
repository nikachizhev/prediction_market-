from decimal import Decimal
from django.test import TestCase
from django.conf import settings
from market import services
from market.models import Trader, Question
from market.services import MarketError

class StakeLimitTests(TestCase):
    def setUp(self):
        self.trader = Trader.objects.create(name="T", pseudonym="t", balance=Decimal("1000"))
        self.q = Question.objects.create(
            title="Q", resolution_criteria="-", status=Question.Status.OPEN,
            b=144, q_yes=0, q_no=0, initial_prob=0.5,
        )

    def test_series_hits_equity_share(self):
        for _ in range(3):
            services.buy(self.trader, self.q, "yes", 100)
        self.trader.refresh_from_db()
        self.assertEqual(services.invested(self.trader, self.q), Decimal("300"))
        with self.assertRaises(MarketError):
            services.buy(self.trader, self.q, "yes", 100)

    def test_topup_opens_room(self):
        for _ in range(3):
            services.buy(self.trader, self.q, "yes", 100)
        self.trader.balance += Decimal("200")
        self.trader.save(update_fields=["balance"])
        self.assertGreaterEqual(services.stake_capacity(self.trader, self.q), Decimal("50"))
        services.buy(self.trader, self.q, "yes", 50)

    def test_other_market_not_affected(self):
        q2 = Question.objects.create(
            title="Q2", resolution_criteria="-", status=Question.Status.OPEN,
            b=144, q_yes=0, q_no=0, initial_prob=0.5,
        )
        for _ in range(3):
            services.buy(self.trader, self.q, "yes", 100)
        services.buy(self.trader, q2, "yes", 50)
