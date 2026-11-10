"""URLconf of a deployment with LEGACY_SITE_ENABLED off (production)."""
from config.urls import build_urlpatterns

urlpatterns = build_urlpatterns(False)
