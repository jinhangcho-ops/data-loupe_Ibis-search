"""S1: 대시보드 비교 기준(compare_mode)을 서버가 저장·적용하고, 카드 여러 장을 한 번에 구할 때(cards/compute) 다른 카드의 계열이 섞이지 않는다.
데이터는 시험 안에서 지어낸 임시 DuckDB."""
import datetime as dt

import duckdb
import pytest

from core import server
from dashboard import dash_refresh


@pytest.fixture
def shop(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute('create table orders ("주문번호" varchar, "주문일" date, "채널" varchar, "금액" double, "수량" integer, "상태" varchar)')
    raw.executemany("insert into orders values (?, ?, ?, ?, ?, ?)",
                    [(f"o{i}", dt.date(2026, 1, 1) + dt.timedelta(days=i % 40), "앱웹"[i % 2], 1000.0 + i, 1 + i % 3, "완료") for i in range(80)])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def body(**extra):
    return {"schema": "", "base": "orders", "joins": [], "periods": [], "conditions": [], "calcs": [], "sorts": [], "columns": [], "series": [], **extra}


def card(kind, view=None, date="주문일", **b):
    return {"id": kind, "kind": kind, "title": kind, "view": {"compare": {"mode": "span"}, **(view or {})},
            "source": {"schema": "", "table": "orders", "body": body(**b), **({"date": date} if date else {})}}


SUM = lambda y: {"y": y, "agg": "sum"}  # noqa: E731


def test_cards_compute_keeps_each_card_series(shop):
    """kpi(금액)가 먼저, 같은 테이블의 donut_kpi(금액·수량)가 뒤에, 모든 컬럼을 읽는 recent_rows가 있으면 kpi가 '수량'을 찾다 실패했다."""
    cards = [card("kpi", series=[SUM("금액")]),
             card("donut_kpi", {"ring": "stack", "slots": [{"label": "금액"}, {"label": "수량"}]}, series=[SUM("금액"), SUM("수량")]),
             card("recent_rows", {"rows": 5}, date=None, columns=["주문번호", "주문일", "금액"])]
    ctx = {"range": {"start": "2026-01-15", "end": "2026-01-28"}, "prev": {"start": "2026-01-01", "end": "2026-01-14", "text": "직전 14일"}}
    together = shop.post("/api/dash/cards/compute", json={"cards": cards, "ctx": ctx}).get_json()["results"]
    assert all("error" not in r for r in together), together
    for one, r in zip(cards, together):  # 한 장씩 구한 것과 같다
        alone = shop.post("/api/dash/card/compute", json={"card": one, "ctx": ctx}).get_json()["data"]
        assert r["data"].get("by_unit") == alone.get("by_unit")


def made(shop, **extra):
    return shop.post("/api/dash", json={"name": "비교", "cards": [], "period": "2026-01-15 ~ 2026-01-28", "as_of": "2026-02-10", **extra}).get_json()["dashboard"]


def test_compare_mode_saved_and_applied(shop):
    d = made(shop)
    assert d["compare_mode"] == "prev"
    assert dash_refresh.context(d)["prev"] == {"start": "2026-01-01", "end": "2026-01-14", "text": "직전 14일"}
    for mode, start in (("same_weekday", "2026-01-01"), ("yoy", "2025-01-16")):
        saved = shop.post("/api/dash/settings", json={"id": d["id"], "compare_mode": mode}).get_json()["dashboard"]
        assert saved["compare_mode"] == mode and saved["period"] == d["period"]
        prev = dash_refresh.context(saved)["prev"]
        assert prev["start"] == start and prev["text"] != "직전 14일"
        parsed = shop.post("/api/period/parse", json={"text": saved["period"], "as_of": saved["as_of"], "compare_mode": mode}).get_json()
        assert parsed["context"]["prev"] == prev and parsed["previous"]["start"] == start
    assert dash_refresh.context({"period": "2026-01-15 ~ 2026-01-28", "as_of": "2026-02-10", "compare_mode": "same_weekday"})["prev"]["text"] == "2주 전 같은 요일 기간"


def test_compare_mode_rejects_unknown(shop):
    d = made(shop)
    r = shop.post("/api/dash/settings", json={"id": d["id"], "compare_mode": "lastweek"})
    assert r.status_code >= 400 or "error" in r.get_json()
    assert shop.post("/api/dash", json={"name": "틀림", "cards": [], "compare_mode": "x"}).get_json().get("error")
    assert shop.post("/api/dash/open", json={"id": d["id"]}).get_json()["dashboard"]["compare_mode"] == "prev"


def test_compare_mode_cut_and_own_range():
    """기준일로 잘린 달은 작년 같은 달의 같은 날까지, 대시보드 기간이 비면 카드 자신의 기간 줄도 같은 비교 기준을 쓴다."""
    from dashboard import dash_calc
    assert dash_refresh.context({"period": "2026-01", "as_of": "2026-01-20", "compare_mode": "yoy"})["prev"] == {"start": "2025-01-01", "end": "2025-01-20", "text": "작년 같은 기간"}
    assert dash_refresh.context({"period": "2026-01", "as_of": "2026-01-20"})["prev"]["start"] == "2025-12-01"  # 기본은 그대로
    ctx = dash_refresh.context({"period": "", "compare_mode": "yoy"})
    c = {"source": {"body": {"periods": ["주문일 2026-03-01 ~ 2026-03-31"]}}}
    assert dash_calc.own_range(c, ctx["compare_mode"])["prev"] == {"start": "2025-03-01", "end": "2025-03-31", "text": "작년 같은 기간"}
    assert dash_calc.own_range(c)["prev"]["text"] == "지난달" and dash_refresh.context({"period": ""}) is None


def test_date_settings_saved_and_timezone_cleared(folder):
    from core import settings
    settings.save({"timezone": "+09:00", "week_start": "sun", "fiscal_start_month": 4})
    assert {k: settings.load().get(k) for k in ("timezone", "week_start", "fiscal_start_month")} == {"timezone": "+09:00", "week_start": "sun", "fiscal_start_month": 4}
    settings.save({"timezone": 0})  # 칸을 비우면 PC 시간대로
    assert "timezone" not in settings.load()
    with pytest.raises(ValueError):
        settings.save({"timezone": "Mars/Base"})
