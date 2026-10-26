"""분석 화면의 분석: 분포, 상관관계, 그룹 비교, 시계열 추이, 흐름(퍼널). 표본(메모리 표)이든 테이블 전체(DB)든 같은 Ibis 집계로 구한다.
상관관계도 DB마다 다른 corr 함수 대신 합계 집계로 구해 어느 연결에서나 같다. 결과는 화면이 그릴 값과 표로 보일 값이다."""
import datetime as dt
import math
import re

from lazy import ibis

from core import funnel
from core import prep_ops
from core import result
from core import source
from core.parse import check_name
from core.result import chart_metric, plain
from core.sql_funcs import period_start  # 주 시작 자르기는 식 truncate와 같은 것을 쓴다(dash_calc도 stats.period_start로 부른다)

BINS = 20  # 히스토그램 구간 수
TOP_VALUES = 20  # 값 빈도에 보일 값 수(나머지는 "그 밖")
MAX_CORR = 12  # 상관관계에 넣을 수 있는 숫자 컬럼 수
MAX_POINTS = result.MAX_POINTS  # 산점도의 점 수
MAX_GROUPS = 30  # 그룹 비교에 보일 그룹 수(행이 많은 순)
MAX_PERIODS = 400  # 시계열 추이에 보일 기간 수(최근 것)
SEED = 20260101  # 점을 고르게 뽑을 때의 고정 씨앗(다시 구해도 같은 점, 대시보드 dash_calc도 쓴다)
UNITS = {"day": "D", "week": "W", "month": "M"}  # 시계열 묶음 단위 → Ibis truncate 단위
WINDOW_UNITS = {"minute": 60, "hour": 3600, "day": 86400}  # 퍼널 기간 제한의 단위 → 초
LIMITS = {"max_steps": funnel.MAX_STEPS, "bins": BINS, "top_values": TOP_VALUES, "max_corr": MAX_CORR, "max_points": MAX_POINTS, "max_groups": MAX_GROUPS, "max_periods": MAX_PERIODS}


def rows(expr):
    """식의 결과를 [{컬럼: 값}]으로(날짜 등은 글자)."""
    return [{k: plain(v) for k, v in row.items()} for row in expr.to_pyarrow().to_pylist()]


def one(expr):
    return rows(expr)[0]


def kind(dtype):
    """컬럼 종류: num(숫자, 참거짓 제외), date(날짜·시간), text(그 밖)."""
    if dtype.is_numeric() and not dtype.is_boolean():
        return "num"
    return "date" if dtype.is_temporal() else "text"


def column_of(t, name, want=None, what="컬럼"):
    """t의 컬럼 name. want("num"·"date")를 주면 그 종류인지 본다."""
    check_name(name, t.columns, what)
    if want and kind(t[name].type()) != want:
        raise ValueError(f"'{name}'은 {'숫자' if want == 'num' else '날짜'} 컬럼이 아닙니다.")
    return t[name]


def finite(c):
    """숫자 컬럼을 실수로. NaN·무한은 빈 값으로 본다(계산이 NaN으로 번지지 않게)."""
    x = c.cast("float64")
    return prep_ops.nan_to_null(x) if c.type().is_floating() else x


def values_top(t, c):
    """행이 많은 TOP_VALUES개 값과 그 수(빈 값 제외)의 행들."""
    top = rows(t.group_by(c.name("value")).aggregate(n=t.count()).order_by([ibis.desc("n"), "value"]).limit(TOP_VALUES + 1))
    return [r for r in top if r["value"] is not None][:TOP_VALUES]


def values(t, c, more=None, top=None):
    """값 빈도: 행이 많은 TOP_VALUES개 값과 그 수, 빈 값 수, 그 밖의 값 수. more(집계 식 dict)를 주면 같은 집계에 실어 구하고 (값 빈도, more의 값들)을 돌려준다. top은 values_top을 미리 구했을 때."""
    top = values_top(t, c) if top is None else top
    counted = one(t.aggregate(total=t.count(), nulls=c.isnull().sum(), unique=c.nunique(where=c.notnull()), **{f"more_{k}": v for k, v in (more or {}).items()}))  # 질의 한 번
    shown = sum(r["n"] for r in top)
    found = {"labels": [str(r["value"]) for r in top], "counts": [r["n"] for r in top], "nulls": counted["nulls"] or 0,
             "other": counted["total"] - (counted["nulls"] or 0) - shown, "unique": counted["unique"]}
    return found if more is None else (found, {k: counted[f"more_{k}"] for k in more})


def distribution(t, column):
    """컬럼 하나의 분포. 숫자면 요약 통계·히스토그램(BINS 구간)·상자 그림 값, 모든 종류에 값 빈도."""
    c = column_of(t, column)
    if c.type().is_floating():  # NaN·무한은 빈 값으로 본다
        t = t.mutate(**{column: finite(c)})
        c = t[column]
    out = {"column": column, "type": kind(c.type())}
    if out["type"] != "num":
        out["values"] = values(t, c)
        return out
    x = c.cast("float64")
    more = dict(n=x.count(), min=x.min(), max=x.max(), mean=x.mean(), q1=x.quantile(0.25), median=x.quantile(0.5), q3=x.quantile(0.75))
    top = values_top(t, c)  # 값 빈도 조회는 한 번만(집계를 다시 해도 다시 읽지 않는다)
    try:
        out["values"], s = values(t, c, {**more, "std": x.std()}, top)  # 값 빈도의 집계에 요약 통계를 같이 실어 질의를 아낀다
    except Exception as e:  # ±1.7e308 같은 값이면 표준편차가 범위를 넘어 질의가 실패한다: 그때만 표준편차를 빼고 집계를 다시. 취소·시간 초과 등 다른 오류는 그대로 올린다
        if not re.search(r"out of range|overflow", str(e), re.I):  # DuckDB "out of range", SQL Server "Arithmetic overflow", Oracle "numeric overflow"
            raise
        out["values"], s = values(t, c, more, top)
        s["std"] = None
    out["stats"] = s
    if not s["n"]:
        return out
    low, high = s["min"], s["max"]
    half = 2.0 if math.isinf(high - low) else 1.0  # 최대-최소가 실수 범위를 넘으면 반으로 줄여 구간을 나눈다
    width = (high / half - low / half) / BINS or 1
    kept = t.filter(x.notnull())
    bucket = ((kept[column].cast("float64") / half - low / half) / width).floor().cast("int64")
    bucket = ibis.ifelse(bucket >= BINS, BINS - 1, bucket)
    found = {r["b"]: r["n"] for r in rows(kept.group_by(bucket.name("b")).aggregate(n=kept.count()))}
    bins = BINS if high > low else 1
    out["histogram"] = {"edges": [low + width * i for i in range(bins + 1)] if half == 1 else [low * (1 - i / bins) + high * (i / bins) for i in range(bins + 1)], "counts": [found.get(i, 0) for i in range(bins)]}
    iqr = s["q3"] - s["q1"]
    fence_low, fence_high = s["q1"] - 1.5 * iqr, s["q3"] + 1.5 * iqr
    inside = (x >= fence_low) & (x <= fence_high)
    w = one(t.aggregate(low=x.min(where=inside), high=x.max(where=inside), out=(x.notnull() & ~inside).sum()))
    out["box"] = {"q1": s["q1"], "median": s["median"], "q3": s["q3"], "low": w["low"], "high": w["high"], "outliers": w["out"] or 0}
    return out


def correlation(t, columns):
    """숫자 컬럼들의 피어슨 상관계수 행렬. 고른 컬럼이 모두 비어 있지 않은 행만 쓴다(n).
    평균을 먼저 구하고 평균을 뺀 곱의 합으로 구한다(두 번 집계, 큰 값에서도 정확도를 지킨다). 값이 하나뿐인 컬럼과는 None."""
    if not isinstance(columns, list) or not 2 <= len(columns) <= MAX_CORR or len(set(columns)) != len(columns):
        raise ValueError(f"상관관계는 서로 다른 숫자 컬럼 2~{MAX_CORR}개를 고르세요.")
    xs = [finite(column_of(t, name, "num")) for name in columns]
    s = t.filter([x.notnull() for x in xs])
    xs = [finite(s[name]) for name in columns]
    means = one(s.aggregate(n=s.count(), **{f"m{i}": x.mean() for i, x in enumerate(xs)}))
    n = means["n"]
    if n < 2:
        return {"columns": columns, "n": n, "matrix": [[None] * len(columns) for _ in columns]}
    d = [x - means[f"m{i}"] for i, x in enumerate(xs)]
    sums = one(s.aggregate(**{f"p{i}_{j}": (d[i] * d[j]).sum() for i in range(len(d)) for j in range(i, len(d))}))
    def r(i, j):
        i, j = min(i, j), max(i, j)
        den = math.sqrt(sums[f"p{i}_{i}"] * sums[f"p{j}_{j}"])
        return None if not den else max(-1.0, min(1.0, sums[f"p{i}_{j}"] / den))
    return {"columns": columns, "n": n, "matrix": [[r(i, j) for j in range(len(columns))] for i in range(len(columns))]}


def scatter(t, x, y):
    """두 숫자 컬럼의 점(둘 다 비어 있지 않은 행, MAX_POINTS개)과 상관계수. 점이 많으면 Table.sample(고정 씨앗 SEED)로 고르게 뽑는다
    (대시보드 points_data와 같은 방법, 연결이 못 하면 앞에서부터)."""
    if x == y:
        raise ValueError("X와 Y는 다른 컬럼으로 고르세요.")
    cx, cy = column_of(t, x, "num"), column_of(t, y, "num")
    corr = correlation(t, [x, y])
    s = t.filter(finite(cx).notnull(), finite(cy).notnull())
    picked = s.select(finite(s[x]).name("x"), finite(s[y]).name("y"))
    if corr["n"] > MAX_POINTS:
        even = picked.sample(MAX_POINTS / corr["n"], seed=SEED)
        picked = even if source.compiles(even) else picked
    found = rows(picked.limit(MAX_POINTS))
    return {"x": x, "y": y, "points": [[p["x"], p["y"]] for p in found], "n": corr["n"], "r": corr["matrix"][0][1]}


def finite_value(t, value, into=None):
    """집계할 값 컬럼이 실수면 NaN·무한을 빈 값으로 바꾼 표(하나가 그룹 값 전체를 빈 값으로 만들지 않게). 아니면 그대로. into를 주면 원래 컬럼은 두고 into 컬럼에 바꾼 값을 둔다."""
    return t.mutate(**{into or value: finite(t[value])}) if value in t.columns and t[value].type().is_floating() else t


def groups(t, by, value, agg, opts=None):
    """그룹 비교: by의 값마다 value의 agg(count·sum·mean). 행이 많은 MAX_GROUPS개 그룹을 값이 큰 순으로."""
    g = column_of(t, by, what="그룹 컬럼")
    if g.type().is_floating():  # NaN·무한과 빈 값이 "(빈 값)" 그룹 여럿으로 갈라지지 않게 빈 값으로
        t = t.mutate(**{by: prep_ops.nan_to_null(t[by])})
    src = "__finite" if by == value and t[value].type().is_floating() else value  # 같은 컬럼으로 묶으면 그룹은 원래 값으로 두고 집계에만 바꾼 값을 쓴다
    t = finite_value(t, value, "__finite" if src != value else None)
    g = t[by]
    metric = chart_metric(t, src, agg, opts)
    found = rows(t.group_by(g.name("group")).aggregate(v=metric, n=t.count()).order_by([ibis.desc("n"), "group"]).limit(MAX_GROUPS + 1))
    more = len(found) > MAX_GROUPS
    found = found[:MAX_GROUPS]
    if all(r["v"] is None or isinstance(r["v"], (int, float)) for r in found):  # 숫자면 값이 큰 순, 글자·목록(최빈값·이어 붙이기 …)이면 행이 많은 순 그대로
        found = sorted(found, key=lambda r: (r["v"] is None, -(r["v"] or 0)))
    return {"by": by, "value": value, "agg": agg, "labels": ["(빈 값)" if r["group"] is None else str(r["group"]) for r in found],
            "values": [r["v"] for r in found], "counts": [r["n"] for r in found], "more": more}


def trend(t, column, unit, value, agg, opts=None):
    """시계열 추이: 날짜 컬럼을 unit(day·week·month)으로 묶어 value의 agg. 최근 기간 MAX_PERIODS개(more면 더 오래된 기간이 있다). 빈 날짜는 뺀다.
    개수·합계는 사이의 빈 기간을 0으로 채운다(행이 없으면 0개·0이라서). 평균 같은 것은 빈 기간을 두지 않는다."""
    if unit not in UNITS:
        raise ValueError("묶음 단위는 day, week, month 중 하나입니다.")
    d = column_of(t, column, "date")
    chart_metric(t, value, agg, opts)  # 고른 값·집계를 먼저 검사한다
    s = finite_value(t, value).filter(d.notnull())
    metric = chart_metric(s, value, agg, opts)
    found = rows(s.group_by(period_start(s[column], UNITS[unit]).name("period")).aggregate(v=metric).order_by(ibis.desc("period")).limit(MAX_PERIODS + 1))
    more, found = len(found) > MAX_PERIODS, found[:MAX_PERIODS][::-1]
    got = {str(r["period"])[:10]: r["v"] for r in found}
    labels = list(got)
    if agg in ("count", "sum") and labels:  # 마지막 기간부터 거꾸로 MAX_PERIODS개 칸을 채운다(첫 기간까지)
        first, day, labels = labels[0], dt.date.fromisoformat(labels[-1]), []
        while len(labels) < MAX_PERIODS and day.isoformat() >= first:
            labels.insert(0, day.isoformat())
            day = {"day": lambda d: d - dt.timedelta(days=1), "week": lambda d: d - dt.timedelta(days=7), "month": lambda d: (d - dt.timedelta(days=1)).replace(day=1)}[unit](day)
        more = more or labels[0] > first
    return {"column": column, "unit": unit, "value": value, "agg": agg, "labels": labels, "values": [got.get(l, 0) for l in labels], "more": more}


def window_seconds(window):
    """퍼널 기간 제한 {n, unit}(unit은 minute·hour·day)을 초로. 비면(None) 제한 없음."""
    if window in (None, {}, ""):
        return None
    if not isinstance(window, dict) or window.get("unit") not in WINDOW_UNITS:
        raise ValueError("기간 제한은 숫자와 단위(분·시간·일)로 입력하세요.")
    n = window.get("n")
    if isinstance(n, bool) or not isinstance(n, (int, float)) or not n > 0:
        raise ValueError("기간 제한의 숫자는 0보다 커야 합니다.")
    return n * WINDOW_UNITS[window["unit"]]


def funnel_flow(t, user, step, time, steps, window=None, span=None):
    """흐름(퍼널): 사용자(user)가 단계 컬럼(step)의 값 steps를 순서대로 거쳐 간 사람 수(funnel.compute). 중간에 한 다른 행동과 되돌아간 행동은 무시한다.
    window = {n, unit} 첫 단계부터 이 시간 안에 도달한 것만, span = {start, end} 첫 단계를 이 날짜 안에 한 사람만(대시보드 기간, 뒤 단계는 기간 밖이어도 센다). 필요한 컬럼은 사용자·단계·시각 셋뿐이다."""
    seconds = window_seconds(window)
    funnel.check(t, user, step, time, steps, seconds)
    entry = None
    if span and (span.get("start") or span.get("end")):
        try:
            entry = tuple(dt.date.fromisoformat(span[k]) if span.get(k) else None for k in ("start", "end"))
        except (TypeError, ValueError):
            raise ValueError("기간(range)의 시작과 끝은 YYYY-MM-DD 모양이어야 합니다.") from None
    found = funnel.compute(t, user, step, time, steps, seconds, entry)
    return {"who": user, "step": step, "time": time, "steps": found["steps"], "window": window or None, "window_seconds": seconds, "range": span or None}


def columns(t):
    """분석할 표의 [컬럼, 종류]. 화면이 고르는 칸을 채운다."""
    return {"columns": [[name, kind(dtype)] for name, dtype in t.schema().items()]}


KINDS = {"columns": columns, "distribution": distribution, "correlation": correlation, "scatter": scatter, "groups": groups, "trend": trend, "funnel": funnel_flow}


def analyze(t, how):
    """how = {"kind", 그 분석의 인자들}. 결과에 kind를 붙여 돌려준다."""
    name = how.get("kind")
    if name not in KINDS:
        raise ValueError(f"분석 종류는 {', '.join(KINDS)} 중 하나입니다.")
    args = {"columns": (), "distribution": ("column",), "correlation": ("columns",), "scatter": ("x", "y"),
            "groups": ("by", "value", "agg", "opts"), "trend": ("column", "unit", "value", "agg", "opts"),
            "funnel": ("who", "step", "time", "steps", "window", "range")}[name]
    return {"kind": name, **KINDS[name](t, *[how.get(a) for a in args])}
