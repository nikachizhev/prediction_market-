class DailyRewardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only real site pages count; bot, API, static and admin requests do not.
        if (request.method == "GET" and request.user.is_authenticated and request.user.is_active
                and not request.path.startswith(("/api/", "/admin/", "/static/"))):
            from django.urls import resolve, Resolver404
            try:
                route = resolve(request.path)
            except Resolver404:
                route = None
            if route and route.app_name == "market" and route.url_name in (
                "markets", "index", "me", "profile", "personal_rating", "leaderboard", "friends", "mascot", "question",
                "groups", "group_detail", "group_create", "notifications",
                "group_risks", "group_report", "subscriptions",
            ):
                from .identity import get_trader
                from .services import claim_daily_reward
                reward = claim_daily_reward(get_trader(request))
                if reward:
                    request.daily_reward = reward
        return self.get_response(request)
