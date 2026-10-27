"""좌표 문장 틀 48~50({영역}별 {값}, 기준점 반경 안, 격자별 값): 요청으로 만들어지고 실제로 돌아 손으로 센 값과 맞는다.
places: 서울시청 근처 세 곳(id 1~3, 1·2번은 시청에서 1km 안, 3번은 약 1.9km), 부산 둘(4·5), 경기 하나(6). gu는 영역 이름 컬럼."""
import duckdb
import pytest

from query import sentence_exec as ex
from query import sentence_run as sr
from query import sentences
from core import server

COLS = {"id": "num", "lat": "num", "lon": "num", "amt": "num", "gu": "text"}
BASE = {"base": "places", "joins": [], "periods": [], "conditions": [], "calcs": []}
ROWS = [(1, 37.5665, 126.978, 10.0, "A"), (2, 37.5700, 126.980, 20.0, "A"), (3, 37.5800, 126.990, 30.0, "B"), (4, 35.1796, 129.0756, 40.0, "C"),
        (5, 35.1800, 129.0760, 50.0, "C"), (6, 37.6000, 127.100, 60.0, "B")]


@pytest.fixture
def c(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query import flow_app  # noqa: F401
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table places (id integer, lat double, lon double, amt double, gu varchar)")
    raw.executemany("insert into places values (?, ?, ?, ?, ?)", ROWS)
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def go(tid, slots):
    return ex.execute_compiled(ex.prepare(tid, slots, BASE, COLS))


def pairs(done, key, value):
    return {r[key]: r[value] for r in done["rows"]}


def test_templates_exist_with_the_expected_slots():
    assert [t["name"] for t in sentences.EXPLORE[-3:]] == ["영역별 집계", "기준점 반경 안 집계", "격자 칸별 집계"]
    assert sentences.get("e48")["text"].startswith("{영역}별 {값}")
    assert {s["name"] for s in sentences.get("e49")["slots"]} == {"위도", "경도", "기준 위도", "기준 경도", "반경", "값", "집계"}
    assert sentences.get("e50")["uses"] == ["grid_id", "grid_lat", "grid_lon"] and sentences.get("e49")["uses"] == ["within_km"]


def test_region_sentence_groups_by_region_column(c):
    done = go("e48", {"영역": "gu", "값": "amt", "집계": "sum"})
    assert pairs(done, "gu", "전체 합계") == {"A": 30.0, "B": 90.0, "C": 90.0}
    counted = go("e48", {"영역": "gu", "집계": "count"})  # 개수는 값 칸이 없어도 된다
    assert pairs(counted, "gu", "전체 개수") == {"A": 2, "B": 2, "C": 2}
    assert "[좌표]" in " ".join(sr.compile("e48", {"영역": "gu", "집계": "count"}, BASE, COLS)["notes"])


def test_radius_sentence_makes_a_near_condition_and_counts_rows_inside(c):
    slots = {"위도": "lat", "경도": "lon", "기준 위도": 37.5665, "기준 경도": 126.978, "반경": 5, "값": "amt", "집계": "sum"}
    made = sr.compile("e49", slots, BASE, COLS)
    assert made["ok"] and made["body"]["conditions"] == ["near lat lon 37.5665 126.978 5"]
    assert go("e49", slots)["rows"][0]["전체 합계"] == 60.0  # 1·2·3번
    assert go("e49", {**slots, "반경": 1})["rows"][0]["전체 합계"] == 30.0  # 1·2번
    assert go("e49", {**slots, "반경": 1, "집계": "count", "값": None})["rows"][0]["전체 개수"] == 2
    assert go("e49", {**slots, "기준 위도": 35.18, "기준 경도": 129.07, "반경": 3})["rows"][0]["전체 합계"] == 90.0  # 부산 둘
    for bad, message in (({**slots, "반경": "멀리"}, "숫자"), ({**slots, "값": None}, "값")):
        got = sr.compile("e49", bad, BASE, COLS)
        assert not got["ok"] and message in got["reason"], got


def test_grid_sentence_adds_grid_column_and_groups(c):
    slots = {"위도": "lat", "경도": "lon", "칸 크기": 0.1, "값": "amt", "집계": "sum"}
    made = sr.compile("e50", slots, BASE, COLS)
    assert made["ok"] and "grid_id(col('lat'), col('lon'), 0.1)" in made["body"]["calcs"][0]
    done = go("e50", slots)
    assert pairs(done, "격자", "전체 합계") == {"375_1269": 60.0, "351_1290": 90.0, "376_1271": 60.0}  # 칸 번호 = 내림(좌표 ÷ 0.1)
    fine = go("e50", {**slots, "칸 크기": 0.01, "집계": "count", "값": None})
    assert len(fine["rows"]) == 6 and {r["전체 개수"] for r in fine["rows"]} == {1}


def test_geo_conditions_work_in_search_lines_and_formulas(c):
    rows = c.post("/api/query", json={**BASE, "conditions": ["near lat lon 37.5665 126.978 1"], "limit": 10}).get_json()["rows"]["rows"]
    assert sorted(r["id"] for r in rows) == [1, 2]
    box = c.post("/api/query", json={**BASE, "conditions": ["box lat lon 35 129 36 130"], "limit": 10}).get_json()["rows"]["rows"]
    assert sorted(r["id"] for r in box) == [4, 5]
    calc = c.post("/api/query", json={**BASE, "calcs": ["식 round(haversine(lat, lon, 37.5665, 126.978), 1) 이름 거리"], "sorts": ["id"], "limit": 10}).get_json()["rows"]["rows"]
    assert calc[0]["거리"] == 0.0 and calc[3]["거리"] == pytest.approx(325.1, abs=0.2)  # 서울시청에서 부산 약 325km
    assert sr.formula_of("near lat lon 37.5 127 3") == "within_km(col('lat'), col('lon'), 37.5, 127, 3)"
