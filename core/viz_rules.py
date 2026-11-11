"""(Phase 9) 시각화 가드레일의 서버 계산: 규칙 수치표(AUTO_PARAMS, 화면 web/lib/autoviz.ts가 상수 dash.auto로 받는다),
히스토그램 구간 수(Freedman-Diaconis·Sturges)와 꼬리 자르기(P1~P99 + 넘친 칸), 산점도 육각 밀도(전체 행), 상위 그룹 밖 '기타' 값."""
import math
from typing import TYPE_CHECKING

from lazy import ibis

# 구현 때 맨 위에 `from lazy import ibis`, 함수 안에서 `from analysis import stats`·`from core import result, sql_funcs`(dash_store가 이 모듈을 불러오므로 돌지 않게, plans/phase-9.md#check_auto)

if TYPE_CHECKING:
    import ibis.expr.types as ir

BIN_RULES = ("fd", "sturges")  # 히스토그램 구간 수 규칙
FD_FACTOR = 2  # Freedman-Diaconis 구간 폭 h = 2·IQR·n^(-1/3)의 계수
FD_ROOT = 3  # Freedman-Diaconis의 n^(-1/3) 세제곱근
MIN_BINS = 10# 자동 구간 수 아래 끝(원문 10)
MAX_BINS = 50  # 자동 구간 수 위 끝(원문 50)
QUARTILES = {"q1": 0.25, "q3": 0.75}  # 사분위 이름 → 분위
CLIP_LO = 0.01 # 꼬리 자르기 아래 분위(원문 P1)
CLIP_HI = 0.99  # 꼬리 자르기 위 분위(원문 P99)
HEX_FROM = 10_000  # 점이 이보다 많으면 육각 밀도(원문 10,000)
FAINT_FROM = 1_000  # 점이 이만큼부터 옅게·작게(원문 1,000)
HEX_HEIGHT = math.sqrt(3)  # 육각 격자의 세로 간격 / 가로 간격
HEX_B_SHIFT = 0.5  # 둘째 격자가 첫 격자에서 어긋난 정도(칸 간격의 절반)
HEX_COLS = 40  #육각 칸의 가로 수(정규화한 0~1 폭을 이만큼으로)
REST_MERGE = {"count": "sum", "sum": "sum", "min": "min", "max": "max"}  # '기타'로 합칠 수 있는 집계(web OTHERS_MERGE와 같음)
SCALES = ("log", "symlog")  # view.scale: 사용자가 고른 값 축 눈금(없으면 보통)
ORDERS = ("auto", "x", "value", "value_asc", "name")  # 차트 정렬 body.order: 자동 / 원래 순서(X 순) / 값 내림 / 값 오름 / 이름 순
LEGACY_ORDER = "x"  # body.order가 없는 기존 카드의 정렬(처음 열 때 지금 순서 유지, 사용자 결정 1)
ORDER_NAMES = {"auto": "자동", "x": "원래 순서", "value": "값 큰 순", "value_asc": "값 작은 순", "name": "이름 순"}  # 정렬 다섯 가지의 보이는 이름(사용자 결정 2026-11-09). 쿼리 화면 #corder·대시보드 Tools [정렬]이 상수 dash.order_names로 받는다
PREFS_FILE = "viz.json"  # 설정 폴더의 시각화 설정 파일(낱말 목록·쿼리 화면 자동 규칙 기본값), settings.read·write
WORD_LISTS = ("good", "bad", "other", "null")  # 의미 색·핀 낱말 목록: 긍정 / 부정 / 기타 / 결측
MAX_WORDS = 200  # 목록 하나의 낱말 수 상한
MAX_WORD_LEN = 50  # 낱말 하나의 글자 수 상한
DEFAULT_WORDS = {  # 설정 파일이 없을 때의 낱말(사용자가 더하고 고치고 지운다)
    "good": ["증가", "상승", "성장", "개선", "유지", "전환", "성공", "긍정", "흑자"],
    "bad": ["감소", "하락", "이탈", "손실", "실패", "부정", "취소", "환불", "적자", "해지"],
    "other": ["기타", "그 밖", "Others", "Other", "others", "other"],
    "null": ["(빈 값)", "None", "null", "NULL"],
}
SERVER_RULES = ("bins", "tail", "density")  # 값을 다시 구해야 하는 규칙(카드 정의 def_hash에 넣는다)
# 규칙 → 수치 → (기본값, 최솟값, 최댓값) 또는 (기본값, 고를 값들). 기본값은 원문 수치와 사용자가 확정한 값(plans/phase-9.md 개요 "확정한 기본값(사용자 결정 2026-11-04)")
AUTO_PARAMS = {
    "sort": {"group_by": ("", None)},
    "pin": {},
    "topn": {"v": (15, 2, 50), "h": (25, 2, 50)},
    "round": {"max": (6, 2, 20), "keep": (5, 1, 19)},
    "horizontal": {"from": (10, 2, 50)},
    "spaghetti": {"max": (7, 2, 50), "keep": (5, 1, 49), "mode": ("gray", ("gray", "small"))},
    "zero": {},
    "yzoom": {"pad": (0.1, 0, 1)},
    "log_hint": {"ratio": (100, 2, 1_000_000), "skew": (2, 0, 100)},
    "sync": {},
    "resample": {"mode": ("lttb", ("lttb", "rollup")), "px": (1, 1, 20)},
    "density": {"faint": (FAINT_FROM, 1, 1_000_000_000), "hex": (HEX_FROM, 1, 1_000_000_000), "alpha": (0.8, 0.05, 1), "faint_alpha": (0.2, 0.05, 1), "cols": (HEX_COLS, 5, 200)},
    "num": {"style": ("ko", ("ko", "en"))},
    "refs": {"lines": (["p80"], ("mean", "median", "p80"))},
    "semantic": {},
    "bins": {"rule": ("fd", BIN_RULES), "min": (MIN_BINS, 1, 100), "max": (MAX_BINS, 1, 100)},
    "tail": {"lo": (CLIP_LO, 0, 0.5), "hi": (CLIP_HI, 0.5, 1)},
}
AUTO_NAMES = {  # 자동 규칙의 보이는 이름(쿼리·분석 화면 autoPanel·대시보드 Tools가 상수 dash.auto_names로 받는다). 키는 AUTO_PARAMS와 같다
    "sort": "정렬", "pin": "기타·빈 값 맨 뒤", "topn": "Top-N", "round": "파이 상위 + 기타", "horizontal": "가로 막대", "spaghetti": "선 많을 때 줄이기",
    "zero": "막대 0 기준선", "yzoom": "선 값 축 확대", "log_hint": "로그 축 제안", "sync": "보조 축 맞춤", "resample": "점 줄이기·묶기", "num": "숫자 줄이기", "refs": "참조선",
    "semantic": "의미 색", "bins": "구간 수 자동", "tail": "꼬리 자르기", "density": "점 밀도",
}


def auto_bins(n: int, q1: float | None, q3: float | None, lo: float, hi: float, rule: str, low: int, high: int) -> int:
    """[SPECIFICATION] plans/phase-9.md#auto_bins
    히스토그램 구간 수: rule "fd"는 Freedman-Diaconis(IQR이 0이면 Sturges), "sturges"는 ⌈log2 n⌉+1, 결과를 low~high로 자른다. hi == lo면 1, n < 1이면 ValueError."""
    if n < 1 or rule not in BIN_RULES or low > high or low < 1:
        raise ValueError(f"구간 수 인자가 올바르지 않아요: n={n}, rule={rule}, low={low}, high={high}")
    if hi == lo:
        return 1
    if rule == "fd" and q1 is not None and q3 is not None and q3 - q1 > 0:
        k = math.ceil((hi - lo) / (FD_FACTOR * (q3 - q1) * n ** (-1 / FD_ROOT)))
    else:
        k = math.ceil(math.log2(n)) + 1
    return min(high, max(low, k))


def tail_histogram(t: "ir.Table", column: str, bins: int | None, rule: str, low: int, high: int, clip: tuple[float, float] | None) -> dict:
    """[SPECIFICATION] plans/phase-9.md#tail_histogram
    숫자 컬럼 column의 히스토그램 {edges, counts, under, over, clip}: clip(lo, hi 분위)이 있으면 그 분위 사이만 구간으로 나누고 밖은 under·over 칸으로 센다. bins가 None이면 auto_bins."""
    from analysis import stats
    from core import sql_funcs

    s = t.filter(stats.finite(t[column]).notnull())
    s = s.select(y=stats.finite(s[column]))
    x = s.y
    need = {"mn": x.min(), "mx": x.max()}
    if clip is not None or rule == "fd":
        need |= {name: sql_funcs.aggregate("quantile", s, "y", None, {"p": p}) for name, p in QUARTILES.items()}
    if clip is not None:
        need |= {f"p{i}": sql_funcs.aggregate("quantile", s, "y", None, {"p": clip[i]}) for i in (0, 1)}
    m = stats.one(s.aggregate(n=s.count(), **need))
    if not m["n"]:
        return {"edges": [], "counts": [], "under": 0, "over": 0, "clip": None}
    a, b = (m["p0"], m["p1"]) if clip is not None else (m["mn"], m["mx"])
    half = 2.0 if math.isinf(b - a) else 1.0  # 최대-최소가 실수 범위를 넘으면 반으로 줄여 구간을 나눈다
    q1, q3 = (None if m.get(q) is None else m[q] / half for q in ("q1", "q3"))
    k = 1 if a == b else bins if bins is not None else auto_bins(m["n"], q1, q3, a / half, b / half, rule, low, high)
    width = (b / half - a / half) / k or 1
    index = ((x / half - a / half) / width).floor().cast("int64")
    slot = ibis.ifelse(x < a, -1, ibis.ifelse(x > b, k, ibis.ifelse(index >= k, k - 1, index)))
    found = {r["b"]: r["c"] for r in stats.rows(s.group_by(slot.name("b")).aggregate(c=s.count()))}
    edges = [a + (b - a) * i / k for i in range(k + 1)] if half == 1 else [a * (1 - i / k) + b * (i / k) for i in range(k + 1)]
    return {"edges": edges, "counts": [found.get(i, 0) for i in range(k)], "under": found.get(-1, 0), "over": found.get(k, 0), "clip": [a, b] if clip is not None else None}


def bin_args(auto: dict, bins: int | None, fixed: int) -> tuple[int | None, str, int, int, tuple[float, float] | None]:
    """[SPECIFICATION] plans/phase-9.md#bin_args
    검사한 view.auto와 사용자 구간 수(bins)로 tail_histogram의 (bins, rule, low, high, clip)를 만든다. bins 규칙을 끄면 fixed, 꼬리 규칙을 끄면 clip None."""
    merged = {
        rule: {name: spec[0] for name, spec in AUTO_PARAMS[rule].items()} | (auto[rule] if isinstance(auto.get(rule), dict) else {})
        for rule in ("bins", "tail")
    }
    if bins is None and auto.get("bins") is False:
        bins = fixed
    clip = None if auto.get("tail") is False else (merged["tail"]["lo"], merged["tail"]["hi"])
    return bins, merged["bins"]["rule"], merged["bins"]["min"], merged["bins"]["max"], clip


def hex_cells(t: "ir.Table", x: str, y: str, cols: int) -> dict:
    """[SPECIFICATION] plans/phase-9.md#hex_cells
    두 숫자 컬럼의 전체 행을 정규화한 0~1 평면의 육각 칸(가로 cols개, 두 격자 중 가까운 중심)으로 세어 {cols, x: [lo, hi], y: [lo, hi], cells: [[u, v, n]…]}."""
    from analysis import stats

    s = t.filter(stats.finite(t[x]).notnull(), stats.finite(t[y]).notnull())
    s = s.select(px=stats.finite(s[x]), py=stats.finite(s[y]))
    m = stats.one(s.aggregate(n=s.count(), xlo=s.px.min(), xhi=s.px.max(), ylo=s.py.min(), yhi=s.py.max()))
    if not m["n"]:
        return {"cols": cols, "x": None, "y": None, "cells": []}
    w = 1 / cols
    h = w * HEX_HEIGHT
    xspan, yspan = m["xhi"] - m["xlo"], m["yhi"] - m["ylo"]
    u = (s.px - m["xlo"]) / xspan if xspan else ibis.literal(0.0)
    v = (s.py - m["ylo"]) / yspan if yspan else ibis.literal(0.0)
    s = s.select(u=u, v=v)
    s = s.mutate(ia=(s.u / w).round(), ja=(s.v / h).round(), ib=(s.u / w).floor(), jb=(s.v / h).floor())
    s = s.mutate(
        da=(s.u - s.ia * w) ** 2 + (s.v - s.ja * h) ** 2,
        db=(s.u - (s.ib + HEX_B_SHIFT) * w) ** 2 + (s.v - (s.jb + HEX_B_SHIFT) * h) ** 2,
    )
    s = s.mutate(grid=ibis.ifelse(s.da <= s.db, 0, 1))
    s = s.select(grid=s.grid, ci=ibis.ifelse(s.grid == 0, s.ia, s.ib).cast("int64"), cj=ibis.ifelse(s.grid == 0, s.ja, s.jb).cast("int64"))
    found = stats.rows(s.group_by("grid", "ci", "cj").aggregate(c=s.count()))
    shift = {0: 0.0, 1: HEX_B_SHIFT}
    cells = [[(r["ci"] + shift[r["grid"]]) * w, (r["cj"] + shift[r["grid"]]) * h, r["c"]] for r in found]
    return {"cols": cols, "x": [m["xlo"], m["xhi"]], "y": [m["ylo"], m["yhi"]], "cells": cells}


def check_prefs(prefs: object) -> dict:
    """[SPECIFICATION] plans/phase-9.md#check_prefs
    시각화 설정 {words: {good, bad, other, null: [낱말…]}, query_auto: {규칙: false}}를 검사해 정리한 사본을 돌려준다(틀리면 ValueError)."""
    from core import dash_store

    if not isinstance(prefs, dict) or set(prefs) != {"words", "query_auto"}:
        raise ValueError("차트 설정은 {words, query_auto} 모양이어야 해요.")
    words = prefs["words"]
    if not isinstance(words, dict) or set(words) != set(WORD_LISTS):
        raise ValueError(f"차트 낱말은 {', '.join(WORD_LISTS)} 목록으로 이루어져야 해요.")
    clean_words = {}
    for name in WORD_LISTS:
        items = words[name]
        if not isinstance(items, list) or len(items) > MAX_WORDS:
            raise ValueError(f"'{name}' 낱말 목록은 {MAX_WORDS}개 이하의 목록이어야 해요.")
        for w in items:
            if not isinstance(w, str) or not 1 <= len(w) <= MAX_WORD_LEN or w != w.strip():
                raise ValueError(f"'{name}' 목록의 낱말이 올바르지 않아요(1~{MAX_WORD_LEN}자, 앞뒤 공백 없음): {w!r}")
        if len(set(items)) != len(items):
            raise ValueError(f"'{name}' 목록에 겹치는 낱말이 있어요.")
        clean_words[name] = list(items)
    query_auto = dash_store.check_auto(prefs["query_auto"])
    if any(v is not False for v in query_auto.values()):
        raise ValueError("query_auto는 {규칙: false} 모양이어야 해요.")
    return {"words": clean_words, "query_auto": query_auto}


def load_prefs() -> dict:
    """[SPECIFICATION] plans/phase-9.md#load_prefs
    설정 폴더의 PREFS_FILE을 읽어 check_prefs한 것. 파일이 없으면 {words: DEFAULT_WORDS, query_auto: {}}."""
    from core import settings  # 늦은 import

    found = settings.read(PREFS_FILE)
    if found is None:
        return {"words": {name: list(items) for name, items in DEFAULT_WORDS.items()}, "query_auto": {}}
    return check_prefs(found)


def save_prefs(prefs: dict) -> dict:
    """[SPECIFICATION] plans/phase-9.md#save_prefs
    check_prefs한 설정을 settings.updating 잠금 안에서 PREFS_FILE에 쓰고(settings.write) 쓴 것을 돌려준다."""
    from core import settings  # 늦은 import

    clean = check_prefs(prefs)
    with settings.updating(PREFS_FILE):
        settings.write(PREFS_FILE, clean)
    return clean


def rest_values(expr: "ir.Table", x: str, series: list[dict], split: str | None, parts: list | None, labels: list) -> list[float | None]:
    """[SPECIFICATION] plans/phase-9.md#rest_values
    받은 X 값(labels) 밖 행의 계열 값(group_chart 계열 차례 = 계열 × 나눈 값). 합칠 수 있는 집계(REST_MERGE)만 구하고 나머지 계열은 None."""
    from core import result, sql_funcs

    known = [v for v in labels if v is not None]
    outside = expr.filter(expr[x].notnull(), ~expr[x].isin(known)) if None in labels else expr.filter(expr[x].isnull() | ~expr[x].isin(known))
    if split and parts:
        outside = outside.filter(outside[split].isin(parts))
    mergeable = {i for i, s in enumerate(series) if sql_funcs.ALIASES.get(s.get("agg"), s.get("agg")) in REST_MERGE}
    metrics = {f"v{i}": result.chart_metric(outside, series[i].get("y"), series[i].get("agg"), series[i].get("opts")) for i in sorted(mergeable)}
    if not split or not parts:
        row = outside.aggregate(**metrics).to_polars().row(0, named=True) if metrics else {}
        return [result.number(row.get(f"v{i}")) if i in mergeable else None for i in range(len(series))]
    found = {str(r[split]): r for r in outside.group_by(split).aggregate(**metrics).to_polars().iter_rows(named=True)} if metrics else {}
    return [result.number(found.get(str(part), {}).get(f"v{i}")) if i in mergeable else None for i in range(len(series)) for part in parts]
