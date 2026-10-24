"""7차 감사 묶음 7C(전처리·분석·지도) 시험. 값은 시험 안에서 지어냈고, 진짜 DuckDB 엔진으로 돈다."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from lazy import ibis

from analysis import stats
from geo import geojson, prep_geo, spatial
from prep import prep_app, prep_expr, prep_profile

ROOT = Path(__file__).resolve().parent.parent
NAN, INF = float("nan"), float("inf")


def run(data, recipe):
    con = ibis.duckdb.connect()
    expr, _ = prep_expr.build(con, {"base": "x", **recipe}, ibis.memtable(pl.DataFrame(data)))
    return expr.to_pyarrow().to_pylist()


# 7C1: 좌표 단계가 행 순서를 바꾸지 않는다
def test_geo_steps_keep_row_order():
    df = pl.DataFrame({"i": range(5), "lat": [37.5, None, NAN, 37.5, 91.0], "lon": [127.0, 127.0, 127.0, None, 127.0]})
    out, _ = prep_geo.apply(ibis.memtable(df), [{"op": "tm", "x": "lon", "y": "lat"}], [], None)
    assert out.i.to_pyarrow().to_pylist() == [0, 1, 2, 3, 4]
    con = ibis.duckdb.connect()
    con.create_table("refs", obj=ibis.memtable(pl.DataFrame({"rlat": [37.5], "rlon": [127.0]})))
    out, _ = prep_geo.apply(ibis.memtable(df), [{"op": "nearest", "lat": "lat", "lon": "lon", "table": "refs", "ref_lat": "rlat", "ref_lon": "rlon"}], [], con)
    assert out.i.to_pyarrow().to_pylist() == [0, 1, 2, 3, 4]


def test_geo_step_keeps_order_on_many_rows():
    n = 60000
    rng = np.random.default_rng(3)
    df = pl.DataFrame({"i": np.arange(n), "lat": rng.uniform(33, 39, n).round(3), "lon": rng.uniform(125, 130, n).round(3)})
    out, _ = prep_geo.apply(ibis.memtable(df), [{"op": "tm", "x": "lon", "y": "lat", "epsg": 5179}], [], None)
    assert out.i.to_pyarrow().to_pylist() == list(range(n))


# 7C2: 점-영역이 옛 방식(영역마다 남은 점 전부를 견줌)과 같은 번호를 낸다
def naive_assign(lat, lon, regions):
    out = np.full(len(lat), -1, dtype="int64")
    for number, polygons in enumerate(regions):
        for polygon in polygons:
            todo = np.flatnonzero((out < 0) & np.isfinite(lat) & np.isfinite(lon))
            if not len(todo) or not polygon or len(spatial.closed(polygon[0])) < 4:
                continue
            outer = spatial.closed(polygon[0])
            x = lon[todo]
            if outer[:, 0].max() > 180:
                x = np.where(x < 0, x + 360, x)
            elif outer[:, 0].min() < -180:
                x = np.where(x > 0, x - 360, x)
            near = (x >= outer[:, 0].min()) & (x <= outer[:, 0].max()) & (lat[todo] >= outer[:, 1].min()) & (lat[todo] <= outer[:, 1].max())
            pick = todo[near]
            if len(pick):
                out[pick[spatial.in_polygon(x[near], lat[pick], polygon)]] = number
    return out


def square(x, y, w):
    return [spatial.closed([[x, y], [x + w, y], [x + w, y + w], [x, y + w], [x, y]])]


def test_assign_same_as_naive():
    rng = np.random.default_rng(7)
    regions = [[square(rng.uniform(0, 9), rng.uniform(0, 9), 1.5)] for _ in range(40)]
    regions.append([square(175, 0, 10)])  # 변경선을 넘는 영역(경도 175~185)
    regions.append([[spatial.closed([[0, 0], [10, 0], [10, 10], [0, 10]]), spatial.closed([[4, 4], [6, 4], [6, 6], [4, 6]])]])  # 구멍
    lon = np.concatenate([rng.uniform(-1, 11, 3000), [178.0, -178.0, 1.0, 2.0, 5.0, 0.0, NAN, 3.0], rng.uniform(170, 180, 25), rng.uniform(-180, -170, 25)])
    lat = np.concatenate([rng.uniform(-1, 11, 3000), [5.0, 5.0, 1.0, 2.0, 5.0, 0.0, 3.0, NAN], rng.uniform(0, 10, 50)])
    assert (spatial.assign(lat, lon, regions) == naive_assign(lat, lon, regions)).all()
    assert (spatial.assign(lat, lon, regions) >= 0).any()


# 7C3: 실수 값 하나의 NaN·inf가 그룹 값을 비우지 않는다
def test_groups_and_trend_ignore_nan_inf():
    t = ibis.memtable(pl.DataFrame({"g": ["x", "x", "y", "y"], "d": [dt.date(2026, 1, 1)] * 2 + [dt.date(2026, 1, 2)] * 2, "v": [1.0, NAN, 5.0, INF]}))
    g = stats.analyze(t, {"kind": "groups", "by": "g", "value": "v", "agg": "sum"})
    assert dict(zip(g["labels"], g["values"])) == {"x": 1.0, "y": 5.0}
    g = stats.analyze(t, {"kind": "groups", "by": "g", "value": "v", "agg": "mean"})
    assert dict(zip(g["labels"], g["values"])) == {"x": 1.0, "y": 5.0}
    tr = stats.analyze(t, {"kind": "trend", "column": "d", "unit": "day", "value": "v", "agg": "sum"})
    assert tr["values"] == [1.0, 5.0]


# 7C4: fill prev의 by 컬럼이 타입 변환을 받아도 컬럼 순서와 무관
def test_fill_prev_by_converted_column_any_order():
    k = ["01/02/2026", "15/01/2026", "20/01/2026", "05/03/2026"]
    v = [None, 10.0, None, 40.0]
    specs = {"k": {"ops": {"type": {"to": "date", "format": "%d/%m/%Y"}}}, "v": {"ops": {"fill": {"how": "prev", "by": "k"}}}}
    got = []
    for order in (("k", "v"), ("v", "k")):
        rows = run({c: {"k": k, "v": v}[c] for c in order}, {"columns": specs})
        got.append(sorted((str(r["k"]), r["v"]) for r in rows))
    assert got[0] == got[1] == [("2026-01-15", 10.0), ("2026-01-20", 10.0), ("2026-02-01", 10.0), ("2026-03-05", 40.0)]


# 7C5
def test_downcast_counts_as_stats():
    assert prep_app.computes_stats({"downcast": True}) is True
    assert prep_app.computes_stats({}) is False


# 7C6: crosses_dateline이 옛 numpy 식과 같은 답
def crosses_numpy(xs):
    v = np.fromiter(xs, dtype=float)
    v = v[np.isfinite(v)]
    v = np.unique(np.where(v == -180, 180.0, v))
    return len(v) >= 2 and bool(np.diff(v).max() > 360 - geojson.DATELINE_SPAN)


def test_crosses_dateline_same_as_numpy():
    rng = np.random.default_rng(5)
    cases = [[], [1], [-180, -170], [-180, 100], [170, -170], [-100, 100], [0, 360], [-180, 180], [NAN, 10, 20], [INF, 1, 2], [-190, -180, 80], [-180, 90], [-180, 91], [100, -170, NAN]]
    cases += [list(rng.choice([-180, -179.5, -170, -90, 0, 10, 90, 170, 179, 180, 190, 350], size=int(rng.integers(1, 5)))) for _ in range(500)]
    cases += [list(rng.uniform(-180, 180, size=int(rng.integers(1, 6)))) for _ in range(500)]
    for xs in cases:
        assert geojson.crosses_dateline(iter(xs)) == crosses_numpy(xs), xs


# 7C7: 이미 닫힌 (N, 2) 배열은 그대로
def test_closed_returns_closed_array_as_is():
    ring = spatial.closed([[0, 0], [1, 0], [1, 1]])
    assert ring.shape == (4, 2)
    assert spatial.closed(ring) is ring
    assert np.array_equal(spatial.closed([[0, 0], [1, 0], [1, 1], [0, 0]]), ring)
    open_ring = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    assert spatial.closed(open_ring).shape == (4, 2)


# 7C8: 깨진 좌표는 안내 ValueError
@pytest.mark.parametrize("coords", ["abc", [5], [["a", "b"]]])
def test_read_file_bad_coordinates_gives_value_error(tmp_path, coords):
    path = tmp_path / "bad.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": coords}}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="좌표"):
        geojson.read_file(path)


# 7C9: ±1.7e308 값이 있어도 분포가 나오고 표준편차만 빈 값
def test_distribution_with_huge_values():
    t = ibis.memtable(pl.DataFrame({"v": [-1.7e308, 1.7e308, 0.0]}))
    out = stats.analyze(t, {"kind": "distribution", "column": "v"})
    assert out["stats"]["std"] is None and out["stats"]["n"] == 3
    assert out["histogram"]["counts"][0] == 1 and out["histogram"]["counts"][-1] == 1 and sum(out["histogram"]["counts"]) == 3
    assert all(e == e and abs(e) != INF for e in out["histogram"]["edges"])
    normal = stats.analyze(ibis.memtable(pl.DataFrame({"v": [1.0, 2.0, 3.0]})), {"kind": "distribution", "column": "v"})
    assert normal["stats"]["std"] == 1.0


# 7C10: |위도| > 90은 거리를 계산하지 않고 빈 값
def test_nearest_ignores_latitude_beyond_90():
    index, dist = spatial.nearest([37.5, 91.0, -95.0], [127.0, 127.0, 127.0], [37.5, 120.0], [127.0, 127.0])
    assert index.tolist() == [0, -1, -1] and np.isnan(dist[1:]).all() and dist[0] == 0
    index, _ = spatial.nearest([37.5], [127.0], [95.0, 36.0], [127.0, 127.0])
    assert index.tolist() == [1]


# 7C11: 행이 없는 표는 "컬럼 빼기"를 추천하지 않는다
def test_empty_table_profile_has_no_drop_suggestion():
    t = ibis.memtable(pl.DataFrame({"a": pl.Series([], dtype=pl.Float64), "s": pl.Series([], dtype=pl.String)}))
    assert all(c["suggest"] == [] for c in prep_profile.profile(t)["columns"])
    one = prep_profile.profile(ibis.memtable(pl.DataFrame({"a": [1, 1, 1]})))["columns"][0]
    assert any(s["text"] == "컬럼 빼기" for s in one["suggest"])
    blank = prep_profile.profile(ibis.memtable(pl.DataFrame({"a": [1, 2], "n": pl.Series([None, None], dtype=pl.String)})))["columns"][1]
    assert any(s["text"] == "컬럼 빼기" for s in blank["suggest"])  # 값이 모두 빈 컬럼은 뺀다(tests.py 원래 설계 "alln")


# 7C12
def test_geo_doc_mentions_0_360_longitude():
    assert "0~360으로 적힌 경도 파일은 그대로 둔다" in (ROOT / "docs" / "geo.md").read_text(encoding="utf-8")
