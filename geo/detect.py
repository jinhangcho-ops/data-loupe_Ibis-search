"""좌표 컬럼 자동 인식: 이름(lat·lng·lon·latitude·longitude·위도·경도·x·y)과 값 범위로 위도·경도·TM x·y 컬럼을 추천하고,
범위를 벗어난 값(위도 ±90, 경도 ±180 밖)은 이유와 함께 알린다. 전처리의 프로파일(컬럼마다 min·max)을 받아 쓰므로 데이터를 읽지 않는다.
값이 미터 단위 큰 숫자인 x·y(또는 이름이 위도·경도인데 값이 큰 컬럼)는 TM 좌표로 보고 맞을 만한 좌표계(EPSG)를 골라 보인다(geo.tm.guess)."""
import re

LAT_WORDS = {"lat", "latitude", "위도"}
LON_WORDS = {"lon", "lng", "long", "longitude", "경도"}
LAT_SUBSTR = ("latitude", "위도")
LON_SUBSTR = ("longitude", "경도")
X_WORDS, Y_WORDS = {"x", "좌표x", "x좌표", "xpos", "tmx", "xcoord"}, {"y", "좌표y", "y좌표", "ypos", "tmy", "ycoord"}
LAT_RANGE, LON_RANGE = (-90.0, 90.0), (-180.0, 180.0)
BIG = 1000  # 이보다 큰 값은 경위도가 아니라 미터 단위로 본다


def words(name):
    """컬럼 이름을 낱말들로(영문 대소문자 경계·밑줄·공백·점·하이픈으로 나눈 소문자). 예: storeLat → store, lat."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return [w.lower() for w in re.split(r"[^0-9A-Za-z가-힣]+", spaced) if w]


def role_of(name):
    """이름만 보고 lat·lon·x·y 중 하나(또는 None)."""
    parts = words(name)
    joined = "".join(parts)
    if joined in X_WORDS or parts == ["x"] or (len(parts) == 2 and "x" in parts):
        return "x"
    if joined in Y_WORDS or parts == ["y"] or (len(parts) == 2 and "y" in parts):
        return "y"
    if any(w in LAT_WORDS for w in parts) or any(s in joined for s in LAT_SUBSTR):
        return "lat"
    if any(w in LON_WORDS for w in parts) or any(s in joined for s in LON_SUBSTR):
        return "lon"
    return None


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def limits(role):
    return LAT_RANGE if role == "lat" else LON_RANGE


def check_range(column, role, low, high):
    """위도·경도 컬럼의 값 범위가 맞는지: 틀리면 이유 글(없으면 None)."""
    lo, hi = limits(role)
    label = "위도" if role == "lat" else "경도"
    bad = []
    if low is not None and low < lo:
        bad.append(f"최솟값이 {low:g}로 {lo:g}보다 작아요")
    if high is not None and high > hi:
        bad.append(f"최댓값이 {high:g}로 {hi:g}보다 커요")
    if not bad:
        return None
    big = max(abs(low or 0), abs(high or 0)) > BIG
    hint = "미터 단위 TM 좌표일 수 있어요. 아래에서 좌표계를 골라 경도·위도로 바꾸세요." if big else \
        ("경도 컬럼과 바뀌었거나 단위가 다를 수 있어요." if role == "lat" and high is not None and high > 90 else "위도 컬럼과 바뀌었거나 단위가 다를 수 있어요." if role == "lon" else "값이 잘못 들어갔는지 확인하세요.")
    return f"‘{column}’ 컬럼은 {label}처럼 보이는데 {lo:g}~{hi:g} 범위를 벗어났어요({', '.join(bad)}). {hint}"


def hints(columns):
    """프로파일 컬럼 목록([{name, kind, min, max, nulls, …}])에서 좌표 컬럼을 찾는다.
    반환 {columns: [{name, role, ok, note}], pairs: [{kind: lonlat, lat, lon} 또는 {kind: tm, x, y, epsg: [후보 EPSG]}], warnings: [글]}.
    숫자(정수·실수) 컬럼만 본다. 이름이 위도·경도인데 값이 범위를 벗어나면 warnings에 이유를 적고, 그 쌍이 모두 큰 숫자이면 TM 쌍으로 추천한다."""
    found, warnings = [], []
    roles = {}
    for c in columns:
        if c.get("kind") not in ("int", "float"):
            continue
        role = role_of(c["name"])
        if role is None:
            continue
        low, high = number(c.get("min")), number(c.get("max"))
        if role in ("x", "y"):
            note = "미터 단위의 큰 숫자라 TM 좌표로 보여요." if (high is not None and abs(high) > BIG) or (low is not None and abs(low) > BIG) else "경도(x)·위도(y) 좌표처럼 보여요."
            ok = True
        else:
            why = check_range(c["name"], role, low, high)
            ok, note = why is None, why or f"{'위도' if role == 'lat' else '경도'} 컬럼으로 보여요(값이 범위 안에 있어요)."
            if why:
                warnings.append(why)
        roles.setdefault(role, []).append((c["name"], low, high, ok))
        found.append({"name": c["name"], "role": role, "ok": ok, "note": note})
    pairs = []
    for lat, lon in zip(roles.get("lat", []), roles.get("lon", [])):
        if lat[3] and lon[3]:
            pairs.append({"kind": "lonlat", "lat": lat[0], "lon": lon[0]})
        elif max(abs(v or 0) for v in (lat[1], lat[2], lon[1], lon[2])) > BIG:
            pairs.append(tm_pair(lon, lat))
    for x, y in zip(roles.get("x", []), roles.get("y", [])):
        if max(abs(v or 0) for v in (x[1], x[2], y[1], y[2])) > BIG:
            pairs.append(tm_pair(x, y))
        else:
            pairs.append({"kind": "lonlat", "lat": y[0], "lon": x[0]})
    return {"columns": found, "pairs": [p for p in pairs if p], "warnings": warnings}


def tm_pair(x, y):
    """x·y 컬럼(이름, 최소, 최대, ok)을 TM 쌍으로: 값 범위로 맞을 만한 좌표계 후보를 붙인다(pyproj가 있을 때)."""
    pair = {"kind": "tm", "x": x[0], "y": y[0], "epsg": []}
    if None in (x[1], x[2], y[1], y[2]):
        return pair
    try:
        from geo import tm
        pair["epsg"] = tm.guess(x[1], x[2], y[1], y[2])
    except Exception:  # pyproj가 없거나 계산이 안 되면 후보 없이 알린다(좌표계는 사용자가 고른다)
        pass
    return pair
