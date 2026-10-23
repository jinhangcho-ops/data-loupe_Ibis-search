"""지표 사전의 라우트(7-2). server.app에 달린다. 화면(`#metric-catalog`)이 부른다.
- GET /api/catalog(FREE): 지표 사전 전체 + (columns를 주면) 그 테이블에서 쓸 수 있는지. 스키마는 화면이 가진 것을 담아 보낸다(연결을 쓰지 않는다).
- POST /api/catalog/compute: 항목 하나를 현재 연결에서 구한다. sentence_exec와 같은 규칙: 원격 DB는 값을 읽기 전에 처리 용량 확인(server.scan_gate), 파일(Polars) 연결과
  설정 prep_where가 all인 원격 DB는 필요한 컬럼만 이 PC의 DuckDB 사본(held)으로 받아 그 위에서 계산하고(prep_where가 new면 창 함수를 쓰는 항목만 사본), DuckDB 파일 연결은 그대로 한다.
  원격 DB에는 행 수를 미리 세는 조회를 보내지 않는다.
- POST /api/catalog/save(FREE): 항목을 지금 테이블(scope)에 묶은 내 지표로 저장한다(my_metrics, 새 id, template = "catalog:항목id").
저장한 내 지표를 문장·카드에서 구할 때는 compute_saved(sentence_exec.metric_run이 부른다)."""
import json

from flask import jsonify, request

import core
import held
import metric_catalog
import my_metrics
import server
import settings
from dash_refresh import current_source
from server import app, get_con, run

server.CONSTANTS["catalog"] = {"areas": metric_catalog.AREAS, "spread": [n for n, _ in metric_catalog.SPREAD], "hints": list(metric_catalog.HINTS), "prefix": metric_catalog.PREFIX}
server.FREE.update({"/api/catalog", "/api/catalog/available", "/api/catalog/save"})


def body_of(data):
    return data if isinstance(data, dict) else {}


def json_arg(name):
    """GET 인자 하나를 JSON으로(없으면 None)."""
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        found = json.loads(raw)
    except ValueError:
        raise ValueError(f"{name}은 JSON이어야 해요.") from None
    return found if isinstance(found, dict) else None


@app.get("/api/catalog")
def catalog_list():
    """지표 사전 전체. columns({컬럼: 종류})를 주면 쓸 수 있는지(availability)도, tables({테이블: {컬럼: 종류}})를 주면 다른 테이블이 필요한 항목도 따져 준다."""
    return jsonify(**metric_catalog.catalog(json_arg("columns"), json_arg("tables")))


@app.post("/api/catalog/available")
def catalog_available():
    """GET과 같은 일을 본문으로(스키마가 길 때)."""
    data = body_of(request.json)
    columns = data.get("columns")
    if not isinstance(columns, dict):
        raise ValueError("테이블의 컬럼({컬럼: 종류})을 함께 보내세요.")
    return jsonify(availability=metric_catalog.available(columns, data.get("tables") if isinstance(data.get("tables"), dict) else None))


# ---------- 구하기 ----------

def needs_copy(con, e):
    """이 항목을 이 PC의 DuckDB 사본에서 구할지. 파일(Polars) 연결은 늘, 원격 DB는 prep_where가 all(기본)이면 늘 new면 창 함수를 쓰는 항목만. DuckDB 파일 연결은 아니다."""
    if con.name == "polars":
        return True
    return held.remote(con) and (settings.load().get("prep_where", "all") == "all" or e["window"])


def stage(body, keep):
    """기간·조건까지 건 식을 keep 컬럼으로 줄인 것(컬럼이 하나도 없으면 첫 컬럼 하나만: 사본을 넓게 받지 않는다)."""
    expr = core.filtered(body)
    return core.slim(expr, keep or expr.columns[:1])


def local_table(key, expr):
    """식을 이 PC 사본(held)으로 받아 두고 그 표를 돌려준다(이미 받은 것이 있으면 다시 쓴다)."""
    name = held.ensure(key, expr, server.state["cancel"], cap=False)[0]
    table = held.table(name)
    return table.select(*[c for c in table.columns if c != held.ROW])


def table_for(e, body, keep):
    """항목이 읽을 표: 사본에서 구하는 항목이면 사본, 아니면 연결의 식 그대로."""
    expr = stage(body, keep)
    return local_table(core.search_key(body, keep or expr.columns), expr) if needs_copy(get_con(), e) else expr


def other_columns(e, slots, align):
    """다른 테이블(광고 테이블)에서 읽을 컬럼 이름들."""
    names = [slots.get(s["name"]) for s in e["slots"] if s.get("other")]
    names += [k[1] for k in (align or {}).get("keys") or [] if isinstance(k, (list, tuple)) and len(k) == 2]
    return [n for n in dict.fromkeys(names) if isinstance(n, str) and n]


def other_for(e, body, slots, align):
    """이름 → 다른 테이블 식(읽을 컬럼만) 함수. 사본에서 구하는 항목이면 이것도 사본으로 받는다."""
    con, schema = get_con(), body.get("schema") or None

    def get(name):
        expr = server.any_table(con, name, schema)
        cols = [c for c in other_columns(e, slots, align) if c in expr.columns]
        expr = expr.select(*(cols or expr.columns[:1]))
        return local_table(core.held_key("catalog_other", {"schema": schema, "table": name, "keep": cols}), expr) if needs_copy(con, e) else expr
    return get


def scan_ask(e, slots, body, request_body, align=None):
    """실행 전 처리 용량 확인(server.scan_gate): 읽을 표마다(이미 사본이 있으면 DB를 읽지 않으므로 묻지 않는다) 묻고, 확인이 필요하면 돌려줄 응답, 아니면 None."""
    con, keep = get_con(), metric_catalog.used_columns(e, slots)
    copy = needs_copy(con, e)
    if not (copy and core.search_key(body, keep or stage(body, keep).columns) in held.store["items"]):
        if ask := server.scan_gate(request_body, lambda: server.expr_sql(con, stage(body, keep))):
            return ask
    if e["cross"] and isinstance(slots.get("광고 테이블"), str):
        schema = body.get("schema") or None

        def other_sql():
            expr = server.any_table(con, slots["광고 테이블"], schema)
            cols = [c for c in other_columns(e, slots, align) if c in expr.columns]
            return server.expr_sql(con, expr.select(*(cols or expr.columns[:1])))
        return server.scan_gate(request_body, other_sql)
    return None


def compute_entry(e, slots, body, align=None, limit=None):
    """항목을 현재 연결에서 구한다(연결 스레드에서). metric_catalog.compute의 결과."""
    if not isinstance(body, dict) or not body.get("base"):
        raise ValueError("기준 테이블이 있는 검색 body가 필요해요.")
    slots = slots if isinstance(slots, dict) else {}
    ctx = metric_catalog.Ctx(fetch=lambda b, keep: table_for(e, b, keep), other=other_for(e, body, slots, align) if e["cross"] else None)
    return run(lambda: metric_catalog.compute(e, slots, body, ctx, align, limit))


def compute_saved(mine, body):
    """내 지표(template이 catalog:…)를 지금 검색 body의 테이블에서 구한다. sentence_exec.metric_run이 scope를 검사한 뒤 부른다."""
    return compute_entry(metric_catalog.get(mine["template"]), mine["slots"], body, mine.get("align"))


@app.post("/api/catalog/compute")
def catalog_compute():
    """항목 하나를 구한다. {id, slots, body(검색 body), align?(다른 테이블이 필요한 지표), limit?({op, value}: 벗어나면 alert)}. 처리 용량이 기준을 넘으면 실행 전에 묻는다(confirmed로 다시 보내면 실행)."""
    data = body_of(request.json)
    e = metric_catalog.get(data.get("id"))
    body, slots = body_of(data.get("body")), body_of(data.get("slots"))
    if not body.get("base"):
        raise ValueError("기준 테이블이 있는 검색 body가 필요해요.")
    if ask := scan_ask(e, slots, body, data, data.get("align")):
        return ask
    return jsonify(**compute_entry(e, slots, body, data.get("align"), data.get("limit")))


# ---------- 내 지표로 저장 ----------

@app.post("/api/catalog/save")
def catalog_save():
    """[내 지표로 저장]: 항목과 채운 빈칸을 지금 테이블(scope)에 묶어 새 id의 내 지표로 저장한다. {id, slots, scope{table, schema?}, name?, columns?(그 테이블의 {컬럼: 종류}), align?}."""
    data = body_of(request.json)
    e = metric_catalog.get(data.get("id"))
    if e["cross"] and not data.get("align"):
        raise ValueError("다른 테이블의 값을 쓰는 지표는 ‘기간 단위로 맞춰’ 또는 ‘공통 키로 맞춰’ 구절을 함께 골라야 저장해요.")
    scope = dict(body_of(data.get("scope")))
    now = current_source()
    if now:
        scope.setdefault("key", now["key"])
        scope.setdefault("label", now["label"])
    columns = data.get("columns") if isinstance(data.get("columns"), dict) else None
    defn = {"name": data.get("name") or e["name"], "scope": scope, "template": metric_catalog.PREFIX + e["id"], "slots": body_of(data.get("slots")),
            "unit": "%" if e["format"] == "percent" else e["unit"], "better": e["better"], "align": data.get("align")}
    made = my_metrics.create(defn, columns)
    return jsonify(metric={**made, "text": my_metrics.scope_text(made)})
