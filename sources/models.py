from django.db import models


class GitHubRepo(models.Model):
    allow_private = models.BooleanField("Доступ серверным токеном одобрен администратором", default=False)
    group = models.ForeignKey("market.Group", null=True, blank=True, on_delete=models.CASCADE, related_name="repositories")
    owner = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["owner", "name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name", "group"], name="unique_group_repo"),
            models.UniqueConstraint(fields=["owner", "name"], condition=models.Q(group__isnull=True), name="unique_public_repo"),
        ]

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"
