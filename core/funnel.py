"""퍼널(단계별 도달) 계산: 사용자가 단계를 순서대로 거쳐 간 사람 수를 Ibis 식으로 구한다. 중간에 한 다른 행동과 되돌아간 행동은 무시한다.
- 첫 단계 T1 = 그 단계 행의 사용자별 가장 이른 시각(기간 제한이 있으면 시각마다). 다음 단계 Ti = (그 단계 행)과 T(i-1)을 사용자로 이어 붙여 t ≥ T(i-1)인 행의 사용자별 가장 이른 시각(기간 제한이 있으면 도달한 시각 전부).
- 같은 시각에 두 단계가 있으면 ≥ 이므로 도달한 것으로 센다.
- 기간 제한이 있으면 단계마다 바로 앞 단계 시각부터 그 시간 안(경계 포함)에 도달한 것만 센다(A→B→C를 각각 1시간 안에). 이때는 첫 단계를 한 시각마다 각각 한 번의 "시도"로 보고,
  어느 한 시도에서라도 단계마다 기간 안에 끝까지 가면 도달로 센다(재방문 서비스). 사람 수는 사람 기준이라 한 사람은 한 번만 센다. 기간 제한이 없으면 사용자별 가장 이른 시각 하나만 쓴다.
- 단계가 k개면 이어 붙이기 k-1번과 조회 한 번이다. 어느 연결에서나 같은 식이다(창 함수를 쓰지 않는다)."""
import datetime as dt

from lazy import ibis

from core.parse import check_name, predicate

MAX_STEPS = 10  # 단계 수 상한
MIN_STEPS = 1
MAX_SEGMENTS = 8  # 세그먼트(by)로 나눌 값 수. 첫 단계 사람이 많은 순, 나머지는 OTHER
OTHER = "기타"
COUNTS = ("order", "each")  # 조건 단계 퍼널의 세기(Phase 3): 순서대로(앞 단계를 거친 사람만) / 단계마다 따로
STAGE_KEYS = frozenset({"name", "conds", "revisit"})  # 조건 단계 하나에 쓸 수 있는 항목
MAX_STAGE_NAME = 40  # 조건 단계 이름 글자 수 상한
MAX_REVISIT_DAYS = 365  # 재방문 단계의 N일 상한
DEFAULT_TRIM = 0.1  # 걸린 시간 절사평균에서 자르는 몫의 기본(위아래 각 절반씩: 0.1 = 위 5%·아래 5%)
MAX_TRIM = 0.5  # 자르는 몫의 상한(위아래 각 25%)
MS_PER_SECOND = 1000  # 기간 제한(초) → 밀리초


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


def compute(table_expr, user, step, time, steps, window_seconds=None, entry=None, by=None, trim=None):
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
    trim = check_trim(trim)
    found = {"steps": step_rows(steps, counts(levels), gaps(levels, window_seconds)), "window": window_seconds,
             "gaps": gap_stats(level_spans(levels), [str(s) for s in steps], trim) if len(levels) > 1 else [], "trim": trim}
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


def check_stages(table: "ibis.expr.types.Table", user: str, time: str, stages: list[dict], count: str, window_seconds: float | None) -> tuple[list[dict], str, float | None]:
    """[SPECIFICATION] plans/phase-3.md#check_stages
    조건 단계 퍼널의 입력(사용자·시각 컬럼, 단계 목록, 세기, 기간 제한)을 검사해 정리한 (단계들, 세기, 기간 제한 초)를 돌려주고, 틀리면 이유를 ValueError로 낸다."""
    _, window_seconds = check(table, user, user, time, [None], window_seconds)  # 조건 단계에는 단계 컬럼이 없어 컬럼·시각 타입·기간 제한 검사만 쓴다(단계 컬럼·단계 목록 자리는 자리 값)
    if count not in COUNTS:
        raise ValueError(f"세기(count)는 {', '.join(COUNTS)} 중 하나예요.")
    if not isinstance(stages, list) or not MIN_STEPS <= len(stages) <= MAX_STEPS:
        raise ValueError(f"단계는 {MIN_STEPS}~{MAX_STEPS}개를 정해 주세요.")
    checked = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise ValueError("단계는 {name, conds, revisit} 객체여야 해요.")
        unknown = set(stage) - STAGE_KEYS
        if unknown:
            raise ValueError(f"단계에 쓸 수 없는 항목이 있어요: {', '.join(sorted(unknown))}")
        name = stage.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_STAGE_NAME:
            raise ValueError(f"단계 이름은 1~{MAX_STAGE_NAME}자 글자예요.")
        name = name.strip()
        conds = stage.get("conds")
        if conds is None:
            conds = []
        if not isinstance(conds, list):
            raise ValueError("단계의 조건(conds)은 조건 줄 목록이에요.")
        revisit = stage.get("revisit")
        if revisit is not None and not (type(revisit) is int and 1 <= revisit <= MAX_REVISIT_DAYS):
            raise ValueError(f"재방문 일수는 1~{MAX_REVISIT_DAYS} 사이의 정수예요.")
        if not conds and revisit is None:
            raise ValueError(f"'{name}' 단계에 조건 줄을 적거나 재방문 일수를 정해 주세요.")
        checked.append({"name": name, "conds": conds, **({"revisit": revisit} if revisit else {})})
    if len({s["name"] for s in checked}) != len(checked):
        raise ValueError("같은 이름의 단계를 두 번 넣을 수 없어요.")
    return checked, count, window_seconds


def stage_rows(table: "ibis.expr.types.Table", user: str, time: str, stages: list[dict]) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-3.md#stage_rows
    단계마다 조건 줄(과 재방문 N일)에 맞는 행을 (사용자 u, 단계 번호 s, 시각 t)로 골라 UNION ALL한 표(reached가 받는 base 모양)를 만든다(실행하지 않음)."""
    pieces = []
    first = None  # 사용자별 첫 활동 표: 재방문 단계가 처음 나올 때 한 번만 만든다
    for i, st in enumerate(stages):
        rows = table.filter(predicate(st["conds"], table)) if st["conds"] else table
        rows = rows.select(u=rows[user], t=rows[time].cast("timestamp"))
        rows = rows.filter(rows.u.notnull(), rows.t.notnull())
        if "revisit" in st:
            if first is None:
                alls = table.select(u=table[user], t=table[time].cast("timestamp"))
                alls = alls.filter(alls.u.notnull(), alls.t.notnull())
                first = alls.group_by("u").aggregate(f=alls.t.min())
            j = rows.join(first, "u")
            d, fd = j.t.cast("date"), j.f.cast("date")
            rows = j.filter(d > fd, d <= fd + ibis.interval(days=st["revisit"])).select("u", "t")
        pieces.append(rows.select("u", s=ibis.literal(i, "int64"), t="t"))
    out = pieces[0]
    for piece in pieces[1:]:
        out = out.union(piece, distinct=False)
    return out


def each_rows(base: "ibis.expr.types.Table", entry: tuple[dt.date | None, dt.date | None] | None, window_seconds: float | None) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-3.md#each_rows
    단계마다 따로 세기의 행: stage_rows 표에 entry 날짜를 모든 단계에 걸고, 기간 제한이 있으면 사용자의 첫 단계 처음 시각부터 그 기간 안의 행만 남긴 (u, s, t)(실행하지 않음)."""
    b, day = base, base.t.cast("date")
    preds = [*([day >= entry[0]] if entry and entry[0] else []), *([day <= entry[1]] if entry and entry[1] else [])]
    if preds:  # filter([])는 오류
        b = b.filter(preds)
    if window_seconds is not None:  # 사용자의 첫 단계 처음 시각부터 그 기간 안(경계 포함)만. 첫 단계가 없는 사용자는 빠진다
        span = ibis.interval(milliseconds=round(window_seconds * MS_PER_SECOND))
        starts = b.filter(b.s == 0)
        t0 = starts.group_by("u").aggregate(t0=starts.t.min())
        b = b.join(t0, "u").filter(ibis._.t >= ibis._.t0, ibis._.t <= ibis._.t0 + span).select("u", "s", "t")
    return b


def each_counts(rows: "ibis.expr.types.Table", k: int) -> tuple[list[int], list[int | None]]:
    """[SPECIFICATION] plans/phase-3.md#each_counts
    each_rows 표에서 단계 0..k-1마다 고유 사용자 수와 이탈 수(앞 단계를 만족했는데 이 단계는 만족하지 않은 사용자 수, 첫 단계 0)를 조회 한 번으로 센다."""
    per = rows.select("s", "u").distinct().group_by("s").aggregate(n=ibis._.count())  # nunique는 Polars에서 UInt32라 아래 UNION ALL의 int64와 스키마가 어긋난다(같은 타입이라 cast도 빠진다)
    parts = [per.select(kind=ibis.literal("n"), i=per.s.cast("int64"), n=per.n.cast("int64"))]
    for i in range(1, k):  # 이웃 쌍마다(단계 수만큼의 반복)
        prev = rows.filter(rows.s == i - 1).select("u").distinct()
        now = rows.filter(rows.s == i).select("u").distinct()
        lost = prev.anti_join(now, "u").aggregate(n=ibis._.count())
        parts.append(lost.select(kind=ibis.literal("d"), i=ibis.literal(i, "int64"), n=lost.n.cast("int64")))
    table = parts[0]
    for part in parts[1:]:
        table = table.union(part, distinct=False)
    found = {(row["kind"], row["i"]): row["n"] for row in table.to_pyarrow().to_pylist()}
    return [int(found.get(("n", i), 0)) for i in range(k)], [0] + [int(found.get(("d", i), 0)) for i in range(1, k)]


def level_spans(levels: list["ibis.expr.types.Table"]) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-3.md#level_spans
    순서대로 센 단계 표(reached)에서 다음 단계로 간 사용자마다 앞 단계 처음 시각 → 이 단계 처음 시각의 초를 (i, u, d)로 UNION ALL한 식(실행하지 않음)."""
    pieces = []
    for i in range(1, len(levels)):  # 이웃 쌍마다(단계 수만큼의 반복)
        a = levels[i - 1].group_by("u").aggregate(t0=ibis._.tp.min())
        b = levels[i].group_by("u").aggregate(t1=ibis._.tp.min())
        j = b.join(a, "u")
        pieces.append(j.select("u", i=ibis.literal(i, "int64"), d=(j.t1.epoch_seconds() - j.t0.epoch_seconds()).cast("float64")))
    out = pieces[0]
    for piece in pieces[1:]:
        out = out.union(piece, distinct=False)
    return out


def each_spans(rows: "ibis.expr.types.Table", k: int) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-3.md#each_spans
    단계마다 따로 센 행(each_rows)에서 이웃한 두 단계를 모두 만족한 사용자마다 앞 단계 처음 시각 → 이 단계 처음 시각의 초(0 이상)를 (i, u, d)로 UNION ALL한 식."""
    pieces = []
    for i in range(1, k):  # 이웃 쌍마다(단계 수만큼의 반복)
        a = rows.filter(rows.s == i - 1).group_by("u").aggregate(t0=ibis._.t.min())
        b = rows.filter(rows.s == i).group_by("u").aggregate(t1=ibis._.t.min())
        j = b.join(a, "u")
        piece = j.select("u", i=ibis.literal(i, "int64"), d=(j.t1.epoch_seconds() - j.t0.epoch_seconds()).cast("float64"))
        pieces.append(piece.filter(piece.d >= 0))
    out = pieces[0]
    for piece in pieces[1:]:
        out = out.union(piece, distinct=False)
    return out


def gap_stats(spans: "ibis.expr.types.Table", names: list[str], trim: float) -> list[dict]:
    """[SPECIFICATION] plans/phase-3.md#gap_stats
    (i, u, d) 표에서 이웃한 단계 쌍마다 {from, to, n, mean, median, trimmed}(초, 사람이 없으면 값은 None)를 조회 두 번(분위수 → 절사평균)으로 구한다."""
    quantiles = spans.group_by("i").aggregate(n=spans.u.nunique(), mean=spans.d.mean(), median=spans.d.median(),
                                              lo=spans.d.quantile(trim / 2), hi=spans.d.quantile(1 - trim / 2))
    by_pair = {row["i"]: row for row in quantiles.to_pyarrow().to_pylist()}
    trimmed = {}
    if by_pair:  # lo·hi를 값으로 식에 넣는다(Polars 규칙): 쌍마다 작은 집계
        parts = [spans.filter(spans.i == i, spans.d >= row["lo"], spans.d <= row["hi"]).aggregate(m=ibis._.d.mean()).mutate(i=ibis.literal(i, "int64"))
                 for i, row in by_pair.items()]  # 쌍 수만큼의 반복
        table = parts[0]
        for part in parts[1:]:
            table = table.union(part, distinct=False)
        trimmed = {row["i"]: row["m"] for row in table.to_pyarrow().to_pylist()}
    out = []
    for i in range(1, len(names)):  # 이웃 쌍마다
        row = by_pair.get(i)
        out.append({"from": names[i - 1], "to": names[i], "n": int(row["n"]) if row else 0,
                    "mean": row["mean"] if row else None, "median": row["median"] if row else None, "trimmed": trimmed.get(i)})
    return out


def check_trim(trim: float | None) -> float:
    """[SPECIFICATION] plans/phase-3.md#check_trim
    절사평균에서 자르는 몫을 검사한다: None이면 DEFAULT_TRIM, 0 이상 MAX_TRIM 이하 숫자(참거짓 제외)가 아니면 ValueError."""
    if trim is None:
        return DEFAULT_TRIM
    if type(trim) not in (int, float) or not 0 <= trim <= MAX_TRIM:
        raise ValueError(f"절사평균에서 자르는 몫은 0~{MAX_TRIM} 사이의 숫자예요.")
    return trim


def stage_compute(table: "ibis.expr.types.Table", user: str, time: str, stages: list[dict], count: str, window_seconds: float | None = None,
                  entry: tuple[dt.date | None, dt.date | None] | None = None, trim: float | None = None) -> dict:
    """[SPECIFICATION] plans/phase-3.md#stage_compute
    조건 단계 퍼널을 순서대로(reached·counts·gaps 재사용) 또는 단계마다 따로(each_rows·each_counts) 세어 {steps: step_rows 모양, gaps: 걸린 시간 표, window, count, trim}을 돌려준다."""
    stages, count, window_seconds = check_stages(table, user, time, stages, count, window_seconds)
    trim = check_trim(trim)
    base, names, k = stage_rows(table, user, time, stages), [s["name"] for s in stages], len(stages)
    if count == "order":
        levels = reached(base, list(range(k)), window_seconds, entry)
        found = gap_stats(level_spans(levels), names, trim) if k > 1 else []
        steps = step_rows(names, counts(levels), gaps(levels, window_seconds))
    else:
        rows = each_rows(base, entry, window_seconds)
        users, drops = each_counts(rows, k)
        found = gap_stats(each_spans(rows, k), names, trim) if k > 1 else []
        steps = [{**r, "drop": drops[i], "median_sec": found[i - 1]["median"] if i else None} for i, r in enumerate(step_rows(names, users))]
    return {"steps": steps, "gaps": found, "window": window_seconds, "count": count, "trim": trim}
