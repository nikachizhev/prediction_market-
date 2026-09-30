"""GitHub question source: milestones with due_on and `predict`-labelled issues in them."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.utils import timezone

from market.models import Question

from .base import QuestionDraft, SourceUpdate
from .models import GitHubRepo

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
API = "https://api.github.com"
Getter = Callable[[str, dict], Any]


def http_get(path: str, params: dict | None = None) -> Any:
    """Default getter: returns all pages of a list endpoint, or the JSON object."""
    headers = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    params = dict(params or {})
    params.setdefault("per_page", 100)
    out: list = []
    page = 1
    while True:
        params["page"] = page
        r = requests.get(API + path, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return data
        out.extend(data)
        if len(data) < params["per_page"] or page >= 10:
            return out
        page += 1


def parse_repo_url(url: str) -> tuple[str, str] | None:
    import re

    url = url.strip().split("?")[0].split("#")[0]
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$", url) or re.fullmatch(
        r"([\w.-]+)/([\w.-]+?)(?:\.git)?", url
    )
    return (m.group(1), m.group(2)) if m else None


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def deadline_from_due(due_on: str) -> datetime:
    """End of the due_on day in Europe/Moscow."""
    d: date = parse_ts(due_on).date()
    return datetime.combine(d, time(23, 59, 59), tzinfo=MSK)


class GitHubSource:
    source_type = "github"

    def __init__(self, getter: Getter | None = None, now: Callable[[], datetime] | None = None):
        self.get = getter or http_get
        self.now = now or timezone.now
        self._cache: dict[tuple, Any] = {}

    def _fetch(self, path: str, params: dict) -> Any:
        key = (path, tuple(sorted(params.items())))
        if key not in self._cache:
            self._cache[key] = self.get(path, params)
        return self._cache[key]

    def _milestones(self, o: str, r: str) -> list[dict]:
        return self._fetch(f"/repos/{o}/{r}/milestones", {"state": "all"})

    def _issues(self, o: str, r: str) -> list[dict]:
        items = self._fetch(f"/repos/{o}/{r}/issues", {"labels": "predict", "state": "all"})
        return [i for i in items if "pull_request" not in i]

    def discover(self) -> list[QuestionDraft]:
        drafts: list[QuestionDraft] = []
        for repo in GitHubRepo.objects.all():
            o, r = repo.owner, repo.name
            try:
                milestones = self._milestones(o, r)
                issues = self._issues(o, r)
            except Exception as e:  # one broken repo must not hide others
                log.warning("GitHub discover failed for %s/%s: %s", o, r, e)
                raise
            by_num = {m["number"]: m for m in milestones if m.get("due_on")}
            for m in by_num.values():
                dl = deadline_from_due(m["due_on"])
                drafts.append(QuestionDraft(
                    title=f"Будет ли веха «{m['title']}» закрыта до {dl:%d.%m.%Y}?",
                    resolution_criteria=(
                        f"ДА, если веха «{m['title']}» в {o}/{r} закрыта не позднее конца {dl:%d.%m.%Y} (МСК). "
                        "НЕТ, если к этому сроку она остаётся открытой. Повторное открытие не отменяет разрешение."
                    ),
                    source_ref=f"gh:{o}/{r}:milestone:{m['number']}", deadline=dl,
                    initial_prob=0.5, conflicted_logins=[],
                ))
            for i in issues:
                ms = i.get("milestone")
                if not ms or ms["number"] not in by_num:
                    continue
                dl = deadline_from_due(by_num[ms["number"]]["due_on"])
                logins = [a["login"] for a in (i.get("assignees") or [])]
                drafts.append(QuestionDraft(
                    title=f"Будет ли задача #{i['number']} «{i['title']}» закрыта до {dl:%d.%m.%Y}?",
                    resolution_criteria=(
                        f"ДА, если задача #{i['number']} в {o}/{r} закрыта как выполненная не позднее конца "
                        f"{dl:%d.%m.%Y} (МСК). НЕТ, если к этому сроку она открыта. Если задача закрыта как "
                        "«not planned», вопрос аннулируется. Повторное открытие не отменяет разрешение."
                    ),
                    source_ref=f"gh:{o}/{r}:issue:{i['number']}", deadline=dl,
                    initial_prob=0.5, conflicted_logins=logins,
                ))
        return drafts

    def check(self, question: Question) -> SourceUpdate | None:
        try:
            _, rest = question.source_ref.split(":", 1)
            repo, kind, num = rest.rsplit(":", 2)
            o, r = repo.split("/")
            num = int(num)
        except ValueError:
            return None
        deadline = question.deadline
        now = self.now()
        if kind == "milestone":
            item = next((m for m in self._milestones(o, r) if m["number"] == num), None)
            if item is None:
                return None
            closed = item.get("state") == "closed"
            reason = "completed"
        else:
            item = next((i for i in self._issues(o, r) if i["number"] == num), None)
            if item is None:
                return None
            closed = item.get("state") == "closed"
            reason = item.get("state_reason") or "completed"
        if closed:
            if reason == "not_planned":
                return SourceUpdate.ANNUL
            closed_at = parse_ts(item.get("closed_at"))
            if closed_at is not None and deadline is not None and closed_at > deadline:
                return SourceUpdate.RESOLVE_NO
            return SourceUpdate.RESOLVE_YES
        if deadline is not None and now > deadline:
            return SourceUpdate.RESOLVE_NO
        return None
