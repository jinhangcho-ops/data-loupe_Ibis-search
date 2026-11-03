"""문장 실행(6-4): sentence_run.compile이 만든 요청들(runs)을 연결에서 실행하고 combine대로 합쳐 끝난 표 하나 {columns, rows, more, notes}로 돌려준다. 화면은 나누거나 합치지 않는다.
요청의 route: /api/query(행), /api/pivot(묶어 집계, 기존 라우트와 같은 querying.runnable·calc.pivot), metric(지표 이름 또는 내 지표 id의 값을 지표 정의로 구함, by가 있으면 그 값마다),
nested(대상마다 구한 값을 다시 집계: 평균·N번 이상 한 대상 비율·두 단계를 다 한 비율·횟수 구간별 대상 수·대상별 요약표·집단 대 전체), series(날짜 단위 합계, 빈 기간 0, 누적·이동·벗어난 기간)이다.
탐색 틀 51~53번의 만들기 e51~e53도 여기 두고 sentence_run.COMPILERS에 붙인다. 합치는 방법 combine: ratio·percent·share(a ÷ b, by 컬럼이 같은 줄끼리)·sub·add·mean_of·change·cumsum·rest(상위 N + 기타)·pareto(누적 비중·ABC).
지표 사전 항목('catalog:id')으로 만든 내 지표는 catalog_app이 구한다(값 하나). 데이터를 읽는 함수는 server.run으로 연결 스레드에서 돈다. 내 지표가 다른 내 지표를 쓰면(조합) 안쪽까지 풀어 구한다(MAX_DEPTH단계)."""
import copy
import datetime as dt
import json
import statistics

from core import calc
from core import querying
from metrics import metric_catalog
from core import metric_file
from metrics import my_metrics
from lazy import ibis  # 맨 위에서 진짜 import하면 미리 올리기 스레드와 연결 스레드의 import 잠금이 맞물린다
from core import parse
from core import result
from query import sentence_run
from core import dash_store
from core import server
from core import sql_funcs
from core import use_counts
from core.server import run

QUERY_ROWS = 1000  # 행을 보는 요청에서 돌려줄 행 수
MAX_DEPTH = 3  # 내 지표가 내 지표를 쓰는 단계
NAMES = {"ratio": "값", "percent": "비율(%)", "share": "비중", "sub": "차이", "add": "합", "mean_of": "평균", "change": "변화율", "cumsum": "누적", "rest": "기타", "pareto": "누적 비중"}
MAX_PERIODS = 5000  # 빈 기간을 채울 때 만들 기간 수의 상한(일 단위로 13년 남짓)
WINDOWS = {"cum_sum": "sum", "cum_mean": "mean", "cum_min": "min", "cum_max": "max", "moving_sum": "sum", "moving_mean": "mean", "moving_min": "min", "moving_max": "max"}  # 36번 → 쌓을 계산
VERSUS_P = "p-value(집단 vs 나머지)"  # 53번 견주기 표의 검정 열
SHARE = "비중 %"  # 51·52·53번 결과 표에 더하는 열(전체 중 몫을 0~100으로)


def share(part, total):
    """part가 total에서 차지하는 몫(%). total이 0이거나 없으면 None."""
    return None if not total or part is None else part / total * 100


def one(keys, col, rows, more=False, columns=None):
    return {"keys": keys, "col": col, "rows": rows, "more": more, **({"columns": columns} if columns is not None else {})}


def execute_run(r, depth=0):
    """요청 하나를 실행한 {keys(묶은 컬럼들), col(값 컬럼), rows([{…}]), more}."""
    route = r["route"]
    if route == "/api/query":
        expr = querying.runnable(r["body"])
        table = server.to_json(run(lambda: result.get_rows(expr, QUERY_ROWS + 1)))  # 한 줄 더 받아 더 있는지 안다
        return one([], None, table["rows"][:QUERY_ROWS], len(table["rows"]) > QUERY_ROWS, table["columns"])
    if route == "/api/pivot":
        spec = r["body"]["pivot"]
        expr = querying.runnable(r["body"], select=False)
        table = run(lambda: calc.pivot(expr, spec, lambda e: e.to_pyarrow()["v"].to_pylist()).to_polars())
        rows = server.to_json(table.head(calc.MAX_ROWS), text_big=False)["rows"]
        if r.get("fill"):
            rows = fill_periods(rows, r["fill"]["key"], r["fill"]["unit"], [r["result"]])
        return one(list(spec.get("rows") or []), r["result"], rows, table.height > calc.MAX_ROWS)
    if route == "metric":
        return metric_run(r, depth)
    if route == "nested":
        return nested_run(r)
    if route == "series":
        return series_run(r)
    raise ValueError(f"알 수 없는 요청 종류: {route}")


def check_scope(mine, body):
    """내 지표가 지금 검색 body의 테이블(연결·스키마·테이블이 같은 scope)의 것인지. 다른 테이블의 지표를 ‘맞춰서’ 쓰는 구절(align)은 아직 실행하지 않으므로 거부한다.
    예전 흐름(flow_id)에 묶은 지표는 흐름 보기가 없어져 구할 수 없다고 알린다."""
    scope = mine.get("scope") or {}
    if my_metrics.is_flow_only(mine):
        raise ValueError(my_metrics.FLOW_GONE)
    now = dash_store.current_source()
    if scope.get("table") != body.get("base") or (scope.get("schema") or "") != (body.get("schema") or "") or (now and not use_counts.same_source(scope.get("key"), now["key"])):
        raise ValueError(f"‘{my_metrics.scope_text(mine)}’은(는) 다른 테이블의 지표라 지금 검색에서 그대로 쓸 수 없어요. [이 테이블에 맞춰 복사]해서 쓰세요.")


def definition_of(ref, body):
    """지표 이름(지표 파일) 또는 내 지표 id의 (표시 이름, metrics.py 모양 정의 또는 None(조합 지표), 내 지표)."""
    try:
        found = metric_file.registry()
    except ValueError:
        found = {}
    if ref in found:
        return ref, found[ref], None
    mine = my_metrics.get(ref)
    check_scope(mine, body)
    if metric_catalog.is_catalog(mine["template"]):  # 지표 사전 항목: catalog_app이 구한다(metric_run이 이 표시를 보고 처리)
        return mine["name"], None, {"catalog": mine}
    made = sentence_run.compile(mine["template"], mine["slots"], body, None)
    if not made["ok"]:
        raise ValueError(f"내 지표 ‘{mine['name']}’을(를) 구할 수 없어요: {made['reason']}")
    return mine["name"], (metric_file.metric(mine["name"], made["definition"]) if made.get("definition") else None), made


def metric_run(r, depth):
    """지표 하나의 값(by가 있으면 그 값마다)."""
    name, definition, made = definition_of(r["ref"], r["body"])
    by = r.get("by")
    if isinstance(made, dict) and "catalog" in made:  # 지표 사전 항목으로 만든 내 지표: 값 하나(묶음은 지원하지 않는다)
        if by:
            raise ValueError(f"‘{name}’은 지표 사전의 지표라 값마다 나눠 구할 수 없어요.")
        from metrics import catalog_app
        return one([], name, [{name: catalog_app.compute_saved(made["catalog"], r["body"])["value"]}])
    if definition is None:  # 다른 지표를 합친 내 지표: 안쪽을 풀어 구한다(묶음은 지원하지 않는다)
        if by:
            raise ValueError(f"‘{name}’은 다른 지표를 합친 지표라 값마다 나눠 구할 수 없어요.")
        if depth >= MAX_DEPTH:
            raise ValueError("내 지표가 내 지표를 너무 깊이 써요.")
        done = execute_compiled(made, depth + 1)
        return one([], name, [{name: done["rows"][0][done["columns"][-1]] if done["rows"] else None}])
    parts = [p for p in (definition["numerator"], definition["denominator"]) if p]
    body = made["body"] if isinstance(made, dict) and "body" in made else r["body"]  # 컬럼 가공(소문자 등)의 계산 줄은 compile한 body에만 있다
    expr = querying.runnable(body, select=False, extra=[(p["agg"], p["column"], p.get("opts")) for p in parts])
    labels, values, more = run(lambda: metric_file.compute(expr, [name], {name: definition}, by))
    if not by:
        return one([], name, [{name: values[name][0]}])
    rows = [{by: x, name: v} for x, v in zip(labels, values[name])]
    if not r.get("fill"):
        return one([by], name, rows, more)
    f = r["fill"]  # 13번: 행이 없는 기간은 0, 자료가 마지막 기간 중간에 끝났으면 그 기간은 뺀다(지표 사전 active와 같은 규칙)
    rows, notes = fill_periods([x for x in rows if x[by] is not None], by, f["unit"], [name]), []
    if len(rows) > 1 and f["unit"] in mc_units():  # shortcut: year 단위는 진행 중인 해를 가리지 않는다(metric_calc가 day·week·month만 센다), 필요해지면 open_bucket에 year를 더한다
        from metrics import metric_calc as mc
        open_no = run(lambda: mc.open_bucket(expr, f["time"], f["unit"]))
        if open_no is not None and mc.bucket_start(open_no, f["unit"]) == day_of(rows[-1][by]):
            notes.append(f"자료가 마지막 기간({str(rows[-1][by])[:10]}) 중간에 끝나 그 기간은 평균에서 뺐어요.")
            rows = rows[:-1]
    return {**one([by], name, rows, more), "notes": notes}


def mc_units():
    from metrics import metric_calc
    return metric_calc.UNITS


def day_of(key):
    """기간 칸 값(날짜·날짜시간 또는 'YYYY-MM-DD…' 글)의 날짜."""
    return key if type(key) is dt.date else key.date() if isinstance(key, dt.datetime) else dt.date.fromisoformat(str(key)[:10])


def fill_periods(rows, key, unit, cols):
    """기간(key) 순 행들 사이의 빈 기간을 cols 0인 행으로 채운다(첫 기간 ~ 마지막 기간). 기간 칸이 빈 행은 맨 뒤에 그대로 둔다. 기간이 MAX_PERIODS개를 넘으면 ValueError."""
    dated = [x for x in rows if x.get(key) is not None]
    if not dated:
        return rows
    sample, have = dated[0][key], {day_of(x[key]): x for x in dated}
    shape = (lambda d: d) if type(sample) is dt.date else (lambda d: dt.datetime.combine(d, dt.time())) if isinstance(sample, dt.datetime) else (lambda d: d.isoformat() + str(sample)[10:])
    day, last, out = min(have), max(have), []
    while day <= last:
        if len(out) >= MAX_PERIODS:
            raise ValueError(f"기간이 {MAX_PERIODS:,}개를 넘어 빈 기간을 채울 수 없어요. 기간을 좁히거나 더 큰 단위를 고르세요.")
        out.append(have.get(day) or {**{k: None for k in dated[0]}, key: shape(day), **{c: 0 for c in cols}})
        day = result.next_period(day, unit)  # 차트의 빈 기간 채우기와 같은 다음 기간(회계연도 첫 달도 그대로)
    return out + [x for x in rows if x.get(key) is None]


def series_run(r):
    """날짜(r.date)를 단위(r.unit)로 묶은 합계(값이 없으면 행 수)를 빈 기간 0으로 채운 뒤 r.op대로: window(누적·이동, 기간 N개) 또는 outlier(z-점수·IQR로 크게 벗어난 기간만)."""
    expr = querying.runnable(r["body"], select=False)
    d = parse.column(expr, r["date"])
    expr = expr.filter(d.notnull())
    d = parse.column(expr, r["date"])
    vname = f"{r['value']} 합계" if r.get("value") else "행 수"
    measure = parse.column(expr, r["value"]).sum() if r.get("value") else expr.count()
    per = expr.group_by(**{"기간": sql_funcs.period_start(d, sql_funcs.UNITS[r["unit"]])}).aggregate(**{vname: measure}).order_by("기간")
    got = server.to_json(run(lambda: per.limit(MAX_PERIODS + 1).to_polars()))["rows"]
    rows = fill_periods(got, "기간", r["unit"], [vname])
    xs = [x[vname] or 0 for x in rows]
    op = r["op"]
    if "window" in op:
        how, n = WINDOWS[op["window"]], max(1, int(op.get("n") or 1))
        reduce = {"sum": sum, "mean": lambda v: sum(v) / len(v), "min": min, "max": max}[how]
        for i, x in enumerate(rows):
            x[op["name"]] = reduce(xs[max(0, i - n + 1):i + 1] if op["window"].startswith("moving") else xs[:i + 1])
        return one(["기간"], op["name"], rows, False, ["기간", vname, op["name"]])
    if len(xs) < 2:
        raise ValueError("벗어난 기간을 찾으려면 기간이 2개 이상이어야 해요.")
    k = float(op["k"])
    if op["outlier"] == "z":
        mid, sd = statistics.mean(xs), statistics.stdev(xs)
        score = lambda v: None if not sd else (v - mid) / sd
        note = f"평균 {mid:,.4g}, 표준편차 {sd:,.4g}에서 {k:g}배 넘게 떨어진 기간이에요."
        hit = lambda v: sd and abs(score(v)) > k
        col = "z-점수"
    else:
        q1, _, q3 = statistics.quantiles(xs, n=4, method="inclusive")
        lo, hi = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
        score = lambda v: v - hi if v > hi else v - lo
        note = f"사분위 범위 {q1:,.4g}~{q3:,.4g}의 {k:g}배 밖({lo:,.4g} 미만, {hi:,.4g} 초과)인 기간이에요."
        hit = lambda v: v < lo or v > hi
        col = "벗어난 크기"
    out = [{"기간": x["기간"], vname: v, col: score(v)} for x, v in zip(rows, xs) if hit(v)]
    return {**one(["기간"], col, out, False, ["기간", vname, col]), "notes": [note]}


def nested_run(r):
    """대상마다 구한 값을 다시 집계한다(DB에서 한 번에). 대상이 비어 있는 행은 뺀다."""
    expr = querying.runnable(r["body"], select=False)
    ent = parse.column(expr, r["entity"])
    expr = expr.filter(ent.notnull())
    ent = parse.column(expr, r["entity"])
    inner, outer = r["inner"], r["outer"]
    if outer["op"] in ("summary", "versus"):
        return (summary if outer["op"] == "summary" else versus)(r, expr, ent)
    if "flags" in inner:
        made = {k: sql_funcs.aggregate("any", expr, None, None, {"cond": c}) for k, c in inner["flags"].items()}
    else:
        made = {"v": sql_funcs.aggregate(inner["how"], expr, inner.get("value"), None, inner.get("opts"))}
    per = expr.group_by(ent.name("e")).aggregate(**made)
    if outer["op"] == "count_bucket":
        return count_bucket(r, per)
    flag = lambda c: c.cast("int64")
    if outer["op"] == "mean":
        final = per.aggregate(v=per.v.mean())
    elif outer["op"] == "share_ge":
        final = per.aggregate(v=flag(per.v >= outer["n"]).mean())
    else:  # both_ratio: 단계 A를 한 대상 중 B도 한 대상의 비율
        final = per.aggregate(num=flag(per.a & per.b).sum(), den=flag(per.a).sum())
    row = run(lambda: final.to_polars().row(0, named=True))
    value = row["v"] if "v" in row else (None if not row["den"] else row["num"] / row["den"])
    return one([], "값", [{"값": value}])


def count_bucket(r, per):
    """대상마다 센 횟수(per.v)를 구간(outer.bounds·labels, sql_funcs bucket)으로 나눈 구간별 대상 수. 구간 순서대로, 대상이 없는 구간은 0.
    r["entities"]가 있으면 그 테이블의 대상을 기준으로 이어 행이 없는 대상은 0회로 센다."""
    if r.get("entities"):
        t = querying.runnable(r["entities"]["body"], select=False)
        e = parse.column(t, r["entities"]["column"])
        ents = t.filter(e.notnull()).select(e.name("e")).distinct()
        j = ents.left_join(per, "e")
        per = j.select("e", v=j.v.fill_null(0))
    labels = r["outer"]["labels"]
    marked = per.mutate(k=sql_funcs.make("bucket", per, {"x": "v", "bounds": r["outer"]["bounds"], "labels": labels}))
    got = run(lambda: marked.group_by("k").aggregate(n=marked.count()).to_polars())
    counts = dict(zip(got["k"].to_list(), got["n"].to_list()))
    total = sum(counts.get(k, 0) for k in labels)
    return one(["횟수"], "대상 수", [{"횟수": k, "대상 수": counts.get(k, 0), SHARE: share(counts.get(k, 0), total)} for k in labels])


def count_bounds(raw):
    """구간 경계 칸('1, 2, 5' 글 또는 목록) → 0 이상의 정수 목록(작은 수부터 겹치지 않게). 아니면 sentence_run.Bad."""
    parts = [x.strip() for x in raw.split(",")] if isinstance(raw, str) else list(raw or [])
    try:
        nums = [int(str(x)) for x in parts if str(x) != ""]
    except ValueError:
        raise sentence_run.Bad("구간 경계에는 1, 2, 5, 10처럼 0 이상의 정수를 쉼표로 넣어 주세요.") from None
    if not nums or min(nums) < 0 or nums != sorted(set(nums)):
        raise sentence_run.Bad("구간 경계는 0 이상의 정수를 작은 수부터 겹치지 않게 넣어 주세요.")
    return nums


def count_labels(bounds, unit="회", low=0):
    """횟수 경계(구간의 시작) → 구간 라벨. [1, 2, 5, 10, 20] → 0회 / 1회 / 2~4회 / 5~9회 / 10~19회 / 20회 이상. low(가장 작은 횟수) 아래 경계는 버린다."""
    edges = [low, *[b for b in bounds if b > low]]
    return [f"{a}{unit}" if b - a == 1 else f"{a}~{b - 1}{unit}" for a, b in zip(edges, edges[1:])] + [f"{edges[-1]}{unit} 이상"]


def e51(p):
    """탐색 틀 51(횟수 구간별 대상 수) → 요청(route nested, outer count_bucket). 대상 테이블이 있으면 그 테이블의 대상이 기준이라 행이 없는 대상은 0회."""
    ent, table = p.col("대상"), p.get("대상 테이블")
    low = 0 if table else 1  # 같은 테이블만 쓰면 센 대상은 모두 1회 이상
    bounds = [b for b in count_bounds(p.get("경계", "1, 2, 5, 10, 20")) if b > low]
    if not bounds:
        raise sentence_run.Bad(f"구간이 하나뿐이에요. {low}보다 큰 경계를 넣어 주세요.")
    labels = count_labels(bounds, str(p.get("단위 글자", "회")), low)
    extra = {}
    if table:
        extra["entities"] = {"body": other_table(p, table), "column": str(p.get("대상 테이블 컬럼", ent))}
        p.notes.append(f"{table} 테이블의 대상을 기준으로 세어 행이 없는 대상은 {labels[0]}에 들어가요.")
    p.run("횟수 구간별 대상 수", "nested", copy.deepcopy(p.body), entity=ent, inner={"how": "count"}, outer={"op": "count_bucket", "bounds": bounds, "labels": labels}, **extra)


def other_table(p, table):
    """다른 테이블 table을 읽는 body. 사건 테이블의 조건·기간·계산은 걸지 않는다."""
    body = {**copy.deepcopy(p.body), "base": table, "joins": [], "conditions": [], "periods": [], "calcs": [], "sorts": [], "columns": []}
    body.pop("union", None)
    return body


SUMMARY_SORT = {"행 수": "n", "고유 상대 수": "np", "활동일": "days", "하루 최대": "mx"}  # 52번 {정렬} → 요약 열


def e52(p):
    """탐색 틀 52(대상별 활동 요약) → 요청(route nested, outer summary). 상대·날짜·대표 값 칸이 비면 그 열은 뺀다."""
    ent = p.col("대상")
    peer, day, rep = (p.col(k) if p.get(k) else None for k in ("상대", "날짜", "대표 값"))
    sort = p.get("정렬", "행 수")
    if sort not in SUMMARY_SORT:
        raise sentence_run.Bad(f"정렬은 {', '.join(SUMMARY_SORT)} 중 하나예요.")
    if (sort == "고유 상대 수" and not peer) or (sort in ("활동일", "하루 최대") and not day):
        raise sentence_run.Bad(f"‘{sort}’ 순으로 보려면 ‘{'상대' if sort == '고유 상대 수' else '날짜'}’ 칸을 채워 주세요.")
    p.run("대상별 활동 요약", "nested", copy.deepcopy(p.body), entity=ent, inner={"how": "count"},
          outer={"op": "summary", "peer": peer, "day": day, "rep": rep, "sort": SUMMARY_SORT[sort], "n": min(int(p.get("N", 20)), sentence_run.MAX_TOP)})


def e53(p):
    """탐색 틀 53(집단 대 전체 비교) → 요청(route nested, outer versus). 집단 = {조건}에 맞는 행이 있는 대상(집단 테이블이 있으면 그 테이블에서 고른 대상)."""
    ent, cond, table = p.col("대상"), p.need("조건"), p.get("집단 테이블")
    body = other_table(p, table) if table else copy.deepcopy(p.body)
    body["conditions"] = [*body["conditions"], *(cond if isinstance(cond, list) else [cond])]
    p.run("집단 대 전체 비교", "nested", copy.deepcopy(p.body), entity=ent, inner={"how": "count"},
          outer={"op": "versus", "day": p.col("날짜") if p.get("날짜") else None, "name": str(p.get("집단 이름", "조건에 맞는 대상"))},
          group={"body": body, "column": str(p.get("집단 테이블 컬럼", ent)) if table else ent})
    p.notes.append("집단 대상 중 이 테이블에 행이 없는 대상은 세지 않아요.")


def most(t, c):
    """대상(e)마다 가장 많이 나온 c 값과 그 행 수 k(같으면 작은 값). 빈 값은 세지 않는다."""
    g = t.filter(t[c].notnull()).group_by(["e", c]).aggregate(k=ibis._.count())
    g = g.mutate(r=ibis.row_number().over(group_by="e", order_by=[ibis.desc("k"), c]))
    return g.filter(g.r == 0)


def summary(r, expr, ent):
    """대상마다 한 줄: 행 수, 상대 고유 수·최다 상대 행 수·비중·같은 상대 하루 최대, 활동일·하루 평균·하루 최대, 대표 값의 최빈값과 건수. 정렬 열 큰 순으로 앞 n개."""
    o = r["outer"]
    peer, day, rep = o["peer"], o["day"], o["rep"]
    t = expr.select(e=ent, **({"p": parse.column(expr, peer)} if peer else {}), **({"d": parse.column(expr, day).cast("date")} if day else {}),
                    **({"v": parse.column(expr, rep)} if rep else {}))
    out = t.group_by("e").aggregate(n=t.count(), **({"np": t.p.nunique()} if peer else {}))
    names = {"e": r["entity"], "n": "행 수", "pct": SHARE}
    parts = []
    if peer:
        parts.append(most(t, "p").select("e", tp="k"))
        names.update(np=f"고유 {peer} 수", tp=f"최다 {peer} 행 수", share="전체 중 비중")
        if day:
            pd = t.group_by(["e", "p", "d"]).aggregate(k=t.count())
            parts.append(pd.group_by("e").aggregate(sp=pd.k.max()))
            names["sp"] = f"같은 {peer} 하루 최대"
    if day:
        dd = t.group_by(["e", "d"]).aggregate(k=t.count())
        parts.append(dd.group_by("e").aggregate(days=dd.count(), avg=dd.k.mean(), mx=dd.k.max()))
        names.update(days="활동일", avg="하루 평균", mx="하루 최대")
    if rep:
        parts.append(most(t, "v").select("e", rv="v", rk="k"))
        names.update(rv=f"주요 {rep}", rk=f"주요 {rep} 건수")
    for x in parts:
        out = out.left_join(x, "e").drop("e_right")
    if peer:
        out = out.mutate(share=out.tp / out.n)
    out = out.cross_join(out.aggregate(all_n=out.n.sum()))  # 상위 N개로 자르기 전의 모든 대상 중 몫(창 함수는 파일(Polars) 연결이 못 한다)
    out = out.mutate(pct=out.n / out.all_n * 100)
    out = out.order_by([ibis.desc(o["sort"]), "e"]).select(**{v: k for k, v in names.items()}).limit(o["n"] + 1)
    table = server.to_json(run(out.to_polars))
    return one([r["entity"]], None, table["rows"][:o["n"]], len(table["rows"]) > o["n"], table["columns"])


def versus(r, expr, ent):
    """집단(r.group의 body·컬럼에서 고른 대상)과 전체 대상의 같은 요약값: 대상 수, 전체 행 수, (날짜가 있으면 대상·하루마다, 없으면 대상마다) 행 수의 평균·중앙값·최소·최대."""
    o = r["outer"]
    day = o.get("day")
    per = expr.group_by(e=ent, **({"d": parse.column(expr, day).cast("date")} if day else {})).aggregate(k=ibis._.count())
    g = querying.runnable(r["group"]["body"], select=False)
    gc = parse.column(g, r["group"]["column"])
    members = g.filter(gc.notnull()).select(e=gc).distinct()
    stats = lambda x: x.aggregate(a=x.e.nunique(), b=x.k.sum(), c=x.k.mean(), d=x.k.median(), f=x.k.min(), h=x.k.max(), n=x.count(), s=x.k.std())
    inside = per.e.isin(members.e)
    *both, rest = run(lambda: [stats(x).to_polars().row(0) for x in (per, per.filter(inside), per.filter(~inside))])
    unit = "하루" if day else "대상당"
    labels = [f"{r['entity']} 수", "전체 행 수", f"{unit} 평균", f"{unit} 중앙값", f"{unit} 최소", f"{unit} 최대"]
    cols = ["지표", f"전체 {r['entity']}", o["name"], SHARE, VERSUS_P]
    shares = [share(both[1][i], both[0][i]) for i in range(2)] + [None] * 4  # 대상 수·전체 행 수만 집단이 전체에서 차지하는 몫이 뜻이 있다(평균·중앙값·최소·최대는 아니다)
    pv = [None, None, welch_p(rest, both[1]), None, None, None]  # 평균만 검정한다: 집단 대 나머지(전체에는 집단이 들어 있어 독립이 아니다)
    return {**one([], None, [dict(zip(cols, x)) for x in zip(labels, *(b[:6] for b in both), shares, pv)], columns=cols),
            "notes": [f"{VERSUS_P}는 집단과 나머지 {r['entity']}의 {unit} 평균을 Welch t 검정으로 견준 값이에요(작을수록 우연이 아닐 가능성이 커요)."]}


def welch_p(a, b):
    """요약 행(stats: c 평균, s 표준편차, n 개수)의 Welch t 검정 p-value. 검정할 수 없으면(표본 2개 미만, 값이 모두 같음) None."""
    from metrics import metric_calc
    try:
        return metric_calc.welch_from_stats(a[2], a[7] or 0, a[6], b[2], b[7] or 0, b[6])["p"]
    except (ValueError, TypeError):
        return None


sentence_run.COMPILERS.update(e51=e51, e52=e52, e53=e53)  # sentence_run은 다른 묶음 소유라 51~53번 틀의 만들기는 여기서 붙인다(flow_app이 이 모듈을 올린다)


def div(x, y):
    return None if x is None or not y else x / y


def scalar(res):
    return res["rows"][0][res["col"]] if res["rows"] else None


def combine(spec, results):
    """합치는 방법대로 끝난 표 {columns, rows}. ratio·percent·share는 a ÷ b(percent는 ×100), by가 같은 줄끼리(b가 한 줄이면 전체로 나눔)."""
    op, a = spec["op"], results[spec["a"]]
    name = spec.get("name") or NAMES[op]
    if op in ("ratio", "percent", "share", "sub", "add", "change"):
        b = results[spec["b"]]
    if op in ("ratio", "percent", "share"):
        scale = 100 if op == "percent" else 1
        if not a["keys"]:
            x, y = scalar(a), scalar(b)
            return {"columns": [name], "rows": [{name: None if div(x, y) is None else div(x, y) * scale}]}
        by = spec.get("by") or a["keys"]
        total = None if b["keys"] else scalar(b)
        under = {tuple(r[k] for k in by): r[b["col"]] for r in b["rows"]} if b["keys"] else {}
        show = spec.get("show_b") and b["keys"]  # b의 값 컬럼도 함께 보인다
        bname = spec.get("b_name") or b["col"]  # 분자와 분모의 결과 이름이 같으면(고유 개수 ÷ 고유 개수) 이름을 따로 준다
        rows = []
        for r in a["rows"]:
            y = total if not b["keys"] else under.get(tuple(r[k] for k in by))
            if spec.get("min_b") and (y is None or y < spec["min_b"]):
                continue  # 표본(분모)이 작은 항목은 뺀다
            v = div(r[a["col"]], y)
            rows.append({**r, **({bname: y} if show else {}), name: None if v is None else v * scale})
        if spec.get("sort") == "desc":  # 값이 큰 순, 값이 없으면 맨 뒤(같은 값은 원래 순서)
            rows.sort(key=lambda x: (x[name] is None, -(x[name] or 0)))
        return {"columns": [*a["keys"], a["col"], *([bname] if show else []), name], "rows": rows}
    if op in ("sub", "add"):
        x, y = scalar(a), scalar(b)
        return {"columns": [name], "rows": [{name: None if x is None or y is None else (x - y if op == "sub" else x + y)}]}
    if op == "change":  # 기준값이 음수여도 늘면 +가 되게 크기(|y|)로 나눈다
        x, y = scalar(a), scalar(b)
        return {"columns": [name], "rows": [{name: None if x is None or not y else (x - y) / abs(y)}]}
    if op == "rest":  # 상위 N 뒤에 '기타 = 전체 − 상위 합' 한 줄(남는 것이 없으면 줄을 더하지 않는다)
        b = results[spec["b"]]
        total, shown = scalar(b), sum(r[a["col"]] or 0 for r in a["rows"])
        rows = list(a["rows"])
        if total is not None and total - shown and a["keys"] and not a["more"]:
            rows.append({**{k: None for k in a["keys"]}, a["keys"][0]: "기타", a["col"]: total - shown})
        return {"columns": [*a["keys"], a["col"]], "rows": rows}
    if op == "pareto":  # 큰 순 합계를 쌓은 누적 비중과 등급(앞 항목들의 누적이 80% 전이면 A, 95% 전이면 B)
        total, run_sum, rows = scalar(results[spec["b"]]), 0, []
        for r in a["rows"]:
            before = div(run_sum, total) or 0
            run_sum += r[a["col"]] or 0
            rows.append({**r, name: div(run_sum, total), "등급": "A" if before < 0.8 else "B" if before < 0.95 else "C"})
        return {"columns": [*a["keys"], a["col"], name, "등급"], "rows": rows}
    if op == "mean_of":
        if a["more"]:  # 앞 기간만 받았으니 평균을 내면 조용히 틀린 값이 된다
            raise ValueError(f"기간이 {metric_file.MAX_GROUPS}개를 넘어 평균을 낼 수 없어요. 기간을 좁혀 주세요.")
        values = [r[a["col"]] for r in a["rows"] if r[a["col"]] is not None]
        return {"columns": [name], "rows": [{name: sum(values) / len(values) if values else None}]}
    if op == "cumsum":
        if spec.get("reverse") and a["more"]:  # 작은 쪽부터 앞부분만 받았으니 거꾸로 더하면 모든 값이 틀린다
            raise ValueError(f"값이 {metric_file.MAX_GROUPS}개를 넘어 거꾸로 누적할 수 없어요. 기간을 좁혀 주세요.")
        total, rows = 0, []
        for r in reversed(a["rows"]) if spec.get("reverse") else a["rows"]:
            total += r[a["col"]] or 0
            rows.append({**r, name: total})
        return {"columns": [*a["keys"], a["col"], name], "rows": rows}
    raise ValueError(f"알 수 없는 합치기: {op}")


def execute_compiled(done, depth=0):
    """compile 결과를 실행해 끝난 표 {columns, rows, more, notes, text}. 만들 수 없는 문장이면 ValueError(이유)."""
    if not done["ok"]:
        raise ValueError(done["reason"])
    results = [execute_run(r, depth) for r in done["runs"]]
    more = any(x["more"] for x in results)
    notes = [*(done.get("notes") or []), *[n for x in results for n in x.get("notes", [])]]
    if done.get("combine"):
        table = combine(done["combine"], results)
    else:
        first = results[0]
        table = {"columns": first.get("columns") or (list(first["rows"][0]) if first["rows"] else [*first["keys"], first["col"]]), "rows": first["rows"]}
    return {**table, "more": more, "notes": notes, "text": done.get("text", "")}


def columns_of(body):
    """검색 body 테이블의 {컬럼: 종류}(스키마만 읽는다)."""
    return {c: sql_funcs.kind_of(t) for c, t in querying.joined(body)[0].schema().items()}


def prepare(tid, slots, body, columns=None):
    """문장을 검사·만든 결과(sentence_run.compile). 만들 수 없는 문장이면 ok False."""
    columns = columns if columns is not None else columns_of(body)
    return sentence_run.compile(tid, slots, body, columns)


def whole_bodies(r):
    """요청이 추가로 읽는 표의 body: 지표 사전의 전환 계열 내 지표는 기간 밖 B를 찾으려 기간을 뺀 표도 읽는다(catalog_app.compute_entry의 whole). 그 밖은 없음."""
    if r.get("route") != "metric" or not r["body"].get("periods") or r.get("ref") is None:
        return []
    try:
        mine = my_metrics.get(r["ref"])
    except (KeyError, ValueError):  # 지표 파일의 지표는 내 지표 목록에 없다
        return []
    if not metric_catalog.is_catalog(mine["template"]) or metric_catalog.get(mine["template"])["calc"] != "conversion":
        return []
    return [{**r["body"], "periods": []}]


def scan_ask(done, request_body):
    """실행 전 처리 용량 확인(server.scan_gate): 요청이 읽을 body마다(같은 body는 한 번, 전환 지표의 기간 뺀 표 포함) 묻고, 확인이 필요하면 돌려줄 응답, 아니면 None."""
    if not done["ok"]:
        return None
    con = server.get_con()
    seen = set()
    for r in done["runs"]:
        for body in [r["body"], *whole_bodies(r)]:
            key = json.dumps(body, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            if ask := server.scan_gate(request_body, lambda b=body: server.expr_sql(con, querying.filtered(b))):
                return ask
    return None
