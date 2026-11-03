"""퍼널(단계별 도달) 계산: 사용자가 단계를 순서대로 거쳐 간 사람 수를 Ibis 식으로 구한다. 중간에 한 다른 행동과 되돌아간 행동은 무시한다.
- 첫 단계 T1 = 그 단계 행의 사용자별 가장 이른 시각(기간 제한이 있으면 시각마다). 다음 단계 Ti = (그 단계 행)과 T(i-1)을 사용자로 이어 붙여 t ≥ T(i-1)인 행의 사용자별 가장 이른 시각(기간 제한이 있으면 도달한 시각 전부).
- 같은 시각에 두 단계가 있으면 ≥ 이므로 도달한 것으로 센다.
- 기간 제한이 있으면 단계마다 바로 앞 단계 시각부터 그 시간 안(경계 포함)에 도달한 것만 센다(A→B→C를 각각 1시간 안에). 이때는 첫 단계를 한 시각마다 각각 한 번의 "시도"로 보고,
  어느 한 시도에서라도 단계마다 기간 안에 끝까지 가면 도달로 센다(재방문 서비스). 사람 수는 사람 기준이라 한 사람은 한 번만 센다. 기간 제한이 없으면 사용자별 가장 이른 시각 하나만 쓴다.
- 단계가 k개면 이어 붙이기 k-1번과 조회 한 번이다. 어느 연결에서나 같은 식이다(창 함수를 쓰지 않는다)."""
from lazy import ibis

from core.parse import check_name

MAX_STEPS = 10  # 단계 수 상한
MIN_STEPS = 1
MAX_SEGMENTS = 8  # 세그먼트(by)로 나눌 값 수. 첫 단계 사람이 많은 순, 나머지는 OTHER
OTHER = "기타"


def check(table, user, step, time, steps, window_seconds):
    """입력을 검사해 (단계 값 목록, 기간 제한 초 또는 None)을 돌려준다. 틀리면 이유를 ValueError로."""
    for what, name in (("사용자", user), ("단계", step), ("시각", time)):
        if not isinstance(name, str) or not name:
            raise ValueError(f"{what} 컬럼을 골라 주세요.")
        check_name(name, table.columns)
    if not table[time].type().is_temporal():
        raise ValueError(f"시각 컬럼 '{time}'은 날짜나 날짜시간 컬럼이어야 해요.")
    if not isinstance(steps, (list, tuple)) or not MIN_STEPS <= len(steps) <= MAX_STEPS:
        raise ValueError(f"단계는 {MIN_STEPS}~{MAX_STEPS}개를 골라 주세요.")
    if len({str(s) for s in steps}) != len(steps):
        raise ValueError("같은 단계를 두 번 넣을 수 없어요.")
    if window_seconds is not None:
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or not window_seconds > 0:
            raise ValueError("기간 제한은 0보다 큰 숫자(초)여야 해요.")
    return list(steps), window_seconds


def step_value(dtype, value):
    """단계 값을 컬럼 타입에 맞춘다(글자 컬럼은 글자로, 숫자 컬럼은 숫자로)."""
    if dtype.is_string():
        return str(value)
    if dtype.is_numeric() and not dtype.is_boolean():
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"단계 값 '{value}'을 숫자로 읽을 수 없어요.") from None
        return int(number) if dtype.is_integer() and number == int(number) else number
    if dtype.is_boolean():
        text = str(value).strip().lower()
        if text not in ("true", "false"):
            raise ValueError(f"단계 값 '{value}'은 true나 false여야 해요.")
        return text == "true"
    return str(value)


def reached(base, steps, window_seconds, entry=None):
    """단계마다 도달한 표(사용자 u, 이번 단계 시각 tp)의 목록. 기간 제한이 없으면 사용자마다 한 줄(가장 이른 시각), 있으면 도달한 후보 시각 전부다
    (다음 단계는 앞 단계 시각만 보므로 첫 단계 시각을 들고 다니지 않는다: 들고 다니면 행이 많은 사람의 표가 시각 수의 제곱으로 커진다).
    entry = (시작 날짜, 끝 날짜): 첫 단계 행만 그 날짜 안의 것으로 거른다. 뒤 단계는 기간 밖이어도 센다(기간 끝 무렵에 시작한 사람의 전환이 빠지지 않게)."""
    starts = base.filter(base.s == steps[0])
    if entry:
        day = starts.t.cast("date")
        starts = starts.filter(*([day >= entry[0]] if entry[0] else []), *([day <= entry[1]] if entry[1] else []))
    if window_seconds is None:
        first = starts.group_by("u").aggregate(tp=starts.t.min())
    else:  # 기간 제한이 있으면 첫 단계를 한 시각마다 한 번의 시도로 본다(재방문하는 사람의 전환이 낮게 나오지 않게)
        first = starts.select("u", tp=starts.t).distinct()
    levels = [first]
    try:
        no_range_join = ibis.get_backend(base).name in ("polars", "druid")  # Polars(파일 연결)·Druid는 같지 않음(>=, <=) 조인을 못 한다
    except Exception:
        no_range_join = False
    for value in steps[1:]:
        prev = levels[-1]
        rows = base.filter(base.s == value).select("u", "t")
        if window_seconds is not None and not no_range_join:  # 시각 조건을 조인 안에 둔다: 사용자로만 잇고 거르면 행이 많은 한 사람 때문에 (앞 후보 × 다음 단계 행)만큼 커진다
            span = ibis.interval(milliseconds=round(window_seconds * 1000))
            joined = rows.join(prev, [rows.u == prev.u, rows.t >= prev.tp, rows.t <= prev.tp + span]).select("u", "t")  # 앞 단계 시각 기준
        else:
            joined = rows.join(prev, "u").filter(lambda j: j.t >= j.tp)
            if window_seconds is not None:
                span = ibis.interval(milliseconds=round(window_seconds * 1000))
                joined = joined.filter(lambda j: j.t <= j.tp + span)
        if window_seconds is None:  # 제한이 없으면 가장 이른 시각 하나로 충분하다
            levels.append(joined.group_by("u").aggregate(tp=joined.t.min()))
        else:  # 제한이 있으면 후보 시각을 모두 남긴다: 가장 이른 B에서는 C가 멀어도 뒤의 B에서는 기간 안일 수 있다
            levels.append(joined.select("u", tp="t").distinct())
    return levels


def counts(levels, seg=None):
    """단계별 사용자 수 목록(조회 한 번). 한 사람은 시도가 여럿이어도 한 번만 센다. seg(사용자 u, 세그먼트 g 표)를 주면 {세그먼트: 목록}."""
    if seg is None:
        parts = [level.aggregate(n=level.u.nunique()).mutate(i=ibis.literal(i), g=ibis.literal("")) for i, level in enumerate(levels)]
    else:
        parts = [level.join(seg, "u").group_by("g").aggregate(n=ibis._.u.nunique()).mutate(i=ibis.literal(i)) for i, level in enumerate(levels)]
    table = parts[0]
    for part in parts[1:]:
        table = table.union(part)
    found = {(row["g"], row["i"]): row["n"] for row in table.to_pyarrow().to_pylist()}
    if seg is None:
        return [int(found.get(("", i), 0)) for i in range(len(levels))]
    return {g: [int(found.get((g, i), 0)) for i in range(len(levels))] for g in dict.fromkeys(g for g, _ in found)}


def gaps(levels, window_seconds=None):
    """단계마다 바로 앞 단계에서 걸린 시간(초)의 중앙값 목록(첫 단계는 None, 조회 한 번). 사람마다 가장 짧은 것으로 센다.
    기간 제한이 있으면 단계마다 후보 시각이 여럿이라 사용자로 잇되 앞 단계 후보 중 이번 시각보다 늦지 않고 기간 안인 것과만 짝짓는다(0 ≤ d ≤ 제한)."""
    if len(levels) < 2:
        return [None]
    parts = []
    for i, (prev, now) in enumerate(zip(levels, levels[1:]), 1):
        prev = prev.select(u2="u", tp2="tp")  # 같은 이름을 피해 사용자로 잇는다
        j = now.join(prev, now.u == prev.u2).select("u", d=now.tp.epoch_seconds() - prev.tp2.epoch_seconds())
        j = j.filter(j.d >= 0, *([j.d <= window_seconds] if window_seconds is not None else []))
        per = j.group_by("u").aggregate(d=j.d.min())
        parts.append(per.aggregate(m=per.d.median().cast("float64")).mutate(i=ibis.literal(i)))
    table = parts[0]
    for part in parts[1:]:
        table = table.union(part)
    found = {row["i"]: row["m"] for row in table.to_pyarrow().to_pylist()}
    return [None] + [found.get(i) for i in range(1, len(levels))]


def segments(base, first_value):
    """세그먼트 표(사용자 u, 세그먼트 g): 사람마다 첫 단계를 처음 한 행의 by 값. 첫 단계 사람이 많은 MAX_SEGMENTS개 밖은 OTHER, 빈 값은 '(빈 값)'."""
    starts = base.filter(base.s == first_value)
    g = starts.g.cast("string").fill_null("(빈 값)")
    per = starts.select("u", "t", g=g).group_by("u").aggregate(g=ibis._.g.argmin(ibis._.t))
    top = per.group_by("g").aggregate(n=per.count()).order_by([ibis.desc("n"), "g"]).limit(MAX_SEGMENTS).to_pyarrow()["g"].to_pylist()
    return per.select("u", g=ibis.ifelse(per.g.isin(top), per.g, ibis.literal(OTHER))) if top else per.select("u", "g")


def compute(table_expr, user, step, time, steps, window_seconds=None, entry=None, by=None):
    """퍼널을 구한다. 결과 {steps: [{name, users, conv, from_first, drop, median_sec}], window, segments?}.
    conv = 이전 단계 대비, from_first = 첫 단계 대비(나누는 수가 0이면 None), drop = 이전 단계에서 빠진 사람 수(첫 단계는 0), median_sec = 앞 단계에서 걸린 시간의 중앙값(초, 첫 단계는 None).
    entry = (시작 date 또는 None, 끝 date 또는 None): 첫 단계를 이 날짜 안에 한 사람만 센다(reached).
    by = 세그먼트 컬럼(없으면 나누지 않음): segments = [{name, steps}] 첫 단계 사람이 많은 순 MAX_SEGMENTS개와 OTHER."""
    steps, window_seconds = check(table_expr, user, step, time, steps, window_seconds)
    if by is not None:
        check_name(by, table_expr.columns)
    values = [step_value(table_expr[step].type(), s) for s in steps]
    base = table_expr.select(u=table_expr[user], s=table_expr[step], t=table_expr[time].cast("timestamp"), **({"g": table_expr[by]} if by is not None else {}))
    base = base.filter(base.u.notnull(), base.t.notnull())
    levels = reached(base, values, window_seconds, entry)
    found = {"steps": step_rows(steps, counts(levels), gaps(levels, window_seconds)), "window": window_seconds}
    if by is not None:
        per = counts(levels, segments(base, values[0]))
        found["segments"] = sorted(({"name": g, "steps": step_rows(steps, n)} for g, n in per.items()), key=lambda x: (x["name"] == OTHER, -x["steps"][0]["users"], x["name"]))
    return found


def step_rows(steps, users, medians=None):
    """단계별 사람 수 → [{name, users, conv, from_first, drop, median_sec}]."""
    out = []
    for i, (name, n) in enumerate(zip(steps, users)):
        prev = users[i - 1] if i else None
        out.append({"name": str(name), "users": n,
                    "conv": (n / prev if prev else None) if i else (1.0 if n else None),
                    "from_first": n / users[0] if users[0] else None,
                    "drop": prev - n if i else 0,
                    "median_sec": medians[i] if medians else None})
    return out
