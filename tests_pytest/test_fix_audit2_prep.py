"""2차 감사 묶음 2C(전처리·분석·좌표) 지적별 시험. 값은 시험 안에서 지어내고 손으로 계산했다."""
import json
import sqlite3

import numpy as np
import pyarrow as pa
import pytest

from lazy import ibis

from core import held, server, settings
from geo import geojson, mapcard, spatial
from prep import prep_app, prep_expr, prep_profile

NAN = float("nan")


@pytest.fixture
def remote(folder, tmp_path, monkeypatch):
    """sqlite 원격 연결: pts 표 50행. 처리 용량 확인은 부른 SQL만 모으고(c.gates) 통과시킨다."""
    from query.search_app import app
    path = tmp_path / "s.db"
    raw = sqlite3.connect(path)
    raw.execute("create table pts (id integer, a real, cx text, dx text)")
    raw.executemany("insert into pts values (?, ?, ?, ?)", [(i, i * 1.5, "x", "y") for i in range(50)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{path}"}).status_code == 200
    c.gates, c.real_gate = [], server.scan_gate
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: (c.gates.append(make_sql()[0]), None)[1])
    c.path = path
    yield c
    held.store["items"].clear()
    c.post("/api/disconnect", json={})


def add_rows(path, n):
    raw = sqlite3.connect(path)
    raw.executemany("insert into pts values (?, ?, ?, ?)", [(1000 + i, 1.0, "n", "n") for i in range(n)])
    raw.commit()
    raw.close()


def fetch_spy(monkeypatch):
    """사본을 새로 받을 때마다 (받지 않는 컬럼 튜플)을 모으는 목록."""
    found = []
    real = held.fetch
    monkeypatch.setattr(held, "fetch", lambda key, *a, **k: (found.append(key[3]), real(key, *a, **k))[1])
    return found


# ---------- 2C1 낡은 사본으로 산출물을 만들지 않는다 ----------

def test_run_table_uses_fresh_download(remote):
    rec = {"base": "pts", "columns": {}}
    assert remote.post("/api/preview", json=rec).get_json()["before"] == 50  # 미리보기가 사본을 받아 둔다
    add_rows(remote.path, 10)
    out = remote.post("/api/run", json={"recipe": rec, "target": {"kind": "table", "name": "out1"}}).get_json()
    assert out["rows"] == 60


def test_run_file_and_full_analysis_use_fresh_download(remote):
    rec = {"base": "pts", "columns": {}}
    remote.post("/api/preview", json=rec)
    add_rows(remote.path, 10)
    how = {"kind": "distribution", "column": "id"}
    assert remote.post("/api/analyze", json={"recipe": rec, "how": how, "full": True}).get_json()["stats"]["n"] == 60
    add_rows(remote.path, 5)
    r = remote.post("/api/run", json={"recipe": rec, "target": {"kind": "file", "format": "csv"}})
    assert r.get_data(as_text=True).count("\n") == 66  # 머리글 + 65행


def test_confirm_step_keeps_old_copy_until_download(remote, monkeypatch):
    """확인이 필요해 멈춘 요청은 사본을 지우지 않는다(확인 뒤 새로 받을 때 지운다)."""
    rec = {"base": "pts", "columns": {}}
    remote.post("/api/preview", json=rec)
    monkeypatch.setattr(server, "scan_gate", lambda body, make_sql, rows=None: server.jsonify(needs_confirm=True))
    remote.post("/api/run", json={"recipe": rec, "target": {"kind": "table", "name": "o"}})
    assert prep_app.copy_key(rec) in held.store["items"]


def test_preview_still_reuses_copy(remote, monkeypatch):
    rec = {"base": "pts", "columns": {}}
    remote.post("/api/preview", json=rec)
    got = fetch_spy(monkeypatch)
    remote.post("/api/preview", json=rec)
    assert got == []


# ---------- 2C2 이상치 제거끼리도 컬럼 순서와 무관 ----------

def test_outlier_drops_use_start_table_stats():
    a, b = [200, 15, 15, 9, 13, 7, 14, 0], [13, 8, 7, 20, 7, 0, 9, 9]
    ops = {"outlier": {"how": "drop", "by": "iqr"}}

    def run(data):
        expr, _ = prep_expr.build(None, {"base": "t", "columns": {c: {"ops": ops} for c in data}}, ibis.memtable(pa.table(data)))
        return expr.to_pyarrow().to_pydict()

    def fence(v):
        q1, q3 = np.percentile(v, [25, 75])
        return q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)

    (al, ah), (bl, bh) = fence(a), fence(b)
    want = sorted((x, y) for x, y in zip(a, b) if al <= x <= ah and bl <= y <= bh)
    first = run({"a": a, "b": b})
    second = run({"b": b, "a": a})
    assert sorted(zip(first["a"], first["b"])) == want
    assert sorted(zip(second["a"], second["b"])) == want


# ---------- 2C3 작업 사본 열쇠 ----------

def test_bigger_copy_is_reused(remote, monkeypatch):
    got = fetch_spy(monkeypatch)
    remote.post("/api/preview", json={"base": "pts", "columns": {}})
    remote.post("/api/preview", json={"base": "pts", "columns": {"dx": {"keep": False}}})  # 적은 컬럼은 큰 사본에서
    assert len(got) == 1
    remote.post("/api/preview", json={"base": "pts", "columns": {"dx": {"keep": False}}, "filters": ["id > 3"]})  # 거르기가 다르면 새 사본
    assert len(got) == 2


def test_reused_copy_hides_unneeded_columns(remote):
    remote.post("/api/preview", json={"base": "pts", "columns": {}})
    r = remote.post("/api/preview", json={"base": "pts", "columns": {"dx": {"keep": False}}}).get_json()
    assert len(r["types"]) == 3 and r["sources"] == ["id", "a", "cx"]


def test_analyze_gate_matches_copy_without_sorts(remote, monkeypatch):
    got = fetch_spy(monkeypatch)
    rec = {"base": "pts", "columns": {"dx": {"keep": False}}, "sorts": ["dx"]}
    remote.post("/api/analyze", json={"recipe": rec, "full": True, "how": {"kind": "columns"}})
    assert remote.gates and all("dx" not in sql for sql in remote.gates)
    assert "dx" in got[0]  # 받지 않는 컬럼 목록에 dx가 들어 있다(= 정렬 때문에 받지 않았다)


def test_unused_columns_compares_names_not_substrings():
    specs = {"id": {"keep": False}, "pr": {"keep": False}, "a b": {"keep": False}}
    recipe = {"columns": specs, "formulas": [{"name": "z", "expr": "valid + price"}]}
    assert prep_app.unused_columns(recipe) == ["a b", "id", "pr"]
    recipe["formulas"] = [{"name": "z", "expr": "id + pr"}]
    assert prep_app.unused_columns(recipe) == ["a b"]
    recipe["formulas"] = []
    recipe["dedupe"] = ["a b"]
    assert prep_app.unused_columns(recipe) == ["id", "pr"]


# ---------- 2C5 prep_where=new 원격 확인은 통계 계산이 있을 때만 ----------

def test_new_mode_asks_only_with_stats(remote, monkeypatch):
    monkeypatch.setattr(settings, "load", lambda: {"prep_where": "new"})
    remote.post("/api/preview", json={"base": "pts", "columns": {"cx": {"ops": {"trim": True}}}})
    assert remote.gates == []
    remote.post("/api/preview", json={"base": "pts", "columns": {"a": {"ops": {"fill": {"how": "mean"}}}}})
    assert len(remote.gates) == 1
    remote.post("/api/dupes", json={"base": "pts", "dedupe": ["cx"]})
    assert len(remote.gates) == 2


def test_new_mode_text_says_db_reads(remote, monkeypatch):
    from core import scan_size
    monkeypatch.setattr(settings, "load", lambda: {"prep_where": "new", "memory": 1})
    monkeypatch.setattr(server, "scan_gate", remote.real_gate)
    monkeypatch.setattr(scan_size, "fetched", lambda con, sql, rows=None, dialect=None: {"bytes": 50 * 10**9, "exact": False, "how": "x", "rows": 1})
    r = remote.post("/api/preview", json={"base": "pts", "columns": {"a": {"ops": {"fill": {"how": "mean"}}}}})
    assert "DB가 이 테이블을 읽어요" in r.get_json()["text"]


# ---------- 2C6 프로파일·prune의 NaN ----------

def test_profile_counts_nan_as_null():
    col = prep_profile.profile(ibis.memtable(pa.table({"x": [1.0, NAN, 3.0, None]})))["columns"][0]
    assert col["nulls"] == 2 and col["min"] == 1.0 and col["max"] == 3.0


def test_prune_nan_and_inf_columns_still_pair_up():
    x = np.arange(60, dtype=float)
    y = x * 2 + 1
    y_nan = y.copy()
    y_nan[3] = NAN
    found = prep_profile.prune(pa.table({"x": x, "y": y_nan}))
    assert [f["column"] for f in found] == ["y"] and found[0]["with"] == "x"
    y_inf = y.copy()
    y_inf[5] = float("inf")
    assert [f["column"] for f in prep_profile.prune(pa.table({"x": x, "y": y_inf}))] == ["y"]


# ---------- 2C7 날짜 변경선, 점 좌표 ----------

def test_center_bounds_across_dateline():
    west = {"type": "Polygon", "coordinates": [[[170, 0], [-170, 0], [-170, 10], [170, 10], [170, 0]]]}
    lon, lat = geojson.center(west)
    assert abs(abs(lon) - 180) < 1e-9 and abs(lat - 5) < 1e-9
    assert geojson.bounds(west) == (170, 0, 190, 10)
    points = {"type": "MultiPoint", "coordinates": [[179, 0], [-179, 0]]}
    assert abs(abs(geojson.center(points)[0]) - 180) < 1e-9  # 평균 0이 아니라 날짜 변경선
    assert geojson.center({"type": "MultiPoint", "coordinates": [[179, 0], [-178, 0]]})[0] == pytest.approx(-179.5)
    assert geojson.bounds({"type": "MultiPoint", "coordinates": [[179, 0], [-178, 0]]})[::2] == (179, 182)


def test_wide_geometry_is_not_shifted():
    spread = {"type": "MultiPoint", "coordinates": [[-100, 0], [0, 0], [100, 0]]}
    assert geojson.bounds(spread)[::2] == (-100, 100) and geojson.center(spread)[0] == 0  # 옮기면 더 넓어지므로 그대로


def test_map_card_points_follow_dateline_rings():
    import polars as pl
    ring = [[170, 0], [190, 0], [190, 10], [170, 10], [170, 0]]
    regions = pl.DataFrame({"name": ["r"], "geometry": [json.dumps({"type": "Polygon", "coordinates": [ring]})]})
    points = pl.DataFrame({"lat": [5.0, NAN, 5.0], "lon": [-175.0, 175.0, 175.0]})
    out = mapcard.build(regions, points, {"name": "name"})["values"]
    assert out["points"] == [[185.0, 5.0], [175.0, 5.0]] and out["assigned"] == 2 and out["bounds"] == [170.0, 0.0, 190.0, 10.0]


def test_map_card_points_only_across_dateline():
    import polars as pl
    out = mapcard.build(None, pl.DataFrame({"lat": [1.0, 2.0], "lon": [179.0, -179.0]}), {"name": "name"})["values"]
    assert out["bounds"][::2] == [179.0, 181.0] and out["points"] == [[179.0, 1.0], [181.0, 2.0]]


# ---------- 2C8 GeoJSON 상한 ----------

def test_geojson_limit_uses_memory_bound(tmp_path, monkeypatch):
    from core import monitor
    p = tmp_path / "g.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]}), encoding="utf-8")
    monkeypatch.setattr(monitor, "bound_bytes", lambda config=None: 100)
    with pytest.raises(ValueError, match="커서"):
        geojson.read_file(p)
    monkeypatch.setattr(monitor, "bound_bytes", lambda config=None: 10**9)
    assert len(geojson.read_file(p)) == 1


# ---------- 2C9 분포 질의 수 ----------

def test_distribution_query_count(monkeypatch):
    from analysis import stats
    from tests_pytest.conftest import spy_queries
    t = ibis.memtable(pa.table({"x": [float(i) for i in range(30)]}))
    found = spy_queries(monkeypatch)
    stats.analyze(t, {"kind": "distribution", "column": "x"})
    assert len(found) <= 5


# ---------- 2C10 y가 NaN인 점이 묶음을 밖으로 만들지 않는다 ----------

def test_nan_y_does_not_push_chunk_outside():
    ring = spatial.closed([[0, 0], [10, 0], [10, 10], [0, 10]])
    out = spatial.in_ring(np.array([5.0, 5.0, 5.0]), np.array([5.0, NAN, 6.0]), ring)
    assert out.tolist() == [True, False, True]
