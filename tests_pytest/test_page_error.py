"""화면 주소가 실패하면 JSON 대신 왼쪽 메뉴가 있는 오류 화면을 준다. /api/ 주소는 그대로 JSON 오류다."""
import importlib
from types import ModuleType

import pytest
from flask.testing import FlaskClient

from core import server

BROKEN = "collect.collect_config"  # 설치본에서 빠졌던 모듈처럼 불러오기를 실패시킬 이름
REASON = f"No module named '{BROKEN}'"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    real = importlib.import_module

    def fail_one(name: str, package: str | None = None) -> ModuleType:
        if name == BROKEN:
            raise ModuleNotFoundError(REASON, name=name)
        return real(name, package)

    monkeypatch.setattr(importlib, "import_module", fail_one)
    found = server.app.test_client()
    found.get("/?key=" + server.KEY)
    return found


def test_failed_screen_is_an_error_screen_with_the_menu(client: FlaskClient) -> None:
    response = client.get("/collect")
    assert response.status_code == 400  # 오류 상태 그대로
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert '<nav class="rail"' in html
    assert all(f'href="{path}"' in html for path in ("/", "/search", "/prep", "/dashboard", "/integrations"))
    assert "/collect" in html and "No module named &#39;collect.collect_config&#39;" in html
    assert 'class="err"' in html and 'class="card"' in html
    assert "<script nonce=" in html and "Content-Security-Policy" in response.headers


def test_api_error_is_still_json(client: FlaskClient) -> None:
    response = client.get("/api/collections")
    assert response.status_code == 400
    assert response.is_json and response.json == {"error": REASON}


def test_request_without_the_window_key_gets_no_screen() -> None:
    response = server.app.test_client().get("/collect")
    assert response.status_code == 403 and response.is_json
    assert "key" not in response.headers.get("Set-Cookie", "")
