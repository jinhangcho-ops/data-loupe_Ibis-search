"""분석 화면의 새 분석 8가지(Phase 12): 코호트 리텐션, RFM, 파레토·ABC, 두 범주 관계 검정, A/B 결과 검정, 장바구니 연관, 지표 변화 요인 분해, 시계열 분해·이상 시점.
계산은 Ibis 집계로 하고 이 PC에는 집계 결과(상한 있음)만 받는다. stats.KINDS에 등록해 /api/analyze로 부른다. 명세는 plans/phase-12.md."""
from typing import TYPE_CHECKING

from lazy import ibis

if TYPE_CHECKING:  # polars는 함수 안에서 import한다(지연 로딩)
    import polars as pl

COHORT_UNITS = ("day", "week", "month")  # 코호트 단위(원문 일·주·월)
COHORT_SPAN = 12  # 경과 기간 칸 수 기본(N = 0 ~ 12)
MAX_COHORT_SPAN = 60  # 경과 기간 칸 수 상한
MAX_COHORTS = 60  # 코호트 줄 수 상한(넘으면 단위를 키우거나 기간을 좁히라고 거절)
RFM_BINS = 5  # R·F·M 점수 단계(분위 5등분, 1~5점)
EPOCH = (1970, 1, 1)  # 날 번호 0의 날짜(metric_calc.day_index 기준, datetime은 함수 안에서 import)
RFM_SEGMENTS = (  # (이름, R 최소, R 최대, F 최소, F 최대) — 위에서부터 처음 맞는 것. 25칸을 모두 덮는다
    ("챔피언", 4, 5, 4, 5),
    ("충성 고객", 3, 3, 4, 5),
    ("잠재 충성", 4, 5, 2, 3),
    ("신규 고객", 4, 5, 1, 1),
    ("관심 필요", 3, 3, 1, 3),
    ("이탈 위험", 1, 2, 4, 5),
    ("휴면 직전", 2, 2, 1, 3),
    ("이탈", 1, 1, 1, 3),
)
PARETO_AGGS = ("sum", "count")  # 파레토 값: 값 컬럼 합계 / 행 수
PARETO_A, PARETO_B = 0.8, 0.95  # A·B 등급 경계(원문 기본 80%·95%, 바꿀 수 있음)
MAX_ITEMS = 100_000  # 파레토 항목 수 상한(이 PC로 받는 항목별 합계 행 수)
PARETO_SHOW = 200  # 표·그림에 보일 앞 항목 수
CURVE_POINTS = 100  # 누적 곡선에 보낼 점 수
CUM_DIGITS = 12  # 등급 경계 비교 전 앞 누적 비중을 반올림할 자릿수(부동소수 오차 제거)
MAX_LEVELS = 30  # 교차표 한 축의 값 수 상한
LOW_EXPECTED = 5  # 기대 빈도 기준(원문 5 미만)
LOW_SHARE = 0.2  # 기대 빈도 5 미만 칸이 이 비율을 넘으면 경고(Cochran 규칙)
AB_METRICS = ("rate", "mean")  # A/B 지표: 비율(0·1 또는 참거짓) / 평균
MAX_AB_GROUPS = 10  # A/B 그룹 수 상한
MIN_SUPPORT = 0.01  # 장바구니 최소 지지도 기본(1%)
BASKET_TOP = 50  # 장바구니 상위 N쌍 기본
MAX_BASKET_TOP = 500  # 상위 N 상한
MAX_BASKET_ITEMS = 1000  # 쌍을 만들 항목 수 상한(넘으면 거래가 많은 항목부터 이만큼만 쓰고 안내)
MAX_PAIR_ROWS = 50_000_000  # 자기 조인이 만들 (거래, 쌍) 행 수 상한(넘으면 거절)
MAX_PAIRS = 10_000  # 이 PC로 받을 쌍 수 상한(많이 함께 산 순)
SUPPORT_DIGITS = 6  # 최소 거래 수(지지도 × 거래 수)를 올림하기 전 반올림 자릿수(부동소수 오차 제거)
MAX_DECOMP_GROUPS = 30  # 요인 분해 그룹 수(행이 많은 순, 나머지는 OTHER)
OTHER = "기타"
EMPTY = "(빈 값)"
SEASON_WINDOWS = {"day": 7, "week": 9, "month": 13}  # 이동평균 창 기본(홀수, 가운데 맞춤)
MIN_WINDOW = 3  # 이동평균 창 하한
MAX_WINDOW = 61  # 이동평균 창 상한
MAD_K = 3.0  # 이상 시점 기준 배수(원문 예 3×MAD, 보정 MAD는 prep_diag.MAD_C로)
MAX_MAD_K = 10.0  # 배수 상한
READS = {"retention": 4, "rfm": 5, "pareto": 1, "chisq": 1, "abtest": 1, "basket": 4, "decompose": 2, "seasonal": 1,
         "distribution": 5, "scatter": 5}  # DB가 계산할 때 표를 읽는 조회 수(rfm은 asof를 주면 4, 거래 키는 같은 조회). 분포: 상위 값·집계·히스토그램 2·상자, 산점도: 상관 2·표본·육각 2
WHOLE_KINDS = ("retention", "rfm", "pareto", "basket", "decompose", "seasonal")  # 늘 테이블 전체로 구하는 분석(건수·합계를 그대로 보인다)


def retention(t: "ibis.expr.types.Table", who: str, time: str, unit: str, span: int | None = None) -> dict:
    """[SPECIFICATION] plans/phase-12.md#retention
    첫 활동 기간 코호트 × 경과 기간 N의 유지율 표(metric_calc.cohort_matrix classic)와 크기 가중 평균 곡선."""
    from analysis import stats
    from metrics import metric_calc

    if unit not in COHORT_UNITS:
        raise ValueError("코호트 단위는 day, week, month 중 하나예요.")
    if span is None:
        span = COHORT_SPAN
    if isinstance(span, bool) or not isinstance(span, int) or not 1 <= span <= MAX_COHORT_SPAN:
        raise ValueError(f"경과 기간 칸 수는 1~{MAX_COHORT_SPAN} 사이의 정수예요.")
    stats.column_of(t, who)
    stats.column_of(t, time, "date")
    if who == time:
        raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")
    m = metric_calc.cohort_matrix(t, who, time, unit, "classic", span)
    if len(m["cohorts"]) > MAX_COHORTS:
        raise ValueError(f"코호트가 {MAX_COHORTS}개를 넘어요. 단위를 키우거나(일 → 주 → 월) 기간·조건으로 좁혀 주세요.")
    return {
        "who": who,
        "time": time,
        "unit": unit,
        "span": span,
        "periods": list(range(span + 1)),
        "cohorts": [{"start": c["cohort"], "size": c["size"], "retained": c["values"]} for c in m["cohorts"]],
        "average": metric_calc.average_curve(m["cohorts"]),
        "partial": m.get("partial", False),
    }


def rfm(t: "ibis.expr.types.Table", who: str, time: str, amount: str, asof: str | None = None, tx: str | None = None) -> dict:
    """[SPECIFICATION] plans/phase-12.md#rfm
    사용자별 최근성·빈도(행 수, tx를 주면 고유 거래 수)·금액을 분위 경계로 1~RFM_BINS점으로 매기고 RFM_SEGMENTS 이름별 사람 수·평균 표와 R×F 칸 수를 돌려준다."""
    import datetime as dt

    import polars as pl

    from analysis import stats
    from core import prep_ops
    from metrics import metric_calc

    epoch = dt.date(*EPOCH)
    if asof is not None:
        try:
            asof_date = dt.date.fromisoformat(asof)
        except (TypeError, ValueError) as e:
            raise ValueError("기준일은 YYYY-MM-DD 모양이어야 해요.") from e
    stats.column_of(t, who)
    stats.column_of(t, time, "date")
    stats.column_of(t, amount, "num")
    if tx is not None:
        stats.column_of(t, tx)
    names = [who, time, amount] + ([tx] if tx is not None else [])
    if len(set(names)) < len(names):
        raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")
    day = metric_calc.day_index(t[time])
    a = (asof_date - epoch).days if asof is not None else stats.one(t.aggregate(a=day.max()))["a"]
    if a is None:
        raise ValueError("시각이 모두 비어 있어요.")
    s = t.filter(t[who].notnull(), t[time].notnull(), day <= a)
    freq = s.count() if tx is None else s[tx].nunique(where=s[tx].notnull())
    u = s.group_by(s[who].name("u")).aggregate(last=metric_calc.day_index(s[time]).max(), f=freq, m=stats.finite(s[amount]).sum())
    u = u.mutate(r=(a - u.last).cast("float64"), f=u.f.cast("float64"), m=u.m.fill_null(0))
    qs = [i / RFM_BINS for i in range(1, RFM_BINS)]
    cuts = {k: prep_ops.quantiles(u, k, qs) for k in ("r", "f", "m")}
    if any(c is None or None in c for c in cuts.values()):
        raise ValueError("사용자가 없어 점수를 매길 수 없어요.")
    f_score = score_up(u.f, cuts["f"])
    m_score = score_up(u.m, cuts["m"])
    r_score = (RFM_BINS + 1) - score_up(u.r, cuts["r"])
    v = u.mutate(rs=r_score, fs=f_score, ms=m_score, seg=segment_of(r_score, f_score))  # 점수 컬럼은 rs·fs·ms: DuckDB는 대소문자를 가리지 않아 R·F·M이 r·f·m과 겹친다
    g = v.group_by(["seg", "rs", "fs"]).aggregate(users=v.count(), r=v.r.sum(), f=v.f.sum(), m=v.m.sum(), mscore=v.ms.sum())
    frame = pl.DataFrame(stats.rows(g))
    total, total_m = frame["users"].sum(), frame["m"].sum()
    order = pl.DataFrame({"seg": [seg[0] for seg in RFM_SEGMENTS], "o": list(range(len(RFM_SEGMENTS)))})
    users = pl.col("users")
    segments = (
        frame.group_by("seg")
        .agg(pl.col("users", "r", "f", "m", "mscore").sum())
        .join(order, on="seg")
        .filter(users > 0)
        .sort("o")
        .select(
            segment=pl.col("seg"),
            users=users,
            share=users / total,
            recency=pl.col("r") / users,
            frequency=pl.col("f") / users,
            monetary=pl.col("m") / users,
            m_score=pl.col("mscore") / users,
            revenue_share=(pl.col("m") / total_m) if total_m else pl.lit(None, dtype=pl.Float64),
        )
    )
    cells = {(r, f): n for r, f, n in frame.group_by("rs", "fs").agg(users.sum()).iter_rows()}
    grid = [[cells.get((r, f), 0) for f in range(1, RFM_BINS + 1)] for r in range(1, RFM_BINS + 1)]
    return {
        "who": who,
        "time": time,
        "amount": amount,
        "tx": tx,
        "asof": (epoch + dt.timedelta(days=a)).isoformat(),
        "users": total,
        "cuts": cuts,
        "bins": RFM_BINS,
        "segments": segments.to_dicts(),
        "grid": grid,
    }


def score_up(x: "ibis.expr.types.NumericValue", cuts: list[float]) -> "ibis.expr.types.IntegerValue":
    """[SPECIFICATION] plans/phase-12.md#score_up
    값이 분위 경계보다 큰 개수 + 1(1~len(cuts)+1점)을 Ibis 식으로."""
    return 1 + sum(((x > ibis.literal(float(c))).cast("int64") for c in cuts), ibis.literal(0, type="int64"))


def segment_of(r: "ibis.expr.types.IntegerValue", f: "ibis.expr.types.IntegerValue") -> "ibis.expr.types.StringValue":
    """[SPECIFICATION] plans/phase-12.md#segment_of
    R·F 점수로 RFM_SEGMENTS의 첫 맞는 이름을 고르는 ibis.cases 식."""
    return ibis.cases(
        *[((r >= r0) & (r <= r1) & (f >= f0) & (f <= f1), name) for name, r0, r1, f0, f1 in RFM_SEGMENTS],
        else_=ibis.null().cast("string"),
    )


def pareto_abc(t: "ibis.expr.types.Table", item: str, value: str | None, agg: str, a: float | None = None, b: float | None = None) -> dict:
    """[SPECIFICATION] plans/phase-12.md#pareto_abc
    항목별 합계(또는 행 수)를 큰 순으로 누적 비중과 A·B·C 등급을 매기고 등급 요약·앞 PARETO_SHOW개 행·누적 곡선을 돌려준다."""
    import math

    import polars as pl

    from analysis import stats

    if agg not in PARETO_AGGS:
        raise ValueError("파레토 값은 sum, count 중 하나예요.")
    a = PARETO_A if a is None else a
    b = PARETO_B if b is None else b
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in (a, b)) or not 0 < a < b < 1:
        raise ValueError("A 경계는 0과 B 경계 사이, B 경계는 1보다 작아야 해요(예: 0.8, 0.95).")
    stats.column_of(t, item)
    if agg == "sum":
        stats.column_of(t, value, "num")
        if item == value:
            raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")
        v = stats.finite(t[value]).sum()
    else:
        v = t.count()
    q = t.group_by(t[item].name("item")).aggregate(v=v, n=t.count()).order_by([ibis.desc("v"), "item"]).limit(MAX_ITEMS + 2)
    got = q.to_polars()
    excluded = got.filter(pl.col("item").is_null())["n"].sum()
    frame = got.filter(pl.col("item").is_not_null()).select(pl.col("item").cast(pl.String), "v")
    if frame.height > MAX_ITEMS:
        raise ValueError(f"항목이 {MAX_ITEMS:,}개를 넘어요. 조건으로 좁히거나 더 큰 묶음 컬럼을 고르세요.")
    positive = pl.col("v") > 0
    dropped = frame.filter(~positive | pl.col("v").is_null()).height
    graded = abc_grades(frame.filter(positive), a, b)
    items = graded.height
    total = graded["v"].sum()
    classes = (
        pl.DataFrame({"grade": ["A", "B", "C"]})
        .join(graded.group_by("grade").agg(items=pl.len(), v=pl.col("v").sum()), on="grade", how="left")
        .select("grade", pl.col("items").fill_null(0), share=pl.col("v").fill_null(0) / total)
        .sort("grade")
    )
    step = max(1, math.ceil(items / CURVE_POINTS))
    curve = (
        graded.with_row_index("i")
        .filter(((pl.col("i") % step) == 0) | (pl.col("i") == items - 1))
        .select(rank=(pl.col("i") + 1) / items, cum_share=pl.col("cum_share"))
    )
    return {
        "item": item,
        "value": value,
        "agg": agg,
        "a": a,
        "b": b,
        "total": total,
        "items": items,
        "dropped": dropped,
        "excluded": excluded,
        "classes": classes.to_dicts(),
        "rows": graded.head(PARETO_SHOW).to_dicts(),
        "curve": curve.to_dicts(),
        "more": items > PARETO_SHOW,
    }


def abc_grades(frame: "pl.DataFrame", a: float, b: float) -> "pl.DataFrame":
    """[SPECIFICATION] plans/phase-12.md#abc_grades
    값 큰 순 Polars 표(item, v)에 share·누적·등급 컬럼을 더한다(crosstab.pareto 재사용)."""
    import polars as pl
    from core import crosstab

    share = (pl.col("v") / pl.col("v").sum()).alias("share")
    cum = crosstab.pareto(frame.with_columns(share), "v", "share")
    cum = cum.select("item", "v", "share", pl.col(crosstab.CUM_SHARE).alias("cum_share"))
    prev = (pl.col("cum_share") - pl.col("share")).round(CUM_DIGITS)
    grade = pl.when(prev < a).then(pl.lit("A")).when(prev < b).then(pl.lit("B")).otherwise(pl.lit("C"))
    return cum.with_columns(grade.alias("grade"))


def chi_square(t: "ibis.expr.types.Table", row: str, col: str) -> dict:
    """[SPECIFICATION] plans/phase-12.md#chi_square
    두 범주 컬럼의 교차표(조회 한 번)와 카이제곱 독립성 검정·Cramér's V·기대 빈도 5 미만 칸 비율 경고."""
    import polars as pl

    from analysis import stats

    stats.column_of(t, row)
    stats.column_of(t, col)
    if row == col:
        raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")
    limit = (MAX_LEVELS + 1) ** 2 + 1
    got = t.group_by([t[row].name("r"), t[col].name("c")]).aggregate(n=t.count()).limit(limit).to_polars()
    too_many = ValueError(f"값 종류가 {MAX_LEVELS}개를 넘는 컬럼이 있어요. 값이 적은 컬럼을 고르거나 전처리에서 묶어 주세요.")
    if got.height >= limit:
        raise too_many
    both = pl.col("r").is_not_null() & pl.col("c").is_not_null()
    excluded = got.filter(~both)["n"].sum()
    kept = got.filter(both).select(pl.col("r", "c").cast(pl.String), "n").group_by("r", "c").agg(pl.col("n").sum())
    rows = sorted(kept["r"].unique().to_list())
    cols = sorted(kept["c"].unique().to_list())
    if len(rows) > MAX_LEVELS or len(cols) > MAX_LEVELS:
        raise too_many
    wide = kept.pivot(on="c", index="r", values="n").sort("r")
    observed = [list(r) for r in wide.select(cols).fill_null(0).rows()]
    found = chi_stats(observed)
    return {"row": row, "col": col, "rows": rows, "cols": cols, "n": kept["n"].sum(), "excluded": excluded, "observed": observed, **found}


def chi_stats(matrix: list[list[int]]) -> dict:
    """[SPECIFICATION] plans/phase-12.md#chi_stats
    관측 빈도 행렬에서 기대 빈도·표준화 잔차·카이제곱·자유도·p값·Cramér's V·낮은 기대 빈도 칸 비율을 구한다."""
    import math

    from metrics import metric_calc

    if len(matrix) < 2 or len(matrix[0]) < 2:
        raise ValueError("두 컬럼 모두 값이 2개 이상이어야 해요.")
    rows = [sum(r) for r in matrix]
    cols = [sum(c) for c in zip(*matrix)]
    n = sum(rows)
    if n == 0:
        raise ValueError("두 컬럼 모두 값이 2개 이상이어야 해요.")
    if 0 in rows or 0 in cols:
        raise ValueError("값이 없는 행이나 열이 있어요.")
    expected = [[r * c / n for c in cols] for r in rows]
    residuals = [[(o - e) / math.sqrt(e) for o, e in zip(orow, erow)] for orow, erow in zip(matrix, expected)]
    chi2 = sum((o - e) ** 2 / e for orow, erow in zip(matrix, expected) for o, e in zip(orow, erow))
    df = (len(rows) - 1) * (len(cols) - 1)
    cells = [e for erow in expected for e in erow]
    low_share = sum(e < LOW_EXPECTED for e in cells) / len(cells)
    return {
        "expected": expected,
        "residuals": residuals,
        "chi2": chi2,
        "df": df,
        "p": metric_calc.chi2_sf(chi2, df),
        "v": math.sqrt(chi2 / (n * min(len(rows) - 1, len(cols) - 1))),
        "low_share": low_share,
        "warn": low_share > LOW_SHARE,
    }


def ab_test(t: "ibis.expr.types.Table", group: str, metric: str, value: str, control: object = None, conf: float | None = None) -> dict:
    """[SPECIFICATION] plans/phase-12.md#ab_test
    그룹별 비율·평균(조회 한 번)으로 두 그룹은 z·Welch t 검정과 신뢰구간, 세 그룹 이상은 ANOVA(평균)·카이제곱(비율)과 대조군 대비 차이를 구한다."""
    import polars as pl

    from analysis import stats
    from core import settings
    from metrics import metric_calc

    if metric not in AB_METRICS:
        raise ValueError("A/B 지표는 rate, mean 중 하나예요.")
    if conf is None:
        conf = settings.get("sample_conf")
    lo, hi = settings.FLOATS["sample_conf"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not lo <= conf <= hi:
        raise ValueError(f"신뢰 수준은 {lo:g}~{hi:g} 사이의 %로 입력하세요.")
    stats.column_of(t, group)
    if metric == "rate":
        stats.column_of(t, value)
        if t[value].type().is_boolean():
            x = t[value].cast("int64").cast("float64")
        else:
            stats.column_of(t, value, "num")
            x = stats.finite(t[value])
    else:
        stats.column_of(t, value, "num")
        x = stats.finite(t[value])
    if group == value:
        raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")
    aggs = {"rows": t.count(), "n": x.count(), "sum": x.sum(), "mean": x.mean(), "sd": x.std()}
    if metric == "rate":
        aggs["bad"] = (x.notnull() & ~x.isin([0.0, 1.0])).sum()
    got = t.group_by(t[group].name("g")).aggregate(**aggs).order_by("g").limit(MAX_AB_GROUPS + 2).to_polars()
    named = pl.col("g").is_not_null()
    excluded = got.filter(~named)["rows"].sum() + got.filter(named).select(pl.col("rows") - pl.col("n"))["rows"].sum()
    if metric == "rate" and got["bad"].sum() > 0:
        raise ValueError("비율 지표의 값은 0 또는 1(참·거짓)이어야 해요.")
    frame = got.filter(named & (pl.col("n") > 0)).select(name=pl.col("g").cast(pl.String), n="n", mean="mean", sd="sd", sum="sum").sort("name")
    if frame.height < 2:
        raise ValueError("그룹이 두 개 이상이어야 해요.")
    if frame.height > MAX_AB_GROUPS:
        raise ValueError(f"그룹이 {MAX_AB_GROUPS}개를 넘어요.")
    groups = frame.to_dicts()
    names = [g["name"] for g in groups]
    if control is not None and str(control) not in names:
        raise ValueError(f"대조군 '{control}'이 그룹에 없어요.")
    base = str(control) if control is not None else names[0]
    c = groups[names.index(base)]
    level = conf / 100
    k = len(groups)
    pairs, small, test = [], False, None
    for g in groups:
        if g["name"] == base:
            continue
        n1, m1, sd1, n2, m2, sd2 = c["n"], c["mean"], c["sd"], g["n"], g["mean"], g["sd"]
        if metric == "rate":
            z = metric_calc.two_proportion_z(c["sum"], n1, g["sum"], n2)
            ci = metric_calc.diff_lift_ci(m1, m1 * (1 - m1) / n1, m2, m2 * (1 - m2) / n2, level)
            test = {"kind": "z", "stat": z["z"], "p": z["p"]}
            small = small or metric_calc.small_sample(n1, n2, c["sum"], g["sum"])
        else:
            w = metric_calc.welch_from_stats(m1, sd1, n1, m2, sd2, n2)
            ci = metric_calc.diff_lift_ci(m1, sd1**2 / n1, m2, sd2**2 / n2, level, w["df"])
            test = {"kind": "welch", "stat": w["t"], "df": w["df"], "p": w["p"]}
            small = small or metric_calc.small_sample(n1, n2)
        pairs.append({
            "name": g["name"],
            "diff": m2 - m1,
            "ci": list(ci["diff"]),
            "lift": metric_calc.lift(m2, m1),
            "lift_ci": list(ci["lift"]) if ci["lift"] is not None else None,
            "p": test["p"],
            "p_adj": None if test["p"] is None else min(1, test["p"] * (k - 1)),  # 두 그룹이 모두 0(또는 1)이면 p가 없다
        })
    if k > 2:
        if metric == "mean":
            test = {"kind": "anova", **anova([(g["n"], g["mean"], g["sd"]) for g in groups])}
        else:
            found = chi_stats([[g["sum"], g["n"] - g["sum"]] for g in groups])
            test = {"kind": "chi2", "stat": found["chi2"], "df": found["df"], "p": found["p"]}
    return {
        "group": group,
        "metric": metric,
        "value": value,
        "conf": conf,
        "control": base,
        "groups": groups,
        "test": test,
        "pairs": pairs,
        "small": small,
        "excluded": excluded,
    }


def anova(groups: list[tuple[int, float, float]]) -> dict:
    """[SPECIFICATION] plans/phase-12.md#anova
    그룹별 (n, 평균, 표본 표준편차)로 일원 분산분석 F·자유도·p값(metric_calc.f_sf)."""
    from metrics import metric_calc

    k = len(groups)
    if k < 2:
        raise ValueError("두 그룹 이상이어야 해요.")
    total = sum(n for n, _, _ in groups)
    if total - k < 1:
        raise ValueError("행이 그룹 수보다 많아야 해요.")
    grand = sum(n * m for n, m, _ in groups) / total
    ssb = sum(n * (m - grand) ** 2 for n, m, _ in groups)
    ssw = sum((n - 1) * (sd or 0.0) ** 2 for n, _, sd in groups)
    if ssw == 0:
        raise ValueError("그룹 안의 값이 모두 같아 검정할 수 없어요.")
    df1, df2 = k - 1, total - k
    f = (ssb / df1) / (ssw / df2)
    return {"f": f, "df1": df1, "df2": df2, "p": metric_calc.f_sf(f, df1, df2), "ssb": ssb, "ssw": ssw}


def basket(t: "ibis.expr.types.Table", key: str, item: str, support: float | None = None, top: int | None = None) -> dict:
    """[SPECIFICATION] plans/phase-12.md#basket
    거래 키별 항목 쌍을 self-join 집계로 세어 지지도·신뢰도(양방향)·향상도 상위 top쌍을 돌려준다."""
    import math

    import polars as pl

    from analysis import stats

    if support is None:
        support = MIN_SUPPORT
    if isinstance(support, bool) or not isinstance(support, (int, float)) or not 0 < support <= 1:
        raise ValueError("최소 지지도는 0보다 크고 1 이하예요(예: 0.01 = 1%).")
    if top is None:
        top = BASKET_TOP
    if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= MAX_BASKET_TOP:
        raise ValueError(f"상위 쌍 수는 1~{MAX_BASKET_TOP} 사이의 정수예요.")
    stats.column_of(t, key)
    stats.column_of(t, item)
    if key == item:
        raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")
    d = t.filter(t[key].notnull(), t[item].notnull()).select(k=t[key], i=t[item].cast("string")).distinct()
    n_tx = stats.one(d.aggregate(n=d.k.nunique()))["n"]
    if not n_tx:
        raise ValueError("거래가 없어요.")
    min_count = math.ceil(round(support * n_tx, SUPPORT_DIGITS))
    c = d.group_by("i").aggregate(n=d.count()).filter(ibis._.n >= min_count).order_by([ibis.desc("n"), "i"]).limit(MAX_BASKET_ITEMS + 1).to_polars()
    notes = []
    truncated = c.height > MAX_BASKET_ITEMS
    if truncated:
        c = c.head(MAX_BASKET_ITEMS)
        notes.append(f"거래가 많은 항목 {MAX_BASKET_ITEMS:,}개로만 쌍을 셌어요.")
    out = {"key": key, "item": item, "support": support, "top": top, "transactions": n_tx, "min_count": min_count, "items": c.height}
    if c.height < 2:
        return {**out, "pairs": [], "truncated": truncated, "note": " ".join(notes) or None}
    f = d.filter(d.i.isin(c["i"].to_list()))
    z = f.group_by("k").aggregate(c=f.count())
    rows = stats.one(z.aggregate(p=(z.c * (z.c - 1) / 2).sum()))["p"]
    if rows > MAX_PAIR_ROWS:
        raise ValueError(f"거래 하나에 항목이 많아 쌍이 {MAX_PAIR_ROWS:,}개를 넘어요. 최소 지지도를 올리거나 조건으로 좁혀 주세요.")
    g = f.view()
    g = g.select(k2=g.k, i2=g.i)
    j = f.join(g, f.k == g.k2)
    j = j.filter(j.i < j.i2)  # 등호 조인 뒤 거르기: 부등호 조인을 못 하는 연결 대비
    p = j.group_by([j.i.name("a"), j.i2.name("b")]).aggregate(n=j.count()).filter(ibis._.n >= min_count).order_by([ibis.desc("n"), "a", "b"]).limit(MAX_PAIRS + 1).to_polars()
    if p.height > MAX_PAIRS:
        p = p.head(MAX_PAIRS)
        truncated = True
        notes.append(f"함께 산 횟수가 많은 쌍 {MAX_PAIRS:,}개 안에서 골랐어요.")
    rules = pair_rules(p, c.select(item=pl.col("i"), n=pl.col("n")), n_tx, top)
    return {**out, "pairs": rules.to_dicts(), "truncated": truncated, "note": " ".join(notes) or None}


def pair_rules(pairs: "pl.DataFrame", counts: "pl.DataFrame", n_tx: int, top: int) -> "pl.DataFrame":
    """[SPECIFICATION] plans/phase-12.md#pair_rules
    쌍 수(a, b, n)와 항목 수(item, n)로 지지도·신뢰도·향상도를 붙여 향상도 큰 순 top행을 고른다."""
    import polars as pl

    def side(name: str) -> "pl.DataFrame":
        return counts.select(pl.col("item").alias(name), pl.col("n").cast(pl.Float64).alias(f"n_{name}"))

    n, n_a, n_b = pl.col("n").cast(pl.Float64), pl.col("n_a"), pl.col("n_b")
    return (
        pairs.join(side("a"), on="a")
        .join(side("b"), on="b")
        .with_columns(support=n / n_tx, conf_ab=n / n_a, conf_ba=n / n_b, lift=n * n_tx / (n_a * n_b))
        .sort(["lift", "n", "a", "b"], descending=[True, True, False, False])
        .head(top)
        .select("a", "b", "n", "support", "conf_ab", "conf_ba", "lift")
    )


def decompose(t: "ibis.expr.types.Table", time: str, base: dict, compare: dict, group: str, value: str) -> dict:
    """[SPECIFICATION] plans/phase-12.md#decompose
    두 기간의 그룹별 행 수·평균(조회 두 번)으로 전체 평균 변화를 구성(믹스)·그룹 안·상호작용으로 나누고 그룹별 기여와 폭포 단계를 돌려준다."""
    import datetime as dt

    import polars as pl

    from analysis import stats

    bad_span = ValueError("기간은 시작·끝 날짜(YYYY-MM-DD)로 입력하세요.")
    spans = []
    for p in (base, compare):
        if not isinstance(p, dict):
            raise bad_span
        try:
            lo, hi = dt.date.fromisoformat(p["start"]), dt.date.fromisoformat(p["end"])
        except (KeyError, TypeError, ValueError) as e:
            raise bad_span from e
        if lo > hi:
            raise ValueError("기간의 시작이 끝보다 뒤예요.")
        spans.append((lo, hi))
    (b0, b1), (c0, c1) = spans
    if b0 <= c1 and c0 <= b1:
        raise ValueError("두 기간이 겹쳐요.")
    stats.column_of(t, time, "date")
    stats.column_of(t, group)
    stats.column_of(t, value, "num")
    names = [time, group, value]
    if len(set(names)) < len(names):
        raise ValueError("두 칸에는 서로 다른 컬럼을 고르세요.")

    def within(tbl: "ibis.expr.types.Table", lo: dt.date, hi: dt.date) -> "ibis.expr.types.BooleanValue":
        return stats.local_start(tbl[time], "D").cast("date").between(ibis.date(lo), ibis.date(hi))

    s = t.filter(within(t, b0, b1) | within(t, c0, c1), stats.finite(t[value]).notnull())
    ranked = s.group_by(s[group].name("g")).aggregate(n=s.count()).order_by([ibis.desc("n"), "g"]).limit(MAX_DECOMP_GROUPS + 1).to_polars()
    truncated = ranked.height > MAX_DECOMP_GROUPS
    shown = s[group].cast("string")
    if truncated:
        keep = ranked.head(MAX_DECOMP_GROUPS)["g"].drop_nulls().to_list()
        label = ibis.cases((s[group].isnull(), EMPTY), (s[group].isin(keep), shown), else_=OTHER)
    else:
        label = ibis.cases((s[group].isnull(), EMPTY), else_=shown)
    got = s.group_by([within(s, c0, c1).name("after"), label.name("g")]).aggregate(n=s.count(), mean=stats.finite(s[value]).mean()).to_polars()
    sides = [
        dict(zip(part["g"], zip(part["n"], part["mean"])))
        for part in (got.filter(~pl.col("after")), got.filter(pl.col("after")))
    ]
    parts = mix_parts(sides[0], sides[1])
    groups = sorted(({"group": g, **v} for g, v in parts["groups"].items()), key=lambda r: (-abs(r["total"]), r["group"]))
    return {
        "time": time,
        "group": group,
        "value": value,
        "base": base,
        "compare": compare,
        "before": parts["before"],
        "after": parts["after"],
        "change": parts["after"] - parts["before"],
        "mix": parts["mix"],
        "rate": parts["rate"],
        "interaction": parts["interaction"],
        "groups": groups,
        "steps": [
            {"name": "기준 기간", "value": parts["before"]},
            {"name": "구성 변화", "value": parts["mix"]},
            {"name": "그룹 안 변화", "value": parts["rate"]},
            {"name": "상호작용", "value": parts["interaction"]},
            {"name": "비교 기간", "value": parts["after"]},
        ],
        "truncated": truncated,
    }


def mix_parts(before: dict[str, tuple[int, float]], after: dict[str, tuple[int, float]]) -> dict:
    """[SPECIFICATION] plans/phase-12.md#mix_parts
    그룹별 (행 수, 평균) 두 벌로 믹스·비율·상호작용 효과를 그룹마다와 합계로 구한다."""
    n0_all, n1_all = sum(n for n, _ in before.values()), sum(n for n, _ in after.values())
    if n0_all == 0 or n1_all == 0:
        raise ValueError("두 기간 중 행이 없는 기간이 있어요.")
    groups: dict[str, dict] = {}
    for g in before.keys() | after.keys():
        n0, m0 = before.get(g, (0, None))
        n1, m1 = after.get(g, (0, None))
        m0, m1 = (m1 if m0 is None else m0), (m0 if m1 is None else m1)  # 한쪽에 없는 그룹은 다른 쪽 평균을 쓴다
        w0, w1 = n0 / n0_all, n1 / n1_all
        mix, rate, inter = (w1 - w0) * m0, w0 * (m1 - m0), (w1 - w0) * (m1 - m0)
        groups[g] = {"n0": n0, "n1": n1, "w0": w0, "w1": w1, "m0": m0, "m1": m1, "mix": mix, "rate": rate, "interaction": inter, "total": mix + rate + inter}
    return {
        "before": sum(v["w0"] * v["m0"] for v in groups.values()),
        "after": sum(v["w1"] * v["m1"] for v in groups.values()),
        "mix": sum(v["mix"] for v in groups.values()),
        "rate": sum(v["rate"] for v in groups.values()),
        "interaction": sum(v["interaction"] for v in groups.values()),
        "groups": groups,
    }


def seasonal(t: "ibis.expr.types.Table", time: str, unit: str, value: str, agg: str, opts: dict | None = None, window: int | None = None, k: float | None = None) -> dict:
    """[SPECIFICATION] plans/phase-12.md#seasonal
    stats.trend의 기간별 값을 이동평균 추세 + 요일·월 계절성 + 나머지로 나누고 나머지의 수정 Z(보정 MAD, prep_diag.MAD_C)가 k를 넘는 기간을 표시한다."""
    import polars as pl

    from analysis import stats
    from prep.prep_diag import MAD_C

    if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window % 2 == 0 or not MIN_WINDOW <= window <= MAX_WINDOW):
        raise ValueError(f"이동평균 창은 {MIN_WINDOW}~{MAX_WINDOW} 사이의 홀수예요.")
    if k is None:
        k = MAD_K
    if isinstance(k, bool) or not isinstance(k, (int, float)) or not 0 < k <= MAX_MAD_K:
        raise ValueError(f"이상 시점 기준 배수는 0보다 크고 {MAX_MAD_K:g} 이하예요.")
    tr = stats.trend(t, time, unit, value, agg, opts)
    if window is None:
        window = SEASON_WINDOWS[unit]
    if len(tr["labels"]) < 2 * window:
        raise ValueError(f"기간이 적어요. 이동평균 창({window})의 두 배 이상 기간이 있어야 해요.")
    frame = pl.DataFrame({"period": tr["labels"], "value": tr["values"]}, schema={"period": pl.String, "value": pl.Float64})
    parts = seasonal_parts(frame, unit, window, k)
    rest = parts["remainder"]
    median = rest.median()
    mad = (rest - median).abs().median()
    return {
        "time": time,
        "unit": unit,
        "value": value,
        "agg": agg,
        "window": window,
        "k": k,
        "season": {"day": "weekday", "month": "month"}.get(unit),
        "labels": parts["period"].to_list(),
        "values": parts["value"].to_list(),
        "trend": parts["trend"].to_list(),
        "seasonal": parts["season"].to_list(),
        "remainder": rest.to_list(),
        "flags": parts.filter(pl.col("flag"))["period"].to_list(),
        "median": median,
        "mad": mad,
        "limit": None if mad is None else k * mad / MAD_C,  # 값이 빈 기간이 끼면 나머지가 비어 mad가 없다
        "more": tr["more"],
        "note": None if mad else "나머지가 거의 같아 이상 시점을 판정하지 않았어요.",
    }


def seasonal_parts(frame: "pl.DataFrame", unit: str, window: int, k: float) -> "pl.DataFrame":
    """[SPECIFICATION] plans/phase-12.md#seasonal_parts
    기간(period 글자)·값(value) Polars 표에 trend·season·remainder·flag 컬럼을 더한다."""
    import polars as pl

    from prep.prep_diag import MAD_C

    value, trend = pl.col("value"), pl.col("trend")
    out = frame.with_columns(trend=value.rolling_mean(window_size=window, center=True, min_samples=window))
    if unit in ("day", "month"):
        date = pl.col("period").str.to_date()
        out = out.with_columns(no=date.dt.weekday() if unit == "day" else date.dt.month())
        out = out.with_columns(sm=(value - trend).mean().over("no"))
        center = out.group_by("no").agg(pl.col("sm").first())["sm"].mean()
        season = pl.col("sm") - pl.lit(center)
    else:  # week: 계절성 없음
        season = pl.lit(0.0)
    out = out.with_columns(season=pl.when(trend.is_not_null()).then(season))
    out = out.with_columns(remainder=value - trend - pl.col("season"))
    rest = pl.col("remainder")
    dev = (rest - rest.median()).abs()
    mad = dev.median()
    flag = ((mad > 0) & (dev > k * mad / MAD_C)).fill_null(False)
    return out.with_columns(flag=flag).select("period", "value", "trend", "season", "remainder", "flag")
