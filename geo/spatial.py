"""이 PC에서 numpy로 하는 좌표 계산: 가장 가까운 지점, 점을 영역(폴리곤)에 넣기.
가장 가까운 지점은 거리 행렬을 묶음씩 계산하고(작을 때), 지점이 많으면 0.1도 격자로 후보를 줄인다(가까운 칸부터 넓혀 가며, 이긴 거리가 칸 밖의 최소 거리보다 짧아지면 멈춘다).
점-영역은 영역마다 범위 상자로 먼저 거르고 레이캐스팅(교차 수)으로 안팎을 가린다. 경계 위의 점은 안으로 본다(겉 고리 경계도, 구멍 경계도)."""
import numpy as np

from geo.funcs import EARTH_KM, KM_PER_DEGREE

BLOCK = 2_000_000  # 한 번에 만드는 거리·교차 행렬의 칸 수
CELL = 0.1  # 후보를 줄이는 격자 칸(도)
GRID_FROM = 200_000  # 점 수 × 지점 수가 이보다 크고 지점이 GRID_MIN개 넘을 때 격자를 쓴다
GRID_MIN = 256
TOLERANCE = 1e-12  # 경계 위인지 볼 때의 오차


def haversine(lat1, lon1, lat2, lon2):
    """numpy 배열 사이의 큰 원 거리(km). geo.funcs.haversine과 같은 식."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def brute(lat, lon, rlat, rlon):
    """모든 지점과 견주는 가장 가까운 지점: (지점 번호, 거리 km). 점을 묶음으로 나눠 거리 행렬이 BLOCK칸을 넘지 않게 한다."""
    n = len(lat)
    index, dist = np.empty(n, dtype="int64"), np.empty(n)
    step = max(1, BLOCK // max(len(rlat), 1))
    for s in range(0, n, step):
        d = haversine(lat[s:s + step, None], lon[s:s + step, None], rlat[None, :], rlon[None, :])
        best = d.argmin(axis=1)
        index[s:s + step], dist[s:s + step] = best, d[np.arange(len(best)), best]
    return index, dist


def gridded(lat, lon, rlat, rlon):
    """격자로 후보를 줄인 가장 가까운 지점(brute와 같은 값). 점이 속한 칸에서 r칸 안의 지점만 견주고, 이긴 거리가 칸 밖의 최소 거리(위도 방향, 경도 방향 중 짧은 쪽)
    이하이면 확정한다. 아니면 r을 두 배로 넓히고, 칸이 너무 많아지면 지점 전부와 견준다."""
    ry, rx = np.floor(rlat / CELL).astype("int64"), np.floor(rlon / CELL).astype("int64")
    order = np.lexsort((rx, ry))
    cells = {}
    for key, at in zip(zip(ry[order].tolist(), rx[order].tolist()), range(len(order))):
        cells.setdefault(key, []).append(at)
    cells = {key: order[np.array(at)] for key, at in cells.items()}
    qy, qx = np.floor(lat / CELL).astype("int64"), np.floor(lon / CELL).astype("int64")
    top = max(np.abs(rlat).max(), np.abs(lat).max())
    cos_top = np.cos(np.radians(min(top, 89.9)))
    index, dist = np.empty(len(lat), dtype="int64"), np.empty(len(lat))
    groups = {}
    for i, key in enumerate(zip(qy.tolist(), qx.tolist())):
        groups.setdefault(key, []).append(i)
    everything = np.arange(len(rlat))
    for (cy, cx), members in groups.items():
        mine, r = np.array(members), 1
        while True:
            if (2 * r + 1) ** 2 >= len(cells):
                pick = everything
            else:
                found = [cells[(y, x)] for y in range(cy - r, cy + r + 1) for x in range(cx - r, cx + r + 1) if (y, x) in cells]
                pick = np.concatenate(found) if found else everything[:0]
            if len(pick):
                d = haversine(lat[mine, None], lon[mine, None], rlat[None, pick], rlon[None, pick])
                best = d.argmin(axis=1)
                found_km = d[np.arange(len(best)), best]
                safe = min(r * CELL * KM_PER_DEGREE, 2 * EARTH_KM * np.arcsin(min(1.0, cos_top * np.sin(np.radians(min(r * CELL, 180) / 2)))))
                if len(pick) == len(everything) or found_km.max() <= safe:
                    index[mine], dist[mine] = pick[best], found_km
                    break
            r *= 2
    return index, dist


def nearest(lat, lon, rlat, rlon):
    """점마다 가장 가까운 지점: (지점 번호 int64, 거리 km). 점이나 지점의 좌표가 비었으면 번호 -1, 거리 nan. 지점이 없으면 모두 -1.
    같은 거리면 먼저 나온 지점. 날짜 변경선을 가로지르는 넓은 자료는 격자를 쓰지 않는다."""
    lat, lon, rlat, rlon = (np.asarray(a, dtype="float64") for a in (lat, lon, rlat, rlon))
    index, dist = np.full(len(lat), -1, dtype="int64"), np.full(len(lat), np.nan)
    want, have = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon)), np.flatnonzero(np.isfinite(rlat) & np.isfinite(rlon))
    if not len(want) or not len(have):
        return index, dist
    q, r = (lat[want], lon[want]), (rlat[have], rlon[have])
    wide = max(q[1].max(), r[1].max()) - min(q[1].min(), r[1].min()) > 300
    use = gridded if len(want) * len(have) > GRID_FROM and len(have) > GRID_MIN and not wide else brute
    found, km = use(*q, *r)
    index[want], dist[want] = have[found], km
    return index, dist


# ---------- 점-영역 ----------

def closed(ring):
    """고리를 float 배열 (N, 2)로, 끝이 시작과 다르면 닫는다."""
    a = np.asarray([p[:2] for p in ring], dtype="float64").reshape(-1, 2)
    return a if len(a) and (a[0] == a[-1]).all() else np.vstack([a, a[:1]]) if len(a) else a


def in_ring(px, py, ring):
    """고리 안인 점(교차 수가 홀수이거나 경계 위)의 참거짓 배열."""
    x0, y0, x1, y1 = ring[:-1, 0], ring[:-1, 1], ring[1:, 0], ring[1:, 1]
    out = np.zeros(len(px), dtype=bool)
    step = max(1, BLOCK // max(len(x0), 1))
    for s in range(0, len(px), step):
        x, y = px[s:s + step, None], py[s:s + step, None]
        crossing = (y0 > y) != (y1 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
        out[s:s + step] = (crossing & (x < at)).sum(axis=1) % 2 == 1
    return out | on_edge(px, py, ring)


def in_polygon(px, py, polygon):
    """폴리곤 [겉 고리, 구멍…] 안인 점. 겉 고리 안(경계 포함)이면서 구멍 안(경계는 포함으로 침)이 아닌 점. 구멍 경계 위의 점은 폴리곤 경계라 안으로 본다."""
    rings = [closed(r) for r in polygon if len(r) >= 3]
    if not rings:
        return np.zeros(len(px), dtype=bool)
    hit = in_ring(px, py, rings[0])
    for hole in rings[1:]:
        if not hit.any():
            break
        inner = in_ring(px[hit], py[hit], hole)
        edge = on_edge(px[hit], py[hit], hole)
        hit[np.flatnonzero(hit)[inner & ~edge]] = False
    return hit


def on_edge(px, py, ring):
    """고리의 선분 위에 있는 점."""
    x0, y0, x1, y1 = ring[:-1, 0], ring[:-1, 1], ring[1:, 0], ring[1:, 1]
    scale = 1 + max(np.abs(ring).max(), 1)
    out = np.zeros(len(px), dtype=bool)
    step = max(1, BLOCK // max(len(x0), 1))
    for s in range(0, len(px), step):
        x, y = px[s:s + step, None], py[s:s + step, None]
        along = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
        edge = (np.abs(along) <= TOLERANCE * scale * scale) & (x >= np.minimum(x0, x1) - TOLERANCE * scale) & (x <= np.maximum(x0, x1) + TOLERANCE * scale) \
            & (y >= np.minimum(y0, y1) - TOLERANCE * scale) & (y <= np.maximum(y0, y1) + TOLERANCE * scale)
        out[s:s + step] = edge.any(axis=1)
    return out


def assign(lat, lon, regions):
    """점마다 들어 있는 영역 번호. regions = 영역마다 폴리곤 목록 [[[겉 고리, 구멍…], …], …](고리는 [[경도, 위도]…]). 어느 영역에도 없거나 좌표가 비면 -1.
    여러 영역에 걸치면 먼저 나온 영역. 영역마다 범위 상자로 후보를 먼저 거르고, 정해진 점은 다음 영역에서 뺀다."""
    lat, lon = np.asarray(lat, dtype="float64"), np.asarray(lon, dtype="float64")
    out = np.full(len(lat), -1, dtype="int64")
    todo = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    for number, polygons in enumerate(regions):
        for polygon in polygons:
            if not len(todo):
                return out
            outer = closed(polygon[0]) if polygon else None
            if outer is None or len(outer) < 4:
                continue
            x, y = lon[todo], lat[todo]
            near = (x >= outer[:, 0].min()) & (x <= outer[:, 0].max()) & (y >= outer[:, 1].min()) & (y <= outer[:, 1].max())
            pick = todo[near]
            if not len(pick):
                continue
            hit = in_polygon(lon[pick], lat[pick], polygon)
            out[pick[hit]] = number
            todo = todo[out[todo] < 0]
    return out
