from django.contrib import admin
from .models import GitHubRepo


@admin.register(GitHubRepo)
class GitHubRepoAdmin(admin.ModelAdmin):
    list_display = ("owner", "name", "group", "allow_private")
    list_filter = ("allow_private",)
    search_fields = ("owner", "name", "group__name")
