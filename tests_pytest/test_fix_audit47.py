"""감사 47(지도): crs 없는 TM 좌표 GeoJSON, Feature id와 같은 이름의 속성, 픽셀 x·y를 경위도로 추천하지 않기, 글자 값 컬럼의 합계. 값은 여기서 지어낸다."""
import pytest

from geo import detect, geojson
from test_geo_dash import GEO, db, geo_card  # noqa: F401  db는 fixture

SQUARE = lambda x0, y0, x1, y1: {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def test_tm_coordinates_without_crs_refused():
    doc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": SQUARE(953401, 1951532, 954401, 1952532)}]}
    with pytest.raises(ValueError, match="EPSG:5179"):
        geojson.table(doc)
    assert geojson.table({"type": "Feature", "properties": {}, "geometry": SQUARE(350, 10, 355, 20)}).height == 1  # 0~360 경도는 받는다(오류가 아니다)


def test_feature_id_kept_beside_same_named_property():
    t = geojson.table({"type": "Feature", "id": "X", "properties": {"feature_id": "Y"}, "geometry": {"type": "Point", "coordinates": [127, 37]}})
    assert t["feature_id"][0] == "Y" and t["feature_id_id"][0] == "X"


def test_small_xy_not_suggested_as_lonlat():
    cols = [{"name": "x", "kind": "int", "min": 0, "max": 800}, {"name": "y", "kind": "int", "min": 0, "max": 600}]
    assert detect.hints(cols)["pairs"] == []
    ok = [{"name": "x", "kind": "float", "min": 126.0, "max": 127.5}, {"name": "y", "kind": "float", "min": 37.0, "max": 37.7}]
    assert detect.hints(ok)["pairs"] == [{"kind": "lonlat", "lat": "y", "lon": "x"}]


def test_sum_of_text_value_column_says_why(db):  # noqa: F811
    c, _ = db
    r = c.post("/api/dash/card/compute", json={"card": geo_card(geo={**GEO, "value": "d"})}).get_json()
    assert "숫자 컬럼" in r["error"]
