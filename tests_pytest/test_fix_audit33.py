"""34차 감사: 기본값으로 되돌린 설정의 예전 쿠키를 화면을 내려줄 때 지운다."""
from core import settings


def test_reset_setting_cookie_is_cleared(client):
    client.post("/api/settings", json={"panel": 400})
    client.get("/")
    assert client.get_cookie("panel").value == "400"
    client.post("/api/settings", json={"panel": 0})  # 경계 더블클릭 = 기본 크기
    assert "panel" not in settings.load()
    client.get("/")
    assert client.get_cookie("panel") is None
