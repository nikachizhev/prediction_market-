from django.contrib import admin
from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

from market.miniapp_views import miniapp
from market.http_metrics import metrics_response

urlpatterns = [
    path("metrics", metrics_response, name="metrics"),
    path("admin/", admin.site.urls),
    path("api/", include("market.api.urls")),
    re_path(r"^app/(?P<path>.*)$", miniapp, name="miniapp"),
    # Evidence photos are shown in the Mini App. Fine for the MVP; behind a proxy, serve MEDIA_ROOT directly.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    path("abmock/", include("abmock.urls")),
    path("sources/", include("sources.urls")),
    path("", include("market.urls")),
]
