"""흐름·문장·내 지표의 라우트(6단계 서버). server.app에 달린다.
연결을 쓰지 않는 라우트(이음 검사, graph↔body 변환, 문장 검사·만들기, 내 지표 저장·검사·복사, 기억한 키)는 차례(turn)를 기다리지 않는다(FREE). 스키마는 화면이 가진 것을 요청에 담아 보낸다
(schemas = {테이블: {columns: {컬럼: 종류}, source_key}}, /api/flow/schemas로 받는다).
연결에서 읽는 라우트(행 수 세기, 최적화 제안·적용, 키 찾기, 이은 뒤 부풂)는 연결 스레드에서 돈다. 원격 DB는 값을 읽는 조회를 확인(confirmed·scan_ok) 없이는 보내지 않는다."""
from flask import jsonify, request

import core
import flow
import flow_opt
import held
import join_keys
import my_metrics
import result
import sentence_exec
import sentence_run
import sentences
import server
from dash_refresh import current_source
from server import app, get_con, run

server.CONSTANTS["flow"] = {"ranks": flow.RANK, "names": flow.NAMES, "order": flow.ORDER_TEXT, "same_rank": list(flow.SAME_OK), "kinds": flow.KIND_TEXT,
                            "opt_rules": {n: t for n, (_, t) in flow_opt.RULES.items()}, "keys": {"min": join_keys.DECIDE_MIN, "gap": join_keys.DECIDE_GAP,
                                                                                               "weights": join_keys.WEIGHTS, "sample_values": join_keys.SAMPLE_VALUES},
                            "my_metrics": {"max": my_metrics.MAX_METRICS, "align_units": list(my_metrics.ALIGN_UNITS), "better": list(my_metrics.BETTER)}}
server.FREE.update({"/api/flow/check_edge", "/api/flow/check", "/api/flow/to_body", "/api/flow/from_body", "/api/flow/keys/remember", "/api/flow/keys/forget",
                    "/api/my_metrics", "/api/my_metrics/validate", "/api/my_metrics/update", "/api/my_metrics/delete", "/api/my_metrics/copy", "/api/my_metrics/status",
                    "/api/my_metrics/check_use", "/api/my_metrics/used_by", "/api/my_metrics/stale", "/api/my_metrics/legacy", "/api/my_metrics/bind_legacy",
                    "/api/sentence/check", "/api/sentence/compile"})


def table_getter():
    """(이름, 스키마) → 테이블 식. 가상 테이블도 푼다."""
    con = get_con()
    return lambda name, schema=None: server.any_table(con, name, schema)


def body_of(data):
    return data if isinstance(data, dict) else {}


# ---------- 흐름: 검사·변환(연결 없음) ----------

@app.post("/api/flow/check_edge")
def flow_check_edge():
    """이음 a→b를 해도 되는지. {ok, reason?, code?, suggest?}. schemas가 없으면 순위·순환·모양만 본다."""
    data = body_of(request.json)
    graph = flow.check_shape(data.get("graph"))
    found = flow.edge_problem(graph, data.get("a"), data.get("b"), data.get("schemas"))
    return jsonify(ok=found is None, **(found or {}))


@app.post("/api/flow/check")
def flow_check():
    """흐름의 이음을 모두 다시 검사한 [{edge, code, reason}]."""
    data = body_of(request.json)
    graph = flow.check_shape(data.get("graph"))
    return jsonify(problems=flow.check_graph(graph, data.get("schemas")), body_problem=flow.body_problem(graph, data.get("target")) if graph["nodes"] else None)


@app.post("/api/flow/to_body")
def flow_to_body():
    """흐름 → 검색 body(단계 카드·문장이 같은 body를 본다). target 단계까지."""
    data = body_of(request.json)
    return jsonify(body=flow.to_body(data.get("graph"), data.get("target")))


@app.post("/api/flow/from_body")
def flow_from_body():
    """검색 body → 흐름."""
    body = body_of(request.json).get("body")
    if not isinstance(body, dict) or not body.get("base"):
        raise ValueError("기준 테이블이 있는 검색 body가 필요해요.")
    return jsonify(graph=flow.from_body(body))


# ---------- 흐름: 연결에서 읽는 일 ----------

@app.post("/api/flow/schemas")
def flow_schemas():
    """흐름의 테이블 단계마다 {테이블: {columns, source_key}}(스키마만 읽는다). 화면이 받아 두고 check_edge 요청에 담는다."""
    graph = flow.check_shape(body_of(request.json).get("graph"))
    key = (current_source() or {}).get("key")
    return jsonify(schemas=run(lambda: flow.schemas_of(graph, table_getter(), key)), source=current_source())


@app.post("/api/flow/rows")
def flow_rows():
    """[단계별 행 수 세기]: target까지의 단계마다 그 단계까지의 행 수. 원격 DB는 확인(confirmed)이 있어야 센다."""
    data = body_of(request.json)
    graph = flow.check_shape(data.get("graph"))
    target = data.get("target") or flow.one_sink(graph)
    con = get_con()
    return jsonify(**flow.row_counts(graph, target, lambda expr: run(lambda: result.get_count(expr)), core.runnable, table_getter(), held.remote(con), data.get("confirmed") is True))


@app.post("/api/flow/optimize")
def flow_optimize():
    """최적화 제안(적용하지 않는다). 원격 DB는 scan_ok(전체를 읽는 확인)가 없으면 구조상 같은 규칙만 '확인 안 됨'으로."""
    data = body_of(request.json)
    graph = flow.check_shape(data.get("graph"))
    con = get_con()
    remote, key, get = held.remote(con), (current_source() or {}).get("key"), table_getter()
    found = run(lambda: flow_opt.propose(graph, get, con, target=data.get("target"), remote=remote, scan_ok=data.get("scan_ok") is True, opened=server.state["opened"], source_key=key))
    return jsonify(**found)


@app.post("/api/flow/optimize/apply")
def flow_optimize_apply():
    """고른 규칙(rules = 번호 목록)만 다시 확인해 적용한 새 흐름. 사용자가 [이 흐름으로 바꾸기]를 눌렀을 때만 부른다."""
    data = body_of(request.json)
    graph = flow.check_shape(data.get("graph"))
    numbers = [n for n in data.get("rules") or [] if n in flow_opt.RULES]
    if not numbers:
        raise ValueError("적용할 규칙을 고르세요.")
    con = get_con()
    remote, key, get = held.remote(con), (current_source() or {}).get("key"), table_getter()
    done = run(lambda: flow_opt.apply(graph, numbers, get, con, target=data.get("target"), remote=remote, scan_ok=data.get("scan_ok") is True,
                                      opened=server.state["opened"], source_key=key))
    return jsonify(**done, body_problem=flow.body_problem(done["graph"], data.get("target")))


# ---------- 공통 키 ----------

def table_pair(data):
    """요청의 a·b({table, schema?})로 (A 식, B 식, 이름들)."""
    get = table_getter()
    a, b = body_of(data.get("a")), body_of(data.get("b"))
    if not a.get("table") or not b.get("table"):
        raise ValueError("이을 두 테이블(a, b)을 고르세요.")
    return get(a["table"], a.get("schema") or None), get(b["table"], b.get("schema") or None), a["table"], b["table"]


def preview_samples(con, a, b, a_name, b_name):
    """원격 DB에서 새 조회 없이 얻을 수 있는 표본: BigQuery의 무과금 미리보기(profiler.preview)뿐. 없으면 None(표본 없음)."""
    import profiler
    import polars as pl
    found = []
    for t, name in ((a, a_name), (b, b_name)):
        got = profiler.preview(con, t, None, name, join_keys.SAMPLE_ROWS, None, None)
        if got is None:
            return None
        found.append(pl.from_arrow(got))
    return {"a": found[0], "b": found[1]}


@app.post("/api/flow/keys")
def flow_keys():
    """공통 키 찾기: 후보 점수(이름·겹침·타입·고유), 결정(auto·ask·none), 상위 3개와 근거, 날짜 맞춤 제안, 기억한 키.
    표본 기준이다. full을 주면 전체 값으로 확인한다(로컬 연결만). 원격 DB는 값을 읽는 새 조회를 보내지 않는다(표본이 없으면 이름·타입으로만)."""
    data = body_of(request.json)
    con, key = get_con(), (current_source() or {}).get("key")
    remote = held.remote(con)
    if remote and data.get("full") is True:
        raise ValueError("원격 DB에서는 전체 값 확인을 하지 않아요(조회 요금). 표본 기준 결과를 쓰세요.")

    def work():
        a, b, an, bn = table_pair(data)
        fp = join_keys.fingerprint(a, b)
        recalled = join_keys.recall(key, an, bn, fp) if key else None
        samples = preview_samples(con, a, b, an, bn) if remote else None
        found = join_keys.suggest(a, b, an, bn, remote=remote, samples=samples, full=data.get("full") is True, recalled=recalled)
        return {**found, "fingerprint": fp, "source_key": key, "remembered": bool(recalled)}
    return jsonify(**run(work))


@app.post("/api/flow/keys/remember")
def flow_keys_remember():
    """고른 키를 기억한다(연결 열쇠·A·B·스키마 지문 단위)."""
    data = body_of(request.json)
    keys = data.get("keys")
    if not all(isinstance(x, str) and x for x in (data.get("source_key"), data.get("a"), data.get("b"), data.get("fingerprint"))) or not isinstance(keys, list) or not keys:
        raise ValueError("기억할 연결·테이블·키가 필요해요.")
    join_keys.remember(data["source_key"], data["a"], data["b"], data["fingerprint"], keys)
    return jsonify(ok=True)


@app.post("/api/flow/keys/forget")
def flow_keys_forget():
    data = body_of(request.json)
    join_keys.forget(data.get("source_key", ""), data.get("a", ""), data.get("b", ""), data.get("fingerprint", ""))
    return jsonify(ok=True)


@app.post("/api/flow/fanout")
def flow_fanout():
    """이은 뒤 행이 늘어나는지(부풂) 결과 전에 알린다: 조인 단계 join의 왼쪽·오른쪽 입력으로. 전체를 읽으므로 원격 DB는 scan_ok가 있어야 센다."""
    data = body_of(request.json)
    graph = flow.check_shape(data.get("graph"))
    node = flow.by_id(graph).get(data.get("join"))
    if node is None or node["kind"] != "join":
        raise ValueError("조인 단계를 고르세요.")
    left, right = flow.join_sides(graph, node["id"])
    if right is None:
        raise ValueError("조인의 오른쪽에 테이블이 이어지지 않았어요.")
    con, get = get_con(), table_getter()
    params = flow.params(node)
    pairs = [tuple(k) for k in params.get("keys") or []]
    return jsonify(**run(lambda: join_keys.fanout(flow.to_expr(graph, left, get), flow.to_expr(graph, right, get), pairs, params.get("how", "inner"),
                                                   flow.table_name(graph, right), held.remote(con), data.get("scan_ok") is True)))


# ---------- 내 지표 ----------

def with_scope(defn):
    """정의의 scope에 key·label이 없으면 지금 연결로 채운다."""
    defn = dict(defn or {})
    scope = dict(defn.get("scope") or {})
    now = current_source()
    if now:
        scope.setdefault("key", now["key"])
        scope.setdefault("label", now["label"])
    return {**defn, "scope": scope}


def columns_of(data):
    cols = data.get("columns")
    return cols if isinstance(cols, dict) else None


def view(metric):
    return {**metric, "text": my_metrics.scope_text(metric)}


@app.get("/api/my_metrics")
def my_metrics_list():
    """내 지표 목록과 지표 틀(scope 없음), 예전 지표 파일의 공통 정의."""
    items = my_metrics.load()
    return jsonify(metrics=[view(m) for m in items], templates=my_metrics.templates(), legacy=my_metrics.legacy_list(), source=current_source())


@app.post("/api/my_metrics")
def my_metrics_create():
    """내 지표를 만든다. 정의 {name, scope(key·label은 지금 연결로 채움), template, slots, unit, better, align?}, columns = 그 테이블의 {컬럼: 종류}."""
    data = body_of(request.json)
    return jsonify(metric=view(my_metrics.create(with_scope(data.get("definition")), columns_of(data))))


@app.post("/api/my_metrics/validate")
def my_metrics_validate():
    """저장하지 않고 정의를 검사한다. {ok, problem?}."""
    data = body_of(request.json)
    try:
        made = my_metrics.build(with_scope(data.get("definition")), columns_of(data), my_metrics.load(), data.get("id"))
    except ValueError as e:
        return jsonify(ok=False, problem=str(e))
    return jsonify(ok=True, deps=made["deps"], cols=made["cols"])


@app.post("/api/my_metrics/update")
def my_metrics_update():
    """지표를 고친다. 정의가 바뀌면 영향받는 지표와 쓰는 카드·목표를 함께 돌려준다."""
    data = body_of(request.json)
    patch = dict(data.get("patch") or {})
    if "scope" in patch:
        patch = with_scope(patch)
    done = my_metrics.update(data.get("id"), patch, columns_of(data))
    return jsonify(**{**done, "metric": view(done["metric"])})


@app.post("/api/my_metrics/delete")
def my_metrics_delete():
    return jsonify(**my_metrics.remove(body_of(request.json).get("id")))


@app.post("/api/my_metrics/copy")
def my_metrics_copy():
    """[이 테이블에 맞춰 복사]: 지표(와 의존하는 지표)를 다른 테이블로 새 id로. mapping = {원래 컬럼: 새 컬럼}."""
    data = body_of(request.json)
    scope = with_scope({"scope": data.get("scope")})["scope"]
    done = my_metrics.copy_to(data.get("id"), scope, data.get("mapping"), columns_of(data), data.get("name"))
    return jsonify(metrics=[view(m) for m in done["metrics"]], id_map=done["id_map"])


@app.post("/api/my_metrics/status")
def my_metrics_status():
    """테이블 구조가 바뀌었는지: {지표 id: {usable, reason}} (columns = 지금 테이블의 {컬럼: 종류}, scope가 같은 지표만)."""
    data = body_of(request.json)
    cols = columns_of(data)
    if cols is None:
        raise ValueError("지금 테이블의 컬럼(columns)을 함께 보내세요.")
    scope = with_scope({"scope": data.get("scope")})["scope"]
    items = my_metrics.load()
    return jsonify(status={m["id"]: my_metrics.status(m, cols, items) for m in items if my_metrics.scope_id(m.get("scope")) == my_metrics.scope_id(scope)})


@app.post("/api/my_metrics/check_use")
def my_metrics_check_use():
    """지표를 이 scope에서 그대로 쓸 수 있는지: {ok, reason?}. 다른 테이블의 지표는 복사해서 쓴다."""
    data = body_of(request.json)
    metric = my_metrics.get(data.get("id"))
    reason = my_metrics.check_use(metric, with_scope({"scope": data.get("scope")})["scope"])
    return jsonify(ok=reason is None, **({"reason": reason} if reason else {}))


@app.post("/api/my_metrics/used_by")
def my_metrics_used_by():
    """정의를 고치기 전에 보일 쓰는 곳: 카드(stale 표시)와 목표 줄, 쓰는 지표."""
    data = body_of(request.json)
    metric = my_metrics.get(data.get("id"))
    return jsonify(**my_metrics.used_by(metric["id"], goals=data.get("goals")), dependents=[{"id": m["id"], "name": my_metrics.scope_text(m)} for m in my_metrics.dependents(metric["id"])])


@app.post("/api/my_metrics/stale")
def my_metrics_stale():
    """카드가 저장해 둔 def_hash가 지금 정의와 다른지: {stale, def_hash, cache_key}."""
    data = body_of(request.json)
    metric = my_metrics.get(data.get("id"))
    return jsonify(stale=my_metrics.is_stale(metric, data.get("def_hash")), def_hash=metric["def_hash"], cache_key=my_metrics.cache_key(metric, data.get("period", "")))


@app.get("/api/my_metrics/legacy")
def my_metrics_legacy():
    return jsonify(legacy=my_metrics.legacy_list())


@app.post("/api/my_metrics/bind_legacy")
def my_metrics_bind_legacy():
    """공통 정의(예전 지표 파일의 지표)를 이 테이블에 묶어 저장한다(컬럼 검사 포함)."""
    data = body_of(request.json)
    scope = with_scope({"scope": data.get("scope")})["scope"]
    return jsonify(metric=view(my_metrics.bind_legacy(data.get("name"), scope, columns_of(data), data.get("new_name"))))


# ---------- 문장 ----------

@app.post("/api/sentence/check")
def sentence_check():
    """문장 검사: 빈칸 이유 [{slot, level(bad·warn·info), text}]와 문장 글. bad가 있으면 실행하지 않는다."""
    data = body_of(request.json)
    tid, slots = data.get("template"), body_of(data.get("slots"))
    sentences.get(tid)
    found = sentence_run.check(tid, slots, columns_of(data))
    return jsonify(issues=found, ok=not any(i["level"] == "bad" for i in found), text=sentence_run.render(tid, slots))


@app.post("/api/sentence/compile")
def sentence_compile():
    """문장 → 기존 라우트가 받는 요청들(runs)과 합치는 방법(combine). 만들 수 없으면 {ok: false, reason}."""
    data = body_of(request.json)
    sentences.get(data.get("template"))
    return jsonify(**sentence_run.compile(data.get("template"), body_of(data.get("slots")), body_of(data.get("body")), columns_of(data)))


@app.post("/api/sentence/run")
def sentence_run_route():
    """문장을 만들어 실행하고 합쳐 끝난 표 {columns, rows, more, notes, text}로 돌려준다(화면은 나누거나 합치지 않는다). 만들 수 없으면 400과 이유.
    columns가 없으면 body의 테이블에서 읽는다."""
    data = body_of(request.json)
    sentences.get(data.get("template"))
    body = body_of(data.get("body"))
    if not body.get("base"):
        raise ValueError("기준 테이블이 있는 검색 body가 필요해요.")
    return jsonify(**sentence_exec.run_sentence(data["template"], body_of(data.get("slots")), body, columns_of(data)))
