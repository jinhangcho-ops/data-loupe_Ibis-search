"""(B10b) KPI 큰 창의 시간 눈금 값(dashboard/kpi_hours.hour_values, /api/dash/card/hours)과 없앤 KPI 모양 설정(아이콘·값 맞춤·추이선 자리)을 받고 버리기.
값은 손으로 계산해 맞춘다. 데이터는 시험 안에서 지어낸다."""
import datetime as dt

import duckdb
import pytest

from core import dash_store
from core import server

# (ts 시각, d 날짜, amt)
ROWS = [(dt.datetime(2026, 6, 1, 9, 10), dt.date(2026, 6, 1), 1.0), (dt.datetime(2026, 6, 1, 9, 50), dt.date(2026, 6, 1), 2.0),
        (dt.datetime(2026, 6, 1, 10, 0), dt.date(2026, 6, 1), 5.0), (dt.datetime(2026, 6, 2, 9, 30), dt.date(2026, 6, 2), 7.0)]
SUM = [{"y": "amt", "agg": "sum"}]


@pytest.fixture
def db(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "h.duckdb"))
    raw.execute("create table events (ts timestamp, d date, amt double)")
    raw.executemany("insert into events values (?, ?, ?)", ROWS)
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "h.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def kpi(date="ts", series=SUM, **body):
    return {"kind": "kpi", "title": "건수", "source": {"schema": "", "table": "events", **({"date": date} if date else {}), "body": {"base": "events", "series": series, **body}}}


def hours(card, ctx=None):
    from dashboard import kpi_hours
    return kpi_hours.hour_values(dash_store.check_card(card), ctx)


def test_hour_values_groups_first_series_by_hour(db):
    got = hours(kpi())
    assert got == {"labels": ["2026-06-01T09", "2026-06-01T10", "2026-06-02T09"], "values": [3.0, 5.0, 7.0], "reason": ""}  # 1+2, 5, 7


def test_hour_values_reads_the_same_window_as_daily_values(db):
    only = hours(kpi(), {"range": {"start": "2026-06-02", "end": "2026-06-02"}, "prev": None})
    assert only["labels"] == ["2026-06-02T09"] and only["values"] == [7.0]  # 직전 기간이 없으면 보는 기간만
    both = hours(kpi(), {"range": {"start": "2026-06-02", "end": "2026-06-02"}, "prev": {"start": "2026-06-01", "end": "2026-06-01", "text": "직전 1일"}})
    assert both["labels"] == ["2026-06-01T09", "2026-06-01T10", "2026-06-02T09"]  # 직전 기간 시작부터 보는 기간 끝까지(fetch_window)


def test_hour_values_first_series_only_and_count(db):
    got = hours(kpi(series=[{"y": None, "agg": "count"}, *SUM]))
    assert got["values"] == [2.0, 1.0, 1.0]  # 첫 계열(개수)만: 9시 2행, 10시 1행, 다음 날 9시 1행. 필터는 라우트가 건다(with_filters)


def test_hour_values_date_only_column_says_why(db):
    assert hours(kpi(date="d")) == {"labels": [], "values": [], "reason": "'d'에는 시각이 없어 일 눈금으로 보여요."}


def test_hour_values_needs_date_column(db):
    with pytest.raises(ValueError, match="기간 기준 날짜 컬럼"):
        hours(kpi(date=None))


def test_hour_values_keeps_most_recent_hours(db, monkeypatch):
    from dashboard import kpi_hours
    monkeypatch.setattr(kpi_hours, "MAX_HOURS", 2)
    got = hours(kpi())
    assert got["labels"] == ["2026-06-01T10", "2026-06-02T09"] and got["values"] == [5.0, 7.0]  # 가장 최근 칸부터 자르고 시간 순으로


def test_card_hours_route_applies_filters(db):
    got = db.post("/api/dash/card/hours", json={"card": kpi(), "ctx": {"filters": [{"column": "amt", "in": ["5", "7"]}]}}).get_json()
    assert got == {"labels": ["2026-06-01T10", "2026-06-02T09"], "values": [5.0, 7.0], "reason": ""}
    other = db.post("/api/dash/card/hours", json={"card": {**kpi(), "source": {**kpi()["source"], "key": "duckdb://다른.duckdb"}}}).get_json()
    assert "다른 연결" in other["error"]


def test_old_kpi_look_settings_are_taken_and_dropped():
    assert dash_store.GONE_VIEW == ("icon", "fit", "layout")
    got = dash_store.check_view({"icon": "x", "fit": "stretch", "layout": "left", "format": "percent"})  # 옛 값·틀린 값 모두 거부하지 않고 버린다
    assert not {"icon", "fit", "layout"} & set(got) and got["format"] == "percent"
    assert not hasattr(dash_store, "FITS") and not hasattr(dash_store, "LAYOUTS")
    assert dash_store.ICONS  # 단추 요소의 아이콘 목록은 그대로


# ---- (B10b 추가) 카드 기간 줄만 있는 KPI도 직전 기간의 일 값을 읽는다(kpi_window) ----
OWN_D = ["d 2026-06-02 ~ 2026-06-02"]  # 카드 기간 줄: 이번 6-02, 직전(prev) 6-01


def values(card, ctx=None):
    from dashboard import dash_calc
    return dash_calc.value_data(dash_store.check_card(card), ctx)


def test_kpi_window_only_for_kpi_with_own_line_on_its_date_column(db):
    from dashboard import dash_calc
    own = dash_store.check_card(kpi(date="d", periods=OWN_D))
    read, window = dash_calc.kpi_window(own, None)
    assert window == {"start": "2026-06-01", "end": "2026-06-02"}  # 직전 기간 시작 ~ 이번 기간 끝
    assert read["source"]["body"]["periods"] == [] and own["source"]["body"]["periods"] == OWN_D  # 읽을 카드에서만 그 줄을 뺀다(원래 카드는 그대로)
    ctx = {"range": {"start": "2026-06-02", "end": "2026-06-02"}, "prev": None}
    assert dash_calc.kpi_window(own, ctx) == (own, dash_calc.fetch_window(ctx))  # 대시보드 기간이 있으면 지금 그대로
    other = {**own, "kind": "dual_trend"}
    assert dash_calc.kpi_window(other, None) == (other, None)  # KPI가 아닌 종류는 넓히지 않는다
    apart = dash_store.check_card(kpi(date="ts", periods=OWN_D))
    assert dash_calc.kpi_window(apart, None) == (apart, None)  # 기간 줄 컬럼(d)이 일 값 날짜 컬럼(ts)과 다르면 넓히지 않는다
    plain = dash_store.check_card(kpi(date="d"))
    assert dash_calc.kpi_window(plain, None) == (plain, None)  # 기간 줄이 없으면 전체


def test_kpi_own_range_reads_previous_days(db):
    got = values(kpi(date="d", periods=OWN_D))
    day = got["by_unit"]["day"]
    assert day["labels"] == ["2026-06-01", "2026-06-02"] and day["series"][0]["sum"] == [8.0, 7.0]  # 직전 6-01(1+2+5)과 이번 6-02(7)
    assert got["window"] == {"start": "2026-06-01", "end": "2026-06-02"}  # 화면 loadedRange·cardValue가 읽은 범위를 안다


def test_kpi_own_range_on_other_column_and_dashboard_range_stay_the_same(db):
    apart = values(kpi(date="ts", periods=OWN_D))
    assert apart["by_unit"]["day"]["labels"] == ["2026-06-02"] and "window" not in apart  # 넓히지 않는 카드는 지금 그대로
    ranged = values(kpi(date="d", periods=OWN_D), {"range": {"start": "2026-06-02", "end": "2026-06-02"}, "prev": None})
    assert ranged["by_unit"]["day"]["labels"] == ["2026-06-02"] and ranged["window"] == {"start": "2026-06-02", "end": "2026-06-02"}


def test_kpi_own_range_scan_reads_the_wider_window(db):
    from dashboard import dash_calc
    shaped = dash_calc.fetched(dash_store.check_card(kpi(date="d", periods=OWN_D)), None)
    assert shaped["periods"] == ["d 2026-06-01 ~ 2026-06-02"]  # 처리 용량 추정(read_sql)도 실제로 읽는 범위로


def test_hour_values_reads_previous_days_of_card_period(db):
    got = hours(kpi(periods=["ts 2026-06-02 ~ 2026-06-02"]))
    assert got["labels"] == ["2026-06-01T09", "2026-06-01T10", "2026-06-02T09"]  # 일 값과 같은 범위(kpi_window)


# ---- (B10b 추가) 비교 이름 "전월" ----
def test_month_compare_name_is_jeonwol():
    from dashboard import dash_refresh
    from dashboard import period
    assert period.previous_text(dt.date(2026, 5, 1), dt.date(2026, 5, 31)) == "전월"
    assert dash_refresh.context({"period": "2026-05", "as_of": "2026-06-10"})["prev"]["text"] == "전월"
    assert dash_refresh.context({"period": "이번 달", "as_of": "2026-06-10"})["prev"]["text"] == "지난달 같은 기간"  # 풀어 쓴 이름은 그대로(사용자 답)
    assert dash_refresh.context({"period": "2026-06", "as_of": "2026-06-10"})["prev"]["text"] == "지난달 같은 기간"  # 기준일로 잘린 달도
    assert period.previous_text(dt.date(2026, 4, 1), dt.date(2026, 6, 30)) == "직전 분기"
