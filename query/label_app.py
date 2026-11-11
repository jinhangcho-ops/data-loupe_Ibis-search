"""태그(행 단위 수동 라벨) 라우트: 상태 GET /api/labels, 켜기 POST /api/labels/setup, 사전(더하기·차례) POST /api/labels/vocab, 이름 바꾸기 POST /api/labels/rename, 지우기 POST /api/labels/drop,
고른 행에 달기·떼기 POST /api/labels/apply, 검색 결과 전체에 달기·떼기 POST /api/labels/apply_where.
search_app 끝에서 불러와 같은 server.app에 붙는다(period_app과 같은 방식). 연결을 쓰므로 FREE가 아니다(차례를 기다린다)."""
from flask import Response, jsonify, request

from core import held, labels, parse, querying, server
from core.server import app, get_con, run


def info(store: dict | None) -> Response:
    if store is None:
        return jsonify(on=False, quick=labels.QUICK_KEYS)
    return jsonify(on=True, key=store["key"], key_type=store["key_type"], text=store["text"], vocab=store["vocab"], labeled=store["labeled"], quick=labels.QUICK_KEYS)


@app.get("/api/labels")
def labels_info() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_info
    ?schema&base 테이블의 라벨 상태 {on, key, key_type, text, vocab, labeled, quick}를 돌려준다."""
    ref = labels.ref_of({"schema": request.args.get("schema", ""), "base": request.args.get("base")})
    return info(labels.load(ref))


@app.post("/api/labels/setup")
def labels_setup() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_setup
    {schema, base, key, text?, vocab?}로 테이블의 라벨을 켠다(키 고유 검사는 처리 용량 확인 뒤 연결 스레드에서)."""
    body = request.json
    ref = labels.ref_of(body)
    table = querying.table_of(body["base"], body.get("schema") or None)
    key = body.get("key")
    if not isinstance(key, str) or not key:
        raise ValueError("고유 키 컬럼을 고르세요.")
    vocab = body.get("vocab") or []
    if not isinstance(vocab, list):
        raise ValueError("태그 사전(vocab)은 글자 목록이에요.")
    parse.check_name(key, table.columns, "키 컬럼")
    con, old = get_con(), labels.load(ref)
    if (old is None or old["key"] != key) and held.remote(con) and (ask := server.scan_gate(body, lambda: server.expr_sql(con, table.aggregate(n=table.count(), u=table[key].nunique())))):
        return ask  # 키를 처음 정하거나 바꿀 때만 전체를 센다
    return info(run(lambda: labels.setup(ref, table, key, body.get("text") or "", vocab)))


@app.post("/api/labels/vocab")
def labels_vocab() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_vocab
    {schema, base, vocab}로 태그 사전(차례)을 바꾼다."""
    body = request.json
    vocab = body.get("vocab")
    if not isinstance(vocab, list):
        raise ValueError("태그 사전(vocab)은 글자 목록이에요.")
    return info(labels.set_vocab(labels.ref_of(body), vocab))


@app.post("/api/labels/rename")
def labels_rename() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_rename
    {schema, base, old, new}로 사전과 달린 행 모두에서 태그 이름을 바꾼다."""
    body = request.json
    old, new = body.get("old"), body.get("new")
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("바꿀 태그(old)와 새 이름(new)을 주세요.")
    return info(labels.rename_tag(labels.ref_of(body), old, new))


@app.post("/api/labels/drop")
def labels_drop() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_drop
    {schema, base, name}으로 사전과 달린 행 모두에서 태그를 지운다."""
    body = request.json
    if not isinstance(body.get("name"), str):
        raise ValueError("지울 태그(name)를 주세요.")
    return jsonify(**labels.drop_tag(labels.ref_of(body), body["name"]))


@app.post("/api/labels/apply")
def labels_apply() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_apply
    {schema, base, keys, add, remove}로 고른 행들에 태그를 달고 뗀다."""
    body = request.json
    keys = body.get("keys")
    if isinstance(keys, list) and len(keys) > labels.MAX_APPLY:
        raise ValueError(f"한 번에 달거나 뗄 수 있는 행은 {labels.MAX_APPLY:,}개까지예요. 많으면 [검색 결과 전체에]를 쓰세요.")
    if not isinstance(body.get("add"), list) or not isinstance(body.get("remove"), list):
        raise ValueError("달 태그(add)·뗄 태그(remove)는 목록이에요.")
    return jsonify(**labels.apply(labels.ref_of(body), keys, body["add"], body["remove"]))


@app.post("/api/labels/apply_where")
def labels_apply_where() -> Response:
    """[SPECIFICATION] plans/phase-4.md#labels_apply_where
    검색 요청(기간·조건·태그 거르기)에 맞는 모든 행의 키에 태그를 달고 뗀다(확인 응답으로 행 수를 먼저 묻는다)."""
    body = request.json
    ref, store = labels.ref_of(body), labels.store_of(body)
    plain = {k: v for k, v in body.items() if k not in ("sorts", "columns", "limit", "offset", "add", "remove")}
    expr = querying.runnable(plain, select=False, keep=[store["key"]], ask=plain)
    keys = run(lambda: labels.keys_of(expr, store["key"]))
    if not keys:
        raise ValueError("검색 결과에 행이 없어요.")
    if body.get("confirmed") is not True:
        response = jsonify(needs_confirm=True, blocked=False, text=f"검색 결과의 {len(keys):,}개 행에 태그를 {'달고 떼요' if body['add'] and body['remove'] else '달아요' if body['add'] else '떼요'}. 그대로 할까요?")
        response.headers["X-Needs-Confirm"] = "1"
        return response
    return jsonify(**labels.apply(ref, keys, body["add"], body["remove"]))


server.CONSTANTS["labels"] = {"column": labels.TAG_COLUMN, "quick": labels.QUICK_KEYS, "max_apply": labels.MAX_APPLY}
