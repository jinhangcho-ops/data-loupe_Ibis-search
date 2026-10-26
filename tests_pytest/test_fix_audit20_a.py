"""감사 20A: 주 시작(period_start)이 SQLite의 글자 날짜('2026-10-18 00:00:00')에서도 돌고, 식 truncate(x, 'week')도 어느 DB에서나 월요일 시작이어야 한다."""

import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from analysis import stats
from core import sql_funcs


@pytest.mark.parametrize("ctype, text", [("date", "2026-10-18 00:00:00"), ("date", "2026-10-18T00:00:00"), ("date", "2026-10-18"),
                                         ("timestamp", "2026-10-18T13:05:00"), ("timestamp", "2026-10-18 13:05:00")])
def test_sqlite_text_dates_week(ctype, text):
    con = ibis.sqlite.connect()
    con.raw_sql(f"create table t(d {ctype})")
    con.raw_sql(f"insert into t values ('{text}')")  # sqlite3 기본 변환기·pandas to_sql이 쓰는 글자 그대로
    t = con.table("t")
    got = t.select(w=stats.period_start(t.d, "W"), f=sql_funcs.make("truncate", t, {"x": "d", "unit": "week"})).execute()
    assert [str(v)[:10] for v in (got.w[0], got.f[0])] == ["2026-10-12", "2026-10-12"]


@pytest.mark.parametrize("dialect", ["mssql", "mysql", "postgres", "sqlite", "bigquery"])
def test_formula_week_has_no_week_truncate(dialect):
    t = ibis.table({"d": "timestamp", "e": "date"}, name="t")
    e = t.select(a=sql_funcs.make("truncate", t, {"x": "d", "unit": "week"}), b=sql_funcs.make("truncate", t, {"x": "e", "unit": "week"}))
    assert "WEEK" not in ibis.to_sql(e, dialect=dialect).upper().replace("DAYOFWEEK", "")


@pytest.mark.parametrize("backend", ["duckdb", "polars"])
def test_formula_truncate_values_unchanged(backend):
    days = [dt.date(2000, 1, 1) + dt.timedelta(days=i) for i in range(0, 3000, 13)]
    df = pl.DataFrame({"e": days, "d": [dt.datetime.combine(x, dt.time(13, 5)) for x in days]})
    con = ibis.polars.connect() if backend == "polars" else ibis.duckdb.connect()
    t = con.create_table("t", df) if backend == "polars" else ibis.memtable(df.to_arrow())
    for unit in ("day", "week", "month", "year"):
        for c in ("e", "d"):
            e = t.select(a=sql_funcs.make("truncate", t, {"x": c, "unit": unit}), b=t[c].truncate(sql_funcs.UNITS[unit]))
            assert e.a.type().is_date() == t[c].type().is_date() and con.to_pyarrow(e.filter(e.a != e.b).count()).as_py() == 0, (unit, c)
