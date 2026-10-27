"""대시보드 카드의 값 구하기: 카드의 source(다시 구할 기록)로 data를 만든다. 저장은 dash_store, 라우트는 dash_app이 맡는다.
날짜 X축 차트는 일 단위로 계열마다 {sum, count, min, max}를 구해 두고(화면이 주·월·연으로 다시 묶는다), 다시 묶을 수 없는 고유 개수(nunique)는 네 단위를 여기서 따로 구한다.
일 값이 많아 카드 크기를 넘으면 앞을 자르고 월·연은 여기서 따로 구한다. 항목별 차트는 result.get_chart, 분석은 prep_app.analyzed, 지표는 core.metric_values를 쓴다."""
import datetime as dt
import re
import json
from decimal import Decimal


from analysis import stats
from core import compare
from core import querying
from core import dash_store
from core import metric_file
from core import parse
from prep import prep_app
from core import result
from core import scan_size
from core import settings
from core import source as connection
from core import sql_funcs
from dashboard import period
from dashboard import summary_text
from core import targets
from core import watch
from core.server import run
from lazy import Later

mapcard = Later("geo.mapcard")  # 지도 카드(geomap)의 값 만들기: 지도 카드를 쓸 때만 geo 패키지가 올라온다

AGGS = metric_file.AGGS  # 날짜 X축 차트의 집계: 함수 사전(sql_funcs)의 집계 전부(예전 이름 nunique 포함)
PARTS = ("sum", "count", "min", "max")  # 일 값 성분. 다시 묶는 방법: sum=Σsum, count=Σcount, mean=Σsum/Σcount, min·max=min·max
MERGE = sql_funcs.MERGE  # 일 값을 주·월·연으로 다시 묶을 수 있는 집계. 나머지(고유 개수·중앙값·분위수 …)는 단위마다 서버가 따로 구한다


def merges(agg):
    """일 값을 다시 묶을 수 있는 집계인지(아니면 일 값은 하루 몫의 값 v 하나이고, 주·월·연은 서버가 따로 구한다)."""
    return agg in MERGE
TRUNC = {"day": "D", "week": "W", "month": "M", "year": "Y"}  # 단위 → stats.period_start 단위(주는 어느 DB에서나 월요일 시작, analysis.trend와 같다)
ROOM = 0.8  # 일 값이 카드 크기 상한의 이만큼까지만 차지하게 한다(나머지는 source 같은 다른 항목)
PERCENT = "percent"
MAX_POINTS = result.MAX_POINTS  # 산점도·버블의 점 수(분석의 산점도와 같다)
SEED = stats.SEED  # 고르게 뽑을 때의 고정 씨앗(다시 구해도 같은 점, 분석의 산점도와 같다)
POINT_TYPES = ("scatter", "bubble")
VALUE_KINDS = ("kpi", "donut_kpi", "gauge", "summary", "dual_trend", "stripes", "blocks", "target_trend")  # 값 하나(또는 일 값)로 그리는 카드 종류
MAX_GROUPS = 100  # 요약의 기여 분해에 쓰는 그룹 수
TARGETS_FILE = "targets_file.json"  # 목표 파일 경로만 둔다 {path}
WATCH_KINDS = ("status", "alerts", "map")  # 엔티티별 최근 행을 상태 규칙(watch.py)으로 보는 카드
TABLE_KINDS = ("table", "sentence", "deviation", *WATCH_KINDS)
MAX_TABLE_ROWS = 500  # 표 카드에 저장하는 집계 행 수
MAX_ENTITY_ROWS = 2000  # 상태·경고·배치도가 읽는 행 수
MAX_ENTITIES = 50  # 편차 표의 엔티티 수
MAX_BUCKETS = 48  # 편차 표의 시간대 수(최근 것)
BUCKET_UNITS = {"hour": "h", "day": "D", "week": "W", "month": "M"}
MAX_NUM_COLS = 200  # 카드에 알려 줄 숫자 컬럼 이름 수(버블의 [크기] 후보)


def series_name(s):
    """계열 이름. 화면의 seriesTitle(static/core/dash.js, 쿼리 차트와 카드 메뉴가 함께 씀)과 같다: 개수, 합계 (컬럼), 옵션이 있으면 분위수 (컬럼) p=0.9처럼 뒤에 붙인다(옵션만 다른 두 계열이 이름이 같아지지 않게)."""
    name = metric_file.AGG_NAMES[s["agg"]] + ("" if s["agg"] == "count" or not s["y"] else f" ({s['y']})")
    opts = " ".join(f"{k}={','.join(str(x) for x in (v if isinstance(v, list) else [v]))}" for k, v in (s.get("opts") or {}).items() if k != "order")
    return f"{name} {opts}" if opts else name


def clean_series(found):
    """요청의 계열 [{y, agg}]을 검사해 정리한다."""
    if not isinstance(found, list) or not found:
        raise ValueError("계열을 하나 이상 넣으세요.")
    out = []
    for s in found:
        agg = s.get("agg") if isinstance(s, dict) else None
        if agg not in AGGS:
            raise ValueError(f"집계 방식은 {', '.join(AGGS[:8])} … 중 하나입니다(함수 사전의 집계 {len(AGGS)}가지).")
        opts = s.get("opts")
        if opts is not None and not isinstance(opts, dict):
            raise ValueError("집계의 옵션(opts)은 객체여야 합니다.")
        out.append({"y": s.get("y") or None, "agg": agg, **({"opts": opts} if opts else {})})
    return out


def label(value):
    """날짜 라벨 'YYYY-MM-DD'."""
    return str(value)[:10]


def component_exprs(t, i, s):
    """계열 i의 일 값 성분 식 {열 이름: 식}. count는 행 수, 다시 묶을 수 없는 집계(고유 개수·중앙값 …)는 하루 몫의 값 하나(v), 그 밖은 PARTS(숫자 컬럼 필요)."""
    if s["agg"] == "count":
        return {f"s{i}_count": t.count()}
    if not merges(s["agg"]):
        return {f"s{i}_v": final_expr(t, s)}
    parse.check_name(s["y"], t.columns, "Y 컬럼")
    if not t[s["y"]].type().is_numeric():
        raise ValueError("합계·평균·최소·최대는 숫자 컬럼을 Y로 골라야 합니다.")
    col = stats.finite(t[s["y"]])  # 실수의 NaN·무한은 빈 값으로(한 칸이 그날 합 전체를 비우지 않게)
    return {f"s{i}_sum": col.sum(), f"s{i}_count": col.count(), f"s{i}_min": col.min(), f"s{i}_max": col.max()}


def final_expr(t, s):
    """계열 하나의 단위별 최종 값 식(월·연을 서버에서 구할 때)."""
    if s["agg"] == "count":
        return t.count()
    if not merges(s["agg"]):
        if s["y"] is not None:
            parse.check_name(s["y"], t.columns, "Y 컬럼")
        return sql_funcs.aggregate(s["agg"], t, s["y"], None, s.get("opts"))
    parse.check_name(s["y"], t.columns, "Y 컬럼")
    if not t[s["y"]].type().is_numeric():
        raise ValueError("합계·평균·최소·최대는 숫자 컬럼을 Y로 골라야 합니다.")
    return getattr(stats.finite(t[s["y"]]), s["agg"])()


def grouped(t, x, unit, split, parts, exprs):
    """x를 unit으로 자르고(나누기가 있으면 그 값과 함께) 묶어 exprs(t)의 집계를 구한 Polars 표(열 x, s, 집계들). 날짜 순."""
    if split:
        t = t.filter(t[split].isin(parts))
    keys = [stats.period_start(t[x], TRUNC[unit]).name("x")] + ([t[split].name("s")] if split else [])
    return t.group_by(keys).aggregate(**exprs(t)).order_by("x").to_polars()


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
        names = ("v",) if not merges(agg) else ("count",) if agg == "count" else PARTS
        out.append({"name": name, "agg": agg, **({"merge": False} if not merges(agg) else {}), **{("values" if n == "v" else n): [row(lab).get(f"s{i}_{n}") for lab in labels] for n in names}})
    return {"labels": labels, "series": out}


def unit_section(frame, series, parts, split, only=None):
    """단위별 최종 값 {labels, series: [{name, values}]}. only(집계 → 참거짓)를 주면 그 집계의 계열만."""
    found, labels = cells(frame, split), sorted({label(v) for v in frame["x"]})
    return {"labels": labels, "series": [{"name": name, "values": [found.get((lab, part), {}).get(f"s{i}_v") for lab in labels]}
                                         for name, i, agg, part in outputs(series, parts) if only is None or only(agg)]}


def trim(section, keep):
    """일 값의 뒤 keep일만 남긴다."""
    return {"labels": section["labels"][-keep:], "series": [{k: v[-keep:] if isinstance(v, list) else v for k, v in s.items()} for s in section["series"]]}


def fit(section):
    """일 값이 MAX_DAYS와 카드 크기 상한(ROOM만큼)을 넘지 않는 일수. 하루 몫의 크기는 만든 값을 직접 재서 구한다."""
    n = len(section["labels"])
    per_day = len(json.dumps(section, ensure_ascii=False, separators=(",", ":"))) / max(n, 1)
    return max(1, min(n, dash_store.MAX_DAYS, int(dash_store.MAX_CARD_BYTES * ROOM / max(per_day, 1))))


def group_key(body):
    """같은 테이블·같은 조건(이어붙이기·조인·기간·조건·계산 줄)을 알아보는 글: 이것이 같은 카드들은 한 번에 읽는다."""
    return json.dumps({k: body.get(k) for k in (*querying.STAGE, "calcs")}, sort_keys=True, ensure_ascii=False, default=str)


def body_columns(body, *more):
    """쿼리 body가 집계에 쓰는 컬럼(계열 컬럼·X·나누기)과 more. 계산 줄(calcs)이 있거나 지표를 쓰면 None(줄이지 않는다). 쓰는 컬럼이 없으면 None."""
    if body.get("calcs") or body.get("names"):
        return None
    found = {s.get("y") for s in body.get("series") or [] if isinstance(s, dict)} | {body.get("x"), body.get("split"), *more}
    return sorted(c for c in found if isinstance(c, str) and c) or None


def card_columns(card, ctx=None):
    """카드가 읽는 컬럼들(없으면 None = 줄이지 않음). 최신화가 같은 테이블·조건의 카드들을 묶었으면(ctx.plan) 그 카드들이 쓰는 컬럼의 합집합이다."""
    src, view = card["source"], card["view"]
    if card["kind"] == "recent_rows" or (card["kind"] == "table" and not src["body"].get("series")):
        return None  # 원래 행을 보이는 카드는 모든 컬럼
    cols = body_columns(src["body"], src.get("date"), view.get("group"), *(view.get("group_cols") or []), view.get("entity"), view.get("time_col"), view.get("kind_col"), view.get("link_col"),
                        *(b.get("y") for b in view.get("blanks") or []), *(r.get("column") for r in view.get("rules") or []),  # 상태 규칙이 보는 컬럼도 읽는다
                        *([(view.get("geo") or {}).get(k) for k in ("lat", "lon", "value")] if card["kind"] == "geomap" else []))  # 지도 카드의 점 컬럼
    union = ((ctx or {}).get("plan") or {}).get("union", {}).get(group_key(src["body"]))
    return union if cols is not None and union is not None else cols


def fetch_window(ctx):
    """다시 구할 때 읽을 날짜 범위 {start, end}: 보는 기간의 시작과 직전 기간의 시작 중 앞선 것부터 보는 기간의 끝까지(비교에 직전 기간이 필요하다). 기간이 없으면 None(전체)."""
    rng, prev = (ctx or {}).get("range"), (ctx or {}).get("prev")
    if not rng or not (rng.get("start") or rng.get("end")):
        return None
    starts = [d for d in (rng.get("start"), (prev or {}).get("start")) if d]
    return {"start": min(starts) if starts else None, "end": rng.get("end")}


def card_expr(card, ctx=None, window=None):
    """카드가 읽을 식: 날짜 컬럼(source.date)에 window의 기간을 걸고 쓰는 컬럼으로 줄인 것(core.runnable의 keep)."""
    body = card["source"]["body"]
    return querying.runnable(with_period(body, card["source"].get("date"), window), select=False, keep=card_columns(card, ctx))


def fetched(card, ctx=None):
    """카드가 읽는 조회(기간을 건 것). 식을 만들 수 없는 종류(조회가 없거나 recent_rows·analysis)는 None."""
    body = (card["source"] or {}).get("body")
    if not isinstance(body, dict) or card["kind"] in ("recent_rows", "analysis"):
        return None
    return with_period(body, card["source"].get("date") or (body.get("x") if card["kind"] == "chart" else None), fetch_window(ctx))


def copies_to_pc(card, ctx=None):
    """이 카드를 구할 때 읽는 행이 이 PC로 옮겨지는지(core.runnable이 연결이 못 하는 계산이라 받아 두는 경우, 아직 받아 둔 것이 없을 때). 그렇지 않으면
    집계는 DB 안에서 하고 결과만 오므로(표 500행, 시간별 값, 점 MAX_POINTS개 이하) 작다."""
    from core import held
    shaped = fetched(card, ctx)
    if shaped is None:
        return False
    points = card["kind"] == "geomap" and bool(((card.get("view") or {}).get("geo") or {}).get("lat"))  # 지도의 점은 행을 limit로 잘라 받아 오므로 집계가 아니다
    if not points and connection.compiles(querying.build(shaped, select=False)):
        return False
    key = querying.search_key(shaped, card_columns(card, ctx))
    return not held.has(key)  # 최신화가 새로 받을 사본이면 받아 둔 것이 없는 것과 같다


def unique_per_period(card, ctx=None):
    """고유 개수류 계열이 있는 날짜 컬럼 카드가 보는 기간이 있어, 그 값을 일 값에서 못 묶고 기간(이번·직전)마다 따로 읽는지."""
    body = (card["source"] or {}).get("body")
    window = (ctx or {}).get("range")
    return card["kind"] in VALUE_KINDS and isinstance(body, dict) and bool(card["source"].get("date")) and bool(window and (window.get("start") or window.get("end"))) and any(not merges(s.get("agg")) for s in body.get("series") or [] if isinstance(s, dict))


def read_sql(card, ctx=None):
    """카드가 읽는 식(필요한 컬럼·기간만)의 SQL 글. 실행하지 않는다. 처리 용량 추정에 쓴다. 식을 만들 수 없는 종류는 None."""
    shaped = fetched(card, ctx)
    if shaped is None:
        return None
    return scan_size.sql_of(source_con(), querying.slim(querying.build(shaped, select=False), card_columns(card, ctx)))[0]


def source_con():
    """지금 연결(없으면 None)."""
    from core import server
    return server.state["con"]


def time_key(body, x, split, window):
    return json.dumps([group_key(body), x, split, window], sort_keys=True, ensure_ascii=False, default=str)


def sliced(full, series):
    """묶어서 구한 시간별 값(full)에서 series 계열만 남긴다(이름으로 찾는다)."""
    names = [series_name(s) for s in series]
    pick = lambda section: {**section, "series": [next(s for s in section["series"] if s["name"] == n) for n in names if any(s["name"] == n for s in section["series"])]}
    by_unit = {unit: pick(section) for unit, section in full["by_unit"].items()}
    return {**full, "by_unit": {u: s for u, s in by_unit.items() if s["series"]}}


def shared_time(expr, body, x, series, split, window, ctx):
    """time_data. 최신화가 같은 테이블·조건·날짜 컬럼의 카드들을 묶었으면(ctx.plan) 그 카드들의 계열을 한 번에 구해 두고(ctx.shared) 카드마다 자기 계열만 돌려준다."""
    shared, key = (ctx or {}).get("shared"), time_key(body, x, split, window)
    union = ((ctx or {}).get("plan") or {}).get("series", {}).get(key)
    if shared is None or union is None or split:
        return time_data(expr, x, series, split)
    if key not in shared:
        shared[key] = time_data(expr, x, union, None)
    return sliced(shared[key], series)


def plan_shared(cards, ctx=None):
    """같은 테이블·조건의 카드들을 묶어 한 번에 읽을 계획 {union: {그룹: 쓸 컬럼 합집합(줄일 수 없으면 None 없음)}, series: {시간별 값 열쇠: 계열 합집합}}.
    날짜 컬럼이 있는 값 카드(kpi 등)는 계열을 합쳐 일 값 집계 하나로 구한다(같은 group by면 집계 하나)."""
    union, series, window = {}, {}, fetch_window(ctx)
    for card in cards:
        body = card["source"].get("body")
        if not isinstance(body, dict):
            continue
        g, cols = group_key(body), card_columns(card)
        if cols is None:
            union[g] = None
        elif union.get(g, 0) is not None:
            union[g] = sorted({*union.get(g, []), *cols})
        if card["kind"] in VALUE_KINDS and card["kind"] not in ("gauge", "summary") and card["source"].get("date") and not body.get("names") and body.get("series"):
            try:
                found = clean_series(body["series"])
            except ValueError:
                continue  # 계열이 틀린 카드는 구할 때 자기 오류를 낸다. 다른 카드의 계획은 막지 않는다
            mine = series.setdefault(time_key(body, card["source"]["date"], None, window), [])
            mine += [s for s in found if s not in mine]
    return {"union": {g: c for g, c in union.items() if c is not None}, "series": {k: v for k, v in series.items() if v}}


def shared_context(cards, ctx=None):
    """같은 테이블·조건의 카드들을 한 번에 읽는 ctx: 기간(ctx)에 plan(plan_shared)과 shared(읽은 값을 나눠 쓰는 곳)를 더한 사본. 최신화(dash_refresh)와 실시간·기간 다시 구하기(dash_app)가 함께 쓴다.
    cards는 dash_store.check_card를 거친 카드가 아니어도 된다: 검사에 실패하거나 계획을 세울 수 없는 카드는 계획에서 빼고(그 카드는 구할 때 자기 오류를 낸다) 나머지만 묶는다."""
    ok = []
    for card in cards:
        try:
            ok.append(dash_store.check_card({**card, "data": {}}))
        except ValueError:
            continue
    return {**(ctx or {}), "plan": plan_shared(ok, ctx), "shared": {}}


def time_data(expr, x, series, split):
    """날짜 X축: by_unit = {day: 일 값, week·month·year: 서버에서 따로 구한 단위별 값(고유 개수 계열, 잘랐을 때는 월·연의 모든 계열)}.
    일 값이 많아 잘랐으면 note에 적는다."""
    parse.check_name(x, expr.columns)
    t = expr.filter(expr[x].notnull())
    parts, more = result.split_parts(t, split) if split else (None, False)
    day = grouped(t, x, "day", split, parts, lambda tt: {k: e for i, s in enumerate(series) for k, e in component_exprs(tt, i, s).items()})
    section = day_section(day, series, parts, split)
    keep = fit(section)
    cut = len(section["labels"]) - keep
    by_unit = {"day": trim(section, keep) if cut else section}
    def values(unit, only=None):
        picked = {i: s for i, s in enumerate(series) if only is None or only(s["agg"])}
        return unit_section(grouped(t, x, unit, split, parts, lambda tt: {f"s{i}_v": final_expr(tt, s) for i, s in picked.items()}), series, parts, split, only)
    for unit in ("week", "month", "year"):
        if cut and unit != "week":
            by_unit[unit] = values(unit)  # 잘라 낸 앞부분까지 월·연은 서버가 구한다
        elif any(not merges(s["agg"]) for s in series):
            by_unit[unit] = values(unit, lambda agg: not merges(agg))  # 고유 개수·중앙값 같은 집계는 일 값을 다시 묶을 수 없다
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
    s = t.filter(stats.finite(t[x]).notnull(), stats.finite(t[y]).notnull())  # NaN·무한은 빈 값(분석 화면 stats.scatter와 같이)
    xs, ys = stats.finite(s[x]), stats.finite(s[y])
    m = stats.one(s.aggregate(n=s.count(), mx=xs.mean(), my=ys.mean()))
    if m["n"] < 2:
        return {"n": m["n"], "r": None, "a": None, "b": None}
    dx, dy = xs - m["mx"], ys - m["my"]
    p = stats.one(s.aggregate(sxx=(dx * dx).sum(), sxy=(dx * dy).sum(), syy=(dy * dy).sum()))
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
    t = expr.filter(*[stats.finite(expr[c]).notnull() for c in (x, y, *([size] if size else []))])
    total = stats.one(t.aggregate(n=t.count()))["n"]
    cols = {"x": stats.finite(t[x]), "y": stats.finite(t[y]), **({"s": stats.finite(t[size])} if size else {}), **({"g": t[split].cast("string")} if split else {})}
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
    span = stats.one(t.aggregate(lo=cols["s"].min(), hi=cols["s"].max())) if size else {}
    note = {"even": f"점이 많아 고르게 {len(rows):,}개만 그렸어요(전체 {total:,}개).", "head": f"점이 많아 앞에서 {len(rows):,}개만 그렸어요(전체 {total:,}개)."}.get(sampled, "")
    return {"x": x, "y": y, "size": size or "", "split": split or "", "points": [list(r) for r in rows], "groups": groups, "total": total, "sampled": sampled,
            **fit_line(expr, x, y), "size_range": [span["lo"], span["hi"]] if size else None, "note": note}


def points_of(body, size):
    """차트 조회(body: X, series[0].y 또는 y, split)와 [크기] 컬럼으로 연결에서 points_data를 구한다."""
    expr = querying.runnable(body, select=False)
    series = body.get("series") or [{}]
    y = body.get("y") or (series[0].get("y") if isinstance(series[0], dict) else None)
    if not isinstance(body.get("x"), str) or not y:
        raise ValueError("산점도·버블은 X와 값(Y)으로 숫자 컬럼을 고르세요.")
    return run(lambda: points_data(expr, body["x"], y, size or None, body.get("split") or None))


def date_columns(t):
    """날짜·시각 컬럼 이름들(카드의 '기간 기준 날짜 컬럼' 후보)."""
    return [c for c, d in t.schema().items() if d.is_date() or d.is_timestamp()]


def with_period(body, date, window):
    """body의 기간 줄에 '날짜컬럼 시작 ~ 끝'(window {start, end}, 열린 쪽은 비움)을 더한 사본. 날짜 컬럼이나 기간이 없으면 그대로."""
    if not date or not window or not (window.get("start") or window.get("end")):
        return body
    return {**body, "periods": [*(body.get("periods") or []), f"{date} {window.get('start') or ''} ~ {window.get('end') or ''}"]}


OWN_PERIOD = re.compile(r"(.+?)\s+(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})")  # 카드의 기간 줄 '날짜컬럼 시작 ~ 끝'(양쪽이 날짜인 것만). 날짜를 끝에서 읽어 공백 있는 컬럼 이름도 된다


def own_range(card):
    """대시보드 기간이 비었을 때 카드 자신의 기간 줄(source.body.periods 중 양쪽 날짜가 다 있고 시작이 끝보다 늦지 않은 첫 줄)을 보는 기간으로,
    바로 앞 같은 일수를 직전 기간으로 삼은 {range, prev: {start, end, text}, column, line}. 열린 쪽이 있는 줄은 건너뛰고, 없으면 None. static/core/dash.js의 ownRange와 같은 규칙이다."""
    for i, line in enumerate(((card.get("source") or {}).get("body") or {}).get("periods") or []):
        m = OWN_PERIOD.fullmatch(str(line).strip())
        if not m or m[2] > m[3]:
            continue
        try:
            start, end = dt.date.fromisoformat(m[2]), dt.date.fromisoformat(m[3])
        except ValueError:  # 2026-02-30 같은 없는 날
            continue
        n = (end - start).days + 1
        prev = {"start": (start - dt.timedelta(days=n)).isoformat(), "end": (start - dt.timedelta(days=1)).isoformat(), "text": f"직전 {n}일"}
        return {"range": {"start": m[2], "end": m[3]}, "prev": prev, "column": m[1], "line": i}
    return None


def comparison(card, ctx):
    """(보는 기간, 직전 기간, at) at(window)는 그 기간으로 건 카드의 식. 대시보드 기간(ctx.range)이 있으면 그것과 ctx.prev(기간 기준 날짜 컬럼이 있어야)를 쓰고,
    비었으면 카드 자신의 기간 줄(own_range)을 쓴다(직전 기간은 그 줄을 빼고 같은 컬럼에 직전 기간을 건다). 둘 다 없으면 직전 기간이 없다(비교 문장·직전 칸을 뺀다)."""
    if (ctx or {}).get("range"):
        return ctx["range"], (ctx.get("prev") if card["source"].get("date") else None), lambda w: card_expr(card, ctx, w)
    own = own_range(card)
    if not own:
        return None, None, lambda w: card_expr(card, ctx, w)
    body = card["source"]["body"]
    before = {**card, "source": {**card["source"], "date": own["column"], "body": {**body, "periods": [l for i, l in enumerate(body["periods"]) if i != own["line"]]}}}
    return None, own["prev"], lambda w: card_expr(before if w is own["prev"] else card, ctx, w)


def scalars(expr, series):
    """계열마다 한 값(식 전체의 집계) [값…]."""
    row = stats.one(expr.aggregate(**{f"v{i}": final_expr(expr, s) for i, s in enumerate(series)}))
    return [row[f"v{i}"] for i in range(len(series))]


def target_period(ctx):
    """대시보드의 보는 기간(ctx)이 달·분기·연 하나면 목표 파일의 기간 글자('2026-10'·'2026-Q4'·'2026'), 아니면 None.
    기준일로 잘린 기간은 직전 기간 글(지난달·직전 분기·작년 같은 기간)로, 다 지난 기간은 1일~그 달 끝으로 알아본다(dash_refresh.context가 만든 모양)."""
    rng = (ctx or {}).get("range") or {}
    start, end = rng.get("start"), rng.get("end")
    if not start or not end or start[8:10] != "01":
        return None
    first, last = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    months = {"지난달 같은 기간": 1, "직전 분기 같은 기간": 3, "작년 같은 기간": 12}.get(((ctx or {}).get("prev") or {}).get("text"))
    if months is None and last == period.month_end(last.year, last.month):
        months = (last.year - first.year) * 12 + last.month - first.month + 1
    if months == 1:
        return start[:7]
    if months == 3 and first.month % 3 == 1:
        return f"{first.year}-Q{first.month // 3 + 1}"
    if months == 12 and first.month == 1:
        return str(first.year)
    return None


def resolve_target(spec, ctx=None):
    """카드의 목표(view.target)를 {value, label, error}로. {value}는 그대로, {name, period, scope}는 설정의 목표 파일(targets.py)에서 찾는다. 없으면 None.
    기간을 정하지 않은 목표는 대시보드 기간(ctx)이 달·분기·연이면 그 기간의 목표를 고른다(target_period)."""
    if not spec:
        return None
    if "value" in spec:
        return {"value": spec["value"], "label": "", "error": ""}
    path = (settings.read(TARGETS_FILE) or {}).get("path")
    if not path:
        return {"value": None, "label": spec["name"], "error": "목표 파일을 먼저 정해 주세요(연결 아래 [목표 파일])."}
    try:
        rows = targets.read(path)["rows"]
    except ValueError as e:
        return {"value": None, "label": spec["name"], "error": str(e)}
    seen = target_period(ctx)
    found = targets.lookup(rows, spec["name"], spec.get("period") or seen, spec.get("scope"))
    if not found["row"] and not found["ambiguous"] and not spec.get("period") and seen:  # 어느 대상인지 모르는 것은 그대로 알린다. 그 기간의 줄이 없으면 그 기간을 품는 분기·연의 목표(연간 목표만 있는 파일). 다른 달·다른 해의 목표는 쓰지 않는다
        wider = [f"{seen[:4]}-Q{(int(seen[5:7]) + 2) // 3}", seen[:4]] if len(seen) == 7 else [seen[:4]] if "-Q" in seen else []
        found = next((f for f in (targets.lookup(rows, spec["name"], w, spec.get("scope")) for w in wider) if f["row"]), found)
    return {"value": found["row"]["value"], "label": found["row"]["name"], "error": ""} if found["row"] else {"value": None, "label": spec["name"], "error": found["error"]}


def part_text(p):
    """지표의 분자·분모 하나를 글로: 개수 [event = click] 같은 모양."""
    return metric_file.AGG_NAMES[p["agg"]] + (f"({p['column']})" if p["column"] else "") + (f" [{' and '.join(p['where'])}]" if p["where"] else "")


def metric_scalar(body, name):
    """지표 하나의 (값, 짝 절대량). 비율 지표(분모가 있는)는 분자·분모의 값을 {num_label, num, den_label, den}로 함께 준다(비율만 보면 규모를 알 수 없어서)."""
    found = metric_file.registry()
    parse.check_name(name, list(found), "지표")
    m, expr = found[name], querying.runnable(body, select=False)
    v = metric_file.metric_expr(expr, name, m)
    if m["denominator"] is None:
        return stats.one(expr.aggregate(v=v))["v"], None, m["format"]
    row = stats.one(expr.aggregate(v=v, num=metric_file.aggregate(expr, m["numerator"]), den=metric_file.aggregate(expr, m["denominator"])))  # 한 번에(원격 DB는 조회마다 요금)
    pair = {"num_label": part_text(m["numerator"]), "num": row["num"], "den_label": part_text(m["denominator"]), "den": row["den"]}
    return row["v"], pair, m["format"]


def group_values(expr, column, s):
    """그룹(column)별 집계 {그룹: 값}. 그룹이 MAX_GROUPS개를 넘으면 None(잘린 그룹으로는 기여를 말할 수 없다)."""
    parse.check_name(column, expr.columns)
    t = expr.filter(expr[column].notnull())
    frame = t.group_by(t[column].name("g")).aggregate(v=final_expr(t, s)).order_by("g").limit(MAX_GROUPS + 1).to_polars()
    if len(frame) > MAX_GROUPS:
        return None
    return {str(g): v for g, v in zip(frame["g"].to_list(), frame["v"].to_list())}


def group_pairs(now, old, agg):
    """그룹별 (이번, 직전). 합계·개수만 구한다: 전체의 변화를 그룹들의 변화로 나눌 수 있는 집계라서다(평균·최소·최대·고유 개수는 그룹 변화를 더해도 전체 변화가 안 돼 None).
    한쪽 기간에 없는 그룹은 0으로 본다(새로 생긴 그룹의 기여를 빼지 않게)."""
    if agg not in ("sum", "count"):
        return None
    return {g: (now.get(g, 0) if now.get(g) is not None else 0, old.get(g, 0) if old.get(g) is not None else 0) for g in {*now, *old}}


def summary_data(card, ctx):
    """summary 카드: 지표(첫 계열)의 이번 기간·직전 기간 값과 그룹별 값(view.group)으로 summary_text의 문장을 만든다. 직전 기간·날짜 컬럼이 없으면 그 문장은 뺀다."""
    view, body = card["view"], card["source"]["body"]
    series = clean_series(body.get("series"))
    window, before, at = comparison(card, ctx)
    cur = run(lambda: scalars(at(window), series[:1]))[0]
    prev, partial = None, False
    if before:  # 직전 값과 그 기간의 첫 날을 한 번에: 직전 기간 시작이 자료 첫 날보다 앞이면 일부(KPI 카드의 compareItem과 같은 덧말)
        b, col = at(before), card["source"].get("date") if (ctx or {}).get("range") else own_range(card)["column"]
        row = run(lambda: stats.one(b.aggregate(v=final_expr(b, series[0]), first=b[col].min())))
        prev, partial = row["v"], bool(before.get("start")) and (row["first"] is None or label(row["first"]) > before["start"])
    groups, note = None, ""
    if view.get("group") and before and series[0]["agg"] in ("sum", "count"):  # 기여를 나누는 건 합계·개수와 직전 기간이 있을 때만(아니면 그룹 값을 읽지 않는다)
        now = run(lambda: group_values(at(window), view["group"], series[0]))
        old = run(lambda: group_values(at(before), view["group"], series[0]))
        if now is None or old is None:
            note = f"그룹이 {MAX_GROUPS}개를 넘어 어느 그룹 덕분인지는 말하지 않았어요."
        else:
            groups = group_pairs(now, old, series[0]["agg"])
    target = resolve_target(view.get("target"), ctx)
    found = summary_text.summarize(series_name(series[0]), cur, prev, view.get("format", "number"), view["compare"]["mode"], (before or {}).get("text") or "직전 기간", view["better"], partial,
                                   groups, (target or {}).get("value"))
    return {"values": {"scalar": [cur], "prev": [prev]}, "text": found["text"], "sentences": found["sentences"], "target": target, **({"note": note} if note else {})}


def value_data(card, ctx):
    """3단계 카드(VALUE_KINDS)의 값. 지표 이름(body.names)이 있으면 지표 하나의 값과 짝 절대량, 게이지는 항목별 값(category_data),
    날짜 컬럼(source.date)이 있으면 일 값(time_data, 화면이 기간으로 묶음), 없으면 식 전체의 집계 값 하나씩. 목표(view.target)도 찾는다."""
    src, view, body = card["source"], card["view"], card["source"]["body"]
    date, window = src.get("date"), (ctx or {}).get("range")
    if card["kind"] == "summary":
        found = summary_data(card, ctx)
    elif body.get("names"):
        name = body["names"][0]
        value, pair, fmt = run(lambda: metric_scalar(with_period(body, date, window), name))
        found = {"values": {"scalar": [value], "names": [name], "format": fmt}, "pair": pair}
    elif card["kind"] == "gauge":
        found = category_data(card_expr(card, ctx, window), body, clean_series(body.get("series")))
    elif date:
        shape = querying.build(body, select=False)  # 컬럼 종류만 본다
        parse.check_name(date, shape.columns, "날짜 컬럼")
        if date not in date_columns(shape):
            raise ValueError(f"'{date}'은 날짜 컬럼이 아닙니다.")
        series, fetch = clean_series(body.get("series")), fetch_window(ctx)  # 읽을 범위: 직전 기간 시작 ~ 보는 기간 끝
        expr = card_expr(card, ctx, fetch)
        found = run(lambda: shared_time(expr, body, date, series, None, fetch, ctx))
        if fetch:
            found = {**found, "window": fetch}
        unique = [i for i, s in enumerate(series) if not merges(s["agg"])]
        if unique and window and (window.get("start") or window.get("end")):  # 고유 개수는 일 값을 다시 묶을 수 없어 그 기간으로 따로 구한다
            def distinct(w):  # 그 기간의 고유 개수류 값 {계열 번호: 값}: 집계 한 번
                at = card_expr(card, ctx, w)
                return {"start": w.get("start"), "end": w.get("end"), "values": {str(i): v for i, v in zip(unique, run(lambda: scalars(at, [series[i] for i in unique])))}}
            before = (ctx or {}).get("prev")  # 직전 기간(기준일 규칙은 dash_refresh.context가 이미 적용)도 같은 집계로: 화면 compareItem의 비교용(기간 비교일 때만. 지점 비교는 일 값으로 한다)
            found["values"] = {"window_nunique": {**distinct(window), **({"prev": distinct(before)} if before and before.get("start") and before.get("end") and view["compare"]["mode"] == "span" else {})}}
    else:
        series, expr = clean_series(body.get("series")), card_expr(card, ctx, window)
        found = {"values": {"scalar": run(lambda: scalars(expr, series)), "names": [series_name(s) for s in series]}}
    return {**found, **({"target": resolve_target(view.get("target"), ctx)} if card["kind"] != "summary" else {}),
            "date_cols": date_columns(querying.build(body, select=False))}


def table_rows(expr, group_cols, series):
    """집계 표의 행: 묶음 컬럼 값들과 계열 집계들 [[…]](묶음 컬럼 순, 최대 MAX_TABLE_ROWS+1행). 묶음 컬럼이 없으면 한 줄."""
    for c in group_cols:
        parse.check_name(c, expr.columns, "묶음 컬럼")
    metrics_ = {f"v{i}": final_expr(expr, s) for i, s in enumerate(series)}
    if not group_cols:
        return [[result.plain(v) for v in stats.one(expr.aggregate(**metrics_)).values()]]
    keys = [expr[c].name(c) for c in group_cols]
    frame = expr.group_by(keys).aggregate(**metrics_).order_by(group_cols).limit(MAX_TABLE_ROWS + 1).to_polars()
    return [[result.plain(v) if not hasattr(v, "isoformat") else str(v) for v in row] for row in frame.rows()]


def js_text(v):
    """JSON으로 받은 값을 JS String(v)처럼 글자로(None → null, True → true, 1.0 → 1, 1e-05 → 0.00001)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))
        text = repr(v)  # 가장 짧은 10진 글자는 JS와 같고, 지수로 쓰는 범위만 다르다(JS는 1e-7 미만·1e21 이상)
        return format(Decimal(text), "f") if "e" in text and 1e-6 <= abs(v) < 1e21 else re.sub(r"e([+-]?)0*(\d)", lambda m: "e" + (m[1] or "+") + m[2], text)
    return str(v)


def table_data(card, ctx):
    """table 카드: 쿼리의 집계(묶음 컬럼 + 계열)를 표로(최대 MAX_TABLE_ROWS행). 날짜 컬럼과 직전 기간이 있으면 직전 기간 값도 줘 ▲▼ 칸을 그린다.
    계열이 없으면 원래 행 표라 값은 저장하지 않고 컬럼 이름만 준다(보일 때마다 연결에서 읽는다)."""
    view, body = card["view"], card["source"]["body"]
    series = body.get("series") or []
    if not series:
        return {"columns": list(querying.build(body).columns)}
    series, groups = clean_series(series), view.get("group_cols") or []
    window, before, at = comparison(card, ctx)
    now = run(lambda: table_rows(at(window), groups, series))
    old = run(lambda: table_rows(at(before), groups, series)) if before and groups else None
    more = len(now) > MAX_TABLE_ROWS
    columns = [{"name": c, "kind": "group"} for c in groups] + [{"name": series_name(s), "kind": "value", "agg": s["agg"]} for s in series]
    key = lambda row: "\x01".join(js_text(v) for v in row[:len(groups)])  # 화면(renderTable)이 행에서 JS String으로 만드는 열쇠와 같게
    return {"values": {"columns": columns, "rows": now[:MAX_TABLE_ROWS], "more": more, "prev": {key(r): r[len(groups):] for r in old} if old else None},
            "note": f"행이 많아 앞 {MAX_TABLE_ROWS}개만 저장했어요." if more else ""}


def sentence_data(card, ctx):
    """sentence 카드: 문장 틀(view.template)의 빈칸마다 값. 빈칸 종류: value(이번 기간 값), prev(직전 기간 값), delta(비교 문장, compare.py), asof(기준일).
    계산하지 못하는 빈칸은 값 없이 이유(error)만 담는다(화면이 '—'와 이유를 보임)."""
    view = card["view"]
    window, before, at = comparison(card, ctx)
    out = {}
    for blank in view.get("blanks") or []:
        name, kind, fmt = blank["name"], blank["kind"], blank["format"]
        if kind == "asof":
            out[name] = {"kind": kind, "text": (window or {}).get("end") or dt.date.today().isoformat(), "format": fmt}
            continue
        try:
            s = clean_series([{"y": blank.get("y") or None, "agg": blank.get("agg") or ("sum" if blank.get("y") else "count")}])
            cur = run(lambda: scalars(at(window), s))[0]
            prev = run(lambda: scalars(at(before), s))[0] if before and kind in ("prev", "delta") else None
            item = {"kind": kind, "cur": cur, "prev": prev, "format": fmt}
            if kind == "delta":
                item["text"] = compare.compare(cur, prev, fmt, view["compare"]["mode"], (before or {}).get("text") or "직전 기간", view["better"])["text"]
            out[name] = item
        except ValueError as e:
            out[name] = {"kind": kind, "error": str(e), "format": fmt}
    return {"values": {"blanks": out}}


ZONE_SUFFIX = r"(Z|[+-]\d\d:?\d\d)$"  # ISO 시각 글자 끝의 시간대 표기


def watch_data(card, now, previous):
    """status·alerts·map 카드: 엔티티(view.entity 컬럼)별 최근 행을 상태 규칙(view.rules)·끊김 규칙(view.stale)으로 판정한다(watch.evaluate).
    연결선은 view.links와 각 행의 연결 대상 컬럼(view.link_col). 나빠진 시각(since)은 이전 값(previous)을 이어 data.since에 둔다."""
    view, body = card["view"], card["source"]["body"]
    if not view.get("entity"):
        raise ValueError("엔티티(서버·장비 이름) 컬럼을 고르세요.")
    expr = querying.runnable(body, select=False, keep=card_columns(card))
    for c in (view["entity"], view.get("time_col"), view.get("kind_col"), view.get("link_col")):
        if c:
            parse.check_name(c, expr.columns, "컬럼")
    time = view.get("time_col")
    kind = expr[time].type() if time else None
    if kind is not None and kind.is_string():  # 글자 시각(수집 결과의 ISO 글자)은 글자 순 max라 시간대 표기(Z·+09:00)가 모두 같을 때만(섞이면 예전처럼 앞 행을 읽고 watch가 시각을 푼다)
        timed = expr.filter(expr[time].notnull())  # 빈 시각은 세지 않는다(Polars는 nunique에 null을 센다)
        kind = kind if run(lambda: stats.one(timed.aggregate(n=timed[time].re_extract(ZONE_SUFFIX, 0).nunique())))["n"] <= 1 else None
    if kind is not None and (kind.is_temporal() or kind.is_string()):  # 엔티티마다 가장 늦은 시각의 행만 DB에서(앞 행만 읽으면 최근 행을 못 본다). 시각이 모두 빈 엔티티는 그 행들을 그대로
        last = expr.group_by(view["entity"]).aggregate(__last=expr[time].max())  # 창 함수 대신 묶어 붙이기(Polars 연결은 창 함수를 못 한다)
        joined = expr.join(last, view["entity"])
        expr = joined.filter((joined[time] == joined["__last"]) | joined["__last"].isnull()).drop("__last")
    frame = run(lambda: result.get_rows(expr, MAX_ENTITY_ROWS))
    rows = frame.to_dicts()
    links = [tuple(l) for l in view.get("links") or []]
    if view.get("link_col"):
        links += [(str(r[view["entity"]]), str(r[view["link_col"]])) for r in rows if r.get(view["entity"]) is not None and r.get(view["link_col"]) is not None]
    found = watch.evaluate(rows, view.get("rules"), view["entity"], dt.datetime.fromisoformat(now), view.get("time_col"), view.get("stale"), previous, links, view.get("kind_col"))
    return {"values": {"entities": found["entities"], "counts": found["counts"], "fresh": found["fresh"], "more": len(rows) >= MAX_ENTITY_ROWS}, "since": found["since"],
            "note": f"행이 많아 {MAX_ENTITY_ROWS}행만 읽었어요(엔티티가 많거나 같은 시각에 쓴 행이 많음)." if len(rows) >= MAX_ENTITY_ROWS else ""}


def deviation_data(card, ctx):
    """deviation 카드: 엔티티(view.entity) × 시간대(날짜 컬럼 source.date를 view.bucket으로 자름)의 첫 계열 값. 기준(baseline)은 화면이 정한다(직접 숫자 또는 엔티티 평균).
    엔티티는 엔티티 순(DB 정렬, 빈 엔티티는 뒤)으로 앞 MAX_ENTITIES개를 먼저 고르고 그것만 집계한다. 시간대는 최근 MAX_BUCKETS개까지."""
    src, view, body = card["source"], card["view"], card["source"]["body"]
    date, entity, unit = src.get("date"), view.get("entity"), view.get("bucket") or "hour"
    if not entity or not date:
        raise ValueError("편차 표는 엔티티 컬럼과 날짜(시각) 컬럼을 고르세요.")
    series = clean_series(body.get("series"))[:1]
    expr = card_expr(card, ctx, (ctx or {}).get("range"))
    parse.check_name(entity, expr.columns, "엔티티 컬럼")
    parse.check_name(date, expr.columns, "날짜 컬럼")
    if unit == "hour" and not expr[date].type().is_timestamp():
        raise ValueError(f"'{date}'은 날짜만 있는 컬럼이라 시간대(시)로 묶을 수 없어요. 일·주·월로 묶으세요.")
    t = expr.filter(expr[date].notnull())  # 시각이 빈 행은 시간대를 만들지 않는다
    def recent():
        """가장 최근 MAX_BUCKETS개 시간대 중 가장 이른 것: 그보다 오래된 행은 읽지 않는다."""
        found = t.select(b=stats.period_start(t[date], BUCKET_UNITS[unit])).distinct()
        return found.order_by(found.b.desc()).limit(MAX_BUCKETS).to_polars()["b"].to_list()
    latest = run(recent)
    if latest:
        t = t.filter(t[date] >= min(latest))
    def first_entities():
        """엔티티 순으로 앞 MAX_ENTITIES+1개(빈 엔티티는 뒤): 집계를 이 엔티티로만 해 결과가 엔티티 수만큼 커지지 않게."""
        found = t.select(e=t[entity]).distinct()
        return found.order_by(found.e.asc(nulls_first=False)).limit(MAX_ENTITIES + 1).to_polars()["e"].to_list()
    picked = run(first_entities)
    more, picked = len(picked) > MAX_ENTITIES, picked[:MAX_ENTITIES]
    known = [e for e in picked if e is not None]
    t = t.filter(t[entity].isin(known) | t[entity].isnull() if None in picked else t[entity].isin(known))
    frame = run(lambda: t.group_by([t[entity].name("e"), stats.period_start(t[date], BUCKET_UNITS[unit]).name("b")]).aggregate(v=final_expr(t, series[0])).to_polars())
    buckets = sorted({str(b) for b in frame["b"].to_list()})[-MAX_BUCKETS:]
    cells = {}
    for e, b, v in frame.rows():
        cells.setdefault("(빈 값)" if e is None else str(e), {})[str(b)] = result.plain(v)
    return {"values": {"buckets": buckets, "entities": [{"name": n, "values": [cells[n].get(b) for b in buckets]} for n in sorted(cells, key=lambda n: (n == "(빈 값)", n))], "more": more}}  # 빈 엔티티는 뒤


def geomap_data(card, ctx):
    """geomap 카드: 영역 표(view.geo.regions: 연결의 테이블 또는 GeoJSON 파일)와 점(위도·경도 컬럼, 값 컬럼)을 읽어 geo.mapcard.build로 카드에 저장할 값을 만든다.
    점은 카드의 조회(source.body, 기간·조건 포함)에서 쓸 컬럼만 MAX_POINT_ROWS행까지 읽는다. 영역은 쓸 컬럼만 읽는다."""
    geo = card["view"].get("geo")
    if not geo:
        raise ValueError("지도 설정(영역 테이블, 점의 위도·경도 컬럼)이 없어요. 카드의 데이터를 정하세요.")
    regions = None
    if geo.get("regions"):
        regions = run(lambda: read_regions(geo))
    points = None
    if geo.get("lat"):
        expr = card_expr(card, ctx, (ctx or {}).get("range"))
        for c in (geo["lat"], geo["lon"], geo.get("value")):
            if c:
                parse.check_name(c, expr.columns, "컬럼")
        cols = {"lat": expr[geo["lat"]].cast("float64"), "lon": expr[geo["lon"]].cast("float64"), **({"value": expr[geo["value"]]} if geo.get("value") and geo.get("agg", "count") != "count" else {})}
        points = run(lambda: expr.select(**cols).limit(mapcard.MAX_POINT_ROWS + 1).to_polars())
    return mapcard.build(regions, points, geo)


def read_regions(geo):
    """영역 표(Polars): 이름·도형(·값) 컬럼만. 연결의 테이블이거나 GeoJSON 파일 경로."""
    from core import server
    want = [c for c in dict.fromkeys([geo["name"], geo.get("geometry") or "geometry", geo.get("region_value")]) if c]
    where = geo["regions"]
    if where.get("path"):
        from geo import geojson
        frame = geojson.read(where["path"])
        for c in want:
            parse.check_name(c, frame.columns, "영역 컬럼")
        return frame.select(want)
    con, schema = server.get_con(), where.get("schema") or None
    parse.check_name(where["table"], connection.list_tables(con, schema), "영역 테이블")
    table = connection.get_table(con, where["table"], schema)
    for c in want:
        parse.check_name(c, table.columns, "영역 컬럼")
    frame = table.select(*want).limit(mapcard.MAX_REGIONS + 1).to_polars()
    return frame


def chart_data(body, window=None):
    """chart 카드의 값. X가 날짜(날짜·시각 컬럼)면 일 값 중심(time_data, window가 있으면 그 날짜 범위만 읽는다), 아니면 항목별(category_data). 쓰는 컬럼만 읽는다."""
    shape = querying.build(body, select=False)  # 컬럼 종류만 본다(읽지 않는다)
    x = body.get("x")
    if not isinstance(x, str):
        raise ValueError("X 컬럼을 고르세요.")
    parse.check_name(x, shape.columns)
    t = shape[x].type()
    if t.is_date() or t.is_timestamp():
        body = with_period(body, x, window)
    expr = querying.runnable(body, select=False, keep=body_columns(body))
    t = expr[x].type()
    kinds = {"x_kind": "date" if t.is_date() or t.is_timestamp() else "num" if is_number(shape, x) else "text",
             "num_cols": [c for c in shape.columns if is_number(shape, c)][:MAX_NUM_COLS]}  # 산점도·버블을 고를 수 있는지 화면이 알 수 있게
    kinds["date_cols"] = date_columns(shape)
    if kinds["x_kind"] == "date":
        return {**run(lambda: time_data(expr, x, clean_series(body.get("series")), body.get("split") or None)), **kinds, **({"window": window} if window else {})}
    return {**category_data(expr, body, clean_series(body.get("series"))), **kinds}


def metric_data(body):
    """metric 카드의 값: 지표마다 한 계열({labels, series}). 나누지 않으면 막대 하나('전체'), 비율은 %로(×100)."""
    names, by = body.get("names"), body.get("by") or None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("구할 지표를 고르세요.")
    found, labels, values, more = querying.metric_values(body, names, by)
    pct = lambda n: found[n]["format"] == PERCENT
    series = [{"name": f"{n} (%)" if pct(n) else n, "values": [v if v is None or not pct(n) else round(v * 100, 6) for v in values[n]]} for n in names]
    return {"x": by or "전체", "values": {"labels": labels or ["전체"], "series": series}, "note": f"값이 많아 앞 {metric_file.MAX_GROUPS}개만 표시합니다." if more else ""}


def analysis_data(source, ctx=None):
    """analysis·funnel 카드의 값: 분석 결과(prep_app.analyzed)와 어느 쪽(표본·전체)으로 구했는지.
    funnel 카드는 기간 기준 날짜 컬럼(source.date, 보통 시각 컬럼)이 있고 대시보드 기간(ctx.range)이 있으면 첫 단계를 그 날짜 안에 한 사람만 센다(뒤 단계는 기간 밖이어도 센다). 읽는 컬럼은 사용자·단계·시각 셋뿐이다(stats.funnel_flow)."""
    how = source.get("analysis")
    window = (ctx or {}).get("range")
    if how and how.get("kind") == "funnel" and source.get("date") == how.get("time") and window and (window.get("start") or window.get("end")):
        how = {**how, "range": {"start": window.get("start"), "end": window.get("end")}}
    found = prep_app.analyzed({"recipe": source.get("recipe"), "how": how, "full": source.get("full") is True,
                               **({"sample": source["sample"]} if "sample" in source else {})})
    info = found.get("data", {})
    return {"values": found, "note": "테이블 전체" if info.get("full") else f"표본 {info.get('rows', 0):,}행"}


def definition(card):
    """카드를 다시 구하는 정의(연결과 이름 빼고): 이 JSON의 지문이 def_hash다."""
    source = card["source"]
    points = {"points": card["view"].get("points")} if card["view"]["type"] in POINT_TYPES else {}
    view = card["view"]
    keys = ("target", "group", "format") if card["kind"] in VALUE_KINDS else ("group_cols", "template", "blanks", "rules", "stale", "entity", "time_col", "kind_col", "link_col", "links", "bucket") if card["kind"] in TABLE_KINDS else ("geo",) if card["kind"] == "geomap" else ()
    extra = {"view": {k: view[k] for k in keys if k in view}} if keys else {}  # 값에 영향을 주는 설정
    return {"kind": card["kind"], **{k: source[k] for k in ("schema", "table", "body", "recipe", "analysis", "metric_id", "flow_id", "full", "sample", "date") if k in source}, **points, **extra}


def compute(card, now, ctx=None):
    """검사한 카드(dash_store.check_card)의 source로 data를 구해 돌려준다(저장하지 않는다). now는 구한 시각 글자.
    ctx = {range: {start, end}, prev: {start, end, text}}(대시보드의 보는 기간과 직전 기간)는 source.date(기간 기준 날짜 컬럼)가 있는 날짜 X축이 아닌 카드에 기간 줄로 걸리고, 요약 문장이 쓴다."""
    source = card["source"]
    if card["kind"] in ("chart", "metric", "recent_rows", "geomap", *VALUE_KINDS, *TABLE_KINDS):
        if not isinstance(source.get("body"), dict):
            raise ValueError("이 카드에는 다시 구할 조회(source.body)가 없습니다.")
        body = with_period(source["body"], source.get("date"), (ctx or {}).get("range")) if card["kind"] in ("chart", "metric") else source["body"]
        if card["kind"] == "recent_rows":
            found = {"columns": list(querying.build(body).columns)}  # 행 값은 저장하지 않는다(보일 때마다 연결에서 읽는다)
        elif card["kind"] in VALUE_KINDS:
            found = value_data(card, ctx)
        elif card["kind"] == "table":
            found = table_data(card, ctx)
        elif card["kind"] == "sentence":
            found = sentence_data(card, ctx)
        elif card["kind"] == "deviation":
            found = deviation_data(card, ctx)
        elif card["kind"] in WATCH_KINDS:
            found = watch_data(card, now, (card.get("data") or {}).get("since"))
        elif card["kind"] == "geomap":
            found = geomap_data(card, ctx)
        else:
            found = chart_data(body, fetch_window(ctx)) if card["kind"] == "chart" else metric_data(body)
        if card["kind"] in ("table", "sentence", "deviation"):  # 기간 기준 날짜 컬럼 후보(하나뿐이면 화면이 자동으로 채운다)
            found.setdefault("date_cols", date_columns(querying.build(source["body"], select=False)))
        if card["kind"] == "chart" and card["view"]["type"] in POINT_TYPES:  # 점 종류를 고른 카드는 점도 다시 구한다
            found["points"] = points_of(source["body"], (card["view"].get("points") or {}).get("size"))
    elif card["kind"] in ("analysis", "funnel"):
        if not isinstance(source.get("recipe"), dict) or not isinstance(source.get("analysis"), dict):
            raise ValueError("이 카드에는 다시 구할 분석(source.recipe, source.analysis)이 없습니다.")
        found = analysis_data(source, ctx)
    else:
        raise ValueError(f"'{card['kind']}' 카드는 아직 다시 구할 수 없습니다.")
    return dash_store.finite({**found, "computed_at": now, "def_hash": dash_store.def_hash(definition(card))})  # NaN·무한은 JSON에 못 쓰므로 빈 값으로
