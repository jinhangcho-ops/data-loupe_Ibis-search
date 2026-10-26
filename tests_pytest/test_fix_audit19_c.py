"""감사 19C1: 주 단위 묶음은 어느 DB에서나 월요일 시작이어야 한다(SQL Server DATETRUNC(WEEK)는 DATEFIRST를 따라 일요일, MySQL은 WEEK를 못 자른다)."""

import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from analysis import stats
from dashboard import dash_calc

DAYS = [dt.date(1700, 1, 1) + dt.timedelta(days=i) for i in range(0, 200000, 37)]  # pandas ns 범위(1677~2262) 안
MONDAYS = [d - dt.timedelta(days=d.weekday()) for d in DAYS]
DF = pl.DataFrame({"e": DAYS, "d": [dt.datetime.combine(d, dt.time(13, 5)) for d in DAYS], "v": list(range(len(DAYS)))})


def as_dates(values):
    return [v.date() if isinstance(v, dt.datetime) else v for v in values]


@pytest.mark.parametrize("backend", ["duckdb", "polars", "sqlite"])
@pytest.mark.filterwarnings("ignore:The default date adapter:DeprecationWarning")  # Ibis가 sqlite에 날짜를 넣을 때 쓰는 기본 변환기
def test_week_start_is_monday_on_engines(backend):
    con = ibis.polars.connect() if backend == "polars" else getattr(ibis, backend).connect()
    t = con.create_table("t", DF) if backend == "polars" else ibis.memtable(DF.to_arrow())
    got = con.to_pyarrow(t.select(we=stats.period_start(t.e, "W"), wd=stats.period_start(t.d, "W"))).to_pydict()
    assert as_dates(got["we"]) == MONDAYS and as_dates(got["wd"]) == MONDAYS
    if backend == "duckdb":  # DuckDB의 truncate('W')는 원래 월요일 시작: 값이 같아야 한다
        e = t.select(a=stats.period_start(t.d, "W"), b=t.d.truncate("W"))
        assert e.a.type().is_timestamp() and con.to_pyarrow(e.filter(e.a != e.b).count()).as_py() == 0


@pytest.mark.parametrize("dialect", ["duckdb", "mssql", "mysql", "postgres", "sqlite", "bigquery"])
def test_week_start_compiles_without_week_truncate(dialect):
    t = ibis.table({"d": "timestamp", "e": "date"}, name="t")
    sql = ibis.to_sql(t.select(a=stats.period_start(t.d, "W"), b=stats.period_start(t.e, "W")), dialect=dialect)
    assert "WEEK" not in sql.upper().replace("DAYOFWEEK", "")


def test_trend_week_on_mssql_is_not_datefirst(monkeypatch):
    seen = []
    monkeypatch.setattr(stats, "rows", lambda expr: seen.append(expr) or [])  # 실행 대신 만든 식을 받는다
    t = ibis.table({"d": "timestamp", "v": "int64"}, name="t")
    stats.trend(t, "d", "week", "v", "sum")
    assert "DATETRUNC(WEEK" not in ibis.to_sql(seen[0], dialect="mssql")
    ibis.to_sql(seen[0], dialect="mysql")  # 전에는 Unsupported truncate unit WEEK


@pytest.mark.parametrize("backend", ["duckdb", "polars"])
def test_dash_grouped_week_monday(backend):
    t = ibis.polars.connect().create_table("t", DF) if backend == "polars" else ibis.memtable(DF.to_arrow())
    frame = dash_calc.grouped(t, "d", "week", None, None, lambda tt: {"n": tt.v.count()})
    assert as_dates(frame["x"].to_list()) == sorted(set(MONDAYS))
