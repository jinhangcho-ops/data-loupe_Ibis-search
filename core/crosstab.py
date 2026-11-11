"""(Phase 8) 피벗(calc.pivot)·결과 요약(result.get_summary)의 다차원 집계 전용 기능: 프리셋 지표 묶음(볼륨 3종·구간 타겟 분포·평균-중앙값 괴리율·분위수 요약·ARPU/ARPPU),
인라인 전환율, 전체·부모 대비 비중(고유 수는 침투율), 전체 합계 100% 기준 비율·리프트, 파레토 누적. 창 함수를 쓰지 않는다(전체 값은 집계 한 번을 cross join, 부모 값은 left join,
누적은 받아 온 작은 결과 위에서 Polars) — 파일(Polars) 연결에서도 같은 식이다."""
from typing import TYPE_CHECKING

from core import parse, sql_funcs
from lazy import ibis

if TYPE_CHECKING:  # 구현의 polars·parse·sql_funcs import는 plans/phase-8.md 절마다 적었다(polars는 함수 안에서: 지연 로딩)
    import polars as pl

PRESETS = ("volume", "target", "gap", "quantiles", "paying")  # 프리셋 지표 묶음 id
QUANTILES = (0.25, 0.5, 0.75, 0.9, 0.99)  # 분위수 요약의 기본 분위(원문 P25·Median·P75·P90·P99, 최소·최대는 따로)
MAX_QUANTILES = 9  # 분위 목록 길이 상한
ABOVE = 0  # '0 제외' 경계: 값 > ABOVE인 행만(ARPPU·0 제외 평균)
CONVERTS = ("prev", "first")  # 인라인 전환율: 바로 앞 칸 대비, 첫 칸 대비
ADDITIVE = ("count", "sum")  # 리프트·파레토 누적을 볼 수 있는 집계(더해지는 값)
DISTINCT_HOWS = ("count_distinct", "approx_count_distinct", "nunique")  # 비중 대신 침투율이라 부르는 집계(더해지지 않는다)
SUMMARY_EXTRAS = ("dup", "gap", "quantiles", "zero")  # 결과 요약에 더할 수 있는 묶음(중복도, 중앙값·괴리율, 분위수, 0 초과 수·평균)
GRAND = "__ibis_grand_"  # cross join한 전체 값 컬럼의 앞글자(결과에 남기지 않는다)
PARENT = "__ibis_parent_"  # left join한 부모 값 컬럼의 앞글자(결과에 남기지 않는다)
SHARE = "비중"
REACH = "침투율"
PARENT_SHARE = "부모 비중"
CUM = "누적"
CUM_SHARE = "누적 비중"
DUP = "중복도"
GAP = "괴리율"
NAME_DIGITS = 6  # 분위 칸 이름을 만들 때 p × 100을 반올림하는 소수 자릿수(0.999 → P99.9)


def quantile_name(p: float) -> str:
    """[SPECIFICATION] plans/phase-8.md#quantile_name
    분위 p(0~1)를 칸 이름 'P25'·'P99.9'로 바꾼다."""
    return "P" + format(round(p * 100, NAME_DIGITS), "g")


def check_ps(ps: object) -> tuple[float, ...]:
    """[SPECIFICATION] plans/phase-8.md#check_ps
    요청의 분위 목록을 검사해 0과 1 사이 실수의 튜플로 돌려준다(없으면 QUANTILES)."""
    if ps is None:
        return QUANTILES
    if (
        not isinstance(ps, list)
        or not ps
        or len(ps) > MAX_QUANTILES
        or any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 < p < 1 for p in ps)
    ):
        raise ValueError(f"분위는 0과 1 사이 수 {MAX_QUANTILES}개까지예요(예: 0.25, 0.9).")
    return tuple(sorted({float(p) for p in ps}))


def expand(preset: dict, expr: "ibis.expr.types.Table") -> tuple[list[dict], list[dict], list[str]]:
    """[SPECIFICATION] plans/phase-8.md#expand
    프리셋 {kind, key?, target?, ps?, above?}를 (피벗 값 목록, 파생 칸 목록, 비중을 붙일 값 이름 목록)으로 펼친다."""
    if not isinstance(preset, dict) or preset.get("kind") not in PRESETS:
        raise ValueError(f"프리셋은 {', '.join(PRESETS)} 중 하나예요.")
    kind = preset["kind"]
    key, target = preset.get("key"), preset.get("target")
    if kind in ("volume", "target") and not key:
        raise ValueError(f"'{kind}' 프리셋에는 기준(key) 컬럼이 필요해요.")
    if kind != "volume" and not target:
        raise ValueError(f"'{kind}' 프리셋에는 값(target) 컬럼이 필요해요.")
    if key and kind != "gap" and kind != "quantiles":
        parse.check_name(key, expr.columns, "기준 컬럼")
    if target and kind != "volume":
        parse.check_name(target, expr.columns, "값 컬럼")
        if not expr[target].type().is_numeric():
            raise ValueError("프리셋의 값 컬럼은 숫자여야 해요.")
    above = preset.get("above", ABOVE)
    if isinstance(above, bool) or not isinstance(above, (int, float)):
        raise ValueError("above는 수여야 해요.")
    a = format(above, "g")

    def m(how: str, value: str | None, name: str, opts: dict | None = None, where: object = None) -> dict:
        return {"how": how, "value": value, "opts": opts, "where": where, "name": name}

    if kind == "volume":
        uniq = f"고유 {key}"
        return ([m("count", None, "행 수"), m("count_distinct", key, uniq)],
                [{"name": DUP, "kind": "ratio", "num": "행 수", "den": uniq}], [])
    if kind == "target":
        uniq = f"고유 {key}"
        return ([m("count_distinct", key, uniq), m("sum", target, f"{target} 합계"),
                 m("mean", target, f"{target} 평균"), m("median", target, f"{target} 중앙값")], [], [uniq])
    if kind == "gap":
        mean, median = f"{target} 평균", f"{target} 중앙값"
        return ([m("mean", target, mean), m("median", target, median)],
                [{"name": f"{target} {GAP}", "kind": "gap", "num": mean, "den": median}], [])
    if kind == "quantiles":
        qs = [m("quantile", target, f"{target} {quantile_name(p)}", {"p": p}) for p in check_ps(preset.get("ps"))]
        return [m("min", target, f"{target} 최소"), *qs, m("max", target, f"{target} 최대")], [], []
    w = expr[target] > above
    total = f"{target} 합계"
    measures = [m("sum", target, total), m("mean", target, f"{target} 평균"),
                m("mean", target, f"{target} 평균({a} 초과)", where=w)]
    derived: list[dict] = []
    if key:
        uniq, paid = f"고유 {key}", f"고유 {key}({a} 초과)"
        measures += [m("count_distinct", key, uniq), m("count_distinct", key, paid, where=w)]
        derived = [{"name": "ARPU", "kind": "ratio", "num": total, "den": uniq},
                   {"name": "ARPPU", "kind": "ratio", "num": total, "den": paid},
                   {"name": f"{a} 초과 비율", "kind": "ratio", "num": paid, "den": uniq}]
    return measures, derived, []


def conversions(names: list[str], way: str) -> list[dict]:
    """[SPECIFICATION] plans/phase-8.md#conversions
    차례대로 놓인 칸 이름들 사이의 전환율 파생 칸 목록(직전 칸 대비 또는 첫 칸 대비)을 만든다."""
    if way not in CONVERTS:
        raise ValueError(f"전환율은 {', '.join(CONVERTS)} 중 하나예요.")
    if len(names) < 2:
        raise ValueError("전환율은 칸이 둘 이상이어야 해요.")
    return [{"name": f"{names[i - 1]} → {names[i]} 전환율", "kind": "ratio", "num": names[i],
             "den": names[i - 1] if way == "prev" else names[0]} for i in range(1, len(names))]


def derive(table: "ibis.expr.types.Table", derived: list[dict]) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-8.md#derive
    집계한 표에 비율(ratio)·괴리율(gap) 파생 칸을 mutate 하나로 붙인다."""
    if not derived:
        return table
    cols: dict[str, "ibis.expr.types.Value"] = {}
    for d in derived:
        for side in (d["num"], d["den"]):
            if side not in table.columns:
                raise ValueError(f"'{side}' 칸이 없어요.")
        if d["name"] in table.columns:
            raise ValueError(f"'{d['name']}' 칸이 이미 있어요.")
        num, den = table[d["num"]].cast("float64"), table[d["den"]].nullif(0).cast("float64")
        cols[d["name"]] = num / den if d["kind"] == "ratio" else (num - table[d["den"]].cast("float64")) / den
    return table.mutate(**cols)


def relative(table: "ibis.expr.types.Table", expr: "ibis.expr.types.Table", aggs: dict[str, "ibis.expr.types.Scalar"], groups: dict[str, list[str]],
             show: str, shares: list[str], distinct: set[str]) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-8.md#relative
    전체 재집계 값을 cross join해 비중·침투율 칸, 전체 비율(total_pct)·리프트(lift) 보기, 고유 개수 칸의 열 비율(col_pct) 분모를 만든다."""
    need = list(shares)
    for t, cs in groups.items():
        if show == "total_pct":
            need.append(t)
        elif show == "lift":
            need += [t, *cs]
        elif show == "col_pct" and t in distinct:
            need += [t, *cs]
    need = list(dict.fromkeys(need))
    if not need:
        return table
    joined = table.cross_join(expr.aggregate(**{GRAND + n: aggs[n] for n in need}))

    def f(n: str) -> "ibis.expr.types.Value":
        return joined[n].cast("float64")

    def g(n: str) -> "ibis.expr.types.Value":
        return joined[GRAND + n].cast("float64")

    added = {f"{n} {REACH if n in distinct else SHARE}": f(n) / g(n).nullif(0) for n in shares}
    cols: dict[str, "ibis.expr.types.Value"] = {}
    for t, cs in groups.items():
        if show == "total_pct":
            cols |= {c: f(c) / g(t).nullif(0) for c in dict.fromkeys([t, *cs])}
        elif show == "lift":
            cols |= {c: (f(c) * g(t)) / (f(t) * g(c)).nullif(0) for c in cs if c != t}
        elif show == "col_pct" and t in distinct:
            cols |= {c: f(c) / g(c).nullif(0) for c in dict.fromkeys([t, *cs])}
    return joined.mutate(**cols, **added).select(*table.columns, *added)


def parent_table(expr: "ibis.expr.types.Table", rows: list[str], aggs: dict[str, "ibis.expr.types.Scalar"], sets: list[list[str]],
                 names: list[str]) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-8.md#parent_table
    묶음 조합마다 집계하고 한 단계 위 묶음의 값을 left join해 부모 대비 비중 칸을 붙인 표를 잇는다."""
    parts = []
    for keep in sets:
        part = sql_funcs.grouping(expr, rows, aggs, [keep])
        if not keep:
            joined = part
            ratios = {f"{n} {PARENT_SHARE}": ibis.literal(1.0, type="float64") for n in names}
        else:
            up = keep[:-1]
            par = sql_funcs.grouping(expr, rows, {PARENT + n: aggs[n] for n in names}, [up])
            joined = part.left_join(par, up) if up else part.cross_join(par)
            ratios = {f"{n} {PARENT_SHARE}": joined[n].cast("float64") / joined[PARENT + n].nullif(0).cast("float64") for n in names}
        parts.append(joined.mutate(**ratios).select(*rows, *aggs, *ratios))
    return ibis.union(*parts, distinct=False)


def pareto(frame: "pl.DataFrame", value: str, share: str) -> "pl.DataFrame":
    """[SPECIFICATION] plans/phase-8.md#pareto
    값이 큰 순으로 받아 온 피벗 결과에 누적 값과 누적 비중 칸을 Polars cum_sum으로 붙인다."""
    import polars as pl

    for name in (value, share):
        if name not in frame.columns:
            raise ValueError(f"'{name}' 칸이 없어요.")
    return frame.with_columns(pl.col(value).cum_sum().alias(f"{CUM} {value}"), pl.col(share).cum_sum().alias(CUM_SHARE))


def summary_columns(extra: list[str], ps: tuple[float, ...]) -> list[str]:
    """[SPECIFICATION] plans/phase-8.md#summary_columns
    결과 요약에 고른 묶음(SUMMARY_EXTRAS)이 더하는 칸 이름들을 정한 차례로 돌려준다."""
    if any(e not in SUMMARY_EXTRAS for e in extra):
        raise ValueError(f"요약 묶음은 {', '.join(SUMMARY_EXTRAS)} 중에서 골라요.")
    names = {"dup": ["dup"], "gap": ["median", "gap"], "quantiles": [quantile_name(p) for p in ps], "zero": ["above", "above_mean"]}
    return [c for e in SUMMARY_EXTRAS if e in extra for c in names[e]]
