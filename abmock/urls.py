from django.urls import path

from . import views

app_name = "abmock"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/<str:action>/", views.action, name="action"),
]
