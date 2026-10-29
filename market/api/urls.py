from django.urls import path

from . import assist_views, create_help_views, views

app_name = "api"

urlpatterns = [
    path("session", views.session, name="session"),
    path("me", views.me, name="me"),
    path("categories", views.categories, name="categories"),
    path("markets", views.markets, name="markets"),
    path("markets/similar", create_help_views.similar, name="similar_markets"),
    path("templates", create_help_views.templates, name="templates"),
    path("assist", assist_views.assist, name="assist"),
    path("feed/reset-hidden", views.reset_dismissals, name="reset_dismissals"),
    path("markets/<int:pk>/dismiss", views.dismiss_market, name="dismiss_market"),
    path("markets/<int:pk>", views.market, name="market"),
    path("markets/<int:pk>/history", views.history, name="history"),
    path("markets/<int:pk>/quote", views.quote, name="quote"),
    path("markets/<int:pk>/buy", views.buy, name="buy"),
    path("markets/<int:pk>/sell", views.sell, name="sell"),
    path("markets/<int:pk>/star", views.star, name="star"),
    path("markets/<int:pk>/share", views.share, name="share"),
    path("markets/<int:pk>/report", views.report, name="report"),
    path("mine", views.mine, name="mine"),
    path("rating", views.rating, name="rating"),
    path("profile", views.profile, name="profile"),
    path("notifications", views.notifications, name="notifications"),
    path("notifications/read", views.notifications_read, name="notifications_read"),
    path("profile/history", views.profile_history, name="profile_history"),
    path("events", views.events, name="events"),
]
