from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from .accounts import register, profile, ProjectPasswordResetView
from .telegram_views import connection
from . import question_views
from . import telegram_auth
from . import email_verification
from . import mascots
from . import friends
from . import groups, notifications
from . import group_analytics, subscriptions

from . import views

app_name = "market"

urlpatterns = [
    path("groups/", groups.index, name="groups"),
    path("groups/create/", groups.create, name="group_create"),
    path("groups/<int:pk>/", groups.detail, name="group_detail"),
    path("groups/<int:pk>/risks/", group_analytics.dashboard, name="group_risks"),
    path("groups/<int:pk>/report/", group_analytics.dashboard, {"report": True}, name="group_report"),
    path("subscriptions/", subscriptions.index, name="subscriptions"),
    path("q/<int:pk>/subscribe/", subscriptions.toggle, name="subscribe"),
    path("notifications/", notifications.inbox, name="notifications"),
    path("api/notifications/", notifications.feed, name="notification_feed"),
    path("notifications/read-all/", notifications.read_all, name="notifications_read_all"),
    path("notifications/<int:pk>/read/", notifications.read, name="notification_read"),
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
    path("password/reset/", ProjectPasswordResetView.as_view(
        template_name="market/password_reset_form.html",
        email_template_name="market/password_reset_email.txt",
        subject_template_name="market/password_reset_subject.txt",
        success_url=reverse_lazy("market:password_reset_done"),
    ), name="password_reset"),
    path("password/reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="market/password_reset_done.html",
    ), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="market/password_reset_confirm.html",
        success_url=reverse_lazy("market:password_reset_complete"),
    ), name="password_reset_confirm"),
    path("password/changed/", auth_views.PasswordResetCompleteView.as_view(
        template_name="market/password_reset_complete.html",
    ), name="password_reset_complete"),
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
    path("profile/", profile, name="profile"),
    path("friends/", friends.index, name="friends"),
    path("q/<int:pk>/invite/", friends.invite, name="invite_friend"),
    path("switch-user/", views.switch_user, name="switch_user"),
]
