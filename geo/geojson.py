"""GeoJSON 읽기. Feature마다 한 행: 속성은 컬럼으로, 그리고 geom_type(도형 종류), center_lon·center_lat(중심), min_lon·min_lat·max_lon·max_lat(범위),
geometry(도형 원본, JSON 글자)를 더한다. 파일의 crs가 WGS84가 아니면 경도·위도로 바꾼 값을 쓴다(geo.tm, pyproj).
변경선을 넘는 도형은 min_lon > max_lon(예: 178, -179)으로 적는다(in_box와 같은 규칙). 표준 json과 Polars만 쓴다. 중심은 폴리곤이면 겉 고리에서 구멍을 뺀 면적 무게중심, 그 밖(점·선)은 꼭짓점 평균이다."""
import json
import math
import re
from pathlib import Path

from core import monitor

DATELINE_SPAN = 90  # 변경선을 건너 모인 도형으로 보는 경도 범위의 상한(도)
GEO_COLUMNS = ("geom_type", "center_lon", "center_lat", "min_lon", "min_lat", "max_lon", "max_lat", "geometry")
NUMBER_COLUMNS = GEO_COLUMNS[1:7]
GEOMETRY_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection")
EPSG = re.compile(r"EPSG[:/]+(?:[\d.]*:)?(\d+)", re.I)
MAX_BYTES = 512 * 1024 * 1024  # 이보다 큰 GeoJSON은 읽지 않는다(통째로 메모리에 올리므로)


def is_geojson(path):
    """파일 앞부분에 FeatureCollection(또는 Feature)이 보이는지. .json 파일이 GeoJSON인지 가릴 때 쓴다."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", "ignore")
    except OSError:
        return False
    return ('"FeatureCollection"' in head and '"features"' in head) or ('"Feature"' in head and '"geometry"' in head and head.lstrip("﻿ \r\n\t").startswith("{"))


def crs_of(doc):
    """문서의 crs 이름에서 EPSG 번호(없거나 CRS84면 4326)."""
    crs = doc.get("crs") if isinstance(doc, dict) else None
    name = ((crs or {}).get("properties") or {}).get("name") if isinstance(crs, dict) else None
    if not isinstance(name, str):
        return 4326
    if "CRS84" in name.upper():
        return 4326
    found = EPSG.search(name)
    if not found:
        raise ValueError(f"좌표계(crs) '{name}'를 알 수 없어요. EPSG 번호가 든 이름이어야 합니다(예: urn:ogc:def:crs:EPSG::5179).")
    return int(found.group(1))


def leaves(coords):
    """좌표 묶음의 위치 [x, y, …]들을 차례로."""
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords or []:
            yield from leaves(c)


def reproject(coords, epsg):
    """좌표 묶음 전체를 epsg에서 경도·위도로(구조는 그대로)."""
    from geo import tm
    if coords and isinstance(coords[0], (int, float)):
        lon, lat = tm.to_wgs84([coords[0]], [coords[1]], epsg)
        return [float(lon[0]), float(lat[0])]
    if coords and coords[0] and isinstance(coords[0][0], (int, float)):  # 위치 목록(고리·선)을 한 번에
        lon, lat = tm.to_wgs84([c[0] for c in coords], [c[1] for c in coords], epsg)
        return [[float(a), float(b)] for a, b in zip(lon, lat)]
    return [reproject(c, epsg) for c in coords or []]


def shape(geom, epsg):
    """geometry 객체를 경도·위도로 바꾼 사본(GeometryCollection은 안의 도형까지). 도형이 아니면 None."""
    if not isinstance(geom, dict):
        return None
    if geom.get("type") == "GeometryCollection":
        return {"type": "GeometryCollection", "geometries": [shape(g, epsg) for g in geom.get("geometries") or [] if isinstance(g, dict)]}
    if epsg == 4326:
        return geom
    return {**geom, "coordinates": reproject(geom.get("coordinates"), epsg)}


def parts(geom):
    """도형 하나를 (종류, 좌표 묶음)들로 편다: GeometryCollection은 안의 도형들."""
    if geom.get("type") == "GeometryCollection":
        return [p for g in geom.get("geometries") or [] if isinstance(g, dict) for p in parts(g)]
    return [(geom.get("type"), geom.get("coordinates"))]


def ring_centroid(ring):
    """고리 하나의 (면적, 무게중심 x, y). 점이 모자라거나 면적이 0이면 면적 0."""
    area = cx = cy = 0.0
    for (x0, y0, *_), (x1, y1, *_) in zip(ring, ring[1:]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area /= 2
    return (area, cx / (6 * area), cy / (6 * area)) if area else (0.0, 0.0, 0.0)


def crosses_dateline(xs):
    """경도들이 날짜 변경선을 사이에 두고 모여 있는지: 경도를 정렬했을 때 안쪽의 가장 큰 빈 구간이 270도를 넘을 때(= 변경선을 건너 모인 범위가 90도 미만).
    -180과 180은 같은 경도로 본다(±180에서 자른 도형은 둘 다 가지며, 세계 상자는 180 하나로 줄어 아니다). 성긴 도형, -100→100 같은 넓은 선도 아니다. NaN·inf는 뺀다."""
    import numpy as np
    xs = [x for x in xs if math.isfinite(x)]  # NaN·inf는 뺀다
    if not xs or (max(xs) - min(xs) <= 360 - DATELINE_SPAN and -180 not in xs):  # 범위가 270도 이하면 빈 구간도 270도를 넘지 못한다(작은 도형 수만 개에 numpy를 부르지 않게)
        return False
    v = np.array(xs, dtype=float)
    v = np.unique(np.where(v == -180, 180.0, v))
    if len(v) < 2:
        return False
    return bool(np.diff(v).max() > 360 - DATELINE_SPAN)


def unwrapped(pieces):
    """parts의 (종류, 좌표 묶음)들을 날짜 변경선에 걸쳐 있으면 음수 경도에 360을 더한 사본으로(아니면 그대로)."""
    if not crosses_dateline(p[0] for kind, coords in pieces for p in leaves(coords)):
        return pieces

    def move(coords):
        if coords and isinstance(coords[0], (int, float)):
            return [coords[0] + 360 if coords[0] < 0 else coords[0], *coords[1:]]
        return [move(c) for c in coords or []]

    return [(kind, move(coords)) for kind, coords in pieces]


def center(geom):
    """도형의 중심 (경도, 위도). 폴리곤이 있으면 면적 무게중심(구멍은 뺌), 아니면 꼭짓점 평균. 비었으면 (None, None).
    날짜 변경선에 걸쳐 있으면 음수 경도에 360을 더해 구한 뒤 180을 넘으면 360을 되돌린다."""
    pieces = unwrapped(parts(geom))
    total = sx = sy = 0.0
    for kind, coords in pieces:
        polys = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
        for poly in polys:
            for i, ring in enumerate(poly or []):
                area, x, y = ring_centroid(ring)
                weight = abs(area) * (1 if i == 0 else -1)
                total, sx, sy = total + weight, sx + weight * x, sy + weight * y
    if total > 0:
        return back(sx / total), sy / total
    points = [p for kind, coords in pieces for p in leaves(coords)]
    if not points:
        return None, None
    return back(sum(p[0] for p in points) / len(points)), sum(p[1] for p in points) / len(points)


def back(lon):
    return lon - 360 if lon > 180 else lon


def bounds(geom):
    """도형의 범위 (min_lon, min_lat, max_lon, max_lat). 비었으면 모두 None. 날짜 변경선에 걸쳐 있으면 경도는 170~190처럼 180을 넘는 값으로 낸다(서쪽 점에 360을 더함)."""
    points = [p for kind, coords in unwrapped(parts(geom)) for p in leaves(coords)]
    if not points:
        return None, None, None, None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def scalar(value):
    """속성 값을 표 칸에 넣을 값으로: 글자·정수·실수·참거짓은 그대로, 중첩(객체·목록)은 JSON 글자."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def features(doc):
    """문서에서 Feature 목록. FeatureCollection, Feature 하나, 도형 하나를 모두 받는다."""
    if not isinstance(doc, dict):
        raise ValueError("GeoJSON은 객체({ … })여야 합니다.")
    kind = doc.get("type")
    if kind == "FeatureCollection":
        found = doc.get("features")
        if not isinstance(found, list):
            raise ValueError("FeatureCollection에 features 목록이 없습니다.")
        return [f for f in found if isinstance(f, dict)]
    if kind == "Feature":
        return [doc]
    if kind in GEOMETRY_TYPES:
        return [{"type": "Feature", "properties": {}, "geometry": doc}]
    raise ValueError("GeoJSON이 아닙니다(type이 FeatureCollection·Feature·도형이어야 합니다).")


def column_values(name, values):
    """한 컬럼 값 목록의 Polars 시리즈. 섞인 타입(숫자와 글자)은 글자로 맞춘다."""
    import polars as pl
    try:
        return pl.Series(name, values)
    except Exception:
        if all(v is None or (type(v) in (int, float)) for v in values):  # 정수와 실수가 섞이면(1, 2.5, 3) 실수로
            return pl.Series(name, values, dtype=pl.Float64)
        return pl.Series(name, [None if v is None else v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v) for v in values], dtype=pl.String)


def table(doc):
    """GeoJSON 문서 → Polars 표(Feature마다 한 행)."""
    import polars as pl
    epsg = crs_of(doc)
    rows, props = [], {}
    for f in features(doc):
        geom = shape(f.get("geometry"), epsg)
        lon, lat = center(geom) if geom else (None, None)
        low = bounds(geom) if geom else (None,) * 4
        xs = [p[0] for kind, coords in parts(geom) for p in leaves(coords)] if geom else []
        moved = bool(xs) and min(xs) < 0 and crosses_dateline(xs)  # unwrapped가 실제로 점을 옮긴 때
        edge = lambda v: None if v is None else back(v) if moved else v  # 변경선을 넘어 점을 옮긴 때만 -180~180 안으로(min_lon > max_lon, in_box와 같은 규칙). 0~360 경도 파일은 그대로
        row = {"geom_type": geom.get("type") if geom else None, "center_lon": lon, "center_lat": lat, "min_lon": edge(low[0]), "min_lat": low[1], "max_lon": edge(low[2]), "max_lat": low[3],
               "geometry": json.dumps(geom, ensure_ascii=False, separators=(",", ":")) if geom else None}
        mine = f.get("properties") if isinstance(f.get("properties"), dict) else {}
        if f.get("id") is not None:
            mine = {"feature_id": f["id"], **mine}
        for key, value in mine.items():
            name = key if key not in GEO_COLUMNS else f"{key}_속성"
            props.setdefault(name, None)
            row[name] = scalar(value)
        rows.append(row)
    columns = []
    for name in [*props, *GEO_COLUMNS]:
        s = column_values(name, [r.get(name) for r in rows])
        columns.append(s.cast(pl.Float64) if name in NUMBER_COLUMNS else s.cast(pl.String) if name in ("geom_type", "geometry") else s)
    return pl.DataFrame(columns)


CACHE = {}  # 마지막으로 읽은 파일 하나 {(경로, 수정 시각, 크기): 표}. 미리보기마다 큰 파일을 다시 읽지 않게


def read(path):
    """GeoJSON 파일 → Polars 표. 파일이 너무 크거나 GeoJSON이 아니면 이유와 함께 ValueError. 같은 파일(경로·수정 시각·크기)은 다시 읽지 않는다."""
    p = Path(path)
    stat = p.stat()
    key = (str(p), stat.st_mtime_ns, stat.st_size)
    if key not in CACHE:
        CACHE.clear()
        CACHE[key] = read_file(p)
    return CACHE[key]


def read_file(p):
    limit = min(MAX_BYTES, monitor.bound_bytes() // 6)  # 작업 메모리 상한(없으면 지금 쓸 수 있는 메모리)의 6분의 1이 더 작으면 그것(파싱에 파일의 약 6배 메모리가 든다)
    if p.stat().st_size > limit:
        raise ValueError(f"{p.name}이 {limit // 1024 // 1024}MB보다 커서 읽지 않았어요. 영역을 나누어 쓰세요.")
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise ValueError(f"{p.name}을 JSON으로 읽을 수 없어요({e}).") from None
    try:
        return table(doc)
    except ValueError as e:
        raise ValueError(f"{p.name}: {e}") from None
    except (RecursionError, IndexError, TypeError):  # coordinates가 글자·빈 목록 등 숫자 목록이 아닐 때
        raise ValueError(f"{p.name}: 좌표가 올바르지 않아요. 도형의 coordinates는 [경도, 위도] 숫자 목록(또는 그 목록의 목록)이어야 합니다.") from None


def parse_geometry(text):
    """geometry 컬럼의 JSON 글자 → 도형 객체(비었거나 깨졌으면 None)."""
    if not isinstance(text, str) or not text:
        return None
    try:
        geom = json.loads(text)
    except ValueError:
        return None
    return geom if isinstance(geom, dict) else None


def polygons(geom):
    """도형 안의 폴리곤들 [[겉 고리, 구멍…]…](점·선은 건너뜀). 고리는 [[x, y]…]."""
    if not geom:
        return []
    out = []
    for kind, coords in parts(geom):
        if kind == "Polygon" and coords:
            out.append(coords)
        elif kind == "MultiPolygon":
            out += [c for c in coords or [] if c]
    return out
