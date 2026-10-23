"""좌표 분석(geo/): GeoJSON 읽기와 파일 연결, 거리·반경·범위 상자·격자, 가장 가까운 지점, 점-영역, TM 변환, 좌표 컬럼 인식.
값은 손으로 계산하거나 따로 쓴 식(math)과 맞춘다. 데이터는 테스트 안에서 지어낸다."""
import json
import math

import numpy as np
import polars as pl
import pytest
from lazy import ibis

from core import parse
from core import source
from core import sql_funcs
from geo import detect, funcs, geojson, spatial, tm

SEOUL, BUSAN, LONDON, PARIS = (37.5665, 126.978), (35.1796, 129.0756), (51.5074, -0.1278), (48.8566, 2.3522)


def poly(*rings):
    return {"type": "Polygon", "coordinates": [list(map(list, r)) for r in rings]}


SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]
HOLE = [(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]


def collection(*features, **extra):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": p, "geometry": g} for p, g in features], **extra}


def hand_haversine(a, b):
    """따로 쓴 haversine(지구 반지름 6371.0088km)."""
    p1, p2, dl = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


@pytest.fixture(params=["duckdb", "polars"])
def points(request):
    """서울·부산·런던과 위도가 빈 행 하나."""
    df = pl.DataFrame({"id": [1, 2, 3, 4], "lat": [SEOUL[0], BUSAN[0], LONDON[0], None], "lon": [SEOUL[1], BUSAN[1], LONDON[1], 127.0]})
    if request.param == "polars":
        return ibis.polars.connect().create_table("p", df)
    return ibis.duckdb.connect().create_table("p", ibis.memtable(df))


# ---------- GeoJSON 읽기 ----------

def test_geojson_geometry_kinds_properties_and_columns():
    doc = collection(({"name": "점", "n": 1}, {"type": "Point", "coordinates": [10, 20]}),
                     ({"name": "선", "n": 2}, {"type": "LineString", "coordinates": [[0, 0], [2, 0], [2, 2]]}),
                     ({"name": "면", "n": 3}, poly(SQUARE)),
                     ({"name": "구멍", "n": 4}, poly(SQUARE, HOLE)),
                     ({"name": "여럿", "n": 5}, {"type": "MultiPolygon", "coordinates": [[SQUARE], [[(10, 10), (12, 10), (12, 12), (10, 12), (10, 10)]]]}),
                     ({"name": "없음", "n": 6}, None))
    df = geojson.table(doc)
    assert df["name"].to_list() == ["점", "선", "면", "구멍", "여럿", "없음"] and df["n"].to_list() == [1, 2, 3, 4, 5, 6]  # 속성이 컬럼으로
    assert df["geom_type"].to_list() == ["Point", "LineString", "Polygon", "Polygon", "MultiPolygon", None]
    assert df.columns[-8:] == ["geom_type", "center_lon", "center_lat", "min_lon", "min_lat", "max_lon", "max_lat", "geometry"]
    row = df.row(0, named=True)
    assert (row["center_lon"], row["center_lat"], row["min_lon"], row["max_lat"]) == (10, 20, 10, 20)
    assert df.row(1, named=True)["center_lon"] == pytest.approx(4 / 3) and df.row(1, named=True)["max_lon"] == 2  # 선: 꼭짓점 평균
    sq, hole, multi, none = (df.row(i, named=True) for i in (2, 3, 4, 5))
    assert (sq["center_lon"], sq["center_lat"], sq["min_lon"], sq["max_lon"]) == (2, 2, 0, 4)
    assert (hole["center_lon"], hole["center_lat"]) == (2, 2)  # 가운데 구멍은 무게중심을 옮기지 않는다
    assert multi["center_lon"] == pytest.approx((2 * 16 + 11 * 4) / 20) and multi["max_lon"] == 12  # 면적 무게중심: 큰 쪽 16, 작은 쪽 4
    assert none["center_lon"] is None and none["geometry"] is None
    assert json.loads(hole["geometry"])["coordinates"][1][0] == [1, 1]  # 원본 도형은 JSON 글자


def test_off_center_hole_moves_the_centroid():
    # 정사각형 4×4(면적 16, 무게중심 2,2)에서 오른쪽 구석 구멍(2×2, 무게중심 3,3)을 빼면 ((2*16) - (3*4)) / 12
    df = geojson.table(collection(({}, poly(SQUARE, [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]))))
    assert df["center_lon"][0] == pytest.approx((2 * 16 - 3 * 4) / 12) == df["center_lat"][0]


def test_geojson_property_name_clash_mixed_types_and_nested():
    df = geojson.table(collection(({"geometry": "x", "mix": 1, "deep": {"a": 1}}, {"type": "Point", "coordinates": [1, 2]}), ({"mix": "글자", "deep": [1]}, {"type": "Point", "coordinates": [3, 4]})))
    assert "geometry_속성" in df.columns and df["geometry_속성"][0] == "x"  # 속성 이름이 만든 컬럼과 같으면 구분한다
    assert df["mix"].to_list() == ["1", "글자"]  # 섞인 종류는 글자
    assert df["deep"].to_list() == ['{"a":1}', "[1]"]  # 중첩은 JSON 글자


def test_crs_conversion_to_wgs84():
    five = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5179"}}
    df = geojson.table(collection(({"n": 1}, {"type": "Point", "coordinates": [953898, 1952036]}), crs=five))
    assert df["center_lon"][0] == pytest.approx(126.978, abs=1e-3) and df["center_lat"][0] == pytest.approx(37.567, abs=1e-3)
    assert json.loads(df["geometry"][0])["coordinates"][0] == pytest.approx(126.978, abs=1e-3)  # 도형 원본도 경도·위도로 바뀐 값
    same = geojson.table(collection(({}, {"type": "Point", "coordinates": [127, 37]}), crs={"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}))
    assert same["center_lon"][0] == 127  # CRS84는 WGS84(바꾸지 않음)
    ring = geojson.table(collection(({}, poly([(953898, 1952036), (954898, 1952036), (954898, 1953036), (953898, 1953036), (953898, 1952036)])), crs=five))
    assert 126.97 < ring["min_lon"][0] < ring["max_lon"][0] < 126.995  # 폴리곤 고리도 한꺼번에 바뀐다
    with pytest.raises(ValueError, match="EPSG 번호"):
        geojson.table(collection(({}, {"type": "Point", "coordinates": [1, 2]}), crs={"type": "name", "properties": {"name": "알 수 없는 이름"}}))


def test_single_feature_geometry_and_errors(tmp_path):
    assert geojson.table({"type": "Feature", "properties": {"a": 1}, "geometry": {"type": "Point", "coordinates": [1, 2]}})["a"].to_list() == [1]
    assert geojson.table({"type": "Point", "coordinates": [1, 2]})["geom_type"].to_list() == ["Point"]
    collected = geojson.table({"type": "Feature", "properties": {}, "geometry": {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [1, 2]}, {"type": "Point", "coordinates": [3, 4]}]}})
    assert collected["center_lon"][0] == 2 and collected["max_lat"][0] == 4
    with pytest.raises(ValueError, match="GeoJSON이 아닙니다"):
        geojson.table({"type": "Table"})
    bad = tmp_path / "bad.geojson"
    bad.write_text("{ 깨진", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON으로 읽을 수 없어요"):
        geojson.read(bad)


def test_geojson_file_becomes_a_table_in_a_file_connection(tmp_path):
    doc = collection(({"name": "서울권"}, poly([(126, 37), (128, 37), (128, 38), (126, 38), (126, 37)])), ({"name": "남쪽"}, poly([(126, 34), (128, 34), (128, 35), (126, 35), (126, 34)])))
    (tmp_path / "areas.geojson").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "plain.json").write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")
    (tmp_path / "layer.json").write_text(json.dumps(doc), encoding="utf-8")  # FeatureCollection인 .json도 GeoJSON으로
    assert source.kind_of("a.GeoJSON") == "geojson" and "geojson" in source.KINDS
    _, files = source.find_files(tmp_path)
    assert {f.name for f in files} == {"areas.geojson", "plain.json", "layer.json"}
    assert source.find_files(tmp_path, kinds=("geojson",))[1] == [tmp_path / "areas.geojson"]
    con = source.open_flat_files(files)
    t = con.table("areas")
    assert list(t.columns[:1]) == ["name"] and t.count().execute() == 2
    assert t.filter(t.name == "남쪽").geom_type.execute().to_list() == ["Polygon"]
    assert con.table("layer").count().execute() == 2 and list(con.table("plain").columns) == ["a"]  # 평범한 JSON은 그대로


# ---------- 거리·반경·범위 상자·격자 ----------

def test_haversine_matches_hand_formula_and_known_distances(points):
    d = points.mutate(d=funcs.haversine(points.lat, points.lon, PARIS[0], PARIS[1])).order_by("id").d.to_polars().to_list()
    assert d[2] == pytest.approx(hand_haversine(LONDON, PARIS), rel=1e-9) and 343 < d[2] < 344  # 런던–파리 약 343.5km
    assert d[0] == pytest.approx(hand_haversine(SEOUL, PARIS), rel=1e-9)
    assert d[3] is None  # 위도가 비면 거리도 빈 값
    sb = points.mutate(d=funcs.haversine(points.lat, points.lon, SEOUL[0], SEOUL[1])).order_by("id").d.to_polars().to_list()
    assert sb[0] == pytest.approx(0, abs=1e-9) and sb[1] == pytest.approx(325.1, abs=0.3)  # 서울–부산 약 325km
    assert funcs.EARTH_KM * math.pi == pytest.approx(20015.1, abs=0.1)  # 정반대 점은 반 둘레


def test_haversine_between_two_columns_and_dictionary_entry(points):
    out = points.mutate(d=sql_funcs.make("haversine", points, {"lat": "lat", "lon": "lon", "lat2": {"col": "lat"}, "lon2": {"col": "lon"}})).d.to_polars().drop_nulls().to_list()
    assert out == [0, 0, 0]  # 자기 자신까지의 거리
    assert sql_funcs.get("haversine")["category"] == "geo" and sql_funcs.get("within_km")["kind_out"] == "bool"


def test_radius_and_box_conditions_filter_rows_in_the_database(points):
    near = parse.predicate("near lat lon 37.5665 126.978 5", points)
    assert points.filter(near).id.to_polars().to_list() == [1]
    wide = parse.predicate("near lat lon 37.5665,126.978,330", points)  # 쉼표로 나눠도 된다
    assert sorted(points.filter(wide).id.to_polars().to_list()) == [1, 2]  # 부산은 약 325km
    assert points.filter(parse.predicate("near lat lon 37.5665 126.978 325", points)).id.to_polars().to_list() == [1]  # 325km 밖
    box = parse.predicate("box lat lon 35 126 38 130", points)
    assert sorted(points.filter(box).id.to_polars().to_list()) == [1, 2]
    assert points.filter(parse.predicate("box lat lon 35 126 36 130", points)).id.to_polars().to_list() == [2]
    both = parse.predicate(["near lat lon 37.5665 126.978 400", "box lat lon 36 126 38 130"], points)
    assert points.filter(both).id.to_polars().to_list() == [1]
    either = parse.predicate("near lat lon 51.5074 -0.1278 10 or box lat lon 35 126 36 130", points)
    assert sorted(points.filter(either).id.to_polars().to_list()) == [2, 3]
    assert "SELECT" in str(ibis.to_sql(points.filter(near), dialect="postgres")).upper() and "SIN" in str(ibis.to_sql(points.filter(near), dialect="postgres")).upper()  # DB가 계산하는 SQL


def test_radius_edge_cases_and_messages(points):
    assert points.filter(parse.predicate("near lat lon 37.5665 126.978 0", points)).id.to_polars().to_list() == [1]  # 반경 0은 같은 점만
    around = ibis.memtable(pl.DataFrame({"lat": [0.0, 0.0], "lon": [179.9, -179.9]}))
    assert around.filter(funcs.in_box(around.lat, around.lon, -1, 179, 1, -179)).count().execute() == 2  # 날짜 변경선을 가로지르는 상자
    for line, message in (("near lat lon 95 126 5", "-90~90"), ("near lat lon 37 200 5", "-180~180"), ("near lat lon 37 126 -1", "0 이상"), ("box lat lon 38 126 35 130", "최소 위도"),
                          ("near id lon 37 126 5 ", None), ("near lat lon x 126 5", "실수 값이 아닙니다")):
        if message is None:
            parse.predicate(line.strip(), points)  # 컬럼이 숫자면 어떤 컬럼이든 위도 자리에 쓸 수 있다
            continue
        with pytest.raises(ValueError, match=message):
            parse.predicate(line, points)
    with pytest.raises(ValueError, match="형식"):
        parse.predicate("near lat lon 37", points)  # 값 개수가 모자라면 안내
    text = ibis.memtable(pl.DataFrame({"name": ["a"], "near": [3]}))
    assert text.filter(parse.predicate("near = 3", text)).count().execute() == 1  # near라는 컬럼 이름도 그대로 쓴다


def test_grid_cells_ids_and_centers(points):
    out = points.mutate(g=funcs.grid_id(points.lat, points.lon, 0.01), cy=funcs.grid_center(points.lat, 0.01), cx=funcs.grid_center(points.lon, 0.01)).order_by("id").to_polars()
    assert out["g"].to_list() == ["3756_12697", "3517_12907", "5150_-13", None]  # floor(위도/0.01)_floor(경도/0.01)
    assert out["cy"].to_list()[:2] == [pytest.approx(37.565), pytest.approx(35.175)] and out["cx"].to_list()[0] == pytest.approx(126.975)  # 칸 중심 = (칸 번호 + 0.5) × 0.01
    same = ibis.memtable(pl.DataFrame({"lat": [37.5601, 37.5699, 37.5700], "lon": [126.9701, 126.9799, 126.98]}))
    ids = same.mutate(g=funcs.grid_id(same.lat, same.lon, 0.01)).g.to_polars().to_list()
    assert ids[0] == ids[1] == "3756_12697" and ids[2] == "3757_12698"  # 경계 값은 위쪽 칸으로
    for bad in (0, -1, 100):
        with pytest.raises(ValueError, match="칸 크기"):
            funcs.grid_id(same.lat, same.lon, bad)


# ---------- 가장 가까운 지점 ----------

def test_nearest_picks_the_closest_and_ignores_empties():
    rlat, rlon = [37.5665, 35.1796, 35.8714], [126.978, 129.0756, 128.6014]  # 서울·부산·대구
    index, dist = spatial.nearest([37.5, 35.2, 35.9, np.nan, 10.0], [127.0, 129.0, 128.5, 127.0, 10.0], rlat, rlon)
    assert index.tolist()[:3] == [0, 1, 2] and index[3] == -1 and np.isnan(dist[3])
    assert dist[0] == pytest.approx(hand_haversine((37.5, 127.0), (37.5665, 126.978)), rel=1e-9)
    far = [hand_haversine((10, 10), (a, b)) for a, b in zip(rlat, rlon)]
    assert index[4] == far.index(min(far)) and dist[4] == pytest.approx(min(far))  # 멀어도 가장 가까운 것
    none = spatial.nearest([1.0], [1.0], [], [])
    assert none[0].tolist() == [-1] and np.isnan(none[1][0])
    tie = spatial.nearest([0.0], [0.0], [1.0, -1.0], [0.0, 0.0])
    assert tie[0].tolist() == [0]  # 같은 거리면 먼저 나온 지점


def test_grid_candidates_give_the_same_answer_as_brute_force():
    rng = np.random.default_rng(7)
    rlat, rlon = rng.uniform(33, 38.5, 1500), rng.uniform(126, 129.5, 1500)
    lat, lon = rng.uniform(32, 39, 3000), rng.uniform(125, 130, 3000)  # 지점 밖의 점도 섞는다
    a, b = spatial.brute(lat, lon, rlat, rlon), spatial.gridded(lat, lon, rlat, rlon)
    assert (a[0] == b[0]).all() and np.allclose(a[1], b[1])
    far = spatial.gridded(np.array([50.0]), np.array([10.0]), rlat, rlon)  # 후보가 한 칸에도 없는 먼 점은 칸을 넓혀 간다
    assert far[0][0] == spatial.brute(np.array([50.0]), np.array([10.0]), rlat, rlon)[0][0]


def test_nearest_switches_to_grid_for_large_inputs(monkeypatch):
    used = []
    monkeypatch.setattr(spatial, "gridded", lambda *a: (used.append("grid"), spatial.brute(*a))[1])
    rng = np.random.default_rng(1)
    spatial.nearest(rng.uniform(33, 38, 1000), rng.uniform(126, 129, 1000), rng.uniform(33, 38, 400), rng.uniform(126, 129, 400))
    assert used == ["grid"]
    used.clear()
    spatial.nearest([37.0], [127.0], [37.5], [127.5])
    assert used == []  # 작으면 거리 행렬을 바로 계산한다


# ---------- 점-영역 ----------

def regions(*shapes):
    return [[[spatial.closed(r) for r in p] for p in shape] for shape in shapes]


def test_points_in_polygon_boundaries_and_holes():
    donut = regions([[SQUARE, HOLE]])
    pts = {"안": (0.5, 0.5), "구멍 안": (2, 2), "구멍 경계": (1, 2), "구멍 모서리": (1, 1), "바깥 경계": (0, 2), "바깥 모서리": (4, 4), "밖": (5, 5), "오른쪽": (3.5, 2), "빈 값": (np.nan, 2)}
    lat = np.array([p[1] for p in pts.values()])
    lon = np.array([p[0] for p in pts.values()])
    got = dict(zip(pts, spatial.assign(lat, lon, donut).tolist()))
    assert got == {"안": 0, "구멍 안": -1, "구멍 경계": 0, "구멍 모서리": 0, "바깥 경계": 0, "바깥 모서리": 0, "밖": -1, "오른쪽": 0, "빈 값": -1}


def test_points_in_multipolygon_and_first_region_wins():
    two = regions([[SQUARE], [[(10, 10), (12, 10), (12, 12), (10, 12), (10, 10)]]], [[[(3, 0), (8, 0), (8, 4), (3, 4), (3, 0)]]])  # 영역 0은 두 조각, 영역 1은 영역 0과 겹침
    lat, lon = np.array([2, 11, 3, 3.5, 11]), np.array([2, 11, 6, 3.5, 20])
    assert spatial.assign(lat, lon, two).tolist() == [0, 0, 1, 0, -1]  # (3.5, 3.5)는 둘 다에 있어 먼저 나온 영역 0, 먼 조각의 점도 영역 0
    assert spatial.assign([], [], two).tolist() == [] and spatial.assign([2.0], [2.0], []).tolist() == [-1]
    concave = regions([[[(0, 0), (4, 0), (4, 4), (2, 1), (0, 4), (0, 0)]]])  # 위쪽이 파인 모양(오목)
    assert spatial.assign(np.array([0.5]), np.array([1.0]), concave).tolist() == [0] and spatial.assign(np.array([3.5]), np.array([2.0]), concave).tolist() == [-1]


# ---------- TM 변환 ----------

def test_tm_epsg_5179_seoul_city_hall():
    lon, lat = tm.to_wgs84([953898], [1952036], 5179)
    assert lon[0] == pytest.approx(126.978, abs=1e-3) and lat[0] == pytest.approx(37.567, abs=1e-3)


@pytest.mark.parametrize("epsg", [5179, 5186, 5185, 5187, 5174, 5181])
def test_tm_round_trip_for_domestic_systems(epsg):
    x, y = tm.convert([126.98], [37.56], 4326, epsg)
    lon, lat = tm.to_wgs84(x, y, epsg)
    assert lon[0] == pytest.approx(126.98, abs=1e-6) and lat[0] == pytest.approx(37.56, abs=1e-6)
    assert epsg in tm.SYSTEMS


def test_tm_central_belt_origin_and_failures():
    lon, lat = tm.to_wgs84([200000, 200000], [600000, 500000], 5186)  # 중부원점 2010: 동경 127°, 북위 38°가 (200000, 600000)
    assert (lon[0], lat[0]) == (pytest.approx(127.0), pytest.approx(38.0)) and lat[1] < 38
    kept = tm.to_wgs84([1.0, 2.0], [3.0, 4.0], 4326)
    assert kept[0].tolist() == [1.0, 2.0]  # 이미 경위도면 그대로
    with pytest.raises(ValueError, match="알 수 없는 좌표계"):
        tm.convert([1], [2], 99999)
    nan = tm.to_wgs84([np.nan, 953898], [1952036, 1952036], 5179)
    assert np.isnan(nan[0][0]) and not np.isnan(nan[0][1])


def test_guess_systems_from_value_ranges():
    assert tm.guess(953000, 954000, 1951000, 1953000)[0] == 5179
    assert 5186 in tm.guess(190000, 210000, 440000, 460000)  # 중부원점 2010의 서울 근처
    assert tm.guess(9e6, 9.1e6, 9e6, 9.1e6) == []  # 한국 밖이면 후보 없음


# ---------- 좌표 컬럼 인식 ----------

def info(name, low, high, kind="float"):
    return {"name": name, "kind": kind, "min": low, "max": high}


def test_detect_names_and_ranges():
    found = detect.hints([info("store_lat", 33.1, 38.2), info("store_lng", 126.0, 129.5), info("이름", None, None, "string"), info("count", 0, 9, "int")])
    assert [(c["name"], c["role"], c["ok"]) for c in found["columns"]] == [("store_lat", "lat", True), ("store_lng", "lon", True)] and found["warnings"] == []
    assert found["pairs"] == [{"kind": "lonlat", "lat": "store_lat", "lon": "store_lng"}]
    for name, role in (("lat", "lat"), ("Latitude", "lat"), ("위도", "lat"), ("lon", "lon"), ("LONG", "lon"), ("longitude", "lon"), ("경도", "lon"), ("storeLat", "lat"), ("x", "x"), ("Y", "y"), ("x_coord", "x"), ("latte", None), ("pixel", None)):
        assert detect.role_of(name) == role, name


def test_detect_out_of_range_values_tell_why():
    found = detect.hints([info("위도", -95.5, 40), info("경도", 120, 250.0)])
    assert [c["ok"] for c in found["columns"]] == [False, False]
    assert "-90" in found["warnings"][0] and "-95.5" in found["warnings"][0] and "위도" in found["warnings"][0]
    assert "180" in found["warnings"][1] and "250" in found["warnings"][1]
    assert found["pairs"] == []  # 범위를 벗어난 쌍은 추천하지 않는다
    swapped = detect.hints([info("lat", 126.0, 129.0), info("lon", 35.0, 38.0)])
    assert "바뀌었거나" in swapped["warnings"][0] and swapped["warnings"][0].count("경도") >= 1


def test_detect_tm_pair_suggests_systems():
    found = detect.hints([info("x", 953000, 954500), info("y", 1951000, 1953500)])
    assert found["pairs"] == [{"kind": "tm", "x": "x", "y": "y", "epsg": [5179]}]
    assert "TM" in found["columns"][0]["note"] and found["warnings"] == []
    named = detect.hints([info("위도", 1951000, 1953500), info("경도", 953000, 954500)])  # 이름은 위경도인데 값이 미터
    assert named["pairs"][0]["kind"] == "tm" and "미터" in named["warnings"][0] and named["pairs"][0]["x"] == "경도"
    small = detect.hints([info("x", 126.9, 127.1), info("y", 37.4, 37.7)])
    assert small["pairs"] == [{"kind": "lonlat", "lat": "y", "lon": "x"}]  # x·y가 경위도 범위면 그대로 경도·위도


def test_prep_screen_epsg_choices_are_known_systems():
    """전처리 화면의 좌표계 목록(prep.html의 EPSG_LIST)은 geo/tm.py의 SYSTEMS 안의 번호만 쓴다(화면이 geo 패키지를 올리지 않으려 따로 적어 둔다)."""
    from conftest import ROOT
    import re
    html = (ROOT / "static" / "prep" / "prep.html").read_text(encoding="utf-8")
    listed = [int(n) for n in re.findall(r"\[(\d{4}), \"", html[html.index("const EPSG_LIST"):html.index("const EPSG_LIST") + 700])]
    assert listed and set(listed) <= set(tm.SYSTEMS) and listed[0] == 5179
