"""묶음 C(전처리·분석·좌표) 감사 지적별 시험. 값은 시험 안에서 지어내고 손으로 계산했다."""
import json
import sqlite3
import time

import numpy as np
import pyarrow as pa
import pytest

from lazy import ibis

from core import prep_ops
from geo import spatial
from prep import prep_expr

NAN = float("nan")


@pytest.fixture(params=["duckdb", "polars"])
def make(request):
    """data(dict) → 표. DuckDB(메모리 표)와 Polars 연결에서 같은 값이 나오는지 보려고 둘 다로 돈다."""
    def table(data):
        arrow = pa.table(data)
        return ibis.memtable(arrow) if request.param == "duckdb" else ibis.polars.connect().create_table("t", arrow)
    return table


def build(t, columns, **more):
    expr, _ = prep_expr.build(None, {"base": "t", "columns": columns, **more}, t)
    return expr.to_pyarrow().to_pydict()


# ---------- 1. 표본 분석의 지점·영역 테이블 확인, 원격 limit ----------

@pytest.fixture
def remote(folder, tmp_path):
    from core import server
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table pts (id integer, lat real, lon real, extra text, unused text)")
    raw.executemany("insert into pts values (?, ?, ?, ?, ?)", [(i, 37.0 + i / 100, 127.0, "x", "y") for i in range(5)])
    raw.execute("create table st (name text, slat real, slon real)")
    raw.executemany("insert into st values (?, ?, ?)", [(f"s{i}", 37.0 + i, 127.0) for i in range(5)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    yield c
    from core import held
    held.store["items"].clear()
    c.post("/api/disconnect", json={})


GEO = [{"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "slat", "ref_lon": "slon", "label": "name"}]


def test_sample_analysis_asks_before_reading_reference_table(remote, monkeypatch):
    from core import held
    from core import server
    asked = []

    def gate(body, make_sql, rows=None):
        asked.append(make_sql()[0])
        return server.jsonify(needs_confirm=True, text="큼")

    monkeypatch.setattr(server, "scan_gate", gate)
    got = remote.post("/api/analyze", json={"recipe": {"base": "pts", "geo": GEO}, "how": {"kind": "columns"}, "full": False}).get_json()
    assert got.get("needs_confirm") is True and any("slat" in q for q in asked)
    assert not held.store["items"]  # 확인 전에는 받지 않았다


def test_remote_reference_is_fetched_with_limit_and_refused_when_over(remote, monkeypatch):
    from core import held
    from core import server
    from geo import prep_geo
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: None)
    monkeypatch.setattr(prep_geo, "MAX_REFERENCE", 3)
    seen = []
    real = held.ensure
    monkeypatch.setattr(held, "ensure", lambda key, expr, *a, **k: (seen.append(str(ibis.to_sql(expr)).upper()), real(key, expr, *a, **k))[1])
    r = remote.post("/api/preview", json={"base": "pts", "geo": GEO})
    assert r.status_code == 400 and "행을 넘어" in r.get_json()["error"]
    assert any("LIMIT 4" in q and "SLAT" in q for q in seen)  # 한도+1행까지만 받는다
    assert not [k for k in held.store["items"] if k[0] == "geo"]  # 넘으면 받은 것을 버린다


# ---------- 2. gridded 메모리 ----------

def test_gridded_keeps_matrix_small_for_many_points_in_one_cell(monkeypatch):
    monkeypatch.setattr(spatial, "BLOCK", 5_000)
    rng = np.random.default_rng(1)
    lat, lon = 37.0 + rng.random(5000) * 0.05, 127.0 + rng.random(5000) * 0.05  # 한 칸(0.1도) 안
    rlat, rlon = 36.0 + rng.random(400) * 2, 126.0 + rng.random(400) * 2
    sizes = []
    real = spatial.haversine
    monkeypatch.setattr(spatial, "haversine", lambda a, b, c, d: (sizes.append(np.broadcast(a, b, c, d).size), real(a, b, c, d))[1])
    index, dist = spatial.gridded(lat, lon, rlat, rlon)
    assert max(sizes) <= 5_000 + len(rlat)
    want, km = spatial.brute(lat, lon, rlat, rlon)
    assert (index == want).all() and np.allclose(dist, km)


# ---------- 3. 컬럼 순서 ----------

def test_row_dropping_runs_before_statistics_regardless_of_column_order(make):
    data = {"a": [1.0, 2.0, None, 3.0], "b": [10.0, 20.0, 30.0, None]}
    fill_a = {"ops": {"fill": {"how": "mean"}}}
    drop_b = {"ops": {"fill": {"how": "drop"}}}
    first = build(make(data), {"a": fill_a, "b": drop_b})
    second = build(make({"b": data["b"], "a": data["a"]}), {"a": fill_a, "b": drop_b})
    # 원래 설계(앞 컬럼에서 뺀 행을 빼고 평균): b가 빈 행(마지막)을 먼저 빼면 a의 평균은 (1+2)/2 = 1.5
    assert first["a"] == [1.0, 2.0, 1.5] and second["a"] == [1.0, 2.0, 1.5]


# ---------- 4. NaN ----------

def test_real_nan_counts_as_empty_for_fill_flag_outlier_scale(make):
    t = make({"a": [1.0, NAN, 3.0, None]})
    assert build(t, {"a": {"ops": {"fill": {"how": "mean"}}}})["a"] == [1.0, 2.0, 3.0, 2.0]
    got = build(make({"a": [1.0, NAN, 3.0, None]}), {"a": {"ops": {"flag": True}}})
    assert got["a_isnull"] == [False, True, False, True]
    kept = build(make({"a": [1.0, NAN, 3.0, 2.0]}), {"a": {"ops": {"outlier": {"how": "clip", "by": "std", "k": 1}}}})["a"]
    assert not any(v != v for v in kept[:1] + kept[2:]) and kept[1] is None
    scaled = build(make({"a": [1.0, NAN, 3.0]}), {"a": {"ops": {"scale": "minmax"}}})["a"]
    assert scaled == [0.0, None, 1.0]
    assert build(make({"a": [1.0, NAN, 3.0]}), {"a": {"ops": {"fill": {"how": "drop"}}}})["a"] == [1.0, 3.0]


def test_pca_treats_nan_as_empty():
    from core import prep_derive
    t = ibis.memtable(pa.table({"x": [1.0, 2.0, 3.0, 4.0, NAN], "y": [2.0, 4.1, 5.9, 8.2, 1.0]}))
    info = {}
    out, names = prep_derive.pca(t, {"columns": ["x", "y"], "n": 1}, info)
    assert info["pca"]["rows"] == 4
    assert out.to_pyarrow().to_pydict()[names[0]][4] is None


# ---------- 5. 두 백엔드에서 같은 값 ----------

def test_float_to_int_truncates_toward_zero(make):
    got = build(make({"f": [1.9, -1.9, 2.5, -0.5, 0.0, None]}), {"f": {"ops": {"type": {"to": "int"}}}})["f"]
    assert got == [1, -1, 2, 0, 0, None]


def test_round_is_half_away_from_zero(make):
    got = build(make({"f": [0.5, 1.5, 2.5, -0.5, -2.5, 2.4, None]}), {"f": {"ops": {"round": 0}}})["f"]
    assert got == [1.0, 2.0, 3.0, -1.0, -3.0, 2.0, None]
    assert build(make({"f": [1.25, -1.25]}), {"f": {"ops": {"round": 1}}})["f"] == [1.3, -1.3]


def test_trim_removes_nbsp_and_fullwidth_space(make):
    got = build(make({"s": [" x　", "  y ", "a b", None]}), {"s": {"ops": {"trim": True}}})["s"]
    assert got == ["x", "y", "a b", None]


def test_regex_backreference_is_same_on_both_backends(make):
    got = build(make({"s": ["ab12", "x", None]}), {"s": {"ops": {"regex": ["([a-z]+)([0-9]+)", "\\2-\\1"]}}})["s"]
    assert got == ["12-ab", "x", None]


# ---------- 6. downcast ----------

def test_downcast_skips_float32_when_value_is_out_of_range():
    t = ibis.memtable(pa.table({"big": [1e300, 1.0], "ok": [0.5, 1.5], "i": [1, 2]}))
    expr, _ = prep_expr.build(None, {"base": "t", "downcast": True}, t)
    schema = expr.schema()
    assert expr.to_pyarrow().num_rows == 2
    assert schema["big"].is_float64() and schema["ok"].is_float32() and schema["i"].is_int8()


# ---------- 7. dedupe last: 빈 값은 늘 뒤 ----------

def test_dedupe_last_never_picks_empty_order_value():  # 창 함수가 필요해 DuckDB만(Polars 백엔드는 창 함수가 없다)
    make = lambda data: ibis.memtable(pa.table(data))
    data = {"k": [1, 1, 1, 2], "d": [3, None, 1, None], "v": ["a", "b", "c", "d"]}
    got = build(make(data), {}, dedupe=["k"], dedupe_keep={"how": "last", "order": ["d"]}, sorts=["k"])
    assert got["v"] == ["a", "d"]  # k=1은 d가 가장 큰 3의 행, k=2는 d가 전부 비어 그 행
    first = build(make(data), {}, dedupe=["k"], dedupe_keep={"how": "first", "order": ["d"]}, sorts=["k"])
    assert first["v"] == ["c", "d"]


# ---------- 8. 작은 것들 ----------

def test_label_after_bins_follows_interval_start(make):
    got = build(make({"x": [7.0, 15.0, 120.0]}), {"x": {"ops": {"bins": [0, 10, 20, 200], "label": True}}})
    assert got["x_label"] == [0, 1, 2]  # "0–10" < "10–20" < "20–200"(글자순이면 "20–200"이 "10–20" 앞이 아니라도 "100"류에서 틀어진다)
    got = build(make({"x": [5.0, 50.0, 500.0]}), {"x": {"ops": {"bins": [5, 10, 100, 1000], "label": True}}})
    assert got["x_label"] == [0, 1, 2]  # 글자순이면 "10–100" < "100–1000" < "5–10"


def test_clean_keeps_exponent_numbers_and_empties_overflow(make):
    got = build(make({"s": ["2.5e3", "$1,200", "1e400"]}), {"s": {"ops": {"type": {"to": "float", "clean": True}}}})["s"]
    assert got[0] == 2500.0 and got[1] == 1200.0 and got[2] is None


def test_cumulative_sum_gives_same_value_to_same_order_key():
    from core import calc
    t = ibis.memtable(pa.table({"k": [1, 1, 2], "v": [1.0, 2.0, 4.0]}))
    out = calc.apply(["누적합 v 기준 k"], t).to_pyarrow().to_pydict()
    assert out["v_누적합"] == [3.0, 3.0, 7.0]


def test_one_new_column_name_rule():
    from core import prep_derive
    from geo import prep_geo
    assert not hasattr(prep_geo, "NAME") and prep_derive.NAME.fullmatch("가_1")


def test_map_card_cuts_points_to_limit(monkeypatch):
    import polars as pl
    from geo import mapcard
    monkeypatch.setattr(mapcard, "MAX_POINT_ROWS", 3)
    pts = pl.DataFrame({"lat": [1.0, 2.0, 3.0, 4.0, 5.0], "lon": [1.0] * 5})
    got = mapcard.build(None, pts, {"agg": "count"})
    assert got["values"]["total_points"] == 3 and "앞 3행" in got["note"]


def test_geojson_mixed_int_and_float_stays_numeric():
    import polars as pl
    from geo import geojson
    s = geojson.column_values("n", [1, 2.5, 3, None])
    assert s.dtype == pl.Float64 and s.to_list() == [1.0, 2.5, 3.0, None]
    assert geojson.column_values("m", [1, "a"]).dtype == pl.String


def test_in_ring_matches_slow_reference_and_is_fast():
    angle = np.linspace(0, 2 * np.pi, 3001)[:-1]
    ring = spatial.closed(np.c_[np.cos(angle), np.sin(angle)])
    rng = np.random.default_rng(2)
    px, py = rng.uniform(-1.2, 1.2, 20000), rng.uniform(-1.2, 1.2, 20000)
    px[:3], py[:3] = [1.0, 0.0, 0.5], [0.0, 1.0, 0.0]  # 꼭짓점 위(경계)와 안쪽
    start = time.time()
    got = spatial.in_ring(px, py, ring)
    assert time.time() - start < 5
    near = np.hypot(px, py)
    assert got[:2].all() and got[2] and (got[near < 0.99]).all() and not got[near > 1.01].any()


def test_polygon_across_dateline_catches_both_sides():
    ring = [[170, -10], [190, -10], [190, 10], [170, 10], [170, -10]]
    got = spatial.assign([0, 0, 0, 0], [175, -175, 160, -160], [[[ring]]])
    assert got.tolist() == [0, 0, -1, -1]


def test_geojson_file_is_parsed_once_per_version(tmp_path, monkeypatch):
    from geo import geojson
    path = tmp_path / "a.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"n": 1}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]}), encoding="utf-8")
    calls = []
    real = geojson.table
    monkeypatch.setattr(geojson, "table", lambda doc: (calls.append(1), real(doc))[1])
    geojson.read(path)
    geojson.read(path)
    assert len(calls) == 1
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")  # 크기가 바뀌면 다시 읽는다
    geojson.read(path)
    assert len(calls) == 2


def test_prune_correlation_matches_pairwise_and_is_vectorized():
    from prep import prep_profile
    rng = np.random.default_rng(3)
    base = rng.random(500)
    data = {"a": base, "b": base * 2 + 1e-6 * rng.random(500), "c": rng.random(500), "d": np.where(np.arange(500) % 7 == 0, np.nan, base + 1e-6 * rng.random(500))}
    found = prep_profile.prune(pa.table({k: pa.array(v, from_pandas=True) if k == "d" else v for k, v in data.items()}))
    pairs = {(f["column"], f["with"]) for f in found if "with" in f}
    assert ("b", "a") in pairs or ("a", "b") in pairs
    assert not any(f["column"] == "c" for f in found)
    wide = pa.table({f"c{i}": rng.random(2000) for i in range(120)})
    start = time.time()
    prep_profile.prune(wide)
    assert time.time() - start < 5


# ---------- 작업 사본 컬럼, COUNT, prep_where=new 확인 ----------

def test_working_copy_skips_unused_columns(remote, monkeypatch):
    from core import held
    from core import server
    from prep import prep_app
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: None)
    recipe = {"base": "pts", "columns": {"unused": {"keep": False}, "extra": {"keep": False, "ops": {"flag": True}}}}
    got = remote.post("/api/preview", json=recipe).get_json()
    copy = [k for k in held.store["items"] if k[0] == "prep"]
    assert len(copy) == 1 and copy[0][3] == ("unused",)
    assert "unused" not in got["rows"]["columns"] and "extra_isnull" in got["rows"]["columns"]
    assert prep_app.unused_columns({**recipe, "sorts": ["unused"]}) == []  # 정렬에 쓰면 받는다


def test_preview_does_not_count_remotely_when_count_false(remote, monkeypatch):
    from core import server
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: None)
    counted = []
    real = server.row_count
    monkeypatch.setattr(server, "row_count", lambda *a: (counted.append(a), real(*a))[1])
    got = remote.post("/api/preview", json={"base": "pts", "count": False}).get_json()
    assert got["before"] is None and not counted
    remote.post("/api/preview", json={"base": "pts"})
    assert counted == []  # 사본이 이미 있으면 그 행 수를 쓴다(DB에 COUNT를 보내지 않는다)


def test_prep_where_new_without_copy_still_asks_scan_gate(remote, monkeypatch):
    from core import server
    from core import settings
    monkeypatch.setattr(settings, "load", lambda: {"prep_where": "new"})
    asked = []

    def gate(body, make_sql, rows=None):
        asked.append(make_sql()[0])
        return server.jsonify(needs_confirm=True, text="큼")

    monkeypatch.setattr(server, "scan_gate", gate)
    got = remote.post("/api/preview", json={"base": "pts", "columns": {"lat": {"ops": {"fill": {"how": "mean"}}}}}).get_json()  # 통계를 내는 일이 있을 때만 DB가 읽는다(2C5)
    assert got.get("needs_confirm") is True and asked


def test_table_key_tells_apart_duckdb_files_in_same_folder(folder, tmp_path):
    from core import server
    from prep import prep_app
    keys = []
    for name in ("a.duckdb", "b.duckdb"):
        server.state["opened"] = {"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / name)}
        keys.append(prep_app.table_key({"base": "t"}))
    server.state["opened"] = None
    assert keys[0] != keys[1]
