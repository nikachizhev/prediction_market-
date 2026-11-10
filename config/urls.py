from django.contrib import admin
from django.conf import settings
from django.shortcuts import redirect
from django.urls import include, path, re_path
from django.views.static import serve

from market.miniapp_views import miniapp
from market.http_metrics import metrics_response


def home(request):
    """Without the old site there is nothing at «/»: send people to the bot, or to the Mini App."""
    if settings.TELEGRAM_BOT_USERNAME:
        return redirect(f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}")
    return redirect("/app/")


def build_urlpatterns(legacy: bool) -> list:
    """Routes of the product; with `legacy` also the old server-rendered site, the A/B mock and sources."""
    patterns = [
        path("metrics", metrics_response, name="metrics"),
        path("admin/", admin.site.urls),
        path("api/", include("market.api.urls")),
        re_path(r"^app/(?P<path>.*)$", miniapp, name="miniapp"),
        # Evidence photos are shown in the Mini App. Fine for the MVP; behind a proxy, serve MEDIA_ROOT directly.
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]
    if legacy:
        return patterns + [
            path("abmock/", include("abmock.urls")),
            path("sources/", include("sources.urls")),
            path("", include("market.urls")),
        ]
    # Sign-in is Telegram-only (spec E1): no e-mail registration, password login or old trading pages.
    return patterns + [path("", home, name="home")]


urlpatterns = build_urlpatterns(settings.LEGACY_SITE_ENABLED)
