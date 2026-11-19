"""T1: 타입 바꾸기 → 날짜시간의 단위 고르기. 고른 단위 아래는 잘라서 바꾸고 타입은 날짜시간 그대로다.
진짜 엔진(DuckDB, 테이블 파일의 Polars, SQLite)에 지어낸 작은 표를 올려 돌린다. 값은 1970년부터의 나노초 정수로 견준다(엔진마다 결과의 자릿수 표기가 달라서)."""
import datetime as dt
import json

import polars as pl
import pytest

from core import calc, prep_ops
from lazy import ibis
from prep import prep_expr

UTC = dt.timezone.utc
NANO = 10**9


def ns(*parts: int) -> int:
    return int(dt.datetime(*parts, tzinfo=UTC).timestamp()) * NANO


SECOND = ns(2024, 3, 17, 14, 25, 36)
FULL = SECOND + 123_456_789  # 2024-03-17 14:25:36.123456789
WANT = {"year": ns(2024, 1, 1), "month": ns(2024, 3, 1), "day": ns(2024, 3, 17), "hour": ns(2024, 3, 17, 14), "minute": ns(2024, 3, 17, 14, 25), "second": SECOND,
        "ms": SECOND + 123_000_000, "us": SECOND + 123_456_000, "ns": FULL}
UNITS = list(WANT)
FRAME = pl.DataFrame({"t": pl.Series([FULL, None], dtype=pl.Int64).cast(pl.Datetime("ns")), "s": ["2024-03-17 14:25:36", "엉뚱한 글자"], "d": ["2024-03-17", None]})
ENGINE_UNITS = {"duckdb": UNITS[:8], "polars": UNITS, "sqlite": UNITS[:6]}  # DuckDB는 us까지, Polars는 ns까지, 확인하지 않은 엔진은 초까지만


def table(engine: str):
    if engine == "polars":
        return ibis.polars.connect({"x": FRAME.lazy()}).table("x")
    con = ibis.duckdb.connect()
    con.con.register("x", FRAME.to_arrow())
    return con.table("x")


def typed(t, column: str, value: dict):
    """컬럼에 타입 바꾸기 단계를 건 결과 (Ibis 타입, 나노초 정수 목록)."""
    out = prep_ops.apply_ops(t, column, {"type": value})
    return out[column].type(), out.to_polars()[column].dt.cast_time_unit("ns").cast(pl.Int64).to_list()


@pytest.mark.parametrize("engine,unit", [(e, u) for e in ("duckdb", "polars") for u in ENGINE_UNITS[e]])
def test_unit_cuts_below_and_stays_timestamp(engine, unit):
    dtype, got = typed(table(engine), "t", {"to": "datetime", "unit": unit})
    assert dtype.is_timestamp() and got == [WANT[unit], None]  # 빈 값은 빈 값으로


def test_units_the_engine_cannot_hold_are_left_out():
    assert UNITS == [u[0] for u in prep_ops.CAST_UNITS]
    assert prep_ops.cast_units(table("duckdb")) == ENGINE_UNITS["duckdb"] and prep_ops.cast_units(table("polars")) == ENGINE_UNITS["polars"]
    lite = ibis.sqlite.connect()
    lite.raw_sql("create table x (t timestamp)")
    assert prep_ops.cast_units(lite.table("x")) == ENGINE_UNITS["sqlite"]
    with pytest.raises(ValueError, match="'ns' 단위까지 담지 못해요"):  # 다른 단위로 바꿔 넣지 않고 거절한다
        typed(table("duckdb"), "t", {"to": "datetime", "unit": "ns"})


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_without_unit_nothing_is_cut(engine):
    t = table(engine)
    dtype, got = typed(t, "t", {"to": "datetime"})
    assert dtype.is_timestamp() and got == [FULL, None]
    old_type, old = typed(t, "s", {"to": "date", "format": "%Y-%m-%d %H:%M:%S"})  # 단위가 없던 옛 단계(날짜 + 시각 형식)는 그대로
    assert old_type.is_timestamp() and old == [SECOND, None]
    assert prep_ops.apply_ops(t, "t", {"type": {"to": "date"}})["t"].type().is_date()


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_string_is_parsed_then_cut(engine):
    t = table(engine)
    dtype, got = typed(t, "s", {"to": "datetime", "format": "%Y-%m-%d %H:%M:%S", "unit": "month"})
    assert dtype.is_timestamp() and got == [WANT["month"], None]  # 못 읽는 글자는 지금처럼 빈 값
    assert typed(t, "s", {"to": "datetime", "format": "%Y-%m-%d %H:%M:%S", "unit": "minute"})[1] == [WANT["minute"], None]
    dtype, got = typed(t, "d", {"to": "datetime", "unit": "year"})  # 형식을 비우면 날짜 글자(%Y-%m-%d)
    assert dtype.is_timestamp() and got == [WANT["year"], None]
    assert typed(t, "d", {"to": "datetime"})[1] == [WANT["day"], None]


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_saved_step_runs_the_same_again(engine):
    t = table(engine)
    saved = json.dumps({"base": "x", "columns": {"t": {"ops": {"type": {"to": "datetime", "unit": "minute"}}}}})  # 저장된 구성(단위가 값에 남는다)
    run = lambda: prep_expr.shape(None, json.loads(saved), t)[0].to_polars()["t"].dt.cast_time_unit("ns").cast(pl.Int64).to_list()
    assert run() == run() == [WANT["minute"], None]
    line = '전처리 type t {"to": "datetime", "unit": "hour"}'  # 쿼리 화면의 계산 줄(단위가 줄 글에 드러난다)
    assert calc.apply([line], t).to_polars()["t"].dt.cast_time_unit("ns").cast(pl.Int64).to_list() == [WANT["hour"], None]


def test_step_value_check_and_screen_fields():
    valid = next(op for op in prep_ops.OPS if op[0] == "type")[2]
    assert valid({"to": "int"}) and valid({"to": "date", "format": "%Y%m%d"}) and valid({"to": "datetime"}) and all(valid({"to": "datetime", "unit": u}) for u in UNITS)
    assert not valid({"to": "date", "unit": "month"}) and not valid({"to": "datetime", "unit": "week"}) and not valid({"to": "datetime", "unit": ""})
    fields = {f["name"]: f for f in next(o for o in prep_ops.LABELS if o["id"] == "type")["fields"]}
    assert ["datetime", "날짜시간"] in fields["to"]["options"] and fields["unit"]["when"] == {"to": "datetime"}
    assert [o[0] for o in fields["unit"]["options"]] == ["", *UNITS]
    assert all(" → " in o[2] for o in fields["unit"]["options"][1:])  # 단위마다 예시 한 줄
    assert "2024-03-17 14:25:36.123 → 2024-03-01 00:00:00" in fields["unit"]["options"][2]
