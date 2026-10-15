from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("abmock/", include("abmock.urls")),
    path("sources/", include("sources.urls")),
    path("", include("market.urls")),
]
