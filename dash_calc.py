"""대시보드 카드의 값 구하기: 카드의 source(다시 구할 기록)로 data를 만든다. 저장은 dash_store, 라우트는 dash_app이 맡는다.
날짜 X축 차트는 일 단위로 계열마다 {sum, count, min, max}를 구해 두고(화면이 주·월·연으로 다시 묶는다), 다시 묶을 수 없는 고유 개수(nunique)는 네 단위를 여기서 따로 구한다.
일 값이 많아 카드 크기를 넘으면 앞을 자르고 월·연은 여기서 따로 구한다. 항목별 차트는 result.get_chart, 분석은 prep_app.analyzed, 지표는 core.metric_values를 쓴다."""
import json

import analysis
import core
import dash_store
import metrics
import parse
import prep_app
import result
import source as connection
from server import run

AGGS = ("count", "sum", "mean", "min", "max", "nunique")  # 날짜 X축 차트의 집계
PARTS = ("sum", "count", "min", "max")  # 일 값 성분. 다시 묶는 방법: sum=Σsum, count=Σcount, mean=Σsum/Σcount, min·max=min·max
TRUNC = {"day": "D", "week": "W", "month": "M", "year": "Y"}  # 단위 → Ibis truncate 단위(주는 월요일 시작, analysis.trend와 같다)
ROOM = 0.8  # 일 값이 카드 크기 상한의 이만큼까지만 차지하게 한다(나머지는 source 같은 다른 항목)
PERCENT = "percent"
MAX_POINTS = analysis.MAX_POINTS  # 산점도·버블의 점 수(분석의 산점도와 같다)
SEED = 20260101  # 고르게 뽑을 때의 고정 씨앗(다시 구해도 같은 점)
POINT_TYPES = ("scatter", "bubble")
MAX_NUM_COLS = 200  # 카드에 알려 줄 숫자 컬럼 이름 수(버블의 [크기] 후보)


def series_name(s):
    """계열 이름. 화면의 seriesName과 같다: 개수, 합계 (컬럼)."""
    return metrics.AGG_NAMES[s["agg"]] + ("" if s["agg"] == "count" else f" ({s['y']})")


def clean_series(found):
    """요청의 계열 [{y, agg}]을 검사해 정리한다."""
    if not isinstance(found, list) or not found:
        raise ValueError("계열을 하나 이상 넣으세요.")
    out = []
    for s in found:
        agg = s.get("agg") if isinstance(s, dict) else None
        if agg not in AGGS:
            raise ValueError(f"집계 방식은 {', '.join(AGGS)} 중 하나입니다.")
        out.append({"y": s.get("y") or None, "agg": agg})
    return out


def label(value):
    """날짜 라벨 'YYYY-MM-DD'."""
    return str(value)[:10]


def component_exprs(t, i, s):
    """계열 i의 일 값 성분 식 {열 이름: 식}. count는 행 수, nunique는 고유 개수 하나(v), 그 밖은 PARTS(숫자 컬럼 필요)."""
    if s["agg"] == "count":
        return {f"s{i}_count": t.count()}
    parse.check_name(s["y"], t.columns, "Y 컬럼")
    if s["agg"] == "nunique":
        return {f"s{i}_v": t[s["y"]].nunique()}
    if not t[s["y"]].type().is_numeric():
        raise ValueError("합계·평균·최소·최대는 숫자 컬럼을 Y로 골라야 합니다.")
    col = t[s["y"]].cast("float64")
    return {f"s{i}_sum": col.sum(), f"s{i}_count": col.count(), f"s{i}_min": col.min(), f"s{i}_max": col.max()}


def final_expr(t, s):
    """계열 하나의 단위별 최종 값 식(월·연을 서버에서 구할 때)."""
    if s["agg"] == "count":
        return t.count()
    parse.check_name(s["y"], t.columns, "Y 컬럼")
    if s["agg"] == "nunique":
        return t[s["y"]].nunique()
    if not t[s["y"]].type().is_numeric():
        raise ValueError("합계·평균·최소·최대는 숫자 컬럼을 Y로 골라야 합니다.")
    return getattr(t[s["y"]].cast("float64"), s["agg"])()


def grouped(t, x, unit, split, parts, exprs):
    """x를 unit으로 자르고(나누기가 있으면 그 값과 함께) 묶어 exprs(t)의 집계를 구한 Polars 표(열 x, s, 집계들). 날짜 순."""
    if split:
        t = t.filter(t[split].isin(parts))
    keys = [t[x].truncate(TRUNC[unit]).name("x")] + ([t[split].name("s")] if split else [])
    return t.group_by(keys).aggregate(**exprs(t)).order_by("x").to_polars()


def split_parts(t, split):
    """나눌 값: 행이 많은 순으로 최대 result.MAX_SPLITS개와 더 있는지."""
    parse.check_name(split, t.columns)
    filled = t.filter(t[split].notnull())
    sizes = filled.group_by(split).aggregate(n=filled.count())
    found = sizes.order_by([sizes.n.desc(), sizes[split]]).limit(result.MAX_SPLITS + 1).to_polars()[split].to_list()
    return found[:result.MAX_SPLITS], len(found) > result.MAX_SPLITS


def outputs(series, parts):
    """출력 계열 [(이름, 계열 번호, 집계, 나눈 값)]. 나누지 않으면 계열마다 하나, 나누면 계열 × 나눈 값(get_chart와 같은 차례, 이름 규칙은 화면의 toDrawn)."""
    if parts is None:
        return [(series_name(s), i, s["agg"], None) for i, s in enumerate(series)]
    return [(f"{series_name(s)} · {p}" if len(series) > 1 else str(p), i, s["agg"], p) for i, s in enumerate(series) for p in parts]


def cells(frame, split):
    """표의 행을 {(라벨, 나눈 값): 행}으로."""
    return {(label(r["x"]), r["s"] if split else None): r for r in frame.iter_rows(named=True)}


def day_section(frame, series, parts, split):
    """일 값 {labels, series: [{name, agg, sum/count/min/max 또는 count 또는 values}]}."""
    found, labels = cells(frame, split), sorted({label(v) for v in frame["x"]})
    out = []
    for name, i, agg, part in outputs(series, parts):
        row = lambda lab: found.get((lab, part), {})
        names = ("v",) if agg == "nunique" else ("count",) if agg == "count" else PARTS
        out.append({"name": name, "agg": agg, **{("values" if n == "v" else n): [row(lab).get(f"s{i}_{n}") for lab in labels] for n in names}})
    return {"labels": labels, "series": out}


def unit_section(frame, series, parts, split, only=None):
    """단위별 최종 값 {labels, series: [{name, values}]}. only를 주면 그 집계의 계열만."""
    found, labels = cells(frame, split), sorted({label(v) for v in frame["x"]})
    return {"labels": labels, "series": [{"name": name, "values": [found.get((lab, part), {}).get(f"s{i}_v") for lab in labels]}
                                         for name, i, agg, part in outputs(series, parts) if only is None or agg == only]}


def trim(section, keep):
    """일 값의 뒤 keep일만 남긴다."""
    return {"labels": section["labels"][-keep:], "series": [{k: v[-keep:] if isinstance(v, list) else v for k, v in s.items()} for s in section["series"]]}


def fit(section):
    """일 값이 MAX_DAYS와 카드 크기 상한(ROOM만큼)을 넘지 않는 일수. 하루 몫의 크기는 만든 값을 직접 재서 구한다."""
    n = len(section["labels"])
    per_day = len(json.dumps(section, ensure_ascii=False, separators=(",", ":"))) / max(n, 1)
    return max(1, min(n, dash_store.MAX_DAYS, int(dash_store.MAX_CARD_BYTES * ROOM / max(per_day, 1))))


def time_data(expr, x, series, split):
    """날짜 X축: by_unit = {day: 일 값, week·month·year: 서버에서 따로 구한 단위별 값(고유 개수 계열, 잘랐을 때는 월·연의 모든 계열)}.
    일 값이 많아 잘랐으면 note에 적는다."""
    parse.check_name(x, expr.columns)
    t = expr.filter(expr[x].notnull())
    parts, more = split_parts(t, split) if split else (None, False)
    day = grouped(t, x, "day", split, parts, lambda tt: {k: e for i, s in enumerate(series) for k, e in component_exprs(tt, i, s).items()})
    section = day_section(day, series, parts, split)
    keep = fit(section)
    cut = len(section["labels"]) - keep
    by_unit = {"day": trim(section, keep) if cut else section}
    def values(unit, only=None):
        picked = {i: s for i, s in enumerate(series) if only is None or s["agg"] == only}
        return unit_section(grouped(t, x, unit, split, parts, lambda tt: {f"s{i}_v": final_expr(tt, s) for i, s in picked.items()}), series, parts, split, only)
    for unit in ("week", "month", "year"):
        if cut and unit != "week":
            by_unit[unit] = values(unit)  # 잘라 낸 앞부분까지 월·연은 서버가 구한다
        elif any(s["agg"] == "nunique" for s in series):
            by_unit[unit] = values(unit, "nunique")  # 고유 개수는 다시 묶을 수 없다
    note = f"앞 {cut:,}일은 월 단위만 있어요." if cut else ""
    return {"x": x, "by_unit": by_unit, "note": " ".join(filter(None, [note, f"나눌 값이 많아 행이 많은 {result.MAX_SPLITS}개만 표시합니다." if more else ""]))}


def category_data(expr, body, series):
    """항목별 차트: result.get_chart의 결과를 {labels, series: [{name, values}]}로."""
    labels, datasets, more = run(lambda: result.get_chart(expr, body["x"], series, body.get("split") or None, body.get("order", "x")))
    name = lambda k: series_name(series[k["series"]]) if not body.get("split") else f"{series_name(series[k['series']])} · {k['split']}" if len(series) > 1 else k["split"]
    notes = [f"그룹이 많아 {result.MAX_GROUPS}개만 표시합니다." if len(labels) == result.MAX_GROUPS else "",
             f"나눌 값이 많아 행이 많은 {result.MAX_SPLITS}개만 표시합니다." if more else ""]
    return {"x": body["x"], "values": {"labels": [str(v) for v in labels], "series": [{"name": name(k), "values": k["values"]} for k in datasets]}, "note": " ".join(filter(None, notes))}


def is_number(t, name):
    """t의 컬럼 name이 숫자(참거짓 제외)인지."""
    dtype = t[name].type()
    return dtype.is_numeric() and not dtype.is_boolean()


def number_column(t, name, what):
    """숫자 컬럼 name(없거나 숫자가 아니면 ValueError)."""
    parse.check_name(name, t.columns, what)
    if not is_number(t, name):
        raise ValueError(f"'{name}'은 숫자 컬럼이 아니라 {what}로 쓸 수 없습니다. 산점도·버블은 숫자 컬럼만 쓸 수 있어요.")
    return name


def fit_line(t, x, y):
    """전체 행(x·y가 비어 있지 않은)의 개수 n, 상관계수 r, 추세선 y = a + b·x. analysis.correlation과 같이 평균을 먼저 구하고 평균을 뺀 곱의 합으로 구한다.
    b = Σ(x−x̄)(y−ȳ)/Σ(x−x̄)², a = ȳ − b·x̄. 행이 2개 미만이거나 x가 모두 같으면 a·b는 None, y가 모두 같으면 r은 None."""
    s = t.filter(t[x].notnull(), t[y].notnull())
    xs, ys = s[x].cast("float64"), s[y].cast("float64")
    m = analysis.one(s.aggregate(n=s.count(), mx=xs.mean(), my=ys.mean()))
    if m["n"] < 2:
        return {"n": m["n"], "r": None, "a": None, "b": None}
    dx, dy = xs - m["mx"], ys - m["my"]
    p = analysis.one(s.aggregate(sxx=(dx * dx).sum(), sxy=(dx * dy).sum(), syy=(dy * dy).sum()))
    b = p["sxy"] / p["sxx"] if p["sxx"] else None
    den = (p["sxx"] * p["syy"]) ** 0.5
    return {"n": m["n"], "r": max(-1.0, min(1.0, p["sxy"] / den)) if den else None, "a": None if b is None else m["my"] - b * m["mx"], "b": b}


def points_data(expr, x, y, size=None, split=None):
    """산점도·버블의 값: 두(세) 숫자 컬럼의 점 [x, y, (크기), (그룹)]과 전체 점 수·상관계수 r·추세선(a, b)·크기의 범위.
    점은 MAX_POINTS개까지: 넘으면 Table.sample(고정 씨앗)로 고르게 뽑고(sampled "even", 조금 넘으면 상한에서 자름), 연결이 못 하면 앞에서부터(sampled "head").
    r·추세선은 뽑은 점이 아니라 전체 행으로 구한다. 나누기(split)가 있으면 행이 많은 result.MAX_SPLITS개 값과 나머지는 '그 밖'."""
    number_column(expr, x, "X")
    number_column(expr, y, "값(Y)")
    if size:
        number_column(expr, size, "크기")
    if split:
        parse.check_name(split, expr.columns)
        if expr[split].type().is_nested():
            raise ValueError(f"'{split}'은 중첩 컬럼이라 나눌 수 없습니다.")
    t = expr.filter(*[expr[c].notnull() for c in (x, y, *([size] if size else []))])
    total = analysis.one(t.aggregate(n=t.count()))["n"]
    cols = {"x": t[x].cast("float64"), "y": t[y].cast("float64"), **({"s": t[size].cast("float64")} if size else {}), **({"g": t[split].cast("string")} if split else {})}
    picked, sampled = t.select(**cols), ""
    if total > MAX_POINTS:
        even = picked.sample(MAX_POINTS / total, seed=SEED)
        picked, sampled = (even, "even") if connection.compiles(even) else (picked, "head")
    rows = picked.limit(MAX_POINTS).to_polars().rows()
    groups = []
    if split:
        counts = {}
        for r in rows:
            counts[r[-1]] = counts.get(r[-1], 0) + 1
        top = [g for g, _ in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:result.MAX_SPLITS]]
        name = lambda g: "(빈 값)" if g is None else g if g in top else "그 밖"
        rows = [[*r[:-1], name(r[-1])] for r in rows]
        groups = ["(빈 값)" if g is None else g for g in top] + (["그 밖"] if any(r[-1] == "그 밖" for r in rows) else [])
    span = analysis.one(t.aggregate(lo=cols["s"].min(), hi=cols["s"].max())) if size else {}
    note = {"even": f"점이 많아 고르게 {len(rows):,}개만 그렸어요(전체 {total:,}개).", "head": f"점이 많아 앞에서 {len(rows):,}개만 그렸어요(전체 {total:,}개)."}.get(sampled, "")
    return {"x": x, "y": y, "size": size or "", "split": split or "", "points": [list(r) for r in rows], "groups": groups, "total": total, "sampled": sampled,
            **fit_line(expr, x, y), "size_range": [span["lo"], span["hi"]] if size else None, "note": note}


def points_of(body, size):
    """차트 조회(body: X, series[0].y 또는 y, split)와 [크기] 컬럼으로 연결에서 points_data를 구한다."""
    expr = core.runnable(body, select=False)
    series = body.get("series") or [{}]
    y = body.get("y") or (series[0].get("y") if isinstance(series[0], dict) else None)
    if not isinstance(body.get("x"), str) or not y:
        raise ValueError("산점도·버블은 X와 값(Y)으로 숫자 컬럼을 고르세요.")
    return run(lambda: points_data(expr, body["x"], y, size or None, body.get("split") or None))


def chart_data(body):
    """chart 카드의 값. X가 날짜(날짜·시각 컬럼)면 일 값 중심(time_data), 아니면 항목별(category_data)."""
    expr = core.runnable(body, select=False)
    x = body.get("x")
    if not isinstance(x, str):
        raise ValueError("X 컬럼을 고르세요.")
    parse.check_name(x, expr.columns)
    t = expr[x].type()
    kinds = {"x_kind": "date" if t.is_date() or t.is_timestamp() else "num" if is_number(expr, x) else "text",
             "num_cols": [c for c in expr.columns if is_number(expr, c)][:MAX_NUM_COLS]}  # 산점도·버블을 고를 수 있는지 화면이 알 수 있게
    if kinds["x_kind"] == "date":
        return {**run(lambda: time_data(expr, x, clean_series(body.get("series")), body.get("split") or None)), **kinds}
    return {**category_data(expr, body, clean_series(body.get("series"))), **kinds}


def metric_data(body):
    """metric 카드의 값: 지표마다 한 계열({labels, series}). 나누지 않으면 막대 하나('전체'), 비율은 %로(×100)."""
    names, by = body.get("names"), body.get("by") or None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("구할 지표를 고르세요.")
    found, labels, values, more = core.metric_values(body, names, by)
    pct = lambda n: found[n]["format"] == PERCENT
    series = [{"name": f"{n} (%)" if pct(n) else n, "values": [v if v is None or not pct(n) else round(v * 100, 6) for v in values[n]]} for n in names]
    return {"x": by or "전체", "values": {"labels": labels or ["전체"], "series": series}, "note": f"값이 많아 앞 {metrics.MAX_GROUPS}개만 표시합니다." if more else ""}


def analysis_data(source):
    """analysis 카드의 값: 분석 결과(prep_app.analyzed)와 어느 쪽(표본·전체)으로 구했는지."""
    found = prep_app.analyzed({"recipe": source.get("recipe"), "how": source.get("analysis"), "full": source.get("full") is True,
                               **({"sample": source["sample"]} if "sample" in source else {})})
    info = found.get("data", {})
    return {"values": found, "note": "테이블 전체" if info.get("full") else f"표본 {info.get('rows', 0):,}행"}


def definition(card):
    """카드를 다시 구하는 정의(연결과 이름 빼고): 이 JSON의 지문이 def_hash다."""
    source = card["source"]
    points = {"points": card["view"].get("points")} if card["view"]["type"] in POINT_TYPES else {}
    return {"kind": card["kind"], **{k: source[k] for k in ("schema", "table", "body", "recipe", "analysis", "metric_id", "flow_id", "full", "sample") if k in source}, **points}


def compute(card, now):
    """검사한 카드(dash_store.check_card)의 source로 data를 구해 돌려준다(저장하지 않는다). now는 구한 시각 글자."""
    source = card["source"]
    if card["kind"] in ("chart", "metric"):
        if not isinstance(source.get("body"), dict):
            raise ValueError("이 카드에는 다시 구할 조회(source.body)가 없습니다.")
        found = chart_data(source["body"]) if card["kind"] == "chart" else metric_data(source["body"])
        if card["kind"] == "chart" and card["view"]["type"] in POINT_TYPES:  # 점 종류를 고른 카드는 점도 다시 구한다
            found["points"] = points_of(source["body"], (card["view"].get("points") or {}).get("size"))
    elif card["kind"] == "analysis":
        if not isinstance(source.get("recipe"), dict) or not isinstance(source.get("analysis"), dict):
            raise ValueError("이 카드에는 다시 구할 분석(source.recipe, source.analysis)이 없습니다.")
        found = analysis_data(source)
    else:
        raise ValueError(f"'{card['kind']}' 카드는 아직 다시 구할 수 없습니다.")
    return {**found, "computed_at": now, "def_hash": dash_store.def_hash(definition(card))}
