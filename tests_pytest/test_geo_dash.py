"""대시보드 지도 카드(geomap): 저장소 검증, 카드 값 만들기(영역별 값·꼭짓점 줄이기·점), 연결에서 구하는 라우트, 지도 그리기 계산(node: 메르카토르·타일 번호).
값은 손으로 계산하고, 타일 번호는 따로 쓴 식(asinh)과 맞춘다. 데이터는 테스트 안에서 지어낸다."""
import json
import math
import shutil
import subprocess

import duckdb
import numpy as np
import polars as pl
import pytest

from core import dash_store
from core import server
from conftest import ROOT
from geo import mapcard

SQUARE = lambda x0, y0, x1, y1: {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}
GEO = {"regions": {"table": "areas"}, "name": "name", "lat": "lat", "lon": "lon", "value": "amt", "agg": "sum"}


def check(**view):
    return dash_store.check_card({"kind": "geomap", "title": "지도", "view": view})


# ---------- 저장소 검증 ----------

def test_geomap_card_is_a_known_kind_and_validates_settings():
    assert "geomap" in dash_store.KINDS
    shell = dash_store.check_card({"kind": "geomap", "title": "지도"})  # 카드 모음에서 끌어 놓은 빈 틀
    assert shell["view"].get("geo") is None
    good = check(geo={**GEO, "tiles": False, "points": True, "color": "#11706f", "region_value": "pop", "geometry": "geometry"})
    assert good["view"]["geo"]["regions"] == {"table": "areas"} and good["view"]["geo"]["tiles"] is False and good["view"]["geo"]["color"] == "#11706f"
    only_points = check(geo={"lat": "lat", "lon": "lon"})["view"]["geo"]
    assert only_points == {"lat": "lat", "lon": "lon"}
    by_file = check(geo={"regions": {"path": "C:/x/areas.geojson"}, "name": "구"})["view"]["geo"]
    assert by_file["regions"] == {"path": "C:/x/areas.geojson"}
    for geo, message in (({"lat": "a"}, "함께"), ({}, "위도·경도"), ({"regions": {"table": "a"}, "lat": "a", "lon": "b"}, "이름 컬럼"), ({**GEO, "agg": "median"}, "agg"),
                         ({**GEO, "tiles": "yes"}, "켬"), ({**GEO, "color": "red"}, "HEX"), ({**GEO, "x": 1}, "쓸 수 없는"), ({**GEO, "regions": {"table": "a", "path": "b"}}, "하나"),
                         ({**GEO, "regions": {"table": ""}}, "하나"), ("글자", "쓸 수 없는")):
        with pytest.raises(ValueError, match=message):
            check(geo=geo)
    assert "tiles" in dash_store.GEO_KEYS and "geomap" in dash_store.check_template({"name": "t", "cards": [{"kind": "geomap", "title": "지도", "layout": {"x": 0, "y": 0, "w": 6, "h": 6}}]})["cards"][0]["kind"]


def test_geomap_card_keeps_color_rule_and_size_limit():
    card = dash_store.check_card({"kind": "geomap", "title": "지도", "view": {"geo": GEO}, "colors": {"series": {"점": "#e8590c"}, "parts": {"서울": "#1c7ed6"}, "order": [], "parts_order": []},
                                  "data": {"values": {"regions": [], "points": [], "bounds": [0, 0, 1, 1]}, "computed_at": "2026-01-01T00:00:00"}})
    assert card["colors"]["parts"] == {"서울": "#1c7ed6"} and card["data"]["values"]["bounds"] == [0, 0, 1, 1]
    with pytest.raises(ValueError, match="HEX"):
        dash_store.check_card({"kind": "geomap", "title": "t", "view": {"geo": GEO}, "colors": {"parts": {"서울": "파랑"}}})
    with pytest.raises(ValueError, match="상한"):
        dash_store.check_card({"kind": "geomap", "title": "t", "view": {"geo": GEO}, "data": {"values": {"x": "가" * (dash_store.MAX_CARD_BYTES + 10)}}})


# ---------- 카드 값 ----------

def frame_regions():
    return pl.DataFrame({"name": ["A", "B", "C"], "pop": [5, 9, None], "geometry": [json.dumps(g) for g in (SQUARE(0, 0, 2, 2), SQUARE(2, 0, 4, 2), SQUARE(10, 10, 11, 11))]})


def test_regions_colored_by_points_inside(monkeypatch):
    points = pl.DataFrame({"lat": [1.0, 1.0, 1.0, 5.0, None], "lon": [1.0, 1.5, 3.0, 3.0, 1.0], "value": [10.0, 5.0, 7.0, 1.0, 9.0]})
    out = mapcard.build(frame_regions(), points, {"name": "name", "agg": "sum", "value": "value"})
    v = out["values"]
    assert [(r["name"], r["value"]) for r in v["regions"]] == [("A", 15.0), ("B", 7.0), ("C", None)]  # A: 10+5, B: 7, C: 점 없음
    assert v["bounds"] == [0, 0, 11, 11] and v["total_points"] == 4 and v["assigned"] == 3 and v["label"] == "value 합계"
    assert "점 1개는 어느 영역에도 없었어요" in out["note"]
    count = mapcard.build(frame_regions(), points, {"name": "name"})["values"]
    assert [r["value"] for r in count["regions"]] == [2, 1, 0] and count["label"] == "점 수"  # 개수는 점이 없는 영역이 0
    mean = mapcard.build(frame_regions(), points, {"name": "name", "agg": "mean", "value": "value"})["values"]
    assert [r["value"] for r in mean["regions"]][:2] == [7.5, 7.0]
    big = mapcard.build(frame_regions(), points, {"name": "name", "agg": "max", "value": "value"})["values"]
    assert [r["value"] for r in big["regions"]][:2] == [10.0, 7.0]
    assert v["points"] == [[1.0, 1.0], [1.5, 1.0], [3.0, 1.0], [3.0, 5.0]]  # 빈 좌표 점은 찍지 않는다
    assert mapcard.build(frame_regions(), points, {"name": "name", "points": False})["values"]["points"] == []


def test_region_value_column_colors_directly_and_regions_only_or_points_only():
    out = mapcard.build(frame_regions(), None, {"name": "name", "region_value": "pop"})["values"]
    assert [r["value"] for r in out["regions"]] == [5, 9, None] and out["label"] == "영역 값" and out["assigned"] == 0 and out["points"] == []
    only = mapcard.build(None, pl.DataFrame({"lat": [37.5, 37.6], "lon": [127.0, 127.1]}), {"lat": "lat", "lon": "lon"})["values"]
    assert only["regions"] == [] and only["bounds"] == [127.0, 37.5, 127.1, 37.6] and len(only["points"]) == 2
    with pytest.raises(ValueError, match="그릴 영역도 점도 없어요"):
        mapcard.build(None, None, {"lat": "lat", "lon": "lon"})


def test_rings_keep_holes_and_stay_small(monkeypatch):
    donut = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]], [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]}
    multi = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]], [[[5, 5], [6, 5], [6, 6], [5, 5]]]]}
    out = mapcard.build(pl.DataFrame({"name": ["도넛", "여럿"], "geometry": [json.dumps(donut), json.dumps(multi)]}), None, {"name": "name"})["values"]
    assert [len(r["rings"]) for r in out["regions"]] == [2, 2] and out["regions"][0]["rings"][1][:2] == [1.0, 1.0]  # 구멍도 고리로 남는다
    circle = [[round(math.cos(a) * 3 + 100, 7), round(math.sin(a) * 3 + 40, 7)] for a in np.linspace(0, 2 * math.pi, 20001)]
    circle[-1] = circle[0]
    big = {"type": "Polygon", "coordinates": [circle]}
    out = mapcard.build(pl.DataFrame({"name": ["원"], "geometry": [json.dumps(big)]}), None, {"name": "name"})
    ring = out["values"]["regions"][0]["rings"][0]
    assert len(ring) // 2 <= mapcard.MAX_VERTICES + 2 and ring[:2] == ring[-2:] and "꼭짓점이 많아" in out["note"]  # 줄여도 처음과 끝이 같아 닫힌 모양
    assert len(json.dumps(out)) < dash_store.MAX_CARD_BYTES / 2
    assert all(len(str(x).split(".")[-1]) <= mapcard.DIGITS for x in ring)  # 소수 4자리


def test_many_points_are_sampled_for_drawing():
    n = mapcard.MAX_DRAW * 3
    points = pl.DataFrame({"lat": np.linspace(30, 31, n), "lon": np.linspace(120, 121, n)})
    out = mapcard.build(None, points, {"lat": "lat", "lon": "lon"})
    assert len(out["values"]["points"]) == mapcard.MAX_DRAW and out["values"]["total_points"] == n and "고르게" in out["note"]
    assert out["values"]["points"][0] == [120.0, 30.0] and out["values"]["points"][-1] == [121.0, 31.0]  # 처음과 끝은 남는다


# ---------- 연결에서 구하기 ----------

@pytest.fixture
def db(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "g.duckdb"))
    raw.execute("create table shops (id integer, lat double, lon double, amt double, d date)")
    raw.executemany("insert into shops values (?, ?, ?, ?, ?)", [(1, 1.0, 1.0, 10.0, "2026-01-01"), (2, 1.0, 1.5, 5.0, "2026-01-02"), (3, 1.0, 3.0, 7.0, "2026-02-01"), (4, 5.0, 3.0, 1.0, "2026-02-02")])
    raw.execute("create table areas (name varchar, geometry varchar, pop integer)")
    raw.executemany("insert into areas values (?, ?, ?)", [("A", json.dumps(SQUARE(0, 0, 2, 2)), 3), ("B", json.dumps(SQUARE(2, 0, 4, 2)), 8)])
    raw.close()
    (tmp_path / "file.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"구": "가"}, "geometry": SQUARE(0, 0, 2, 2)}]}), encoding="utf-8")
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "g.duckdb")}).status_code == 200
    yield c, tmp_path
    c.post("/api/disconnect", json={})


def geo_card(table="shops", conditions=(), **view):
    return {"kind": "geomap", "title": "지도", "view": {"geo": GEO, **view}, "source": {"schema": "", "table": table, "body": {"base": table, "conditions": list(conditions), "series": []}}}


def test_compute_route_builds_choropleth_from_connection(db):
    c, _ = db
    found = c.post("/api/dash/card/compute", json={"card": geo_card()}).get_json()
    v = found["data"]["values"]
    assert [(r["name"], r["value"]) for r in v["regions"]] == [("A", 15.0), ("B", 7.0)] and v["points"][-1] == [3.0, 5.0]
    assert found["data"]["computed_at"] and len(found["data"]["def_hash"]) == 16 and found["source"]["key"].startswith("folder:")
    filtered = c.post("/api/dash/card/compute", json={"card": geo_card(conditions=["amt >= 6"])}).get_json()["data"]["values"]
    assert [r["value"] for r in filtered["regions"]] == [10.0, 7.0]  # 카드의 조건이 점을 거른다
    before = c.post("/api/dash/card/compute", json={"card": geo_card(geo={**GEO, "agg": "count", "value": None})}).get_json()
    assert before["data"]["values"]["label"] == "점 수" and [r["value"] for r in before["data"]["values"]["regions"]] == [2, 1]


def test_compute_route_period_limits_points(db):
    c, _ = db
    card = geo_card()
    card["source"]["date"] = "d"
    found = c.post("/api/dash/card/compute", json={"card": card, "ctx": {"range": {"start": "2026-01-01", "end": "2026-01-31"}}}).get_json()
    assert [r["value"] for r in found["data"]["values"]["regions"]] == [15.0, None]  # 1월의 점 둘만 쓴다(B에는 점이 없어 합계가 빈 값)


def test_compute_route_regions_from_file_region_column_and_errors(db):
    c, tmp = db
    file_card = geo_card(geo={"regions": {"path": str(tmp / "file.geojson")}, "name": "구", "lat": "lat", "lon": "lon"})
    got = c.post("/api/dash/card/compute", json={"card": file_card}).get_json()["data"]["values"]
    assert [(r["name"], r["value"]) for r in got["regions"]] == [("가", 2)]  # 파일 영역(0~2)에 든 점은 id 1·2 둘
    only_regions = c.post("/api/dash/card/compute", json={"card": geo_card(table="areas", geo={"regions": {"table": "areas"}, "name": "name", "region_value": "pop"})}).get_json()["data"]["values"]
    assert [r["value"] for r in only_regions["regions"]] == [3, 8] and only_regions["points"] == []
    for card, message in ((geo_card(geo={**GEO, "regions": {"table": "nope"}}), "영역 테이블"), (geo_card(geo={**GEO, "name": "nope"}), "영역 컬럼"), (geo_card(geo={**GEO, "lat": "nope"}), "컬럼"),
                          ({**geo_card(), "view": {}}, "지도 설정")):
        r = c.post("/api/dash/card/compute", json={"card": card}).get_json()
        assert message in r["error"], (message, r)


# ---------- 지도 그리기 계산(node) ----------

SCRIPT = """
const assert = require("assert");
%(source)s
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const out = {};
// 메르카토르: 적도·날짜 변경선·위도 85 근처
near(mercX(-180), 0); near(mercX(0), 0.5); near(mercX(180), 1); near(mercY(0), 0.5);
assert.ok(mercY(85) < 0.01 && mercY(-85) > 0.99 && mercY(60) < mercY(30));  // 북쪽일수록 위(작은 y)
// 틀: 범위에 맞춰 너비에 맞추고, 높이가 상한을 넘으면 높이에 맞춘다. 범위의 모서리가 틀 안에 든다
const f = mapFrame([126.8, 37.4, 127.2, 37.7], 640);
const [x0, y0] = f.project(126.8, 37.7), [x1, y1] = f.project(127.2, 37.4);
assert.ok(x0 > 0 && y0 > 0 && x1 < f.w && y1 < f.h && x1 > x0 && y1 > y0);
assert.ok(f.w <= 640 + 1e-6 && f.h <= 520 + 1e-6 && (Math.abs(f.w - 640) < 1e-6 || Math.abs(f.h - 520) < 1e-6));  // 너비나 높이 중 하나에 꼭 맞는다
const tall = mapFrame([126, 33, 127, 38], 640, 520);
assert.ok(tall.h <= 520 + 1e-6 && tall.w < 640);
const one = mapFrame([127, 37, 127, 37], 640);  // 점 하나도 틀이 생긴다
assert.ok(Number.isFinite(one.w) && one.w > 0 && Number.isFinite(one.h));
// 타일 번호
out.tiles = [8, 10, 12, 14].map(z => { const t = tileCount(f, z); return [z, t]; });
out.zoom = tileZoom(f);
const z = tileZoom(f), tiles = tilesFor(f, z);
out.sample = { z, n: tiles.length, first: tiles[0], last: tiles[tiles.length - 1], urls: tiles.map(t => t.url) };
out.seoul = (() => { const n = 2 ** 10, x = Math.floor(mercX(126.978) * n), y = Math.floor(mercY(37.5665) * n); return [x, y]; })();
assert.ok(tilesFor(mapFrame([-180, -80, 180, 80], 640), 0).length === 1);  // 세계 전체는 줌 0 타일 하나
assert.ok(tileCount(mapFrame([100, 10, 140, 50], 640), tileZoom(mapFrame([100, 10, 140, 50], 640))) <= MAX_TILES);
// 색칠
assert.deepStrictEqual(fillLevels([0, 10, null]).map(v => v == null ? null : +v.toFixed(3)), [0.18, 0.9, null]);
assert.deepStrictEqual(fillLevels([5, 5]), [0.54, 0.54]);  // 모두 같으면 가운데
assert.deepStrictEqual(fillLevels([null, undefined, "x"]), [null, null, null]);
assert.strictEqual(ringPath([0, 0, 1, 0, 1, 1], (lon, lat) => [lon * 10, lat * 10]), "M0.0 0.0L10.0 0.0L10.0 10.0Z");
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_map_math_in_node_matches_independent_tile_formula():
    source = (ROOT / "static" / "geo" / "geomap.js").read_text(encoding="utf-8")
    done = subprocess.run(["node", "-e", SCRIPT % {"source": source}], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-1500:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    # 서울시청 근처 타일(줌 10)을 슬리피 맵 식(asinh)으로 따로 구한다
    lon, lat, n = 126.978, 37.5665, 2 ** 10
    expected = [math.floor((lon + 180) / 360 * n), math.floor((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)]
    assert out["seoul"] == expected == [873, 396]
    sample = out["sample"]
    assert sample["n"] <= 16 and all(u.startswith("https://tile.openstreetmap.org/%d/" % sample["z"]) and u.endswith(".png") for u in sample["urls"])
    z = sample["z"]
    n = 2 ** z
    first_x, first_y = math.floor((126.8 + 180) / 360 * n), math.floor((1 - math.asinh(math.tan(math.radians(37.7))) / math.pi) / 2 * n)  # 북서쪽 모서리 타일
    assert sample["first"]["x"] <= first_x and sample["first"]["y"] <= first_y and sample["first"]["url"].endswith(f"/{z}/{sample['first']['x']}/{sample['first']['y']}.png")
    # 줌이 높을수록 타일이 는다
    counts = dict(out["tiles"])
    assert counts[8] <= counts[10] <= counts[12] <= counts[14]
