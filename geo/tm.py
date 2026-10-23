"""TM(미터 단위 평면) 좌표 → 경도·위도(WGS84) 변환. pyproj는 변환 함수 안에서만 불러온다(좌표를 바꿀 때만 올라온다).
국내 주요 좌표계는 SYSTEMS에 이름을 붙여 두고, 다른 EPSG 번호도 pyproj가 알면 바꾼다."""
import functools

import numpy as np

WGS84 = 4326
SYSTEMS = {5179: "UTM-K (KGD2002 통합, 전국 한 장 · 네이버·카카오 지도 등)", 5186: "중부원점 2010 (KGD2002, 서울·경기·충청·전라 · 공공데이터 최신)",
           5185: "서부원점 2010 (KGD2002)", 5187: "동부원점 2010 (KGD2002)", 5188: "동해원점 2010 (KGD2002)",
           5174: "중부원점 (Bessel 1841, 예전 지적도·옛 공공데이터)", 5181: "중부원점 (KGD2002, 옛 표준)", 5178: "UTM-K (Bessel)",
           4326: "경위도 WGS84 (바꿀 필요 없음)"}
KOREA = (124.0, 33.0, 132.0, 39.5)  # 경도 최소, 위도 최소, 경도 최대, 위도 최대: 변환 결과가 한국 안에 떨어지는지 보는 상자
GUESS = (5179, 5186, 5174, 5181, 5185, 5187)  # 좌표계 추측 후보(앞의 것이 우선)


@functools.lru_cache(maxsize=16)
def transformer(epsg, target=WGS84):
    from pyproj import Transformer  # 여기서만 불러온다
    return Transformer.from_crs(int(epsg), int(target), always_xy=True)


def convert(xs, ys, epsg=5179, target=WGS84):
    """x·y 배열(숫자 목록)을 epsg 좌표계에서 target(기본 경위도)으로. 반환은 (경도 또는 x, 위도 또는 y) float64 배열이고, 바꿀 수 없는 값은 nan."""
    xs, ys = np.asarray(xs, dtype="float64"), np.asarray(ys, dtype="float64")
    try:
        a, b = transformer(epsg, target).transform(xs, ys)
    except Exception as e:
        if type(e).__name__ == "CRSError":
            raise ValueError(f"EPSG:{epsg}는 알 수 없는 좌표계입니다. 국내는 {', '.join(map(str, SYSTEMS))}를 씁니다.") from None
        raise
    a, b = np.asarray(a, dtype="float64"), np.asarray(b, dtype="float64")
    bad = ~(np.isfinite(a) & np.isfinite(b))
    a[bad] = b[bad] = np.nan
    return a, b


def to_wgs84(xs, ys, epsg=5179):
    """경도·위도로. 4326이면 그대로."""
    if int(epsg) == WGS84:
        return np.asarray(xs, dtype="float64"), np.asarray(ys, dtype="float64")
    return convert(xs, ys, epsg)


def inside_korea(lon, lat):
    return KOREA[0] <= lon <= KOREA[2] and KOREA[1] <= lat <= KOREA[3]


def guess(x_min, x_max, y_min, y_max):
    """x·y 값의 범위로 국내 좌표계 후보 EPSG 번호들(GUESS 순서). 범위의 네 모서리와 가운데가 모두 한국 안에 떨어지는 좌표계만 남는다."""
    xs = np.array([x_min, x_max, x_min, x_max, (x_min + x_max) / 2], dtype="float64")
    ys = np.array([y_min, y_min, y_max, y_max, (y_min + y_max) / 2], dtype="float64")
    found = []
    for epsg in GUESS:
        lon, lat = convert(xs, ys, epsg)
        if all(inside_korea(a, b) for a, b in zip(lon, lat)):
            found.append(epsg)
    return found
