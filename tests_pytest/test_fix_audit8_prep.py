"""8차 감사 묶음 8C(전처리·분석·지도) 시험. 값은 시험 안에서 지어냈고, 진짜 DuckDB·Polars 엔진으로 돈다."""
import warnings

import numpy as np
import polars as pl
import pytest
from lazy import ibis

from analysis import stats
from core import source
from geo import geojson, prep_geo
from prep import prep_expr

NAN, INF = float("nan"), float("inf")


def run(data, recipe):
    con = ibis.duckdb.connect()
    expr, _ = prep_expr.build(con, {"base": "x", **recipe}, ibis.memtable(pl.DataFrame(data)))
    return expr.to_pyarrow().to_pylist()


# 8C1: Polars 파일 연결에서도 좌표 단계가 되고 행 순서가 그대로
def test_geo_steps_on_polars_file_connection(tmp_path):
    n = 3000
    rng = np.random.default_rng(1)
    pl.DataFrame({"i": np.arange(n), "lat": rng.uniform(33, 39, n).round(2), "lon": rng.uniform(125, 130, n).round(2)}).write_csv(tmp_path / "pts.csv")
    pl.DataFrame({"rlat": [35.0, 37.0], "rlon": [127.0, 128.0], "name": ["a", "b"]}).write_csv(tmp_path / "refs.csv")
    con = source.open_flat_files([tmp_path / "pts.csv", tmp_path / "refs.csv"])
    assert con.name == "polars"
    t = con.table("pts")
    for step in ({"op": "tm", "x": "lon", "y": "lat", "epsg": 5179},
                 {"op": "nearest", "lat": "lat", "lon": "lon", "table": "refs", "ref_lat": "rlat", "ref_lon": "rlon", "label": "name"}):
        out, _ = prep_geo.apply(t, [step], [], con)
        got = out.to_polars()
        assert got["i"].to_list() == list(range(n))
        assert got.height == n and len(got.columns) > len(t.columns)


def test_attach_keeps_order_on_both_backends():
    n = 2000
    df = pl.DataFrame({"i": np.arange(n), "k": (np.arange(n) % 7).astype("float64")})
    frame = pl.DataFrame({"__k0": [float(v) for v in range(7)], "v": list(range(7))})
    for con in (ibis.duckdb.connect(), ibis.polars.connect()):
        con.create_table("t", obj=ibis.memtable(df) if con.name == "duckdb" else df)
        got = prep_geo.attach(con.table("t"), ["k"], frame, ["v"]).to_polars()
        assert got["i"].to_list() == list(range(n)) and got["v"].to_list() == [i % 7 for i in range(n)]


# 8C2: fill prev의 by 컬럼 타입 변환은 행을 빼는 일이 같이 있어도 같은 결과
def test_fill_prev_by_converted_column_with_row_drop():
    data = {"a": [1.0, None, 5.0], "b": ["9", "10", "11"]}
    plain = {"a": {"ops": {"fill": {"how": "prev", "by": "b"}}}, "b": {"ops": {"type": {"to": "int"}}}}
    withdrop = {"a": {"ops": {"fill": {"how": "prev", "by": "b"}, "outlier": {"how": "drop", "by": "range", "low": -100, "high": 100}}}, "b": plain["b"]}
    got = [sorted((r["b"], r["a"]) for r in run(data, {"columns": spec})) for spec in (plain, withdrop)]
    assert got[0] == got[1] == [(9, 1.0), (10, 1.0), (11, 5.0)]


# 8C3: 넘침이 아닌 오류는 다시 하지 않고, 넘침에서도 값 빈도 조회는 한 번
def test_distribution_overflow_top_queried_once(monkeypatch):
    t = ibis.memtable(pl.DataFrame({"v": [1.7e308, -1.7e308, 1.0, 2.0]}))
    calls = []
    real = stats.rows
    monkeypatch.setattr(stats, "rows", lambda e: (calls.append(1), real(e))[1])
    out = stats.distribution(t, "v")
    assert out["stats"]["std"] is None and len(calls) == 5  # top 1 + 집계 2(넘침 한 번 실패 뒤 std 없이) + 히스토그램 1 + 상자 1. 전에는 6


def test_distribution_other_error_is_not_retried(monkeypatch):
    t = ibis.memtable(pl.DataFrame({"v": [1.0, 2.0, 3.0]}))
    calls = []
    real = stats.one

    def boom(e):
        calls.append(1)
        raise ValueError("연결이 끊겼어요")
    monkeypatch.setattr(stats, "one", boom)
    with pytest.raises(ValueError, match="연결"):
        stats.distribution(t, "v")
    assert len(calls) == 1
    monkeypatch.setattr(stats, "one", real)


# 8C4: 같은 컬럼으로 묶어도 NaN·inf가 (빈 값)에 합쳐지지 않는다
def test_groups_by_value_column_keeps_nan_inf_groups():
    t = ibis.memtable(pl.DataFrame({"v": [1.0, NAN, INF]}))
    g = stats.analyze(t, {"kind": "groups", "by": "v", "value": "v", "agg": "count"})
    # 10차: NaN·무한은 화면에 둘 다 "(빈 값)"으로 보여 그룹 둘이 같은 이름이 되므로 빈 값 그룹 하나로(앱 전체에서 무한은 빈 값, docs/functions/prep.md)
    assert sorted(zip(g["labels"], g["counts"])) == [("(빈 값)", 2), ("1.0", 1)]


# 8C5
def test_crosses_dateline_numpy_inf_no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert geojson.crosses_dateline([np.float64(178), np.float64("inf"), np.float64(-179), np.float64("nan")]) is True
    for bad in (["a", 1], [None, 1]):
        with pytest.raises(TypeError):
            geojson.crosses_dateline(bad)
