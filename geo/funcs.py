"""좌표 함수(Ibis 식): 두 점 거리(km, haversine), 반경 안·범위 상자 안 거르기, 격자 묶기. 식은 sin·cos·asin·sqrt 같은 기본 수식뿐이라
원격 DB에서도 DB 안에서 계산된다(좌표를 PC로 받지 않는다). 함수 사전(core/sql_funcs)과 조건 줄(core/parse의 near·box)이 쓴다."""
import math

from lazy import ibis

EARTH_KM = 6371.0088  # 지구 평균 반지름(km)
KM_PER_DEGREE = EARTH_KM * math.pi / 180  # 위도 1도의 길이(km)
RAD = math.pi / 180


def number(value, what):
    """칸 값(컬럼 식, 숫자, 숫자 글자)을 실수 식으로."""
    if isinstance(value, ibis.expr.types.Value):
        return value.cast("float64")
    try:
        return ibis.literal(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"{what}에는 숫자나 숫자 컬럼을 넣으세요.") from None


def plain(value, what):
    """상수 칸 값 → 실수(컬럼이면 거부)."""
    if isinstance(value, ibis.expr.types.Value):
        raise ValueError(f"{what}에는 숫자를 직접 넣으세요(컬럼은 안 돼요).")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}에는 숫자를 넣으세요.") from None
    if out != out or out in (math.inf, -math.inf):
        raise ValueError(f"{what}에는 유한한 숫자를 넣으세요.")
    return out


def haversine(lat1, lon1, lat2, lon2):
    """두 점의 큰 원 거리(km). 값은 컬럼 식이나 숫자. 빈 값이 있으면 빈 값. 반올림 오차로 a가 1을 넘지 않게 막는다."""
    p1, p2 = number(lat1, "위도") * RAD, number(lat2, "위도") * RAD
    dp, dl = p2 - p1, (number(lon2, "경도") - number(lon1, "경도")) * RAD
    a = (dp / 2).sin() ** 2 + p1.cos() * p2.cos() * (dl / 2).sin() ** 2
    return 2 * EARTH_KM * (a >= 1.0).ifelse(1.0, a).sqrt().asin()  # least는 빈 값을 건너뛰어 쓰지 않는다


def check_lat(lat, what="기준 위도"):
    lat = plain(lat, what)
    if not -90 <= lat <= 90:
        raise ValueError(f"{what}는 -90~90 사이여야 합니다({lat}).")
    return lat


def check_lon(lon, what="기준 경도"):
    lon = plain(lon, what)
    if not -180 <= lon <= 180:
        raise ValueError(f"{what}는 -180~180 사이여야 합니다({lon}).")
    return lon


def within_km(lat, lon, center_lat, center_lon, km):
    """기준점에서 km 이내(경계 포함)인 행. 위도 차이로 먼저 걸러 DB가 위도 인덱스를 쓸 수 있게 한다(위도 1도 = 약 111.2km, 큰 원 거리는 위도 차이보다 짧을 수 없다)."""
    clat, clon, km = check_lat(center_lat), check_lon(center_lon), plain(km, "반경(km)")
    if km < 0:
        raise ValueError("반경(km)은 0 이상이어야 합니다.")
    lat, lon = number(lat, "위도"), number(lon, "경도")
    reach = km / KM_PER_DEGREE * 1.0001 + 1e-9
    return lat.between(clat - reach, clat + reach) & (haversine(lat, lon, clat, clon) <= km)


def in_box(lat, lon, min_lat, min_lon, max_lat, max_lon):
    """범위 상자(최소·최대 위경도, 경계 포함) 안인 행. 최소 경도가 최대 경도보다 크면 날짜 변경선(±180)을 가로지르는 상자로 본다."""
    lo_lat, lo_lon, hi_lat, hi_lon = check_lat(min_lat, "최소 위도"), check_lon(min_lon, "최소 경도"), check_lat(max_lat, "최대 위도"), check_lon(max_lon, "최대 경도")
    if lo_lat > hi_lat:
        raise ValueError("최소 위도가 최대 위도보다 클 수 없습니다.")
    lat, lon = number(lat, "위도"), number(lon, "경도")
    across = (lon >= lo_lon) | (lon <= hi_lon) if lo_lon > hi_lon else lon.between(lo_lon, hi_lon)
    return lat.between(lo_lat, hi_lat) & across


def cell_size(size):
    out = plain(size, "칸 크기(도)")
    if not 1e-6 <= out <= 90:
        raise ValueError("칸 크기는 0.000001~90도 사이로 넣으세요(예: 0.01 = 약 1.1km).")
    return out


def cell(value, size):
    """칸 번호(정수 식): 값 ÷ 칸 크기의 내림. 칸 경계 값이 부동소수 오차로 이웃 칸에 떨어지지 않게 아주 작은 값을 더한다."""
    return (number(value, "좌표") / size + 1e-9).floor().cast("int64")


def grid_id(lat, lon, size):
    """격자 칸 id 글자: '위도칸_경도칸'(칸 번호). 같은 칸의 점은 같은 id."""
    s = cell_size(size)
    return cell(lat, s).cast("string") + "_" + cell(lon, s).cast("string")


def grid_center(value, size):
    """칸 중심 좌표(위도 또는 경도): (칸 번호 + 0.5) × 칸 크기, 소수 8자리까지."""
    s = cell_size(size)
    return ((cell(value, s) + 0.5) * s).round(8)
