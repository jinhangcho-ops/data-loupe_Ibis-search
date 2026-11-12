"""(감사51) 계산 함수를 무작위로 돌려 찾은 엔진 차이·오류: DuckDB 파일 연결과 Parquet·CSV 연결(Polars)이 같은 답을 낸다. 값은 시험 안에서 지어낸다."""
import math

import pyarrow as pa
import pytest
from lazy import ibis

from core import prep_derive, prep_ops
from core import sql_funcs as sf

BIG = 2**40
INF, NAN = float("inf"), float("nan")
DATA = pa.table({"s": pa.array(["가나", "ab", None], pa.string()), "n": pa.array([BIG, 3, None], pa.int64()), "f": pa.array([1.5, NAN, 3.0], pa.float64()),
                 "g": pa.array([1.0, INF, 2.0], pa.float64()), "by": pa.array([None, None, None], pa.int64())})


@pytest.fixture(params=["duckdb", "polars"])
def t(request):
    if request.param == "duckdb":  # 등록한 Arrow가 아니라 진짜 테이블(파일 연결과 같게)
        con = ibis.duckdb.connect()
        con.con.register("a", DATA)
        con.con.execute("create table t as select * from a")
        return con.table("t")
    con = ibis.polars.connect()
    con.create_table("t", DATA)
    return con.table("t")


def rows(t, fid, raw):
    return t.select(v=sf.make(fid, t, raw)).to_pyarrow().column("v").to_pylist()


def agg(t, fid, raw):
    return t.aggregate(v=sf.make(fid, t, raw)).to_pyarrow().column("v").to_pylist()[0]


def test_length_counts_letters_not_bytes(t):
    assert rows(t, "length", {"x": "s"}) == [2, 2, None]
    out = prep_derive.apply_formulas(t, [{"name": "v", "expr": "length(s)"}])[0].to_pyarrow()  # 전처리 계산 컬럼도
    assert out.column("v").to_pylist() == [2, 2, None]


def test_power_of_big_int_does_not_wrap(t):
    assert rows(t, "power", {"x": "n", "n": 2}) == [float(BIG) ** 2, 9.0, None]


def test_find_of_empty_value_is_empty(t):
    assert rows(t, "find", {"x": "s", "find": "b"}) == [0, 2, None]


def test_nan_and_inf_are_left_out(t):
    assert agg(t, "max", {"x": "f"}) == 3.0
    assert agg(t, "range", {"x": "f"}) == 1.5
    assert rows(t, "ceil", {"x": "f"}) == [2, None, 3]
    assert math.isclose(agg(t, "std_pop", {"x": "g"}), 0.5)
    assert agg(t, "median", {"x": "g"}) == 1.5


def test_arg_min_without_any_key_is_empty(t):
    assert agg(t, "arg_min", {"x": "s", "by": "by"}) is None
    assert agg(t, "arg_max", {"x": "s", "by": "by"}) is None
    assert agg(t, "arg_max", {"x": "s", "by": "f"}) is None  # NaN은 가장 큰 값이 아니다: 3.0인 행의 값(빈 값)


def test_label_of_all_empty_column(t):
    out = prep_ops.apply_ops(t, "by", {"label": True}).to_pyarrow()
    assert out.column("by_label").to_pylist() == [None, None, None]
