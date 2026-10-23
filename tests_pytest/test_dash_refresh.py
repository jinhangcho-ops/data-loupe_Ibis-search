"""대시보드 최신화(dash_refresh)와 기간·비교 라우트: 주기 검사, 연결별 실시간 간격 최소, due_cards, 최신화 작업(가짜 연결, 실패한 카드는 이전 값 유지, 멈추기),
/api/period/parse·day, /api/compare, /api/dash/settings. 값은 테스트 안에서 지어낸다."""
import contextlib
import datetime as dt

import pytest

import core
import dash_calc
import dash_refresh as refresh
import dash_store as store
import server

NOW = dt.datetime(2026, 10, 22, 12, 0, 0)
FOLDER = {"kind": "folder", "path": "C:/data/sales"}
KEY = store.source_key(FOLDER)
OTHER = "url:postgres://elsewhere:5432/x"


def card(title, key=KEY, computed=None, kind="chart", data=None):
    return {"kind": kind, "title": title, "source": {"key": key, "label": "x", "table": "t", "body": {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}},
            "data": {"values": {"labels": ["old"], "series": []}, **({"computed_at": computed} if computed else {}), **(data or {})}}


@pytest.fixture
def fake(folder, monkeypatch):
    """연결을 흉내 낸다(열린 연결 요청 값만). 카드를 구하는 일은 시험이 바꿔 끼운다."""
    monkeypatch.setitem(server.state, "opened", FOLDER)
    monkeypatch.setattr(core, "using", lambda: contextlib.nullcontext())
    refresh.JOB.update(running=False, total=0, done=0, current="", results=[])
    yield
    refresh.wait(10)


def save(name, cards, every="1일", **more):
    return store.save_dashboard({"name": name, "cards": cards, "refresh": {"every": every}, **more})


def test_every_checks_and_live_min_per_connection(folder):
    assert refresh.every_seconds("", 3600, "최신화") == 0 and refresh.every_seconds("90분", 3600, "최신화") == 5400
    with pytest.raises(ValueError, match="최신화 주기는 1시간 이상"):
        refresh.every_seconds("30분", 3600, "최신화")
    with pytest.raises(ValueError, match="읽을 수 없는 주기"):
        refresh.every_seconds("가끔", 3600, "최신화")
    assert refresh.live_mins() == {} and refresh.live_min(KEY) == 0  # 비면 제한 없음
    refresh.check_dashboard_every({"cards": [card("a")], "live": {"every": "1초"}})
    refresh.set_live_min(KEY, 30)
    assert refresh.live_min(KEY) == 30 and refresh.live_min(OTHER) == 0
    with pytest.raises(ValueError, match="30초 이상"):
        refresh.check_dashboard_every({"cards": [card("a")], "live": {"every": "10초"}})
    refresh.check_dashboard_every({"cards": [card("a")], "live": {"every": "30초"}})
    refresh.check_dashboard_every({"cards": [card("a", key=OTHER)], "live": {"every": "1초"}})  # 다른 연결은 그 연결의 최소만
    with pytest.raises(ValueError, match="최신화 주기는 1시간 이상"):
        refresh.check_dashboard_every({"cards": [], "refresh": {"every": "10분"}})
    refresh.set_live_min(KEY, 0)
    assert refresh.live_mins() == {}
    with pytest.raises(ValueError):
        refresh.set_live_min(KEY, -1)
    with pytest.raises(ValueError, match="먼저 데이터에 연결"):
        refresh.set_live_min(None, 5)


def test_due_cards_by_key_and_age():
    old, fresh = (NOW - dt.timedelta(days=2)).isoformat(), (NOW - dt.timedelta(hours=5)).isoformat()
    d = {"id": "d1", "name": "a", "refresh": {"every": "1일"}, "cards": [
        {**card("오래됨", computed=old), "id": "c1"}, {**card("방금", computed=fresh), "id": "c2"}, {**card("값 없음"), "id": "c3"},
        {**card("다른 연결", key=OTHER, computed=old), "id": "c4"}, {**card("recent", kind="recent_rows", computed=old), "id": "c5"}]}
    off = {"id": "d2", "name": "b", "refresh": {"every": ""}, "cards": [{**card("주기 없음", computed=old), "id": "c6"}]}
    assert [c["id"] for _, c in refresh.due_cards([d, off], KEY, NOW)] == ["c1", "c3"]  # 구한 시각이 없거나 주기(1일)가 지난 것, 같은 연결만
    assert refresh.skip_reason(d, d["cards"][1], KEY, NOW) == "아직 주기가 지나지 않았어요"
    assert "다른 연결" in refresh.skip_reason(d, d["cards"][3], KEY, NOW) and "종류" in refresh.skip_reason(d, d["cards"][4], KEY, NOW)
    assert [c["id"] for _, c in refresh.due_cards([d], KEY, NOW + dt.timedelta(days=1))] == ["c1", "c2", "c3"]  # now는 실제 지금 시각


def test_job_saves_ok_keeps_old_value_on_failure(fake, monkeypatch):
    d = save("주간", [card("성공"), card("실패"), card("다른 연결", key=OTHER)])
    ids = {c["title"]: c["id"] for c in d["cards"]}

    def compute(c, now):
        if c["title"] == "실패":
            raise ValueError("조회가 실패했어요")
        return {"values": {"labels": ["new"], "series": []}, "computed_at": now}
    monkeypatch.setattr(dash_calc, "compute", compute)
    first = refresh.start(NOW)
    assert first["total"] == 2 and len(first["results"]) == 3
    refresh.wait()
    done = refresh.status()
    assert not done["running"] and done["done"] == 2
    by = {r["title"]: r for r in done["results"]}
    assert by["성공"]["state"] == "ok" and by["실패"]["state"] == "failed" and "조회가 실패" in by["실패"]["message"]
    assert by["다른 연결"]["state"] == "skipped" and "다른 연결" in by["다른 연결"]["message"]
    saved = {c["id"]: c for c in store.get_dashboard(d["id"])["cards"]}
    assert saved[ids["성공"]]["data"]["values"]["labels"] == ["new"] and saved[ids["성공"]]["data"]["computed_at"] == NOW.isoformat(timespec="seconds")
    assert saved[ids["실패"]]["data"]["values"]["labels"] == ["old"]  # 실패한 카드는 이전 값 유지
    assert saved[ids["다른 연결"]]["data"]["values"]["labels"] == ["old"]
    assert refresh.start(NOW)["total"] == 1  # 성공한 카드는 이제 최신이라 실패한 카드만 다시 대상이 된다
    refresh.wait()


def test_job_stop_skips_the_rest(fake, monkeypatch):
    d = save("멈춤", [card("하나"), card("둘"), card("셋")])
    seen = []

    def compute(c, now):
        seen.append(c["title"])
        refresh.stop()  # 첫 카드를 구하는 도중에 멈추기를 누른다
        return {"values": {"labels": ["new"], "series": []}}
    monkeypatch.setattr(dash_calc, "compute", compute)
    refresh.start(NOW)
    refresh.wait()
    done = refresh.status()
    assert seen == ["하나"] and [r["state"] for r in done["results"]] == ["ok", "skipped", "skipped"] and done["results"][1]["message"] == "멈췄어요" and not done["running"]
    assert {c["title"]: c["data"]["values"]["labels"] for c in store.get_dashboard(d["id"])["cards"]} == {"하나": ["new"], "둘": ["old"], "셋": ["old"]}


def test_start_needs_connection_and_nothing_due(fake, monkeypatch):
    save("아직", [card("최근", computed=NOW.isoformat())], every="1일")
    assert refresh.start(NOW + dt.timedelta(hours=1))["total"] == 0 and not refresh.status()["running"]
    monkeypatch.setitem(server.state, "opened", None)
    with pytest.raises(ValueError, match="먼저 데이터에 연결"):
        refresh.start(NOW)


def test_routes_period_day_compare_settings(client):
    post = lambda url, **body: client.post(url, json=body).get_json()
    span = post("/api/period/parse", text="최근 7일", as_of="2026-10-22")
    assert span["span"]["start"] == "2026-10-16" and span["span"]["end"] == "2026-10-22" and span["span"]["days"] == 7 and span["previous"]["start"] == "2026-10-09"
    assert "단위" in post("/api/period/parse", text="최근 3개", as_of="")["span"]["error"]
    bad = post("/api/period/parse", text="2026-13-01")
    assert bad["span"]["error"] and bad["previous"] is None
    assert post("/api/period/day", text="20261001")["label"] == "2026-10-01(목) 기준"
    assert "아직 오지 않은 날" in post("/api/period/day", text="2099-01-01")["error"]
    got = post("/api/compare", items=[{"cur": 120, "prev": 100, "label": "직전 7일"}, {"cur": 1, "prev": None, "label": "7일 전", "mode": "point", "better": "down"}])["results"]
    assert got[0]["pct"] == 20 and got[0]["direction"] == "up" and got[0]["tone"] == "good" and "직전 7일보다" in got[0]["text"]
    assert got[1]["tone"] == "bad" and "값이 없었어요" in got[1]["text"]
    assert "비교할 값" in post("/api/compare", items="x")["error"]
    d = post("/api/dash", name="설정", cards=[card("카드")])["dashboard"]
    fixed = post("/api/dash/settings", id=d["id"], as_of="2026-10-01", period="2026-Q3", refresh={"every": "2일"}, live={"every": "5분"})["dashboard"]
    assert (fixed["as_of"], fixed["period"], fixed["refresh"], fixed["live"]) == ("2026-10-01", "2026-Q3", {"every": "2일"}, {"every": "5분"}) and len(fixed["cards"]) == 1
    assert "1시간 이상" in post("/api/dash/settings", id=d["id"], refresh={"every": "10분"})["error"]
    assert "기준일" in post("/api/dash/settings", id=d["id"], as_of="2026-02-30")["error"]
    assert "1시간 이상" in post("/api/dash", id=d["id"], name="설정", cards=[], refresh={"every": "5분"})["error"]
    post("/api/dash/last", id=d["id"])
    assert client.get("/api/dash").get_json()["last"] == d["id"]
    assert "찾을 수 없" in post("/api/dash/last", id="nope")["error"]


def test_live_min_routes(client, monkeypatch):
    assert client.get("/api/dash/live_min").get_json() == {"source": None, "seconds": 0}
    assert "먼저 데이터에 연결" in client.post("/api/dash/live_min", json={"seconds": 5}).get_json()["error"]
    monkeypatch.setitem(server.state, "opened", FOLDER)
    got = client.post("/api/dash/live_min", json={"seconds": 45}).get_json()
    assert got["seconds"] == 45 and got["source"]["key"] == KEY
    d = client.post("/api/dash", json={"name": "실시간", "cards": [card("a")], "live": {"every": "10초"}}).get_json()
    assert "45초 이상" in d["error"]
    assert client.post("/api/dash/live_min", json={"seconds": 0}).get_json()["seconds"] == 0
    monkeypatch.setitem(server.state, "opened", None)
