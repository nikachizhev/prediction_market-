from django.db import models


class GitHubRepo(models.Model):
    owner = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["owner", "name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="uniq_github_repo")]

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"
