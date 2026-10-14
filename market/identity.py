from .models import Trader


def get_trader(request):
    if not request.user.is_authenticated:
        return None
    trader, _ = Trader.objects.get_or_create(
        user=request.user,
        defaults={"name": request.user.first_name or request.user.username, "pseudonym": request.user.username},
    )
    return trader
