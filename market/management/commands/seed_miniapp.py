"""Demo markets for the Mini App: user-created, with a week of anonymous trading history."""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from market import services
from market.models import Question, Trade, Trader
from market.services import MarketError

PEOPLE = [("Аня", "Лиса"), ("Борис", "Барсук"), ("Вика", "Сова"), ("Гоша", "Енот"), ("Даша", "Ласточка"),
          ("Егор", "Бобёр"), ("Женя", "Выдра"), ("Зоя", "Коала")]

# (title, criteria, category, days until close, YES bias)
MARKETS = [
    ("Выйдет ли GTA VI до конца 2026 года?", "ДА, если игра поступит в продажу хотя бы на одной платформе до 31.12.2026.", "games", 60, 0.7),
    ("Сдадим матан всей группой без пересдач?", "По ведомости после сессии: ни одного «неуд».", "study", 12, 0.35),
    ("Наберёт ли видео с посвята 10 тысяч просмотров до 1 декабря?", "ДА, если до 1 декабря у видео с посвята будет 10 000 просмотров или больше — по скриншоту счётчика.", "fun", 30, 0.62),
    ("Нейросеть наберёт 100 баллов на ЕГЭ по профильной математике в 2027 году?", "Публичный прогон на официальных вариантах 2027 года.", "tech", 200, 0.45),
    ("Спартак попадёт в тройку РПЛ по итогам сезона?", "Итоговая таблица РПЛ на сайте лиги.", "sport", 150, 0.4),
    ("Будет ли в столовой новая пицца до ноября?", "Пицца появится в меню хотя бы на один день.", "study", 5, 0.55),
    ("Пройдёт ли хакатон без ночёвки в универе?", "Всех отпустят домой до полуночи — по сообщению в чате хакатона.", "tech", 2, 0.5),
]


class Command(BaseCommand):
    help = "Create Mini App demo markets (safe to rerun with --reset)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="delete the demo traders and their markets first")

    def handle(self, *args, **opts):
        demo = Trader.objects.filter(pseudonym__in=[p for _, p in PEOPLE], telegram_chat_id__isnull=True)
        if opts["reset"]:
            Question.objects.filter(creator__in=demo).delete()
            demo.delete()
        elif demo.exists():
            self.stdout.write("Демо-данные уже есть; используйте --reset.")
            return
        rng = random.Random(25)
        now = timezone.now()
        people = [Trader.objects.create(name=n, pseudonym=p, balance=Decimal(5000)) for n, p in PEOPLE]
        for i, (title, criteria, category, days, bias) in enumerate(MARKETS):
            creator = people[i % len(people)]
            q = services.create_market(creator, title=title, criteria=criteria, category=category,
                                       closes_at=now + timedelta(days=days), resolves_at=now + timedelta(days=days, hours=12))
            start = now - timedelta(days=8)
            Question.objects.filter(pk=q.pk).update(created_at=start)
            others = [t for t in people if t != creator]
            stamps = sorted(start + (now - start) * rng.random() for _ in range(rng.randint(12, 24)))
            for ts in stamps:
                side = "yes" if rng.random() < bias else "no"
                try:
                    trade = services.buy(rng.choice(others), q, side, Decimal(rng.choice([10, 20, 30, 50, 80, 120])))
                except MarketError:
                    continue
                Trade.objects.filter(pk=trade.pk).update(created_at=ts)
        self.stdout.write(self.style.SUCCESS(
            f"Готово: {len(MARKETS)} рынков, {Trade.objects.filter(question__creator__in=people).count()} сделок."))
