from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from . import lmsr


class Trader(models.Model):
    show_companion = models.BooleanField(default=True)
    mascot = models.CharField(max_length=12, blank=True, default="", choices=[("vesta", "Веста"), ("klep", "Клёп"), ("fox", "Фокс")])
    telegram_chat_id = models.BigIntegerField(null=True, blank=True, unique=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="trader")
    name = models.CharField("имя", max_length=100)
    pseudonym = models.CharField("псевдоним", max_length=100)
    show_real_name = models.BooleanField("показывать имя", default=False)
    github_login = models.CharField("логин GitHub", max_length=100, null=True, blank=True)
    balance = models.DecimalField(
        "баланс", max_digits=14, decimal_places=2, default=Decimal(settings.START_BALANCE)
    )

    class Meta:
        verbose_name = "участник"
        verbose_name_plural = "участники"

    def __str__(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        return self.name if self.show_real_name else self.pseudonym


class TelegramLink(models.Model):
    trader = models.OneToOneField(Trader, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class TelegramAuth(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    telegram_id = models.BigIntegerField(null=True)
    first_name = models.CharField(max_length=100, blank=True)
    consumed = models.BooleanField(default=False)


class BotRegistration(models.Model):
    chat_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField()


class EmailVerification(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    email = models.EmailField()
    expires_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True)
    verified_at = models.DateTimeField(null=True)


class TelegramSiteLink(models.Model):
    trader = models.OneToOneField(Trader, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class Question(models.Model):
    class Kind(models.TextChoices):
        LMSR = "lmsr", "LMSR"
        LS = "ls_lmsr", "LS-LMSR"
        CPMM = "cpmm", "CPMM"
        POLL = "poll", "Анонимный опрос"
        NUMBER = "number", "Угадай число"
        MULTIPLE = "multiple", "Множественный выбор"

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.LMSR)
    creator = models.ForeignKey(Trader, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_questions")
    options = models.JSONField(default=list, blank=True)
    answer = models.JSONField(null=True, blank=True)
    number_min = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    number_max = models.DecimalField(max_digits=16, decimal_places=4, default=100)
    tolerance = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    alpha = models.FloatField(default=0.05)
    pool_yes = models.FloatField(default=200)
    pool_no = models.FloatField(default=200)

    @property
    def is_trading(self):
        return self.kind in (self.Kind.LMSR, self.Kind.LS, self.Kind.CPMM)

    class Source(models.TextChoices):
        MANUAL = "manual", "Вручную"
        GITHUB = "github", "GitHub"
        ABTEST = "abtest", "A/B-тест"

    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        CLOSED = "closed", "Ставки закрыты"
        RESOLVED = "resolved", "Разрешён"
        ANNULLED = "annulled", "Аннулирован"

    class Outcome(models.TextChoices):
        YES = "yes", "ДА"
        NO = "no", "НЕТ"

    title = models.CharField("вопрос", max_length=300)
    resolution_criteria = models.TextField("критерий разрешения")
    source_type = models.CharField(
        "источник", max_length=20, choices=Source.choices, default=Source.MANUAL
    )
    source_ref = models.CharField("ссылка на источник", max_length=300, blank=True, default="")
    deadline = models.DateTimeField("дедлайн", null=True, blank=True)
    status = models.CharField("статус", max_length=20, choices=Status.choices, default=Status.OPEN)
    outcome = models.CharField(
        "исход", max_length=3, choices=Outcome.choices, null=True, blank=True
    )
    b = models.FloatField("параметр b", default=settings.LMSR_DEFAULT_B)
    q_yes = models.FloatField(default=0.0)
    q_no = models.FloatField(default=0.0)
    initial_prob = models.FloatField("начальная вероятность", default=0.5)
    conflicted_traders = models.ManyToManyField(
        Trader, blank=True, related_name="conflicted_questions", verbose_name="конфликт интересов"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "вопрос"
        verbose_name_plural = "вопросы"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_ref"],
                condition=~Q(source_ref=""),
                name="uniq_question_source",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def prob(self) -> float:
        """Current YES price."""
        from .mechanisms import probability
        return probability(self, self.q_yes, self.q_no)


class Submission(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="submissions")
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="submissions")
    value = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["question", "trader"], name="unique_submission")]


class Position(models.Model):
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="positions")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="positions")
    yes_shares = models.DecimalField(max_digits=16, decimal_places=6, default=Decimal(0))
    no_shares = models.DecimalField(max_digits=16, decimal_places=6, default=Decimal(0))
    spent = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))

    class Meta:
        verbose_name = "позиция"
        verbose_name_plural = "позиции"
        constraints = [
            models.UniqueConstraint(fields=["trader", "question"], name="uniq_position")
        ]

    def __str__(self) -> str:
        return f"{self.trader} / {self.question_id}"


class Trade(models.Model):
    class Side(models.TextChoices):
        YES = "yes", "ДА"
        NO = "no", "НЕТ"

    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="trades")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="trades")
    side = models.CharField(max_length=3, choices=Side.choices)
    # Negative shares/cost mean a sale.
    shares = models.DecimalField(max_digits=16, decimal_places=6)
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    prob_before = models.FloatField()
    prob_after = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "сделка"
        verbose_name_plural = "сделки"
        ordering = ["created_at", "id"]


class Payout(models.Model):
    class Kind(models.TextChoices):
        PAYOUT = "payout", "Выплата"
        REFUND = "refund", "Возврат"

    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="payouts")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="payouts")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PAYOUT)

    class Meta:
        verbose_name = "выплата"
        verbose_name_plural = "выплаты"
