"""문장·내 지표·공통 키의 라우트. server.app에 달린다(파일 이름은 예전 흐름 보기 때의 것을 그대로 둔다).
연결을 쓰지 않는 라우트(문장 검사·만들기, 내 지표 저장·검사·복사)는 차례(turn)를 기다리지 않는다(FREE). 스키마·컬럼은 화면이 요청에 담아 보낸다.
연결에서 읽는 라우트(테이블 컬럼, 공통 키 찾기)는 연결 스레드에서 돈다. 원격 DB는 값을 읽는 새 조회를 보내지 않는다."""
from flask import jsonify, request

from lazy import Later
from core import held
from core import server
from core import settings
from core import use_counts
from core.server import app, get_con, run


dash_store = Later("core.dash_store")
join_keys = Later("query.join_keys")
my_metrics = Later("metrics.my_metrics")
sentence_exec = Later("query.sentence_exec")
sentence_run = Later("query.sentence_run")
sentences = Later("query.sentences")
sql_funcs = Later("core.sql_funcs")

MAX_SET_ITEMS, MAX_SET_SIZE = 30, 40_000  # 질문 묶음 하나의 문장 수·저장 크기
server.FREE.update({"/api/sentences", "/api/question_sets",
                    "/api/my_metrics", "/api/my_metrics/validate", "/api/my_metrics/update", "/api/my_metrics/delete", "/api/my_metrics/copy", "/api/my_metrics/status",
                    "/api/my_metrics/used_by",
                    "/api/sentence/check", "/api/sentence/compile"})


def table_getter():
    """(이름, 스키마) → 테이블 식. 가상 테이블도 푼다."""
    con = get_con()
    return lambda name, schema=None: server.any_table(con, name, schema)


def body_of(data):
    return data if isinstance(data, dict) else {}


@app.post("/api/table_columns")
def table_columns():
    """테이블 하나의 {컬럼: 종류}(스키마만 읽는다). 지표 사전이 두 번째 테이블의 컬럼을 고를 때 쓴다. 요청 {table, schema?}."""
    data = body_of(request.json)
    if not isinstance(data.get("table"), str) or not data["table"]:
        raise ValueError("테이블(table)을 고르세요.")
    get = table_getter()
    t = run(lambda: get(data["table"], data.get("schema") or None).schema())
    return jsonify(columns={c: sql_funcs.kind_of(ty) for c, ty in t.items()})


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
    from core import profiler
    import polars as pl
    found = []
    for t, name in ((a, a_name), (b, b_name)):
        got = profiler.preview(con, t, None, name, join_keys.SAMPLE_ROWS, None, None)
        if got is None:
            return None
        found.append(pl.from_arrow(got))
    return {"a": found[0], "b": found[1]}


@app.post("/api/join_keys")
def find_join_keys():
    """공통 키 찾기: 후보 점수(이름·겹침·타입·고유), 결정(auto·ask·none), 상위 3개와 근거.
    표본 기준이다. 원격 DB는 값을 읽는 새 조회를 보내지 않는다(표본이 없으면 이름·타입으로만)."""
    data = body_of(request.json)
    con = get_con()
    remote = held.remote(con)

    def work():
        a, b, an, bn = table_pair(data)
        samples = preview_samples(con, a, b, an, bn) if remote else None
        return join_keys.suggest(a, b, an, bn, remote=remote, samples=samples)
    return jsonify(**run(work))


# ---------- 내 지표 ----------

def with_scope(defn):
    """정의의 scope에 key·label이 없으면 지금 연결로 채운다."""
    defn = dict(defn or {})
    scope = dict(defn.get("scope") or {})
    now = dash_store.current_source()
    if now:
        scope["key"] = use_counts.renewed(scope.get("key"), now["key"])
        scope.setdefault("label", now["label"])
    return {**defn, "scope": scope}


def scope_of(data):
    """요청 본문의 scope(key·label은 지금 연결로 채움)."""
    return with_scope({"scope": data.get("scope")})["scope"]


def columns_of(data):
    cols = data.get("columns")
    return cols if isinstance(cols, dict) else None


def view(metric):
    return {**metric, "text": my_metrics.scope_text(metric)}


@app.get("/api/my_metrics")
def my_metrics_list():
    """내 지표 목록과 지표 틀(scope 없음), 예전 지표 파일의 공통 정의, 좋고 나쁨 값(better: 쿼리·대시보드 지표 화면이 같이 쓴다. 대시보드가 화면 상수로 받으면 지표 모듈이 미리 올라온다)."""
    items = my_metrics.load()
    return jsonify(metrics=[view(m) for m in items], templates=sentences.METRICS, legacy=my_metrics.legacy_list(), source=dash_store.current_source(), better=list(my_metrics.BETTER))


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
    scope = scope_of(data)
    done = my_metrics.copy_to(data.get("id"), scope, data.get("mapping"), columns_of(data), data.get("name"))
    return jsonify(metrics=[view(m) for m in done["metrics"]], id_map=done["id_map"])


@app.post("/api/my_metrics/status")
def my_metrics_status():
    """테이블 구조가 바뀌었는지: {지표 id: {usable, reason}} (columns = 지금 테이블의 {컬럼: 종류}, scope가 같은 지표만)."""
    data = body_of(request.json)
    cols = columns_of(data)
    if cols is None:
        raise ValueError("지금 테이블의 컬럼(columns)을 함께 보내세요.")
    scope = scope_of(data)
    items = my_metrics.load()
    return jsonify(status={m["id"]: my_metrics.status(m, cols, items) for m in items if my_metrics.same_scope(m.get("scope"), scope)})


@app.post("/api/my_metrics/used_by")
def my_metrics_used_by():
    """정의를 고치기 전에 보일 쓰는 곳: 카드(stale 표시)와 쓰는 지표."""
    data = body_of(request.json)
    metric = my_metrics.get(data.get("id"))
    return jsonify(**my_metrics.used_by(metric["id"]), dependents=[{"id": m["id"], "name": my_metrics.scope_text(m)} for m in my_metrics.dependents(metric["id"])])


# ---------- 문장 ----------

QUESTION_SETS = "question_sets.json"


@app.get("/api/sentences")
def sentences_catalog():
    """문장 틀(탐색·지표)과 빈칸 타입·컬럼 가공 옵션. 문장 화면이 처음 열릴 때 한 번 받는다(모든 화면에 싣지 않으려고 CONSTANTS에 두지 않는다)."""
    return jsonify(**sentences.catalog(), preps=sentence_run.PREPS, max_top=sentence_run.MAX_TOP, units=sql_funcs.UNIT_NAMES)


def check_set(item):
    """질문 묶음 {period?: 글, shared?: 참거짓, items: [{template, slots}]}의 모양. 아니면 ValueError."""
    if item is None:
        return None
    items = item.get("items") if isinstance(item, dict) else None
    if not isinstance(items, list) or not items or len(items) > MAX_SET_ITEMS:
        raise ValueError(f"질문 묶음에는 문장이 1개 이상 {MAX_SET_ITEMS}개 이하로 있어야 해요.")
    for one in items:
        if not isinstance(one, dict):
            raise ValueError("질문 묶음의 문장이 올바르지 않아요.")
        sentences.get(one.get("template"))
    return item


@app.get("/api/question_sets")
def question_sets_list():
    """저장한 질문 묶음 {이름: 묶음}."""
    return jsonify(sets=settings.named(QUESTION_SETS))


@app.post("/api/question_sets")
def question_sets_save():
    """질문 묶음을 이름으로 저장한다(같은 이름은 덮어씀). set이 null이면 그 이름을 지운다. 바뀐 전체를 돌려준다."""
    data = body_of(request.json)
    return jsonify(sets=settings.save_named(QUESTION_SETS, "질문 묶음", MAX_SET_SIZE, data.get("name"), check_set(data.get("set"))))


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
    done = sentence_exec.prepare(data["template"], body_of(data.get("slots")), body, columns_of(data))
    if ask := sentence_exec.scan_ask(done, data):  # 처리 용량이 기준을 넘으면 실행 전에 묻는다(confirmed로 다시 보내면 실행)
        return ask
    return jsonify(**sentence_exec.execute_compiled(done))
