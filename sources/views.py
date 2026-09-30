from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .github import parse_repo_url
from .models import GitHubRepo
from .sync import sync_all


@require_POST
def sync(request):
    for m in sync_all():
        level = messages.WARNING if ("ошибка" in m or "недоступен" in m) else messages.INFO
        messages.add_message(request, level, m)
    nxt = request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    return redirect(nxt)


def github(request):
    if request.method == "POST":
        parsed = parse_repo_url(request.POST.get("url") or "")
        if not parsed:
            messages.error(request, "Не удалось разобрать ссылку. Ожидается https://github.com/owner/repo")
        else:
            repo, created = GitHubRepo.objects.get_or_create(owner=parsed[0], name=parsed[1])
            messages.success(request, f"Репозиторий {repo} {'подключён' if created else 'уже подключён'}.")
            if created:
                for m in sync_all():
                    messages.info(request, m)
        return redirect("sources:github")
    return render(request, "sources/github.html", {"repos": GitHubRepo.objects.all()})


@require_POST
def github_delete(request, pk):
    repo = get_object_or_404(GitHubRepo, pk=pk)
    repo.delete()
    messages.success(request, f"Репозиторий {repo} отключён (созданные вопросы сохранены).")
    return redirect("sources:github")
