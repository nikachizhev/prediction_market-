from django.urls import path
from django.contrib.auth import views as auth_views
from .accounts import register
from .telegram_views import connection
from . import question_views
from . import telegram_auth
from . import email_verification
from . import mascots

from . import views

app_name = "market"

urlpatterns = [
    path("welcome/", mascots.welcome, name="welcome"),
    path("me/mascot/", mascots.choose, name="mascot"),
    path("me/mascot/visibility/", mascots.visibility, name="mascot_visibility"),
    path("email/pending/", email_verification.pending, name="email_pending"),
    path("email/verify/<str:token>/", email_verification.verify, name="verify_email"),
    path("auth/telegram/link/", telegram_auth.site_login, name="telegram_site_login"),
    path("auth/telegram/start/", telegram_auth.start, name="telegram_start"),
    path("auth/telegram/", telegram_auth.complete, name="telegram_complete"),
    path("markets/create/", question_views.create, name="create"),
    path("q/<int:pk>/respond/", question_views.respond, name="respond"),
    path("q/<int:pk>/finish/", question_views.finish_view, name="finish"),
    path("me/telegram/", connection, name="telegram"),
    path("register/", register, name="register"),
    path("login/", auth_views.LoginView.as_view(template_name="market/auth.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
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
