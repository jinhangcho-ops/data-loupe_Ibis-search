"""문장 실행(6-4): sentence_run.compile이 만든 요청들(runs)을 연결에서 실행하고 combine대로 합쳐 끝난 표 하나 {columns, rows, more, notes}로 돌려준다. 화면은 나누거나 합치지 않는다.
요청의 route: /api/query(행), /api/pivot(묶어 집계, 기존 라우트와 같은 querying.runnable·calc.pivot), metric(지표 이름 또는 내 지표 id의 값을 지표 정의로 구함, by가 있으면 그 값마다),
nested(대상마다 구한 값을 다시 집계: 평균·N번 이상 한 대상 비율·두 단계를 다 한 비율)이다. 합치는 방법 combine: ratio·percent·share(a ÷ b, by 컬럼이 같은 줄끼리)·sub·add·mean_of·change·cumsum.
지표 사전 항목('catalog:id')으로 만든 내 지표는 catalog_app이 구한다(값 하나). 데이터를 읽는 함수는 server.run으로 연결 스레드에서 돈다. 내 지표가 다른 내 지표를 쓰면(조합) 안쪽까지 풀어 구한다(MAX_DEPTH단계)."""
import json

from core import calc
from core import querying
from metrics import metric_catalog
from core import metric_file
from metrics import my_metrics
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
NAMES = {"ratio": "값", "percent": "비율(%)", "share": "비중", "sub": "차이", "add": "합", "mean_of": "평균", "change": "변화율", "cumsum": "누적"}


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
        return one(list(spec.get("rows") or []), r["result"], server.to_json(table.head(calc.MAX_ROWS), text_big=False)["rows"], table.height > calc.MAX_ROWS)
    if route == "metric":
        return metric_run(r, depth)
    if route == "nested":
        return nested_run(r)
    raise ValueError(f"알 수 없는 요청 종류: {route}")


def check_scope(mine, body):
    """내 지표가 지금 검색 body의 테이블(연결·스키마·테이블이 같은 scope)의 것인지. 다른 테이블의 지표를 ‘맞춰서’ 쓰는 구절(align)은 아직 실행하지 않으므로 거부한다.
    예전 흐름(flow_id)에 묶은 지표는 흐름 보기가 없어져 구할 수 없다고 알린다."""
    scope = mine.get("scope") or {}
    if my_metrics.is_flow_only(mine):
        raise ValueError(my_metrics.FLOW_GONE)
    now = dash_store.current_source()
    if scope.get("table") != body.get("base") or (scope.get("schema") or "") != (body.get("schema") or "") or (now and not use_counts.same_source(scope.get("key"), now["key"])):
        raise ValueError(f"‘{my_metrics.scope_text(mine)}’은(는) 다른 테이블(흐름)의 지표라 지금 검색에서 그대로 쓸 수 없어요. [이 테이블에 맞춰 복사]해서 쓰세요.")


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
    if mine["template"] == my_metrics.LEGACY:
        return mine["name"], metric_file.metric(mine["name"], mine["slots"]["def"]), mine
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
    return one([by], name, [{by: x, name: v} for x, v in zip(labels, values[name])], more)


def nested_run(r):
    """대상마다 구한 값을 다시 집계한다(DB에서 한 번에). 대상이 비어 있는 행은 뺀다."""
    expr = querying.runnable(r["body"], select=False)
    ent = parse.column(expr, r["entity"])
    expr = expr.filter(ent.notnull())
    ent = parse.column(expr, r["entity"])
    inner, outer = r["inner"], r["outer"]
    if "flags" in inner:
        made = {k: sql_funcs.aggregate("any", expr, None, None, {"cond": c}) for k, c in inner["flags"].items()}
    else:
        made = {"v": sql_funcs.aggregate(inner["how"], expr, inner.get("value"), None, inner.get("opts"))}
    per = expr.group_by(ent.name("e")).aggregate(**made)
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
        rows = []
        for r in a["rows"]:
            y = total if not b["keys"] else under.get(tuple(r[k] for k in by))
            v = div(r[a["col"]], y)
            rows.append({**r, **({b["col"]: y} if show else {}), name: None if v is None else v * scale})
        return {"columns": [*a["keys"], a["col"], *([b["col"]] if show else []), name], "rows": rows}
    if op in ("sub", "add"):
        x, y = scalar(a), scalar(b)
        return {"columns": [name], "rows": [{name: None if x is None or y is None else (x - y if op == "sub" else x + y)}]}
    if op == "change":
        x, y = scalar(a), scalar(b)
        return {"columns": [name], "rows": [{name: None if x is None or not y else (x - y) / y}]}
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
    if done.get("combine"):
        table = combine(done["combine"], results)
    else:
        first = results[0]
        table = {"columns": first.get("columns") or (list(first["rows"][0]) if first["rows"] else [*first["keys"], first["col"]]), "rows": first["rows"]}
    return {**table, "more": more, "notes": done.get("notes") or [], "text": done.get("text", "")}


def columns_of(body):
    """검색 body 테이블의 {컬럼: 종류}(스키마만 읽는다)."""
    return {c: sql_funcs.kind_of(t) for c, t in querying.joined(body)[0].schema().items()}


def prepare(tid, slots, body, columns=None):
    """문장을 검사·만든 결과(sentence_run.compile). 만들 수 없는 문장이면 ok False."""
    columns = columns if columns is not None else columns_of(body)
    return sentence_run.compile(tid, slots, body, columns)


def scan_ask(done, request_body):
    """실행 전 처리 용량 확인(server.scan_gate): 요청이 읽을 body마다(같은 body는 한 번) 묻고, 확인이 필요하면 돌려줄 응답, 아니면 None."""
    if not done["ok"]:
        return None
    con = server.get_con()
    seen = set()
    for r in done["runs"]:
        key = json.dumps(r["body"], sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        if ask := server.scan_gate(request_body, lambda b=r["body"]: server.expr_sql(con, querying.filtered(b))):
            return ask
    return None
