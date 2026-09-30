from datetime import datetime, timezone

import pytest

from market.models import Question, Trader
from sources.base import SourceUpdate
from sources.github import GitHubSource, deadline_from_due, parse_repo_url
from sources.models import GitHubRepo
from sources.sync import sync_all

DUE = "2026-10-05T07:00:00Z"  # deadline = 2026-10-05 23:59:59 MSK = 20:59:59 UTC


def api(milestones, issues):
    calls = []

    def getter(path, params):
        calls.append(path)
        if path.endswith("/milestones"):
            return milestones
        if path.endswith("/issues"):
            assert params.get("labels") == "predict"
            return issues
        raise AssertionError(path)

    getter.calls = calls
    return getter


def ms(n=1, due=DUE, state="open", closed_at=None):
    return {"number": n, "title": f"M{n}", "due_on": due, "state": state, "closed_at": closed_at}


def iss(n=7, milestone=1, state="open", reason=None, closed_at=None, assignees=(), **kw):
    d = {"number": n, "title": f"I{n}", "state": state, "state_reason": reason, "closed_at": closed_at,
         "milestone": {"number": milestone} if milestone else None,
         "assignees": [{"login": a} for a in assignees]}
    d.update(kw)
    return d


@pytest.fixture
def repo(db):
    return GitHubRepo.objects.create(owner="acme", name="demo")


def now_at(s):
    dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return lambda: dt


def src(milestones, issues, now="2026-10-01T00:00:00"):
    return GitHubSource(getter=api(milestones, issues), now=now_at(now))


def test_deadline_end_of_day_moscow():
    assert deadline_from_due(DUE).astimezone(timezone.utc) == datetime(2026, 10, 5, 20, 59, 59, tzinfo=timezone.utc)


def test_parse_repo_url():
    assert parse_repo_url("https://github.com/acme/demo") == ("acme", "demo")
    assert parse_repo_url("https://github.com/acme/demo.git/") == ("acme", "demo")
    assert parse_repo_url("github.com/acme/demo/issues/3") == ("acme", "demo")
    assert parse_repo_url("acme/demo") == ("acme", "demo")
    assert parse_repo_url("nonsense") is None


def test_discover_rules(repo):
    s = src(
        [ms(1), ms(2, due=None)],
        [iss(7, 1, assignees=["anya-gh"]), iss(8, 2), iss(9, None), iss(10, 1, pull_request={"url": "x"})],
    )
    drafts = {d.source_ref: d for d in s.discover()}
    assert set(drafts) == {"gh:acme/demo:milestone:1", "gh:acme/demo:issue:7"}
    d = drafts["gh:acme/demo:issue:7"]
    assert d.title == "Будет ли задача #7 «I7» закрыта до 05.10.2026?"
    assert d.conflicted_logins == ["anya-gh"]
    assert drafts["gh:acme/demo:milestone:1"].title == "Будет ли веха «M1» закрыта до 05.10.2026?"


def q_for(ref, deadline="2026-10-05T20:59:59"):
    dl = datetime.fromisoformat(deadline).replace(tzinfo=timezone.utc)
    return Question(source_type="github", source_ref=ref, deadline=dl)


ISSUE = "gh:acme/demo:issue:7"
MILE = "gh:acme/demo:milestone:1"


@pytest.mark.parametrize("issue,now,expected", [
    (iss(), "2026-10-01T00:00:00", None),
    (iss(), "2026-10-06T00:00:00", SourceUpdate.RESOLVE_NO),
    (iss(state="closed", reason="completed", closed_at="2026-10-02T10:00:00Z"), "2026-10-02T11:00:00", SourceUpdate.RESOLVE_YES),
    (iss(state="closed", reason="completed", closed_at="2026-10-05T20:59:00Z"), "2026-10-07T00:00:00", SourceUpdate.RESOLVE_YES),
    (iss(state="closed", reason="completed", closed_at="2026-10-05T21:30:00Z"), "2026-10-07T00:00:00", SourceUpdate.RESOLVE_NO),
    (iss(state="closed", reason="not_planned", closed_at="2026-10-02T10:00:00Z"), "2026-10-02T11:00:00", SourceUpdate.ANNUL),
    (iss(state="closed", reason=None, closed_at="2026-10-02T10:00:00Z"), "2026-10-02T11:00:00", SourceUpdate.RESOLVE_YES),
])
def test_issue_resolution(issue, now, expected):
    assert src([ms()], [issue], now).check(q_for(ISSUE)) == expected


@pytest.mark.parametrize("m,now,expected", [
    (ms(), "2026-10-01T00:00:00", None),
    (ms(), "2026-10-06T00:00:00", SourceUpdate.RESOLVE_NO),
    (ms(state="closed", closed_at="2026-10-04T10:00:00Z"), "2026-10-04T11:00:00", SourceUpdate.RESOLVE_YES),
    (ms(state="closed", closed_at="2026-10-06T10:00:00Z"), "2026-10-07T00:00:00", SourceUpdate.RESOLVE_NO),
])
def test_milestone_resolution(m, now, expected):
    assert src([m], [], now).check(q_for(MILE)) == expected


def test_sync_end_to_end_with_conflict_and_final_not_reverted(repo):
    anya = Trader.objects.create(name="Аня", pseudonym="A", github_login="Anya-GH")
    issues = [iss(7, 1, assignees=["anya-gh"])]
    s = src([ms(1)], issues)
    sync_all([s])
    sync_all([s])  # idempotent
    assert Question.objects.count() == 2
    q = Question.objects.get(source_ref=ISSUE)
    assert list(q.conflicted_traders.all()) == [anya]
    # close issue -> YES; reopening afterwards must not undo it
    issues[0] = iss(7, 1, state="closed", reason="completed", closed_at="2026-10-02T10:00:00Z")
    sync_all([src([ms(1)], issues, "2026-10-02T11:00:00")])
    q.refresh_from_db()
    assert (q.status, q.outcome) == ("resolved", "yes")
    sync_all([src([ms(1)], [iss(7, 1)], "2026-10-02T12:00:00")])
    q.refresh_from_db()
    assert (q.status, q.outcome) == ("resolved", "yes")


def test_source_failure_isolated(db):
    class Boom:
        source_type = "github"

        def discover(self):
            raise RuntimeError("rate limit")

        def check(self, q):
            return None

    from sources.abtest import ABTestSource

    msgs = sync_all([Boom(), ABTestSource()])
    assert any("недоступен" in m and "rate limit" in m for m in msgs)
