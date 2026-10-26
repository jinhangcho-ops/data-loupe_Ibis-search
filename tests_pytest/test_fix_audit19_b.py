"""감사 19B: 소수(decimal) 조건을 실수로 비교하던 문제, 대소문자만 다른 값의 피벗 열 이름 충돌, 없는 날짜(2025-02-30) 하나로 전처리 전체가 실패하던 문제."""
import datetime as dt
from decimal import Decimal

import ibis
import polars as pl
import pytest

from core import calc, parse, prep_ops

IDS = [Decimal("12345678901234567890"), Decimal("12345678901234567891"), Decimal("12345678901234567000")]


def decimal_table(backend):
    df = pl.DataFrame({"id": IDS, "amt": [Decimal("0.10"), None, Decimal("0.20")]}, schema={"id": pl.Decimal(38, 0), "amt": pl.Decimal(10, 2)})
    if backend == "duckdb":
        con = ibis.duckdb.connect()
        con.raw_sql("create table t(id decimal(38,0), amt decimal(10,2))")
        con.insert("t", df)
        return con.table("t")
    return ibis.polars.connect().create_table("t", df)


BACKENDS = ["duckdb", "polars"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_decimal_conditions_exact(backend):
    t = decimal_table(backend)
    ids = lambda cond: sorted(str(x) for x in t.filter(parse.parse_condition(cond, t)).to_polars()["id"].to_list())
    assert ids("id = 12345678901234567891") == ["12345678901234567891"]
    assert ids("id != 12345678901234567891") == ["12345678901234567000", "12345678901234567890"]
    assert ids("id in 12345678901234567891, 12345678901234567000") == ["12345678901234567000", "12345678901234567891"]
    assert ids("id except 12345678901234567891") == ["12345678901234567000", "12345678901234567890"]
    assert ids("id between 12345678901234567001 12345678901234567890") == ["12345678901234567890"]
    assert ids("id > 12345678901234567890") == ["12345678901234567891"]
    assert ids("amt = 0.1") == ["12345678901234567890"]
    assert ids("amt < 0.155") == ["12345678901234567890"]
    with pytest.raises(ValueError, match="실수 값이 아닙니다"):
        parse.parse_condition("id = abc", t)


@pytest.mark.parametrize("backend", BACKENDS)
def test_decimal_fill(backend):
    t = decimal_table(backend)
    assert prep_ops.fill(t, "amt", {"how": "value", "value": "0.5"}).to_polars()["amt"].to_list()[1] == Decimal("0.5")
    assert calc.each("빈값채우기", ["0.5", "amt"], t).to_polars()["amt"].to_list()[1] == Decimal("0.5")


@pytest.mark.parametrize("backend", BACKENDS)
def test_pivot_case_only_values(backend):
    df = pl.DataFrame({"g": ["x", "x", "x", "z"], "flag": ["Y", "y", "y", "Y"]})
    if backend == "duckdb":
        con = ibis.duckdb.connect()
        con.raw_sql("create table t(g varchar, flag varchar)")
        con.insert("t", df)
        t = con.table("t")
    else:
        t = ibis.polars.connect().create_table("t", df)
    out = calc.pivot(t, {"rows": ["g"], "column": "flag", "how": "count"}, lambda e: e.to_pyarrow()["v"].to_pylist()).to_polars()
    assert len({c.casefold() for c in out.columns}) == len(out.columns)
    assert out.row(0) == ("x", 3, 1, 2)


def text_table(backend, df):
    if backend == "polars":
        return ibis.polars.connect().create_table("t", df)
    con = ibis.duckdb.connect()
    con.raw_sql("create table t(d varchar)")
    con.insert("t", df)
    return con.table("t")


DATES = ["2025-01-31", "2025-02-30", "2024-02-29", "2025-02-29", "2025-04-31", "abc", None]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("fmt,vals", [(None, DATES), ("%Y%m%d", [None if v is None else v.replace("-", "") for v in DATES]), ("%d %b %y", ["31 Jan 25", "30 feb 25", "29 Feb 24", "29 Feb 25", "31 Apr 25", "abc", None])])
def test_impossible_date_becomes_null(backend, fmt, vals):
    t = text_table(backend, pl.DataFrame({"d": vals}))
    got = t.mutate(d2=prep_ops.retype(t.d, {"to": "date", **({"format": fmt} if fmt else {})})).to_polars()["d2"].to_list()
    assert got == [dt.date(2025, 1, 31), None, dt.date(2024, 2, 29), None, None, None, None]


@pytest.mark.parametrize("backend", BACKENDS)
def test_century_leap_and_time(backend):
    df = pl.DataFrame({"d": ["2000-02-29 10:00:00", "1900-02-29 10:00:00", "2025-06-31 00:00:00", "2025-06-30 23:59:59"]})
    t = text_table(backend, df)
    got = t.mutate(d2=prep_ops.retype(t.d, {"to": "date", "format": "%Y-%m-%d %H:%M:%S"})).to_polars()["d2"].to_list()
    assert got == [dt.datetime(2000, 2, 29, 10), None, None, dt.datetime(2025, 6, 30, 23, 59, 59)]
