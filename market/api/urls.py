from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("session", views.session, name="session"),
    path("me", views.me, name="me"),
    path("categories", views.categories, name="categories"),
    path("markets", views.markets, name="markets"),
    path("markets/<int:pk>", views.market, name="market"),
    path("markets/<int:pk>/history", views.history, name="history"),
    path("markets/<int:pk>/quote", views.quote, name="quote"),
    path("markets/<int:pk>/buy", views.buy, name="buy"),
    path("markets/<int:pk>/sell", views.sell, name="sell"),
    path("markets/<int:pk>/star", views.star, name="star"),
    path("markets/<int:pk>/report", views.report, name="report"),
    path("mine", views.mine, name="mine"),
    path("rating", views.rating, name="rating"),
    path("profile", views.profile, name="profile"),
    path("events", views.events, name="events"),
]
