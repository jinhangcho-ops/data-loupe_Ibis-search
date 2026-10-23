"""대시보드 지도 카드(geomap)의 값: GeoJSON 영역 표와 점 표(읽어 온 Polars 표)로 카드에 저장할 작은 값을 만든다. 읽기는 하지 않는다(dash_calc가 읽어 넘긴다).
- 영역: 이름·값·고리들(경도, 위도를 번갈아 놓은 숫자 목록). 카드 크기 상한을 지키려고 꼭짓점을 MAX_VERTICES개로 줄이고 좌표는 소수 4자리(약 11m)로 둔다.
- 값: 영역 테이블의 값 컬럼(region_value)이 있으면 그것, 없으면 점을 영역에 넣어(spatial.assign) 영역마다 count·sum·mean·min·max(점의 값 컬럼)로 구한다.
- 점: MAX_DRAW개까지 고르게 뽑은 [경도, 위도]. bounds는 영역(없으면 점)의 범위 [최소 경도, 최소 위도, 최대 경도, 최대 위도].
지도는 확대·이동 없이 이 범위에 맞춘 한 장이다(그리는 쪽은 static/geo/geomap.js)."""
import math

import numpy as np

from geo import geojson, spatial

MAX_REGIONS = 500
MAX_VERTICES = 6000
MAX_DRAW = 2000
MAX_POINT_ROWS = 200_000  # 영역에 넣으려고 읽는 점 행 수
DIGITS = 4


def thinned(ring, total, cap):
    """꼭짓점 합(total)이 cap을 넘으면 고리를 몫만큼 고르게 솎는다(처음과 끝은 남겨 닫힌 모양을 지킨다). 넘지 않으면 그대로."""
    if total <= cap:
        return ring
    keep = max(4, int(cap * len(ring) / total))
    return ring if len(ring) <= keep else ring[np.unique(np.linspace(0, len(ring) - 1, keep).round().astype(int))]


def flat(ring):
    return [round(float(v), DIGITS) for v in ring.reshape(-1)]


def region_rings(frame, geometry):
    """영역 표의 행마다 (폴리곤 목록, 고리 배열 목록). 폴리곤은 [겉 고리, 구멍…] 배열 목록."""
    out = []
    for text in frame[geometry].to_list():
        polys = [[spatial.closed(r) for r in poly] for poly in geojson.polygons(geojson.parse_geometry(text))]
        out.append(polys)
    return out


def reduce_values(values, agg):
    """한 영역 점들의 값 목록 → 값 하나(없으면 None)."""
    if agg == "count":
        return len(values)
    real = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not real:
        return None
    return {"sum": sum, "mean": lambda v: sum(v) / len(v), "min": min, "max": max}[agg](real)


def build(regions, points, geo):
    """regions: 영역 Polars 표(또는 None), points: 점 Polars 표(열 lat, lon[, value], 또는 None), geo: 카드의 view.geo. 카드에 저장할 값 dict를 돌려준다."""
    notes = []
    agg = geo.get("agg") or "count"
    shapes, names, values = [], [], []
    if regions is not None:
        if len(regions) > MAX_REGIONS:
            notes.append(f"영역이 많아 앞 {MAX_REGIONS}개만 그렸어요.")
            regions = regions.head(MAX_REGIONS)
        geometry = geo.get("geometry") or "geometry"
        shapes = region_rings(regions, geometry)
        names = [("" if v is None else str(v)) for v in regions[geo["name"]].to_list()]
        values = regions[geo["region_value"]].to_list() if geo.get("region_value") else [None] * len(shapes)
    lat = lon = np.empty(0)
    got = None
    if points is not None and len(points):
        lat, lon = points["lat"].cast(float).to_numpy(), points["lon"].cast(float).to_numpy()
        got = points["value"].to_list() if "value" in points.columns else None
        if len(points) > MAX_POINT_ROWS:
            notes.append(f"점이 많아 앞 {MAX_POINT_ROWS:,}행만 썼어요.")
    assigned = 0
    if shapes and len(lat) and not geo.get("region_value"):
        index = spatial.assign(lat, lon, shapes)
        assigned = int((index >= 0).sum())
        groups = {}
        for i, r in enumerate(index.tolist()):
            if r >= 0:
                groups.setdefault(r, []).append(None if got is None else got[i])
        values = [reduce_values(groups[r], agg) if r in groups else (0 if agg == "count" else None) for r in range(len(shapes))]
        outside = int((np.isfinite(lat) & np.isfinite(lon)).sum()) - assigned
        if outside:
            notes.append(f"점 {outside:,}개는 어느 영역에도 없었어요.")
    if not shapes and not len(lat):
        raise ValueError("그릴 영역도 점도 없어요. 영역 테이블의 도형(geometry) 컬럼과 점의 위도·경도 컬럼을 확인하세요.")
    all_rings = [r for polys in shapes for poly in polys for r in poly]
    total = sum(len(r) for r in all_rings)
    if total > MAX_VERTICES:
        notes.append(f"꼭짓점이 많아 {MAX_VERTICES:,}개 안팎으로 줄여 그렸어요.")
    out_regions = [{"name": name, "value": value, "rings": [flat(thinned(r, total, MAX_VERTICES)) for poly in polys for r in poly]} for polys, name, value in zip(shapes, names, values)]
    ok = np.isfinite(lat) & np.isfinite(lon)
    drawn = np.flatnonzero(ok)
    if len(drawn) > MAX_DRAW:
        drawn = drawn[np.unique(np.linspace(0, len(drawn) - 1, MAX_DRAW).round().astype(int))]
        notes.append(f"점이 많아 고르게 {MAX_DRAW:,}개만 찍었어요.")
    dots = [[round(float(lon[i]), DIGITS), round(float(lat[i]), DIGITS)] for i in drawn] if geo.get("points", True) else []
    xs = [v for r in all_rings for v in r[:, 0]] or [float(v) for v in lon[ok]]
    ys = [v for r in all_rings for v in r[:, 1]] or [float(v) for v in lat[ok]]
    bounds = [round(float(min(xs)), DIGITS), round(float(min(ys)), DIGITS), round(float(max(xs)), DIGITS), round(float(max(ys)), DIGITS)]
    label = "영역 값" if geo.get("region_value") else {"count": "점 수", "sum": f"{geo.get('value', '')} 합계", "mean": f"{geo.get('value', '')} 평균", "min": f"{geo.get('value', '')} 최소", "max": f"{geo.get('value', '')} 최대"}[agg]
    return {"values": {"regions": out_regions, "points": dots, "bounds": bounds, "label": label.strip(), "total_points": int(ok.sum()), "assigned": assigned}, "note": " ".join(notes)}
