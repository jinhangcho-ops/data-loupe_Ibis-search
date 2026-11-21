"""대시보드 최신화(dash_refresh)와 기간·비교 라우트: 주기 검사, 연결별 실시간 간격 최소, 최신화 작업(가짜 연결, 실패한 카드는 이전 값 유지, 멈추기),
/api/period/parse·day, /api/compare, /api/dash/settings. 값은 테스트 안에서 지어낸다."""
import contextlib
import datetime as dt
import types

import pytest

from core import querying
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
    yield


def save(name, cards, **more):
    return store.save_dashboard({"name": name, "cards": cards, **more})


def test_routes_period_day_compare_settings(client):
    post = lambda url, **body: client.post(url, json=body).get_json()
    span = post("/api/period/parse", text="최근 7일", as_of="2026-10-22")
    assert span["span"]["start"] == "2026-10-15" and span["span"]["end"] == "2026-10-21" and span["span"]["days"] == 7 and span["previous"]["start"] == "2026-10-08"  # 최근 N일은 어제까지
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
    pull = {"calc": "db", "every": {"n": 1, "unit": "h"}}
    fixed = post("/api/dash/settings", id=d["id"], as_of="2026-10-01", period="2026-Q3", pull=pull)["dashboard"]
    assert (fixed["as_of"], fixed["period"], fixed["pull"]["calc"], fixed["pull"]["every"]) == ("2026-10-01", "2026-Q3", "db", {"n": 1, "unit": "h"}) and len(fixed["cards"]) == 1
    assert "10초 이상" in post("/api/dash/settings", id=d["id"], pull={**pull, "every": {"n": 5, "unit": "s"}})["error"]
    assert "기준일" in post("/api/dash/settings", id=d["id"], as_of="2026-02-30")["error"]
    assert "10초 이상" in post("/api/dash", id=d["id"], name="설정", cards=[], pull={**pull, "every": {"n": 5, "unit": "s"}})["error"]
    post("/api/dash/last", id=d["id"])
    assert client.get("/api/dash").get_json()["last"] == d["id"]
    assert "찾을 수 없" in post("/api/dash/last", id="nope")["error"]


def test_context_for_refresh_uses_period_and_as_of():
    got = refresh.context({"period": "2026-03", "as_of": "2026-03-15"})
    assert got == {"range": {"start": "2026-03-01", "end": "2026-03-15"}, "prev": {"start": "2026-02-01", "end": "2026-02-15", "text": "지난달 같은 기간"}, "months": 1}
    assert refresh.context({"period": "최근 7일", "as_of": ""}, dt.date(2026, 10, 22))["range"] == {"start": "2026-10-15", "end": "2026-10-21"}  # 최근 N일은 어제까지
    assert refresh.context({"period": "~ 2026-03-31", "as_of": ""}, dt.date(2026, 10, 22))["range"] == {"start": None, "end": "2026-03-31"} and refresh.context({"period": "엉터리"}) is None


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
    monkeypatch.setattr(settings, "load", lambda: {"scan_unknown": "block"})
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


