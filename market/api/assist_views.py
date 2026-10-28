"""AI rules helper endpoint (spec E9.1, docs/api.md «Фаза 3a»)."""
from __future__ import annotations

from django.http import JsonResponse

from .views import endpoint


@endpoint("POST")
def assist(request):
    return JsonResponse({"error": "Помощник пока не готов."}, status=501)
