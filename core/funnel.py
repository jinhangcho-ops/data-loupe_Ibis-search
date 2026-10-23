"""퍼널(단계별 도달) 계산: 사용자가 단계를 순서대로 거쳐 간 사람 수를 Ibis 식으로 구한다. 중간에 한 다른 행동과 되돌아간 행동은 무시한다.
- 첫 단계 T1 = 그 단계 행의 사용자별 가장 이른 시각(기간 제한이 있으면 시각마다). 다음 단계 Ti = (그 단계 행)과 T(i-1)을 사용자로 이어 붙여 t ≥ T(i-1)인 행의 사용자별 가장 이른 시각.
- 같은 시각에 두 단계가 있으면 ≥ 이므로 도달한 것으로 센다.
- 기간 제한이 있으면 첫 단계 시각부터 그 시간 안(경계 포함)에 도달한 것만 센다. 이때는 첫 단계를 한 시각마다 각각 한 번의 "시도"로 보고,
  어느 한 시도에서라도 기간 안에 끝까지 가면 도달로 센다(재방문 서비스). 사람 수는 사람 기준이라 한 사람은 한 번만 센다. 기간 제한이 없으면 사용자별 가장 이른 시각 하나만 쓴다.
- 단계가 k개면 이어 붙이기 k-1번과 조회 한 번이다. 어느 연결에서나 같은 식이다(창 함수를 쓰지 않는다)."""
from lazy import ibis

from core.parse import check_name

MAX_STEPS = 10  # 단계 수 상한
MIN_STEPS = 1


def check(table, user, step, time, steps, window_seconds):
    """입력을 검사해 (단계 값 목록, 기간 제한 초 또는 None)을 돌려준다. 틀리면 이유를 ValueError로."""
    for what, name in (("사용자", user), ("단계", step), ("시각", time)):
        if not isinstance(name, str) or not name:
            raise ValueError(f"{what} 컬럼을 골라 주세요.")
        check_name(name, table.columns)
    if not table[time].type().is_temporal():
        raise ValueError(f"시각 컬럼 '{time}'은 날짜나 날짜시간 컬럼이어야 합니다.")
    if not isinstance(steps, (list, tuple)) or not MIN_STEPS <= len(steps) <= MAX_STEPS:
        raise ValueError(f"단계는 {MIN_STEPS}~{MAX_STEPS}개를 골라 주세요.")
    if len({str(s) for s in steps}) != len(steps):
        raise ValueError("같은 단계를 두 번 넣을 수 없습니다.")
    if window_seconds is not None:
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or not window_seconds > 0:
            raise ValueError("기간 제한은 0보다 큰 숫자(초)여야 합니다.")
    return list(steps), window_seconds


def step_value(dtype, value):
    """단계 값을 컬럼 타입에 맞춘다(글자 컬럼은 글자로, 숫자 컬럼은 숫자로)."""
    if dtype.is_string():
        return str(value)
    if dtype.is_numeric() and not dtype.is_boolean():
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"단계 값 '{value}'을 숫자로 읽을 수 없습니다.") from None
        return int(number) if dtype.is_integer() and number == int(number) else number
    if dtype.is_boolean():
        text = str(value).strip().lower()
        if text not in ("true", "false"):
            raise ValueError(f"단계 값 '{value}'은 true나 false여야 합니다.")
        return text == "true"
    return str(value)


def reached(base, steps, window_seconds, entry=None):
    """단계마다 도달한 표(사용자 u, 이번 단계 시각 tp, 첫 단계 시각 t1)의 목록. 기간 제한이 없으면 사용자마다 한 줄, 있으면 시도(첫 단계 시각)마다 한 줄이다.
    entry = (시작 날짜, 끝 날짜): 첫 단계 행만 그 날짜 안의 것으로 거른다. 뒤 단계는 기간 밖이어도 센다(기간 끝 무렵에 시작한 사람의 전환이 빠지지 않게)."""
    starts = base.filter(base.s == steps[0])
    if entry:
        day = starts.t.cast("date")
        starts = starts.filter(*([day >= entry[0]] if entry[0] else []), *([day <= entry[1]] if entry[1] else []))
    if window_seconds is None:
        first = starts.group_by("u").aggregate(tp=starts.t.min())
    else:  # 기간 제한이 있으면 첫 단계를 한 시각마다 한 번의 시도로 본다(재방문하는 사람의 전환이 낮게 나오지 않게)
        first = starts.select("u", tp=starts.t).distinct()
    levels = [first.mutate(t1=first.tp)]
    for value in steps[1:]:
        prev = levels[-1]
        rows = base.filter(base.s == value).select("u", "t")
        joined = rows.join(prev, "u").filter(lambda j: j.t >= j.tp)
        if window_seconds is not None:
            span = ibis.interval(milliseconds=round(window_seconds * 1000))
            joined = joined.filter(lambda j: j.t <= j.t1 + span)
        levels.append(joined.group_by(["u", "t1"]).aggregate(tp=joined.t.min()))
    return levels


def counts(levels):
    """단계별 사용자 수 목록(조회 한 번). 한 사람은 시도가 여럿이어도 한 번만 센다."""
    parts = [level.aggregate(n=level.u.nunique()).mutate(i=ibis.literal(i)) for i, level in enumerate(levels)]
    table = parts[0]
    for part in parts[1:]:
        table = table.union(part)
    found = {row["i"]: row["n"] for row in table.to_pyarrow().to_pylist()}
    return [int(found.get(i, 0)) for i in range(len(levels))]


def compute(table_expr, user, step, time, steps, window_seconds=None, entry=None):
    """퍼널을 구한다. 결과 {steps: [{name, users, conv, from_first, drop}], window}.
    conv = 이전 단계 대비, from_first = 첫 단계 대비(나누는 수가 0이면 None), drop = 이전 단계에서 빠진 사람 수(첫 단계는 0).
    entry = (시작 date 또는 None, 끝 date 또는 None): 첫 단계를 이 날짜 안에 한 사람만 센다(reached)."""
    steps, window_seconds = check(table_expr, user, step, time, steps, window_seconds)
    values = [step_value(table_expr[step].type(), s) for s in steps]
    base = table_expr.select(u=table_expr[user], s=table_expr[step], t=table_expr[time].cast("timestamp"))
    base = base.filter(base.u.notnull(), base.t.notnull())
    users = counts(reached(base, values, window_seconds, entry))
    out = []
    for i, (name, n) in enumerate(zip(steps, users)):
        prev = users[i - 1] if i else None
        out.append({"name": str(name), "users": n,
                    "conv": (n / prev if prev else None) if i else (1.0 if n else None),
                    "from_first": n / users[0] if users[0] else None,
                    "drop": prev - n if i else 0})
    return {"steps": out, "window": window_seconds}
