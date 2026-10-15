import pytest
from django.core.management import call_command
from django.urls import reverse

from market.models import Question, Trader


def test_pages_render_after_seed(client, db):
    call_command("seed_demo", "--reset", verbosity=0)
    call_command("seed_demo", "--reset", verbosity=0)  # reset is repeatable
    t = Trader.objects.get(name="Аня")
    from django.contrib.auth.models import User
    user = User.objects.create_user(username="smoke-user")
    t.user = user
    t.save(update_fields=["user"])
    client.force_login(user)
    q = Question.objects.first()
    for url in ["/", f"/q/{q.pk}/", "/leaderboard/", "/me/", "/abmock/", "/sources/github/", "/abmock/1/"]:
        r = client.get(url)
        assert r.status_code in (200, 404), url
        if url != "/abmock/1/":
            assert r.status_code == 200, url


def test_sync_post_and_add_repo(client, db, monkeypatch):
    from django.contrib.auth.models import User
    client.force_login(User.objects.create_user("source-admin", is_staff=True))
    import sources.github as gh
    monkeypatch.setattr(gh, "http_get", lambda p, params=None: [])
    r = client.post(reverse("sources:sync"), HTTP_REFERER="http://testserver/leaderboard/")
    assert r.status_code == 302 and r.url == "http://testserver/leaderboard/"
    r = client.post(reverse("sources:github"), {"url": "https://github.com/acme/demo"}, follow=True)
    assert "acme/demo" in r.content.decode()
    from sources.models import GitHubRepo
    client.post(reverse("sources:github_delete", args=[GitHubRepo.objects.get().pk]))
    assert GitHubRepo.objects.count() == 0


def test_abmock_ui_flow(client, db):
    t = Trader.objects.create(name="Аня", pseudonym="A")
    r = client.post("/abmock/", {"name": "Новый онбординг карты", "metric": "активация", "author": t.pk,
                                 "baseline_rate": "0.04", "true_lift": "0.3", "n_per_group": "20000"})
    assert r.status_code == 302
    q = Question.objects.get()
    assert round(q.prob, 2) == 0.25
    experiment_id = q.source_ref.split(":", 1)[1]
    client.post(f"/abmock/{experiment_id}/start/")
    client.post(f"/abmock/{experiment_id}/finish/")
    q.refresh_from_db()
    assert q.status == "resolved"
    assert client.get(f"/abmock/{experiment_id}/").status_code == 200
