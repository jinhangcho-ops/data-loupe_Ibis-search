"""3차 감사 묶음 3C(전처리·좌표) 지적별 시험. 값은 시험 안에서 지어내고 손으로 계산했다."""
import json

import numpy as np
import pyarrow as pa
import pytest

from lazy import ibis

from analysis import stats
from core import held
from geo import geojson, spatial
from prep import prep_app, prep_expr, prep_profile

NAN = float("nan")


def poly(*lons):
    return {"type": "Polygon", "coordinates": [[[x, 0] for x in lons] + [[lons[0], 0]]]}


# ---------- 3C1 fresh_copy: 쓸 수 있는 사본을 모두 버린다 ----------

def recipe_skipping(*cols):
    return {"base": "t", "columns": {c: {"keep": False} for c in cols}}


@pytest.fixture
def two_copies(monkeypatch):
    monkeypatch.setattr(prep_app, "table_key", lambda b: "T")
    monkeypatch.setattr(prep_app, "local_mode", lambda r: True)
    monkeypatch.setattr(held, "drop", lambda name: None)
    saved = dict(held.store["items"])
    held.store["items"].clear()
    for skipped in (["x"], []):  # 먼저 x를 뺀 사본 B, 다음에 아무것도 안 뺀 사본 A
        held.store["items"][prep_app.copy_key(recipe_skipping(*skipped))] = ("n" + str(len(skipped)), 5)
    yield
    held.store["items"].clear()
    held.store["items"].update(saved)


def test_cached_key_and_fresh_copy_with_two_copies(two_copies):
    r = recipe_skipping("x", "y")
    assert prep_app.cached_key(r)[3] == ("x",)  # 먼저 찾은 쓸 수 있는 사본
    prep_app.fresh_copy(r)
    assert not held.store["items"]  # A도 지워서 working_copy가 낡은 사본을 고르지 않는다
    assert prep_app.cached_key(r) == prep_app.copy_key(r) and prep_app.cached_key(r) not in held.store["items"]


def test_fresh_copy_keeps_copies_with_fewer_columns(two_copies):
    held.store["items"][prep_app.copy_key(recipe_skipping("x", "y", "z"))] = ("n3", 5)  # 이 recipe가 쓰는 컬럼(z)이 빠진 사본은 쓸 수 없으니 남긴다
    prep_app.fresh_copy(recipe_skipping("x", "y"))
    assert [k[3] for k in held.store["items"]] == [("x", "y", "z")]


# ---------- 3C2 computes_stats ----------

@pytest.mark.parametrize("ops,want", [({"rare": {"min": 5}}, True), ({"label": True}, True), ({"onehot": True}, True), ({"scale": "standard"}, True),
                                      ({"scale": "minmax"}, True), ({"scale": "log"}, False), ({"fill": {"how": "mean"}}, True), ({"fill": {"how": "value", "value": 1}}, False)])
def test_computes_stats(ops, want):
    assert prep_app.computes_stats({"columns": {"c": {"ops": ops}}}) is want


# ---------- 3C3 fill mean은 다른 컬럼의 drop 유무와 무관 ----------

def run(cols):
    t = ibis.memtable(pa.table({"a": [1.0, None, 3.0, 4.0], "b": [10.0, 1000.0, None, NAN]}))
    return prep_expr.shape(None, {"base": "x", "columns": cols}, t)[0].to_pyarrow().to_pylist()


def test_fill_mean_after_all_row_drops():
    # 채울 값(fill mean)은 모든 행 빼기가 끝난 뒤의 표로: a의 빈 행이 빠지면 b는 (10, 빈, 빈) → 평균 10.
    # b에 outlier drop이 있든 없든 같은 규칙이라 같은 값(경계는 시작 표 기준이라 아무 행도 안 빠짐).
    drop_a = {"a": {"ops": {"fill": {"how": "drop"}}}, "b": {"ops": {"fill": {"how": "mean"}}}}
    assert [r["b"] for r in run(drop_a)] == [10.0, 10.0, 10.0]
    assert [r["b"] for r in run({"b": {"ops": {"fill": {"how": "mean"}}}})][2:] == [505.0, 505.0]  # 뺀 행이 없으면 (10, 1000)의 505
    both = {**drop_a, "b": {"ops": {"fill": {"how": "mean"}, "outlier": {"how": "drop", "by": "std", "k": 1}}}}
    assert [r["b"] for r in run(both)] == [10.0, 10.0, 10.0]


# ---------- 3C4 GeoJSON 상한 ----------

def test_geojson_limit_is_a_sixth_of_bound(tmp_path, monkeypatch):
    from core import monitor
    p = tmp_path / "g.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]}), encoding="utf-8")
    size = p.stat().st_size
    monkeypatch.setattr(monitor, "bound_bytes", lambda config=None: size * 5)  # 6배 메모리가 필요한데 5배뿐
    with pytest.raises(ValueError, match="커서"):
        geojson.read_file(p)
    monkeypatch.setattr(monitor, "bound_bytes", lambda config=None: size * 6)
    assert len(geojson.read_file(p)) == 1


# ---------- 3C5 날짜 변경선 판단 ----------

def test_dateline_world_long_line_sparse_not_shifted():
    world = {"type": "Polygon", "coordinates": [[[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]]}
    assert geojson.bounds(world)[::2] == (-180, 180) and geojson.center(world)[0] == 0
    line = {"type": "LineString", "coordinates": [[-100, 0], [100, 0]]}
    assert geojson.bounds(line)[::2] == (-100, 100) and geojson.center(line)[0] == 0
    sparse = poly(-180, -90, 0, 90, 180)
    assert geojson.bounds(sparse)[::2] == (-180, 180)


def test_dateline_fiji_is_shifted():
    fiji = poly(178, -179, -179, 178)
    assert geojson.crosses_dateline([178, -179]) and geojson.bounds(fiji)[::2] == (178, 181)
    assert abs(abs(geojson.center(fiji)[0]) - 180) < 2


# ---------- 3C7 표에는 -180~180 안의 값 ----------

def test_table_bounds_follow_in_box_rule():
    doc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": poly(178, -179, -179, 178)}]}
    row = geojson.table(doc).to_dicts()[0]
    assert (row["min_lon"], row["max_lon"]) == (178, -179)  # min_lon > max_lon = 변경선을 넘는 상자
    assert -180 <= row["center_lon"] <= 180


# ---------- 3C8 지도 타일 x 감싸기 ----------

def test_geomap_tile_x_wraps():
    src = open("static/geo/geomap.js", encoding="utf-8").read()
    assert "% r.n) + r.n) % r.n" in src and "clamp(Math.floor(frame.x0" not in src


# ---------- 3C9 프로필 inf ----------

def test_profile_treats_inf_as_null():
    t = ibis.memtable(pa.table({"v": [1.0, 5.0, float("inf"), -float("inf"), NAN, None]}))
    info = prep_profile.profile(t)["columns"][0]
    assert (info["min"], info["max"], info["nulls"], info["unique"]) == (1.0, 5.0, 4, 2)


# ---------- 3C6 그 밖: y_chunks NaN, distribution 묶음 집계 ----------

def test_y_chunks_nan_points_get_edges():
    ring = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]])
    py = np.array([5.0, NAN, 5.0, NAN])
    got = {int(i): list(e) for at, e in spatial.y_chunks(py, ring) for i in at}
    assert got[0] and got[2]  # NaN이 묶음 끝이어도 y가 있는 점의 변 후보가 남는다


def test_distribution_summary_in_one_aggregate():
    t = ibis.memtable(pa.table({"v": [1.0, 2.0, 3.0, 4.0, float("inf"), NAN, None]}))
    out = stats.distribution(t, "v")
    s = out["stats"]
    assert (s["n"], s["min"], s["max"], s["mean"]) == (4, 1.0, 4.0, 2.5)
    assert sum(out["histogram"]["counts"]) == 4
