from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from . import lmsr


class Trader(models.Model):
    birth_date = models.DateField("Дата рождения", null=True, blank=True)
    city = models.CharField("Город", max_length=100, blank=True)
    workplace = models.CharField("Место работы", max_length=200, blank=True)
    education = models.CharField("Место учёбы", max_length=200, blank=True)
    bio = models.CharField("О себе", max_length=500, blank=True)
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
    # Mini App profile. telegram_chat_id doubles as the Telegram user id (equal in private chats).
    telegram_username = models.CharField(max_length=64, blank=True)
    photo_url = models.URLField(max_length=500, blank=True)
    referral_code = models.CharField(max_length=16, unique=True, null=True, blank=True)
    referred_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals")
    onboarded_at = models.DateTimeField(null=True, blank=True)
    # Category slugs picked in onboarding («Что тебе интересно?»), see CATEGORIES.
    interests = models.JSONField(default=list, blank=True)
    # Bot notification switches (spec E6.3), keyed by NOTIFY_KINDS; a missing key means «on».
    notify_prefs = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(null=True, blank=True, auto_now_add=True)

    class Meta:
        verbose_name = "участник"
        verbose_name_plural = "участники"

    def __str__(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        return self.name if self.show_real_name else self.pseudonym


class Friendship(models.Model):
    first = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="friendships_first")
    second = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="friendships_second")
    requested_by = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="friend_requests_sent")
    accepted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["first", "second"], name="unique_friend_pair"),
            models.CheckConstraint(condition=Q(first__lt=models.F("second")), name="ordered_friend_pair"),
            models.CheckConstraint(condition=Q(requested_by=models.F("first")) | Q(requested_by=models.F("second")), name="friend_request_member"),
        ]


class MarketInvitation(models.Model):
    sender = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="invitations_sent")
    recipient = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="invitations_received")
    question = models.ForeignKey("Question", on_delete=models.CASCADE, related_name="friend_invitations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sender", "recipient", "question"], name="unique_market_invitation")]
        ordering = ["-created_at", "-pk"]


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


class Group(models.Model):
    name = models.CharField("Название", max_length=120)
    description = models.TextField("Описание", max_length=2000, blank=True)
    kind = models.CharField("Тип группы", max_length=12, choices=[("social", "Сообщество"), ("work", "Рабочая группа")], default="social")
    owner = models.ForeignKey(Trader, on_delete=models.PROTECT, related_name="owned_groups")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.name


class GroupMembership(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="memberships")
    status = models.CharField(max_length=12, choices=[("active", "Участник"), ("invited", "Приглашён"), ("requested", "Заявка")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "trader"], name="unique_group_member")]


# Bot notification types a user can switch off in the profile (spec E6.3). Order = order in the settings screen.
NOTIFY_KINDS = [
    ("results", "Итоги и выплаты"),
    ("moves", "Сдвиги вероятности"),
    ("closing", "Скоро конец приёма прогнозов"),
    ("referral", "Награды за друзей"),
    ("creator", "Мои рынки"),
]


class Notification(models.Model):
    """In-app inbox (Mini App «Уведомления»); every notification lands here regardless of bot settings."""
    recipient = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=120, blank=True, default="")
    text = models.CharField(max_length=500)
    kind = models.CharField(max_length=30, default="info")
    question = models.ForeignKey("Question", null=True, blank=True, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.CASCADE)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    key = models.CharField(max_length=160, unique=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


CATEGORIES = [
    ("games", "Игры"),
    ("study", "Учёба"),
    ("weather", "Погода"),
    ("tech", "Технологии"),
    ("sport", "Спорт"),
    ("fun", "Фан"),
]


class Question(models.Model):
    tracks_deadline = models.BooleanField("ДА означает выполнение в срок", default=False, help_text="Включает вопрос в панель рисков. Используйте, только если ДА означает выполнение задачи к дедлайну.")
    resolved_at = models.DateTimeField(null=True, blank=True)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.PROTECT, related_name="questions")
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

    @property
    def accepting_entries(self):
        from django.utils import timezone
        return self.status == self.Status.OPEN and (not self.deadline or self.deadline > timezone.now())

    @property
    def display_state(self):
        if self.status == self.Status.ANNULLED:
            return "annulled"
        if self.status == self.Status.RESOLVED or (self.kind == self.Kind.POLL and self.status == self.Status.CLOSED):
            return "resolved"
        return "open" if self.accepting_entries else "closed"

    @property
    def state_label(self):
        return {"open": "Можно участвовать", "closed": "Ожидает результата",
                "resolved": "Завершён", "annulled": "Отменён"}[self.display_state]

    @property
    def result_label(self):
        if self.status == self.Status.ANNULLED:
            return "Ставки возвращены" if self.is_trading else "Вопрос отменён"
        if self.display_state != "resolved":
            return ""
        if self.is_trading:
            return f"Итог: {self.get_outcome_display()}"
        if self.kind == self.Kind.NUMBER:
            return f"Ответ: {self.answer}"
        return "Результаты опубликованы"

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
    category = models.CharField("категория", max_length=20, blank=True, default="")
    resolves_at = models.DateTimeField("итог станет известен", null=True, blank=True)
    # Money the creator put into the market maker; settled at resolution.
    subsidy = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))
    # Fees held in the market; split between the creator and burning at resolution, refunded on cancel.
    fee_pool = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))
    hidden_at = models.DateTimeField("скрыт до решения модератора", null=True, blank=True)
    moderation_hidden = models.BooleanField("скрыт модератором", default=False)
    refund_compensation = models.DecimalField("компенсация платформы при отмене", max_digits=14, decimal_places=2, default=Decimal(0))
    # Client-generated key of the create request: a repeated POST /api/markets returns this market.
    request_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    # Resolution by the creator in the bot (spec E4).
    class Resolution(models.TextChoices):
        NONE = "", "Итог не указан"
        PENDING = "pending", "Окно спора"
        DISPUTED = "disputed", "Спор у модератора"
        FINAL = "final", "Завершено"

    resolution_state = models.CharField("разрешение", max_length=10, choices=Resolution.choices, blank=True, default="")
    proposed_outcome = models.CharField("итог от создателя", max_length=6, blank=True, default="",
                                        choices=[("yes", "ДА"), ("no", "НЕТ"), ("cancel", "Отменить рынок")])
    evidence = models.FileField("доказательство", upload_to="evidence/%Y/%m/", blank=True)
    evidence_file_id = models.CharField(max_length=200, blank=True, default="")
    evidence_note = models.CharField("подпись или ссылка", max_length=500, blank=True, default="")
    proposed_at = models.DateTimeField(null=True, blank=True)
    dispute_until = models.DateTimeField("окно спора до", null=True, blank=True)
    creator_at_fault = models.BooleanField("создатель ошибся", default=False)
    # Bot bookkeeping, so the worker never repeats a message.
    asked_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    results_sent_at = models.DateTimeField(null=True, blank=True)
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
    yes_spent = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))
    no_spent = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))

    class Meta:
        verbose_name = "позиция"
        verbose_name_plural = "позиции"
        constraints = [
            models.UniqueConstraint(fields=["trader", "question"], name="uniq_position")
        ]

    def __str__(self) -> str:
        return f"{self.trader} / {self.question_id}"


class Trade(models.Model):
    request_key = models.UUIDField(null=True, blank=True, unique=True)
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    class Side(models.TextChoices):
        YES = "yes", "ДА"
        NO = "no", "НЕТ"

    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="trades")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="trades")
    side = models.CharField(max_length=3, choices=Side.choices)
    # Negative shares/cost mean a sale.
    shares = models.DecimalField(max_digits=16, decimal_places=6)
    # Coins that left (+) or reached (-) the trader's balance, fee included.
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    fee = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal(0))
    prob_before = models.FloatField()
    prob_after = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "сделка"
        verbose_name_plural = "сделки"
        ordering = ["created_at", "id"]


class MarketSubscription(models.Model):
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="market_subscriptions")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="subscriptions")
    last_probability = models.FloatField(null=True)
    last_alert_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["trader", "question"], name="unique_market_subscription")]


class PurchaseConfirmation(models.Model):
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    side = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    snapshot = models.JSONField()
    expires_at = models.DateTimeField()
    trade = models.OneToOneField(Trade, null=True, on_delete=models.CASCADE)


class DailyReward(models.Model):
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="daily_rewards")
    day = models.DateField()
    coins = models.DecimalField(max_digits=8, decimal_places=2, default=5)
    xp = models.PositiveSmallIntegerField(default=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["trader", "day"], name="unique_daily_reward")]


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


class LedgerEntry(models.Model):
    """Every balance change, for the operation history and the economy dashboard."""

    class Kind(models.TextChoices):
        STARTER = "starter", "Стартовый пакет"
        DAILY = "daily", "Бонус дня"
        INSURANCE = "insurance", "Страховка"
        REFERRAL = "referral", "Награда за друга"
        BUY = "buy", "Прогноз"
        SELL = "sell", "Продажа"
        PAYOUT = "payout", "Выплата"
        REFUND = "refund", "Возврат"
        CREATION = "creation", "Создание рынка"
        SUBSIDY_RETURN = "subsidy_return", "Возврат за создание рынка"
        CREATOR_FEE = "creator_fee", "Комиссия создателю"
        DISPUTE = "dispute", "Спор"
        DISPUTE_REFUND = "dispute_refund", "Спор признан"

    # Coins that appear from nothing; excluded from profit in the rating.
    BONUS_KINDS = (Kind.STARTER, Kind.DAILY, Kind.INSURANCE, Kind.REFERRAL)

    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="ledger")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "операция"
        verbose_name_plural = "операции"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["trader"], condition=Q(kind="starter"), name="one_starter_pack"),
        ]


class MarketComplaint(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает проверки"
        UPHELD = "upheld", "Подтверждена"
        DISMISSED = "dismissed", "Отклонена"

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="complaints")
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="complaints")
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "жалоба"
        verbose_name_plural = "жалобы"
        constraints = [models.UniqueConstraint(fields=["question", "trader"], name="one_complaint_per_trader")]


class ModerationAction(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="moderation_actions")
    action = models.CharField(max_length=12, choices=[("hide", "Скрыть"), ("cancel", "Отменить с возвратом"), ("dismiss", "Отклонить жалобы"), ("restore", "Вернуть в ленту")])
    actor = models.CharField(max_length=80)
    complaint_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "решение модератора"
        verbose_name_plural = "решения модераторов"


class ModerationDelivery(models.Model):
    complaint = models.ForeignKey(MarketComplaint, on_delete=models.CASCADE, related_name="moderation_deliveries")
    admin_id = models.PositiveBigIntegerField()
    sent_at = models.DateTimeField(null=True, blank=True)
    next_attempt = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["complaint", "admin_id"], name="one_moderation_delivery")]


class ModerationBotState(models.Model):
    token_fingerprint = models.CharField(max_length=64, primary_key=True)
    offset = models.BigIntegerField(default=0)


class ModerationPanel(models.Model):
    token_fingerprint = models.CharField(max_length=64)
    admin_id = models.PositiveBigIntegerField()
    message_id = models.BigIntegerField(null=True, blank=True)
    notice_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['token_fingerprint', 'admin_id'], name='one_moderation_panel')]


class Event(models.Model):
    """Product analytics (spec E7): one row per user action, read by BI without personal data."""
    trader = models.ForeignKey(Trader, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    name = models.CharField(max_length=40, db_index=True)
    props = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "событие"
        verbose_name_plural = "события"
        ordering = ["-created_at", "-id"]


class Dispute(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="disputes")
    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="disputes")
    stake = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=500, blank=True)
    upheld = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "спор"
        verbose_name_plural = "споры"
        constraints = [models.UniqueConstraint(fields=["question", "trader"], name="one_dispute_per_trader")]


class ResolutionDraft(models.Model):
    """The creator's unfinished outcome dialog in the bot; one per creator, survives bot restarts."""
    class Stage(models.TextChoices):
        EVIDENCE = "evidence", "ждём доказательство"
        PREVIEW = "preview", "предпросмотр"

    trader = models.OneToOneField(Trader, on_delete=models.CASCADE, related_name="resolution_draft")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="resolution_drafts")
    outcome = models.CharField(max_length=10)
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.EVIDENCE)
    evidence = models.FileField(upload_to="evidence/drafts/", blank=True)
    evidence_file_id = models.CharField(max_length=200, blank=True, default="")
    # Telegram can't reuse a document's file_id in sendPhoto, so remember what was sent.
    evidence_is_document = models.BooleanField(default=False)
    note = models.CharField(max_length=500, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "черновик итога"
        verbose_name_plural = "черновики итогов"


class BotMessage(models.Model):
    """Outbox of bot notifications (spec E6.3).

    The worker sends queued rows honouring the user's switches, the daily cap and quiet hours,
    so a Telegram failure never rolls back money and nothing is sent twice (`key` is unique).
    """
    class Status(models.TextChoices):
        QUEUED = "queued", "в очереди"
        SENT = "sent", "отправлено"
        SKIPPED = "skipped", "пропущено"
        FAILED = "failed", "ошибка"

    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="bot_messages")
    # One of NOTIFY_KINDS.
    kind = models.CharField(max_length=20)
    key = models.CharField(max_length=160, unique=True)
    text = models.TextField()
    # Extra sendMessage parameters: reply_markup, message_effect_id, ...
    payload = models.JSONField(default=dict, blank=True)
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.CASCADE, related_name="bot_messages")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    send_after = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "сообщение бота"
        verbose_name_plural = "сообщения бота"
        ordering = ["send_after", "id"]


class ReferralReward(models.Model):
    """Reward for an invited friend who made REFERRAL_PREDICTIONS predictions in different markets (spec E5).

    One row per friend, ever. `granted=False` records that the referrer's monthly cap was reached
    and nobody was paid, so the check is not repeated.
    """
    friend = models.OneToOneField(Trader, on_delete=models.CASCADE, related_name="referral_reward")
    referrer = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="referral_rewards")
    granted = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "награда за друга"
        verbose_name_plural = "награды за друзей"


class ChartAnimation(models.Model):
    """Rendered 7-day probability animation for the bot (spec E6.2), cached per market and hour."""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="chart_animations")
    hour = models.DateTimeField()
    file = models.FileField(upload_to="charts/", blank=True)
    # Telegram file_id after the first upload; later sends reuse it instead of the file.
    file_id = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["question", "hour"], name="one_chart_per_market_hour")]
