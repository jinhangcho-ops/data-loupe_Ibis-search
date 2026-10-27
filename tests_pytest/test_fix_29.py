"""29차 리뷰·감사: 대시보드 시각 기간·달 비교 라벨·기준일 빈칸·산점도 기간, 이름 길이·대소문자, MySQL 상관 서브쿼리, 지도 하와이 점, 시간대 붙은 시각."""
import json
import types

import polars as pl
from lazy import ibis

from core import scan_size
from dashboard import dash_refresh
from geo import mapcard
from prep import prep_expr
from tests_pytest.test_dash_calc import card, compute, db  # noqa: F401  db는 fixture


def test_dashboard_period_with_time_is_refused(db):
    span = db.post("/api/period/parse", json={"text": "2026-10-05 09:00 ~ 2026-10-05 17:00", "as_of": "", "dashboard": True}).get_json()
    assert "날짜로" in span["span"]["error"] and span["context"] is None


def test_month_period_compare_label():
    assert dash_refresh.context({"period": "2026-10", "as_of": "2026-11-20"})["prev"]["text"] == "지난달"
    assert dash_refresh.context({"period": "2026-Q3", "as_of": "2026-11-20"})["prev"]["text"] == "직전 분기"
    assert dash_refresh.context({"period": "", "as_of": "2026-09-30"}) == {"as_of": "2026-09-30"}


def test_scatter_points_follow_dashboard_period(db):
    c = card("chart", date="day", body={"base": "orders", "x": "amt", "series": [{"y": "amt", "agg": "sum"}]})
    c["view"] = {"type": "scatter"}
    data = compute(db, card=c, ctx={"range": {"start": "2026-01-05", "end": "2026-01-06"}})["data"]
    assert len(data["points"]["points"]) == 4  # 1-05·1-06의 네 행만(전체는 6)


def test_sentence_asof_uses_dashboard_day_when_no_window(db):
    view = {"template": "{기준일}", "compare": {"mode": "span"}, "better": "up", "blanks": [{"name": "기준일", "kind": "asof", "format": "number"}]}
    c = {**card("sentence", body={"base": "orders"}), "view": view, "series": [{"y": "amt", "agg": "sum"}]}
    got = compute(db, card=c, ctx={"as_of": "2026-01-31"})
    assert got["data"]["values"]["blanks"]["기준일"]["text"] == "2026-01-31", got


def test_source_columns_differing_in_case_still_open():
    t = ibis.memtable(pl.DataFrame({"Name": ["a"], "name": ["b"]}))
    out, _ = prep_expr.build(None, {"base": "t"}, table=t)
    assert out.columns == ("Name", "name")


def test_mysql_dependent_subquery_multiplies():
    cols = ["id", "select_type", "table", "rows"]
    rows = [(1, "PRIMARY", "o", 100000), (2, "DEPENDENT SUBQUERY", "i", 1000)]
    cursor = type("C", (), {"description": [(c,) for c in cols], "fetchall": lambda self: rows, "close": lambda self: None})
    con = types.SimpleNamespace(name="mysql", raw_sql=lambda sql: cursor())
    assert scan_size.plan_rows(con, "SELECT 1", "mysql") == 100_000_000


def test_hawaii_point_stays_when_alaska_crosses_dateline():
    box = lambda x0, y0, x1, y1: [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    alaska = {"type": "MultiPolygon", "coordinates": [[box(-170, 52, -130, 71)], [box(172, 51, 180, 53)]]}
    hawaii = {"type": "Polygon", "coordinates": [box(-161, 18, -154, 23)]}
    regions = pl.DataFrame({"name": ["ak", "hi"], "geometry": [json.dumps(alaska), json.dumps(hawaii)]})
    out = mapcard.build(regions, pl.DataFrame({"lat": [21.3, 61.2], "lon": [-157.8, -149.9]}), {"name": "name"})["values"]
    assert out["points"] == [[-157.8, 21.3], [210.1, 61.2]]


def test_zoned_timestamp_widens_partition_range():
    low, high = scan_size.date_bounds("SELECT * FROM t WHERE d >= TIMESTAMP('2025-01-02 00:00:00+09:00') AND d < TIMESTAMP '2025-01-05'", "bigquery", "d")
    assert (str(low), str(high)) == ("2025-01-01", "2025-01-05")
