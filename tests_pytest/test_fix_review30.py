"""30차 리뷰: 달 비교 이름의 달력 맞춤, MySQL select_type만 보기, 시각 리터럴 안의 시간대."""
import types

import pytest

from core import scan_size
from dashboard import dash_refresh


def test_month_names_follow_calendar():
    assert dash_refresh.context({"period": "2025-03 ~ 2026-02", "as_of": "2026-11-20"})["prev"]["text"] == "직전 12개월"
    assert dash_refresh.context({"period": "2026-02 ~ 2026-04", "as_of": "2026-11-20"})["prev"]["text"] == "직전 3개월"
    assert dash_refresh.context({"period": "2026-Q3", "as_of": "2026-11-20"})["prev"]["text"] == "직전 분기"


def test_table_named_dependents_is_not_a_dependent_subquery():
    cols = ["id", "select_type", "table", "rows"]
    rows = [(1, "PRIMARY", "employees", 1000), (2, "UNION", "dependents", 2000)]
    cursor = type("C", (), {"description": [(c,) for c in cols], "fetchall": lambda self: rows, "close": lambda self: None})
    assert scan_size.plan_rows(types.SimpleNamespace(name="mysql", raw_sql=lambda sql: cursor()), "SELECT 1", "mysql") == 3000


@pytest.mark.parametrize("value", ["TIMESTAMP '2025-01-02 00:00:00+09'", "TIMESTAMP '2025-01-02 00:00:00 Asia/Seoul'"])
def test_zone_inside_timestamp_literal_widens(value):
    assert str(scan_size.date_bounds(f"SELECT * FROM t WHERE d >= {value}", "bigquery", "d")[0]) == "2025-01-01"
    assert str(scan_size.date_bounds("SELECT * FROM t WHERE d >= '2025-01-02'", "bigquery", "d")[0]) == "2025-01-02"
