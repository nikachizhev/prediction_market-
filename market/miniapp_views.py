"""Serve the built Mini App (miniapp/dist) under /app/ with a SPA fallback to index.html."""
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.static import serve


@xframe_options_exempt  # Telegram Web opens Mini Apps in an iframe
def miniapp(request, path=""):
    root = Path(settings.MINIAPP_DIST)
    index = root / "index.html"
    if not index.exists():
        return HttpResponse("Mini App не собран: выполните «npm run build» в папке miniapp.", status=503,
                            content_type="text/plain; charset=utf-8")
    if path and (root / path).is_file():
        try:
            return serve(request, path, document_root=str(root))
        except Http404:
            pass
    if path.startswith("assets/"):
        raise Http404
    response = FileResponse(index.open("rb"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response
