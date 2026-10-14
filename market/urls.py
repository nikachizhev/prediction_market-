from django.urls import path

from . import views

app_name = "market"

urlpatterns = [
    path("", views.question_list, name="index"),
    path("markets/", views.question_list, name="markets"),
    path("api/markets/", views.markets_api, name="markets_api"),
    path("q/<int:pk>/", views.question_detail, name="question"),
    path("q/<int:pk>/quote", views.quote_view, name="quote"),
    path("q/<int:pk>/buy/", views.buy_view, name="buy"),
    path("q/<int:pk>/sell/", views.sell_view, name="sell"),
    path("leaderboard/", views.leaderboard, name="leaderboard"),
    path("rating/", views.personal_rating, name="personal_rating"),
    path("me/", views.me, name="me"),
    path("switch-user/", views.switch_user, name="switch_user"),
]
