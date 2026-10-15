from django.urls import path

from . import views

app_name = "sources"

urlpatterns = [
    path("sync/", views.sync, name="sync"),
    path("github/", views.github, name="github"),
    path("github/<int:pk>/delete/", views.github_delete, name="github_delete"),
]
