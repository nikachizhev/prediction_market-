from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from abmock import services as ab
from abmock.models import Experiment
from market import services
from market.models import Payout, Position, Question, Trade, Trader
from market.services import MarketError
from sources.models import GitHubRepo
from sources.sync import sync_all

TRADERS = [
    ("Аня", "Лиса"), ("Борис", "Барсук"), ("Вика", "Сова"),
    ("Гоша", "Енот"), ("Даша", "Ласточка"), ("Егор", "Бобёр"),
]


class Command(BaseCommand):
    help = "Create deterministic demo data."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="wipe market/abmock/sources data first")

    def _history(self, rng, question, traders, start, end, n, bias_yes):
        """Buy via services, then backdate trades evenly between start and end."""
        Question.objects.filter(pk=question.pk).update(created_at=start)
        stamps = sorted(start + (end - start) * rng.random() for _ in range(n))
        for ts in stamps:
            question.refresh_from_db()
            if question.status != Question.Status.OPEN:
                break
            t = rng.choice(traders)
            side = "yes" if rng.random() < bias_yes else "no"
            amount = Decimal(rng.choice([20, 30, 40, 50, 60, 80, 100]))
            try:
                trade = services.buy(t, question, side, amount)
            except MarketError:
                continue
            Trade.objects.filter(pk=trade.pk).update(created_at=ts)

    def handle(self, *args, **opts):
        if opts["reset"]:
            for m in (Payout, Trade, Position, Question, Experiment, GitHubRepo, Trader):
                m.objects.all().delete()
        elif Trader.objects.exists():
            self.stdout.write("Данные уже есть; используйте --reset.")
            return
        rng = random.Random(2024)
        now = timezone.now()
        traders = [Trader.objects.create(name=n, pseudonym=p) for n, p in TRADERS]
        anya, boris, vika, gosha, dasha, egor = traders

        def exp(name, metric, author, lift, hyp):
            return Experiment.objects.create(
                name=name, metric=metric, author=author, hypothesis=hyp,
                baseline_rate=0.04, true_lift=lift, n_per_group=20000,
            )

        e_done = exp("Кнопка «Оформить» крупнее", "конверсия в оформление кредита", anya, 0.3,
                     "Крупная кнопка повысит конверсию в оформление.")
        e_run = exp("Персональные кэшбэк-категории", "доля пользователей с активным кэшбэком", boris, 0.05,
                    "Персональные категории повысят активность.")
        e_draft = exp("Новый экран переводов", "конверсия в повторный перевод", vika, 0.2,
                      "Упрощённый экран увеличит повторные переводы.")
        sync_all()  # creates questions at AB_BASE_RATE
        manual = services.create_question(
            title="Выйдет ли мобильное приложение v5.0 до конца квартала?",
            resolution_criteria="ДА, если релиз v5.0 опубликован в сторах до 31 декабря. Разрешается вручную через /admin/.",
            initial_prob=0.6, deadline=now + timedelta(days=60),
        )
        q = {e.pk: Question.objects.get(source_type="abtest", source_ref=f"ab:{e.pk}") for e in (e_done, e_run, e_draft)}

        self._history(rng, q[e_done.pk], [t for t in traders if t != anya], now - timedelta(days=6), now - timedelta(days=3), 14, 0.65)
        self._history(rng, q[e_run.pk], [t for t in traders if t != boris], now - timedelta(days=5), now - timedelta(days=2), 10, 0.35)
        self._history(rng, q[e_draft.pk], [t for t in traders if t != vika], now - timedelta(days=3), now - timedelta(hours=3), 9, 0.4)
        self._history(rng, manual, traders, now - timedelta(days=4), now - timedelta(hours=1), 12, 0.6)

        ab.start(e_run)
        ab.start(e_done)
        ab.finish(e_done, random.Random(7))
        for m in sync_all():
            self.stdout.write(m)
        self.stdout.write(self.style.SUCCESS(
            f"Готово: {Trader.objects.count()} участников, {Question.objects.count()} вопросов, "
            f"{Trade.objects.count()} сделок. Эксперимент «Новый онбординг карты» создайте вживую."
        ))
