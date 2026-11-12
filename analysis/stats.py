"""분석 화면의 분석: 분포, 상관관계, 그룹 비교, 시계열 추이, 흐름(퍼널). 표본(메모리 표)이든 테이블 전체(DB)든 같은 Ibis 집계로 구한다.
상관관계도 DB마다 다른 corr 함수 대신 합계 집계로 구해 어느 연결에서나 같다. 결과는 화면이 그릴 값과 표로 보일 값이다."""
import datetime as dt
import math
import re

from lazy import ibis

from analysis import insight
from core import dash_store
from core import funnel
from core import prep_ops
from core import result
from core import source
from core import viz_rules
from core.parse import check_name
from core.result import chart_metric, plain
from core.sql_funcs import period_start  # 주 시작 자르기는 식 truncate와 같은 것을 쓴다(dash_calc도 stats.period_start로 부른다)

def local_start(col, unit):
    """설정 시간대의 기간 시작(시간대 있는 시각만 local_time으로 벽시계 시각으로 바꾼 뒤 자른다)."""
    return period_start(result.local_time(col), unit)


BINS = 20  # 히스토그램 구간 수
TOP_VALUES = 20  # 값 빈도에 보일 값 수(나머지는 "그 밖")
MAX_CORR = 12  # 상관관계에 넣을 수 있는 숫자 컬럼 수
MAX_POINTS = result.MAX_POINTS  # 산점도의 점 수
MAX_GROUPS = 30  # 그룹 비교에 보일 그룹 수(행이 많은 순)
MAX_PERIODS = 400  # 시계열 추이에 보일 기간 수(최근 것)
SEED = 20260101  # 점을 고르게 뽑을 때의 고정 씨앗(다시 구해도 같은 점, 대시보드 dash_calc도 쓴다)
TOP_PERIODS, MAX_TOP_PERIODS = 5, 20  # 가장 많은 기간 찾기에서 보일 기본 개수, 상한
UNITS = {"day": "D", "week": "W", "month": "M"}  # 시계열 묶음 단위 → Ibis truncate 단위
WINDOW_UNITS = {"minute": 60, "hour": 3600, "day": 86400}  # 퍼널 기간 제한의 단위 → 초
LIMITS = {"max_steps": funnel.MAX_STEPS, "trim": funnel.DEFAULT_TRIM, "max_trim": funnel.MAX_TRIM, "max_revisit": funnel.MAX_REVISIT_DAYS, "bins": BINS, "top_values": TOP_VALUES, "max_corr": MAX_CORR, "max_points": MAX_POINTS, "max_groups": MAX_GROUPS, "max_periods": MAX_PERIODS,
          "whole": list(insight.WHOLE_KINDS), "cohort_span": insight.COHORT_SPAN, "max_cohort_span": insight.MAX_COHORT_SPAN, "pareto_a": insight.PARETO_A, "pareto_b": insight.PARETO_B,
          "pareto_show": insight.PARETO_SHOW, "min_support": insight.MIN_SUPPORT, "basket_top": insight.BASKET_TOP, "max_basket_top": insight.MAX_BASKET_TOP,
          "season_windows": insight.SEASON_WINDOWS, "max_window": insight.MAX_WINDOW, "mad_k": insight.MAD_K, "max_mad_k": insight.MAX_MAD_K, "scale_q": insight.SCALE_Q, "min_scale_q": insight.MIN_SCALE_Q, "max_scale_q": insight.MAX_SCALE_Q,
          "rfm_bins": insight.RFM_BINS, "max_levels": insight.MAX_LEVELS, "low_share": insight.LOW_SHARE}


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


def distribution(t, column, auto=None):
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
    kept = t.filter(x.notnull())
    out["histogram"] = viz_rules.tail_histogram(kept, column, *viz_rules.bin_args(auto or {}, None, BINS))
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


def scatter(t, x, y, auto=None):
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
    dens = (auto or {}).get("density")
    tier = {k: v[0] for k, v in viz_rules.AUTO_PARAMS["density"].items()} | (dens or {})
    hexed = {"hex": viz_rules.hex_cells(s, x, y, tier["cols"])} if dens is not False and corr["n"] > tier["hex"] else {}
    return {"x": x, "y": y, "points": [[p["x"], p["y"]] for p in found], "n": corr["n"], "r": corr["matrix"][0][1], **hexed}


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
    found = rows(s.group_by(local_start(s[column], UNITS[unit]).name("period")).aggregate(v=metric).order_by(ibis.desc("period")).limit(MAX_PERIODS + 1))
    more, found = len(found) > MAX_PERIODS, found[:MAX_PERIODS][::-1]
    got = {str(r["period"])[:10]: r["v"] for r in found}
    labels = list(got)
    if agg in ("count", "sum") and labels:  # 마지막 기간부터 거꾸로 MAX_PERIODS개 칸을 채운다(첫 기간까지)
        first, day, labels = labels[0], dt.date.fromisoformat(labels[-1]), []
        while len(labels) < MAX_PERIODS and day.isoformat() >= first:
            labels.insert(0, day.isoformat())
            day = {"day": lambda d: d - dt.timedelta(days=1), "week": lambda d: d - dt.timedelta(days=7), "month": lambda d: (d - dt.timedelta(days=1)).replace(day=1)}[unit](day)
        more = more or labels[0] > first
    return {"column": column, "unit": unit, "value": value, "agg": agg, "labels": labels, "values": [(got.get(l) or 0) if agg in ("count", "sum") else got.get(l) for l in labels], "more": more}  # 값이 모두 빈 기간의 합계(NULL)도 행이 없는 기간처럼 0


def busiest(t, column, unit, top=None):
    """데이터가 가장 많은 기간: 날짜 컬럼을 unit(day·week·month)으로 묶어(trend와 같은 period_start) 센 뒤 건수가 많은 상위 top개(기본 TOP_PERIODS)를 [{start, end, count}]로(건수가 같으면 앞선 기간).
    모든 기간에서 DB가 고른다(trend처럼 최근 MAX_PERIODS개만 보지 않는다)."""
    top = TOP_PERIODS if top is None else top
    if type(top) is not int or not 1 <= top <= MAX_TOP_PERIODS:
        raise ValueError(f"찾을 기간 개수는 1~{MAX_TOP_PERIODS} 사이의 정수로 입력하세요.")
    if unit not in UNITS:
        raise ValueError("묶음 단위는 day, week, month 중 하나입니다.")
    s = t.filter(column_of(t, column, "date").notnull())
    found = rows(s.group_by(local_start(s[column], UNITS[unit]).name("period")).aggregate(n=s.count()).order_by([ibis.desc("n"), "period"]).limit(top))
    ends = {"day": lambda d: d, "week": lambda d: d + dt.timedelta(days=6), "month": lambda d: (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)}[unit]
    labels = [(str(r["period"])[:10], r["n"]) for r in found]
    return {"column": column, "unit": unit, "periods": [{"start": l, "end": ends(dt.date.fromisoformat(l)).isoformat(), "count": n} for l, n in labels]}


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


def funnel_flow(t, user, step, time, steps, window=None, span=None, by=None, trim=None):
    """흐름(퍼널): 사용자(user)가 단계 컬럼(step)의 값 steps를 순서대로 거쳐 간 사람 수(funnel.compute). 중간에 한 다른 행동과 되돌아간 행동은 무시한다.
    by = 세그먼트 컬럼(있으면 응답에 segments), 단계마다 median_sec(앞 단계에서 걸린 시간 중앙값)이 steps에 들어 있다. window = {n, unit} 앞 단계부터 이 시간 안에 도달한 것만, span = {start, end} 첫 단계를 이 날짜 안에 한 사람만(대시보드 기간, 뒤 단계는 기간 밖이어도 센다). 필요한 컬럼은 사용자·단계·시각 셋뿐이다."""
    seconds = window_seconds(window)
    funnel.check(t, user, step, time, steps, seconds)
    entry = span_entry(span)
    found = funnel.compute(t, user, step, time, steps, seconds, entry, by=by or None, trim=trim)
    return {"who": user, "step": step, "time": time, "steps": found["steps"], **({"segments": found["segments"]} if "segments" in found else {}), "by": by or None, "window": window or None, "window_seconds": seconds, "range": span or None, "gaps": found["gaps"], "trim": found["trim"]}


def span_entry(span: dict | None) -> tuple[dt.date | None, dt.date | None] | None:
    """[SPECIFICATION] plans/phase-3.md#span_entry
    흐름 분석의 range {start, end}(YYYY-MM-DD)를 첫 단계 날짜 범위 (시작 date 또는 None, 끝 date 또는 None)로 읽는다. 둘 다 비면 None, 모양이 틀리면 ValueError."""
    if not span or not (span.get("start") or span.get("end")):
        return None
    try:
        return tuple(dt.date.fromisoformat(span[k]) if span.get(k) else None for k in ("start", "end"))
    except (TypeError, ValueError):
        raise ValueError("기간(range)의 시작과 끝은 YYYY-MM-DD 모양이어야 합니다.") from None


def stage_flow(t: "ibis.expr.types.Table", who: str, time: str, stages: list[dict], count: str, window: dict | None = None, span: dict | None = None, trim: float | None = None) -> dict:
    """[SPECIFICATION] plans/phase-3.md#stage_flow
    조건 단계 퍼널(AARRR·RARRA 틀 포함): funnel.stage_compute로 단계별 사용자 수·걸린 시간 표를 구해 funnel_flow와 같은 모양(who·time·steps·window·range)에 count·stages·gaps·trim을 더해 돌려준다."""
    seconds = window_seconds(window)
    found = funnel.stage_compute(t, who, time, stages, count, seconds, span_entry(span), trim)
    return {"who": who, "time": time, "count": found["count"], "stages": [s["name"] for s in found["steps"]], "steps": found["steps"], "gaps": found["gaps"],
            "trim": found["trim"], "window": window or None, "window_seconds": seconds, "range": span or None}


def columns(t):
    """분석할 표의 [컬럼, 종류]. 화면이 고르는 칸을 채운다."""
    return {"columns": [[name, kind(dtype)] for name, dtype in t.schema().items()]}


KINDS = {"columns": columns, "distribution": distribution, "correlation": correlation, "scatter": scatter, "groups": groups, "trend": trend, "busiest": busiest, "funnel": funnel_flow, "stages": stage_flow,
         "retention": insight.retention, "rfm": insight.rfm, "pareto": insight.pareto_abc, "chisq": insight.chi_square, "abtest": insight.ab_test,
         "basket": insight.basket, "decompose": insight.decompose, "seasonal": insight.seasonal}


def analyze(t, how):
    """how = {"kind", 그 분석의 인자들}. 결과에 kind를 붙여 돌려준다."""
    name = how.get("kind")
    if name not in KINDS:
        raise ValueError(f"분석 종류는 {', '.join(KINDS)} 중 하나입니다.")
    if how.get("auto") is not None:
        dash_store.check_auto(how["auto"])
    args = {"columns": (), "distribution": ("column", "auto"), "correlation": ("columns",), "scatter": ("x", "y", "auto"),
            "groups": ("by", "value", "agg", "opts"), "trend": ("column", "unit", "value", "agg", "opts"), "busiest": ("column", "unit", "top"),
            "funnel": ("who", "step", "time", "steps", "window", "range", "by", "trim"),
            "stages": ("who", "time", "stages", "count", "window", "range", "trim"),
            "retention": ("who", "time", "unit", "span"), "rfm": ("who", "time", "amount", "asof", "tx"), "pareto": ("item", "value", "agg", "a", "b"),
            "chisq": ("row", "col"), "abtest": ("group", "metric", "value", "control", "conf", "who"), "basket": ("key", "item", "support", "top"),
            "decompose": ("time", "base", "compare", "group", "value"), "seasonal": ("time", "unit", "value", "agg", "opts", "window", "k", "q")}[name]
    return {"kind": name, **KINDS[name](t, *[how.get(a) for a in args])}
