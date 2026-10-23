"""대시보드 최신화(dash_refresh)와 기간·비교 라우트: 주기 검사, 연결별 실시간 간격 최소, 최신화 작업(가짜 연결, 실패한 카드는 이전 값 유지, 멈추기),
/api/period/parse·day, /api/compare, /api/dash/settings. 값은 테스트 안에서 지어낸다."""
import contextlib
import datetime as dt
import types

import pytest

from core import querying
from dashboard import dash_calc
from dashboard import dash_refresh as refresh
from core import dash_store as store
from core import server

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
    monkeypatch.setattr(querying, "using", lambda **k: contextlib.nullcontext())
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


def test_skip_reason_by_key_and_age():
    old, fresh = (NOW - dt.timedelta(days=2)).isoformat(), (NOW - dt.timedelta(hours=5)).isoformat()
    d = {"id": "d1", "name": "a", "refresh": {"every": "1일"}, "cards": [
        {**card("오래됨", computed=old), "id": "c1"}, {**card("방금", computed=fresh), "id": "c2"}, {**card("값 없음"), "id": "c3"},
        {**card("다른 연결", key=OTHER, computed=old), "id": "c4"}, {**card("recent", kind="recent_rows", computed=old), "id": "c5"}]}
    assert refresh.skip_reason(d, d["cards"][1], KEY, NOW) == "아직 주기가 지나지 않았어요"
    assert "다른 연결" in refresh.skip_reason(d, d["cards"][3], KEY, NOW) and "종류" in refresh.skip_reason(d, d["cards"][4], KEY, NOW)


def test_job_saves_ok_keeps_old_value_on_failure(fake, monkeypatch):
    d = save("주간", [card("성공"), card("실패"), card("다른 연결", key=OTHER)])
    ids = {c["title"]: c["id"] for c in d["cards"]}

    def compute(c, now, ctx=None):
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

    def compute(c, now, ctx=None):
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


def test_context_for_refresh_uses_period_and_as_of():
    got = refresh.context({"period": "2026-03", "as_of": "2026-03-15"})
    assert got == {"range": {"start": "2026-03-01", "end": "2026-03-15"}, "prev": {"start": "2026-02-01", "end": "2026-02-15", "text": "지난달 같은 기간"}}
    assert refresh.context({"period": "최근 7일", "as_of": ""}, dt.date(2026, 10, 22))["range"] == {"start": "2026-10-16", "end": "2026-10-22"}
    assert refresh.context({"period": "~ 2026-03-31", "as_of": ""}, dt.date(2026, 10, 22))["range"] == {"start": None, "end": "2026-03-31"} and refresh.context({"period": "엉터리"}) is None
    assert {"kpi", "donut_kpi", "gauge", "summary", "dual_trend", "stripes", "blocks", "target_trend"} <= set(refresh.REFRESHABLE) and "recent_rows" not in refresh.REFRESHABLE


def test_scan_decision_adapter(fake, monkeypatch):
    from core import scan_size
    from core import settings
    c = store.check_card({"kind": "chart", "title": "t", "source": {"key": KEY, "body": {"base": "t", "x": "a"}}})
    monkeypatch.setattr(refresh.dash_calc, "read_sql", lambda card, ctx: "SELECT 1")
    monkeypatch.setattr(refresh, "run", lambda work: work())  # 연결 스레드 대신 바로(연결이 가짜)
    monkeypatch.setitem(server.state, "con", types.SimpleNamespace(name="duckdb"))  # 로컬 연결: 읽는 양을 센다
    monkeypatch.delattr(scan_size, "limit_check", raising=False)
    assert refresh.scan_decision(c, {}) == ""  # 판정 함수가 아직 없으면 막지 않는다
    answer = {}
    monkeypatch.setattr(scan_size, "limit_check", lambda con, sql, opened=None, dialect=None: answer, raising=False)
    answer.update(over=True, unknown=False, text="약 12GB를 읽어요 · 상한 4GB")
    assert refresh.scan_decision(c, {}) == "약 12GB를 읽어요 · 상한 4GB"  # 기준을 넘으면 그 이유로 건너뜀
    answer.update(over=False, unknown=True, blocked=False, text="이 연결은 미리 알 수 없어요")
    assert refresh.scan_decision(c, {}) == ""  # 추정할 수 없어도 기본은 실행
    answer.update(blocked=True)
    assert refresh.scan_decision(c, {}) == "이 연결은 미리 알 수 없어요"  # 설정이 차단이면(blocked) 건너뜀
    answer.update(over=False, unknown=False, blocked=False)
    assert refresh.scan_decision(c, {}) == ""
    monkeypatch.setattr(settings, "load", lambda: {"scan_unknown": "차단"})
    monkeypatch.setattr(scan_size, "limit_check", lambda *a: (_ for _ in ()).throw(RuntimeError("측정 실패")), raising=False)
    assert "측정 실패" in refresh.scan_decision(c, {})  # 추정이 실패하면 추정 불가로 보고 차단 설정이면 건너뜀
    monkeypatch.setattr(settings, "load", lambda: {})
    assert refresh.scan_decision(c, {}) == ""
    monkeypatch.setattr(refresh.dash_calc, "read_sql", lambda card, ctx: None)
    assert refresh.scan_decision(c, {}) == ""  # 읽는 식이 없는 카드는 묻지 않는다


def test_scan_decision_remote_asks_only_when_data_is_copied(fake, monkeypatch):
    """원격 DB(요금이 나올 수 있음): DB 안의 집계로 구하는 카드는 크기를 알아보려고 DB에 묻지 않고, PC로 옮기는 카드만 판정한다."""
    from core import scan_size
    c = store.check_card({"kind": "chart", "title": "t", "source": {"key": KEY, "body": {"base": "t", "x": "a"}}})
    monkeypatch.setattr(refresh.dash_calc, "read_sql", lambda card, ctx: "SELECT 1")
    monkeypatch.setattr(refresh, "run", lambda work: work())
    monkeypatch.setitem(server.state, "con", types.SimpleNamespace(name="postgres"))
    asked = []
    monkeypatch.setattr(scan_size, "limit_check", lambda con, sql, opened=None, dialect=None: asked.append(sql) or {"over": True, "text": "크다"}, raising=False)
    monkeypatch.setattr(refresh.dash_calc, "copies_to_pc", lambda card, ctx: False)
    assert refresh.scan_decision(c, {}) == "" and asked == []  # 집계는 묻지 않는다
    monkeypatch.setattr(refresh.dash_calc, "copies_to_pc", lambda card, ctx: True)
    assert refresh.scan_decision(c, {}) == "크다" and asked == ["SELECT 1"]


def test_job_skips_card_over_the_limit_without_computing(fake, monkeypatch):
    d = save("용량", [card("크다"), card("작다")])
    computed = []
    monkeypatch.setattr(dash_calc, "compute", lambda c, now, ctx=None: computed.append(c["title"]) or {"values": {"labels": ["new"], "series": []}})
    monkeypatch.setattr(refresh, "scan_decision", lambda c, ctx: "약 12GB를 읽어요" if c["title"] == "크다" else "")
    refresh.start(NOW)
    refresh.wait()
    done = refresh.status()
    by = {r["title"]: r for r in done["results"]}
    assert by["크다"]["state"] == "skipped" and by["크다"]["message"] == "약 12GB를 읽어요" and by["작다"]["state"] == "ok" and computed == ["작다"]
    assert done["done"] == 2 and not done["running"]  # 건너뛴 카드도 끝난 것으로 센다
    saved = {c["title"]: c["data"]["values"]["labels"] for c in store.get_dashboard(d["id"])["cards"]}
    assert saved == {"크다": ["old"], "작다": ["new"]}  # 건너뛴 카드는 이전 값 그대로
