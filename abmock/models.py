from django.db import models

from market.models import Trader


class Experiment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        RUNNING = "running", "Идёт"
        FINISHED = "finished", "Завершён"
        CANCELLED = "cancelled", "Отменён"

    name = models.CharField("название", max_length=200)
    hypothesis = models.TextField("гипотеза", blank=True)
    metric = models.CharField("метрика", max_length=200)
    author = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="experiments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    baseline_rate = models.FloatField("базовая конверсия", default=0.04)
    true_lift = models.FloatField("истинный рост (скрыт)", default=0.0)
    n_per_group = models.PositiveIntegerField("размер группы", default=20000)
    n_a = models.PositiveIntegerField(null=True, blank=True)
    x_a = models.PositiveIntegerField(null=True, blank=True)
    n_b = models.PositiveIntegerField(null=True, blank=True)
    x_b = models.PositiveIntegerField(null=True, blank=True)
    z = models.FloatField(null=True, blank=True)
    p_value = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.name

    @property
    def rate_a(self) -> float | None:
        return self.x_a / self.n_a if self.n_a else None

    @property
    def rate_b(self) -> float | None:
        return self.x_b / self.n_b if self.n_b else None

    @property
    def significant(self) -> bool | None:
        return None if self.p_value is None else self.p_value < 0.05
