"""전처리의 좌표 단계(recipe.geo): TM 변환·가장 가까운 지점·점-영역이 새 컬럼으로 붙는지, 원격 DB에서는 이 PC 사본과 처리 용량 확인을 거치는지(sqlite를 원격으로 본다),
프로파일 응답에 좌표 컬럼 인식이 얹히는지. 데이터는 테스트 안에서 지어낸다."""
import json
import sqlite3

import polars as pl
import pytest
from lazy import ibis

from core import server
from geo import prep_geo
from prep import prep_expr

SQUARE = {"type": "Polygon", "coordinates": [[[126, 37], [128, 37], [128, 38], [126, 38], [126, 37]]]}
ISLAND = {"type": "Polygon", "coordinates": [[[126, 33], [127, 33], [127, 34], [126, 34], [126, 33]]]}
HOLED = {"type": "Polygon", "coordinates": [[[126, 35], [130, 35], [130, 36], [126, 36], [126, 35]], [[128, 35.25], [129, 35.25], [129, 35.75], [128, 35.75], [128, 35.25]]]}

POINTS = {"id": [1, 2, 3, 4, 5], "x": [953898.0, 953898.0, None, 960000.0, 953000.0], "y": [1952036.0, 1952036.0, 1952036.0, 1950000.0, 1951000.0],
          "lat": [37.5665, 35.1796, 10.0, None, 35.5], "lon": [126.978, 129.0756, 10.0, 127.0, 128.5]}
STATIONS = {"name": ["서울", "부산", "대구"], "slat": [37.5665, 35.1796, 35.8714], "slon": [126.978, 129.0756, 128.6014]}
REGIONS = {"name": ["서울권", "제주권", "경북"], "code": [11, 50, 47], "geometry": [json.dumps(g) for g in (SQUARE, ISLAND, HOLED)]}


@pytest.fixture
def con():
    c = ibis.duckdb.connect()
    for name, data in (("pts", POINTS), ("st", STATIONS), ("reg", REGIONS)):
        c.create_table(name, ibis.memtable(pl.DataFrame(data)))
    return c


def built(con, geo, **recipe):
    info = {}
    t, origin = prep_expr.build(con, {"base": "pts", "geo": geo, **recipe}, None, info)
    return t.order_by("id").to_polars(), origin, info


def test_tm_step_adds_lon_lat_columns_once_per_distinct_pair(con):
    df, origin, info = built(con, [{"op": "tm", "x": "x", "y": "y", "epsg": 5179}])
    assert df.columns[-2:] == ["경도", "위도"] and df["id"].to_list() == [1, 2, 3, 4, 5]
    assert df["경도"][0] == pytest.approx(126.978, abs=1e-3) and df["위도"][0] == pytest.approx(37.567, abs=1e-3)
    assert df["경도"][1] == df["경도"][0]  # 같은 좌표의 행은 같은 값
    assert df["경도"][2] is None and df["위도"][2] is None  # x가 빈 행은 변환하지 않는다
    assert info["geo"] == [{"op": "tm", "pairs": 3, "failed": 0, "epsg": 5179}]  # 서로 다른 (x, y)는 3쌍
    assert origin["경도"] == ("", True) and origin["위도"] == ("", True)  # 파생 컬럼으로 알린다
    renamed, *_ = built(con, [{"op": "tm", "x": "x", "y": "y", "epsg": 5186, "lon": "lon5186", "lat": "lat5186"}])
    assert renamed.columns[-2:] == ["lon5186", "lat5186"] and "경도" not in renamed.columns and renamed["lon5186"][0] != df["경도"][0]  # 새 컬럼 이름과 좌표계를 고를 수 있다


def test_nearest_step_picks_station_and_distance(con):
    step = {"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "slat", "ref_lon": "slon", "label": "name"}
    df, _, info = built(con, [step])
    assert df["가까운_지점"][0] == "서울" and df["가까운_지점"][1] == "부산" and df["가까운_지점"][3] is None  # 위도가 빈 행은 비어 있다
    assert df["가까운_거리km"][0] == pytest.approx(0, abs=1e-6) and df["가까운_거리km"][3] is None
    assert df["가까운_지점"][4] == "대구" and 0 < df["가까운_거리km"][4] < 100
    assert info["geo"][0]["matched"] == 4 and info["geo"][0]["reference"] == 3
    only, *_ = built(con, [{**step, "label": None, "dist": "거리"}])
    assert only.columns[-1] == "거리" and "가까운_지점" not in only.columns  # 이름 컬럼을 안 고르면 거리만


def test_region_step_attaches_attributes_with_holes_and_misses(con):
    df, _, info = built(con, [{"op": "region", "lat": "lat", "lon": "lon", "table": "reg", "take": ["name", "code"]}])
    assert df["영역_name"][0] == "서울권" and df["영역_code"][0] == 11
    assert df["영역_name"][1] == "경북"  # 부산(129.07, 35.18)은 경북 영역(구멍 밖)
    assert df["영역_name"][4] is None  # (35.5, 128.5)는 경북 영역의 구멍 안이라 어느 영역에도 없다
    assert df["영역_name"][2] is None and df["영역_name"][3] is None  # (10, 10)은 영역 밖, 위도가 빈 행은 계산하지 않는다
    assert info["geo"][0]["matched"] == 2 and info["geo"][0]["regions"] == 3
    edge, *_ = built(con, [{"op": "region", "lat": "lat", "lon": "lon", "table": "reg", "take": ["name"], "prefix": "구_"}], filters=[])
    assert "구_name" in edge.columns
    number, *_ = built(con, [{"op": "region", "lat": "lat", "lon": "lon", "table": "reg"}])
    assert number["영역_번호"].to_list() == [0, 2, None, None, None]


def test_region_from_a_geojson_file(con, tmp_path):
    path = tmp_path / "areas.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"구": "중구"}, "geometry": SQUARE}]}), encoding="utf-8")
    df, *_ = built(con, [{"op": "region", "lat": "lat", "lon": "lon", "path": str(path), "take": ["구"]}])
    assert df["영역_구"][0] == "중구" and df["영역_구"][1] is None


def test_steps_chain_and_keep_prep_derivation_working(con):
    steps = [{"op": "tm", "x": "x", "y": "y"}, {"op": "nearest", "lat": "위도", "lon": "경도", "table": "st", "ref_lat": "slat", "ref_lon": "slon", "label": "name"}]  # 앞 단계의 새 컬럼을 뒤 단계가 쓴다
    df, *_ = built(con, steps, formulas=[{"name": "합", "expr": "id * 2"}], drop=["합"])
    assert df["가까운_지점"][0] == "서울" and "합" not in df.columns
    assert df.columns[-4:] == ["경도", "위도", "가까운_거리km", "가까운_지점"]


def test_geo_step_errors_say_why(con):
    cases = [([{"op": "bogus"}], "op가"), ([{"op": "tm", "x": "id", "y": "nope"}], "비슷한|없습니다"), ([{"op": "tm", "x": "x", "y": "y", "epsg": 3}], "EPSG"),
             ([{"op": "tm", "x": "x", "y": "y", "lon": "id"}], "이미 있어"), ([{"op": "tm", "x": "x", "y": "y", "extra": 1}], "쓸 수 없는"),
             ([{"op": "nearest", "lat": "lat", "lon": "lon", "table": "nope", "ref_lat": "a", "ref_lon": "b"}], "테이블"),
             ([{"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "name", "ref_lon": "slon"}], "숫자 컬럼"),
             ([{"op": "region", "lat": "lat", "lon": "lon", "table": "st", "geometry": "name"}], "폴리곤을 찾지 못"),
             ([{"op": "region", "lat": "lat", "lon": "lon"}], "테이블을 고르세요"), ([{"op": "tm"}] * 11, "10개"),
             ([{"op": "tm", "x": "x", "y": "y", "lon": "a b"}], "새 컬럼 이름")]
    for geo, message in cases:
        with pytest.raises(ValueError, match=message):
            built(con, geo)


def test_too_many_distinct_coordinates_are_refused(con, monkeypatch):
    monkeypatch.setattr(prep_geo, "MAX_PAIRS", 2)
    with pytest.raises(ValueError, match="서로 다른 좌표가"):
        built(con, [{"op": "tm", "x": "x", "y": "y"}])


# ---------- 원격 DB: 이 PC 사본, 처리 용량 확인 ----------

@pytest.fixture
def remote(folder, tmp_path):
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table pts (id integer, lat real, lon real, x real, y real)")
    raw.executemany("insert into pts values (?, ?, ?, ?, ?)", list(zip(POINTS["id"], POINTS["lat"], POINTS["lon"], POINTS["x"], POINTS["y"])))
    raw.execute("create table st (name text, slat real, slon real)")
    raw.executemany("insert into st values (?, ?, ?)", list(zip(*STATIONS.values())))
    raw.execute("create table reg (name text, code integer, geometry text)")
    raw.executemany("insert into reg values (?, ?, ?)", list(zip(*REGIONS.values())))
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    yield c
    from core import held
    held.store["items"].clear()
    c.post("/api/disconnect", json={})


def test_remote_geo_goes_through_the_scan_gate_and_computes_on_the_pc_copy(remote, monkeypatch):
    seen = []

    def gate(body, make_sql, rows=None):
        seen.append(make_sql()[0])  # 어떤 조회를 읽으려는지 모아 보고 통과시킨다
        return None

    monkeypatch.setattr(server, "scan_gate", gate)
    recipe = {"base": "pts", "geo": [{"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "slat", "ref_lon": "slon", "label": "name"},
                                    {"op": "region", "lat": "lat", "lon": "lon", "table": "reg", "take": ["name"]}]}
    got = remote.post("/api/preview", json=recipe).get_json()
    assert got["local"] is True  # 좌표 단계가 있으면 사본(이 PC)에서 계산한다
    assert len(seen) == 3 and "pts" in seen[0] and "slat" in seen[1] and "geometry" in seen[2]  # 기준 테이블 사본, 지점 테이블, 영역 테이블 순으로 처리 용량 확인
    assert "slon" in seen[1] and "SELECT" in seen[1].upper() and "code" not in seen[2]  # 쓸 컬럼만 읽는 조회
    columns = got["rows"]["columns"]
    rows = {r["id"]: r for r in got["rows"]["rows"]}
    assert columns[-3:] == ["가까운_거리km", "가까운_지점", "영역_name"] and rows[1]["가까운_지점"] == "서울" and rows[1]["영역_name"] == "서울권"
    assert got["info"]["geo"][0]["op"] == "nearest" and got["info"]["geo"][1]["matched"] == 2


def test_remote_asks_before_reading_reference_tables(remote, monkeypatch):
    held_keys = []
    from core import held

    def gate(body, make_sql, rows=None):
        sql = make_sql()[0]
        held_keys.append(sql)
        return server.jsonify(needs_confirm=True, text="큼") if "slat" in sql else None  # 지점 테이블 읽기만 확인이 필요하다고

    monkeypatch.setattr(server, "scan_gate", gate)
    recipe = {"base": "pts", "geo": [{"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "slat", "ref_lon": "slon"}]}
    for url, body in (("/api/preview", recipe), ("/api/dupes", {**recipe, "dedupe": ["id"]}), ("/api/analyze", {"recipe": recipe, "how": {}, "full": True})):
        r = remote.post(url, json=body)
        assert r.get_json().get("needs_confirm") is True, url
    held.store["items"].clear()


def test_remote_reference_tables_are_fetched_once_per_connection(remote, monkeypatch):
    """같은 연결·테이블·컬럼의 지점·영역 표는 한 번 받으면 미리보기를 거듭해도 다시 묻거나 받지 않고, 연결을 바꾸면 버린다."""
    from core import held
    asked = []

    def gate(body, make_sql, rows=None):
        asked.append(make_sql()[0])
        return None

    monkeypatch.setattr(server, "scan_gate", gate)
    recipe = {"base": "pts", "geo": [{"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "slat", "ref_lon": "slon", "label": "name"}]}
    first = remote.post("/api/preview", json=recipe).get_json()
    n = len(asked)
    assert n == 2 and any("slat" in q for q in asked)  # 기준 테이블 사본 + 지점 표
    assert [k for k in held.store["items"] if k[0] == "geo"] == [("geo", None, "st", ("slat", "slon", "name"))]
    fetched = []
    real = held.fetch
    monkeypatch.setattr(held, "fetch", lambda *a, **k: (fetched.append(a[0]), real(*a, **k))[1])
    second = remote.post("/api/preview", json=recipe).get_json()
    assert len(asked) == n and not fetched  # 두 번째는 묻지도 받지도 않는다
    assert second["rows"]["rows"] == first["rows"]["rows"]
    assert remote.post("/api/disconnect", json={}).status_code == 200 and not held.store["items"]


def test_remote_confirm_popup_comes_once_for_reference_tables(remote, monkeypatch):
    """처리 용량이 기준을 넘는 지점 표도 한 번 확인(confirmed)해 받으면, 다음 미리보기에서는 다시 묻지 않는다."""
    from core import scan_size
    monkeypatch.setattr(scan_size, "limit_check", lambda con, sql, opened=None, dialect=None, config=None, rows=None: {"over": "slat" in sql, "blocked": False, "unknown": False, "bytes": 1, "limit": 0, "text": "큼"})
    recipe = {"base": "pts", "geo": [{"op": "nearest", "lat": "lat", "lon": "lon", "table": "st", "ref_lat": "slat", "ref_lon": "slon"}]}
    assert remote.post("/api/preview", json=recipe).get_json().get("needs_confirm") is True
    assert "rows" in remote.post("/api/preview", json={**recipe, "confirmed": True}).get_json()
    assert "rows" in remote.post("/api/preview", json=recipe).get_json()  # 확인 없이도 통과(이미 받아 둔 표)


def test_profile_response_carries_coordinate_hints(remote):
    got = remote.post("/api/profile", json={"base": "pts", "sample": 1000}).get_json()
    geo = got["geo"]
    assert {c["name"]: c["role"] for c in geo["columns"]} == {"lat": "lat", "lon": "lon", "x": "x", "y": "y"}
    assert {"kind": "lonlat", "lat": "lat", "lon": "lon"} in geo["pairs"] and any(p["kind"] == "tm" and p["x"] == "x" for p in geo["pairs"])
    tm_pair = next(p for p in geo["pairs"] if p["kind"] == "tm")
    assert tm_pair["epsg"] and tm_pair["epsg"][0] in (5179, 5186, 5174, 5181, 5185, 5187)
