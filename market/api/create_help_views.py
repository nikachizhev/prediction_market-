"""Creation wizard helpers without AI: templates and similar markets (spec E9.2, E9.3)."""
from __future__ import annotations

from django.http import JsonResponse

from .views import endpoint


@endpoint("GET")
def templates(request):
    return JsonResponse({"error": "Шаблоны пока не готовы."}, status=501)


@endpoint("GET")
def similar(request):
    return JsonResponse({"error": "Похожие рынки пока не готовы."}, status=501)
