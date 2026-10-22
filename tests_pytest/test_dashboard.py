"""대시보드 라우트(/api/dash): 저장·열기·지우기·카드 더하기와 크기·개수 검사, 고른 그래프 그림을 폴더에 저장(덮어쓰지 않음, 파일 이름 정리), 받침에 맞는 조사."""
import base64

import pytest

import dash_store
import server
import settings

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
JPG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 20).decode()
CARD = {"kind": "chart", "title": "매출", "view": {"ratio": 1.5, "styles": [{"type": "bar"}]},
        "source": {"schema": "", "table": "orders", "body": {"base": "orders", "x": "매장", "series": [{"y": "금액", "agg": "sum"}]}}}


def post(client, url, **body):
    return client.post(url, json=body).get_json()


def test_save_open_delete(client):
    saved = post(client, "/api/dash", name=" 주간 ", cards=[CARD])["dashboard"]
    assert saved["name"] == "주간" and saved["cards"][0]["source"]["body"]["base"] == "orders" and saved["cards"][0]["layout"] == {"x": 0, "y": 0, "w": 6, "h": 6}
    listed = client.get("/api/dash").get_json()
    assert listed == {"dashboards": [{"id": saved["id"], "name": "주간", "cards": 1, "updated": saved["updated"]}], "locked": False}
    assert client.get(f"/api/dash/{saved['id']}").get_json()["dashboard"]["cards"][0]["id"] == saved["cards"][0]["id"]
    assert post(client, "/api/dash", id=saved["id"], name="주간(고침)", cards=[])["dashboards"][0]["name"] == "주간(고침)"  # 같은 id는 덮어쓴다
    assert post(client, "/api/dash/delete", id=saved["id"]) == {"dashboards": [], "locked": False}
    assert "찾을 수 없" in client.get(f"/api/dash/{saved['id']}").get_json()["error"]


def test_board_checks(client):
    assert "이름" in post(client, "/api/dash", name="", cards=[CARD])["error"]
    assert "48개" in post(client, "/api/dash", name="많음", cards=[CARD] * (dash_store.MAX_CARDS + 1))["error"]
    big = {**CARD, "title": "가" * 101}
    assert "100자" in post(client, "/api/dash", name="큼", cards=[big])["error"]
    post(client, "/api/dash", name="같은 이름", cards=[])
    assert "이미 있습니다" in post(client, "/api/dash", name="같은 이름", cards=[])["error"]


def test_add_card_to_saved_or_new(client):
    """분석 화면의 [대시보드에 추가]: 저장한 대시보드에 더하거나 새로 만든다. 카드는 맨 아래에 놓인다."""
    first = post(client, "/api/dash/card/add", dashboard="", name="새것", card=CARD)["dashboard"]
    assert first["name"] == "새것" and first["cards"] == 1
    again = post(client, "/api/dash/card/add", dashboard=first["id"], card=CARD)["dashboard"]
    assert again["cards"] == 2
    cards = client.get(f"/api/dash/{first['id']}").get_json()["dashboard"]["cards"]
    assert [c["layout"]["y"] for c in cards] == [0, 6]


def test_josa():
    assert [settings.josa(w, "이", "가") for w in ("검색", "대시보드", "csv")] == ["검색이", "대시보드가", "csv가"]


def test_images_without_window(client, monkeypatch):
    monkeypatch.setattr(server, "ask_folder", None)
    assert post(client, "/api/images", images=[{"name": "a", "data": PNG}]) == {"window": False}


def test_images_saved_to_folder(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "ask_folder", lambda: str(tmp_path))
    (tmp_path / "매출.png").write_bytes(b"old")
    d = post(client, "/api/images", images=[{"name": "매출", "data": PNG}, {"name": "매출", "data": PNG}, {"name": 'a/b:c?', "data": JPG},
                                            {"name": "CON", "data": PNG}, {"name": " . ", "data": PNG}])
    assert d["saved"] == ["매출 (2).png", "매출 (3).png", "a_b_c_.jpg", "_CON.png", "chart.png"]
    assert (tmp_path / "매출.png").read_bytes() == b"old"  # 있던 파일은 덮어쓰지 않는다
    assert (tmp_path / "a_b_c_.jpg").read_bytes().startswith(b"\xff\xd8\xff")


def test_images_cancel_and_checks(client, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(server, "ask_folder", lambda: asked.append(1))
    assert post(client, "/api/images", images=[{"name": "a", "data": PNG}]) == {"cancelled": True}
    for images, error in [([], "1개 이상"), ([{"name": "a", "data": PNG}] * (dash_store.MAX_CARDS + 1), "1개 이상"),
                          ([{"name": "a", "data": "data:image/svg+xml;base64,AAAA"}], "PNG·JPG"),
                          ([{"name": "a", "data": "data:image/png;base64,@@@"}], "올바르지")]:
        assert error in post(client, "/api/images", images=images)["error"]
    assert asked == [1]  # 잘못된 요청에는 폴더를 묻지 않는다


@pytest.mark.parametrize("name, value", [("board_ratio", "4:3"), ("board_format", "jpeg")])
def test_board_settings(folder, name, value):
    settings.save({name: value})
    assert settings.load()[name] == value
