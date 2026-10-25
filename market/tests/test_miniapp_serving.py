import pytest
from django.test import override_settings


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    with override_settings(MINIAPP_DIST=tmp_path):
        yield tmp_path


def test_serves_assets_and_spa_fallback(client, dist):
    assert b"console.log" in b"".join(client.get("/app/assets/app.js").streaming_content)
    for path in ("/app/", "/app/market/5", "/app/mine"):
        response = client.get(path)
        assert response.status_code == 200 and b"root" in b"".join(response.streaming_content)
        assert "X-Frame-Options" not in response
    assert client.get("/app/assets/missing.js").status_code == 404
    assert client.get("/app/../config/settings.py").status_code in (200, 404)  # never leaves dist
    assert b"SECRET" not in b"".join(client.get("/app/../config/settings.py").streaming_content or [b""])


def test_not_built(client, tmp_path):
    with override_settings(MINIAPP_DIST=tmp_path / "none"):
        assert client.get("/app/").status_code == 503
