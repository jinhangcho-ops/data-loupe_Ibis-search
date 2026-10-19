"""대시보드: 이름별 저장·지우기와 크기·개수 검사, 고른 그래프 그림을 폴더에 저장(덮어쓰지 않음, 파일 이름 정리), 받침에 맞는 조사."""
import base64

import pytest

import server
import settings

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
JPG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 20).decode()
CHART = {"title": "매출", "ratio": 1.5, "ranges": {"y": {}, "y1": {}},
         "query": {"base": "orders", "x": "매장", "series": [{"y": "금액", "agg": "sum"}]}, "used": [{"y": "금액", "agg": "sum", "type": "bar"}]}


def post(client, url, **body):
    return client.post(url, json=body).get_json()


def test_save_open_delete(client):
    assert post(client, "/api/dashboards", name=" 주간 ", board={"charts": [CHART]})["dashboards"] == {"주간": {"charts": [CHART]}}
    assert client.get("/api/dashboards").get_json()["dashboards"]["주간"]["charts"][0]["query"]["base"] == "orders"
    assert post(client, "/api/dashboards", name="주간", board=None)["dashboards"] == {}


def test_board_checks(client):
    assert "1개 이상" in post(client, "/api/dashboards", name="빈", board={"charts": []})["error"]
    assert "1개 이상" in post(client, "/api/dashboards", name="많음", board={"charts": [CHART] * (settings.MAX_BOARD_CHARTS + 1)})["error"]
    assert "이름" in post(client, "/api/dashboards", name="", board={"charts": [CHART]})["error"]
    big = {**CHART, "title": "가" * settings.MAX_BOARD_SIZE}
    assert post(client, "/api/dashboards", name="큼", board={"charts": [big]})["error"] == "저장할 대시보드가 너무 큽니다."


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
    for images, error in [([], "1개 이상"), ([{"name": "a", "data": PNG}] * (settings.MAX_BOARD_CHARTS + 1), "1개 이상"),
                          ([{"name": "a", "data": "data:image/svg+xml;base64,AAAA"}], "PNG·JPG"),
                          ([{"name": "a", "data": "data:image/png;base64,@@@"}], "올바르지")]:
        assert error in post(client, "/api/images", images=images)["error"]
    assert asked == [1]  # 잘못된 요청에는 폴더를 묻지 않는다


@pytest.mark.parametrize("name, value", [("board_ratio", "4:3"), ("board_format", "jpeg")])
def test_board_settings(folder, name, value):
    settings.save({name: value})
    assert settings.load()[name] == value
