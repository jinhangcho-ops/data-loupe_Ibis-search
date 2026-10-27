"""Data Loupe 쿼리 화면(탐색·SQL·지표·대시보드)의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다."""
import base64
import binascii
import contextlib
import functools
import os
from pathlib import Path

from flask import jsonify, request

from lazy import Later, ibis, preload
from core import held
from core import result
from core import scan_size
from core import server
from core import settings
from core import source
from core import sql_exec
from core import use_counts
from core.server import app, get_con, run


calc = Later("core.calc")
dash_store = Later("core.dash_store")
metric_file = Later("core.metric_file")
nl2sql = Later("query.nl2sql")
parse = Later("core.parse")
plan = Later("core.plan")
querying = Later("core.querying")

# 그래프 그림 저장(/api/images): 연결을 쓰지 않으므로 차례를 기다리지 않는다.
server.FREE.add("/api/images")
IMAGE_TYPES = {"data:image/png;base64,": ".png", "data:image/jpeg;base64,": ".jpg"}  # 화면이 보내는 그림(data URL 머리) → 확장자
MAX_IMAGE_BYTES = 30 * 1024 * 1024  # 그림 하나의 크기


@server.late_constants("query")
def search_constants(found):  # 계산 줄·차트·지표 파일 화면이 서버와 같은 형식·집계 목록을 쓴다(계산 모듈은 쿼리 화면을 내려줄 때 올라온다)
    found["calc"] = {"guide": calc.GUIDE, "examples": calc.EXAMPLES, "hows": calc.HOWS, "max_pivot": calc.MAX_PIVOT, "subtotals": calc.SUBTOTALS,
                     "windows": list(calc.NEW_WINDOWS), "formulas": list(calc.FORMULAS)}  # 계산 줄 형식·예시, 피벗 집계(함수 사전의 집계 전부)·소계 방식, 함수 사전의 창 함수·식 줄
    found["chart"] = {"aggs": result.chart_aggs(), "names": metric_file.AGG_NAMES}  # 탐색 차트·분석 계열의 집계(함수 사전의 집계 전부)와 보이는 이름
    found["metrics"] = {"aggs": list(metric_file.AGGS)}  # 지표 파일의 분자·분모에 쓸 수 있는 집계


PRELOAD = ("query.flow", "query.flow_opt", "query.join_keys", "query.sentences", "query.sentence_run", "query.sentence_exec", "query.nl2sql",
           "metrics.my_metrics", "metrics.metric_catalog", "metrics.metric_kernels")  # 쿼리 화면을 열면 뒤에서 미리 올리는 기능 모듈(흐름·문장·지표 사전·내 지표)


@app.get("/search")
def search_page():
    preload(*PRELOAD)
    return server.page("query/index.html")


@app.post("/api/columns")
def columns():
    """줄을 모두 검사한 식의 [컬럼, 한글 타입], 구조 컬럼 안의 필드(fields: [user.name, 타입]), 컬럼마다 원래 테이블(origins: 머리글 아래 작은 글씨)."""
    expr = querying.build(request.json, select=False)
    return jsonify(columns=result.columns(expr), fields=result.fields(expr), origins=querying.joined(request.json)[1])


def confirms(view):
    """처리 용량 확인이 필요하면(querying.Confirm) 그 확인 응답을 돌려준다(화면의 send()가 X-Needs-Confirm을 처리)."""

    @functools.wraps(view)
    def wrapper():
        try:
            return view()
        except querying.Confirm as e:
            return e.response
    return wrapper


@app.post("/api/values")
@confirms
def values():
    """조건을 만들 때 고를 수 있게 컬럼의 값 목록을 돌려준다. 화면에서 [목록]을 누를 때만 부른다."""
    expr, column = querying.runnable(request.json, select=False, ask=request.json), request.json.get("column", "")
    found, more = run(lambda: result.get_values(expr, column))
    return jsonify(values=found, more=more)


def paged(body):
    """(검색 식, 행 수, 건너뛸 행 수)"""
    return querying.build(body), parse.parse_limit(body.get("limit", 20)), parse.parse_offset(body.get("offset", 0))


@app.post("/api/scan")
def scan():
    """탐색 식을 실행하지 않고 처리할 용량을 알린다(scan_size). 표시 컬럼까지 적용한 식의 SQL로 센다."""
    con, expr = get_con(), querying.build(request.json)
    sql, dialect = server.expr_sql(con, expr)
    found = scan_size.safe(lambda: run(lambda: scan_size.estimate(con, sql, server.state["opened"], dialect)))
    return jsonify(message=scan_size.message(found), **found)


def explore_gate(body, streams=False):
    """탐색 식의 실행 전 처리 용량 확인(server.scan_gate). 확인이 필요하면 돌려줄 응답, 아니면 None.
    로컬(DuckDB·파일)은 읽는 양을 센다. 원격 DB는 이 PC로 받는 양만 센다: 검색은 기간·조건까지의 결과를 받아 두므로(이미 받아 둔 것이 있으면 안 셈) 그 식을,
    저장·내보내기(streams)는 파일로 흘려 쓰므로 exporting이 본다(연결이 못 하는 계산이라 받아 둔 사본으로 만들 때만 그 식을 센다)."""
    con = get_con()
    if not held.remote(con):
        return server.scan_gate(body, lambda: server.expr_sql(con, querying.build(body)))
    if streams and source.compiles(querying.build(body)):
        return exporting(con, body, lambda: server.expr_sql(con, querying.build(body)))
    if not body.get("fresh") and querying.search_key(body) in held.store["items"]:
        return None
    plain = not (body.get("conditions") or body.get("periods") or body.get("joins") or body.get("union"))
    known = (server.state["rows"].get((body.get("schema") or None, body.get("base"))) or (None,))[0] if plain else None  # 세어 둔 값은 (행 수, csv_gen)
    return server.scan_gate(body, lambda: server.expr_sql(con, querying.filtered(body)), known)


def sql_gate(body, streams=False):
    """SQL 탭 실행·저장의 실행 전 처리 용량 확인. 검사를 통과한 SQL에 가상 테이블을 풀어 넣은 글로 센다.
    원격 DB는 실행(받아 둠)에서만, 이미 받아 둔 결과가 있으면 안 센다. 저장(streams)은 파일로 흘려 쓰므로 exporting이 본다."""
    con = get_con()

    def make():
        return sql_exec.expand(con, sql_exec.check(body.get("sql"), sql_exec.dialect_of(con)), server.virtual_tables()).sql(dialect=sql_exec.dialect_of(con)), None
    if held.remote(con) and streams:
        return exporting(con, body, make)
    if held.remote(con) and not body.get("fresh") and querying.held_key("sql", {"sql": body.get("sql")}) in held.store["items"]:
        return None
    return server.scan_gate(body, make)


EXPORT_BIG = "큰 저장은 DB의 조회(스냅숏)를 오래 붙잡아 다른 작업에 부담이 됩니다. 되도록 복제본(읽기 전용 DB)에 연결해 저장하세요. 그래도 저장할까요?"


def exporting(con, body, make_sql):
    """원격 DB 저장(파일로 흘려 씀)의 실행 전 확인. BigQuery는 읽는 양에 요금이 나므로 처리 용량 확인(scan_gate, dry run)을 한다.
    그 밖에는 PC 메모리와 상관없어 막지 않지만, 받는 양 추정(scan_size.fetched, 메타데이터만)이 받아 두기 상한을 넘으면 복제본에서 하라고 한 번 묻는다."""
    if con.name == "bigquery":
        return server.scan_gate(body, make_sql)
    if body.get("confirmed") is True:
        return None

    def work():
        sql, dialect = make_sql()
        return scan_size.fetched(con, sql, None, dialect)
    found = scan_size.safe(lambda: run(work))
    if (found.get("rows") or 0) <= held.MAX_ROWS and (found["bytes"] or 0) <= held.MAX_BYTES:
        return None
    response = jsonify(needs_confirm=True, blocked=False, bytes=found["bytes"], text=f"PC로 약 {scan_size.size_text(found['bytes'])}를 받아 저장합니다(추정 · {found['how']}). {EXPORT_BIG}")
    response.headers["X-Needs-Confirm"] = "1"
    return response


TOO_BIG = f"결과가 커서({held.MAX_ROWS:,}행·{held.MAX_BYTES // 1024 // 1024}MB 넘음) 이 PC에 받아 두지 않고 DB에서 쪽마다 가져옵니다. 정렬이 없거나 정렬 값이 같은 행은 쪽마다 겹치거나 빠질 수 있으니 고유한 컬럼으로 정렬하세요."
LOCAL_PAGES = "정렬 값이 같은 행이 있어, 쪽을 넘길 때 그 행들이 겹치거나 빠질 수 있습니다. 고유한 컬럼(번호 등)을 정렬 끝에 더하세요."
FILE_SQL_PAGES = "파일 연결의 SQL 결과는 쪽마다 다시 계산합니다. 정렬이 없거나 정렬 값이 같은 행은 쪽마다 겹치거나 빠질 수 있으니 고유한 컬럼의 ORDER BY를 넣으세요."
SERVER_PAGES = ("결과가 커서 이 PC에 받아 두지 않고, 쿼리를 한 번만 돌려 DB의 결과 임시 테이블(BigQuery는 24시간)에서 쪽마다 읽습니다. "
                "쪽을 넘겨도 다시 조회하지 않아 요금이 다시 나지 않고, 정렬·조건을 바꾸거나 [검색 실행]을 누르면 다시 조회합니다.")


def reruns(key, body):
    """서버에 결과를 두는 DB(BigQuery)에서 이 요청이 쿼리를 다시 돌리는지(새로 받기, 또는 결과 테이블을 잊음). 쪽 넘기기라도 그때는 처리 용량을 묻는다."""
    return body.get("fresh") or key not in held.store["jobs"]


TIES = {}  # 검색(조건·계산·정렬)마다 정렬 값이 겹치는지. 쪽을 넘길 때마다 다시 세지 않게


def tied(body):
    """정렬 값이 같은 행이 있는지(querying.sort_keys의 행 수 ≠ 서로 다른 값 수)를 검색마다 한 번만 센다."""
    key = (querying.search_key(body), repr(body.get("calcs")), repr(body.get("sorts")))
    if key not in TIES:
        if len(TIES) > 64:
            TIES.clear()
        keys = querying.sort_keys(body)
        TIES[key] = run(lambda: keys.count().to_pyarrow().as_py() != keys.distinct().count().to_pyarrow().as_py())
    return TIES[key]


def db_page(con, body, expr, limit, offset, count=None):
    """DB에서 쪽 하나와 전체 행 수(body의 count가 false면 세지 않음, count를 주면 그 값)를 함께 받는다.
    행 수 세기와 쪽 받기는 같은 연결을 따로 읽을 수 있으면(DuckDB·파일) 동시에 돌린다. 건너뛰기를 SQL로 못 쓰는 DB는 행 번호로 거른다."""
    page, skip = (expr, offset) if not offset or result.can_offset(expr) else (querying.numbered(body, limit, offset), 0)
    counter = (lambda backend: result.get_count(expr, backend)) if count is None and body.get("count", True) else (lambda backend: count)
    return run(lambda: source.together(con, counter, lambda: result.get_rows(page, limit, skip)))


@app.post("/api/query")
def query():
    """count를 false로 주면 전체 행 수를 세지 않고 null로 돌려준다. offset을 주면 그만큼 건너뛴 다음 행부터 준다(count는 그대로).
    주소로 접속한 DB와 연결이 하지 못하는 계산(파일 연결의 창 함수 등)은 기간·조건까지의 결과를 한 번 받아 두고(held) 계산·정렬·쪽 넘기기를 이 PC에서 한다
    (fresh면 새로 받는다). 받아 두면 행 수는 세지 않아도 안다. held: 받아 둔 결과에서 왔는지, note: 받아 두지 못한 까닭."""
    body = request.json
    expr, limit, offset = paged(body)  # 받기 전에 줄을 모두 검사한다
    con, count, rows, note = get_con(), None, None, ""
    able = source.compiles(expr)
    pages = able and held.remote(con) and held.pager(con) is not None  # 서버에 결과를 두는 DB(BigQuery): 받아 두거나 세지 않고 처음부터 server_page 한 번
    key = querying.held_key("server", {k: v for k, v in body.items() if k not in ("limit", "offset", "count", "fresh", "confirmed")})
    if (not offset or (pages and reruns(key, body))) and (ask := explore_gate(body)):  # 새로 검색할 때만(쪽 넘기기는 묻지 않는다, 서버의 결과 테이블을 잊어 다시 돌릴 때는 묻는다)
        return ask
    counts = body.get("count", True)
    if (held.remote(con) or not able) and not pages:
        source_expr = querying.filtered(body)
        counter = (lambda: result.get_count(source_expr if counts else source_expr.limit(held.MAX_ROWS + 1)))  # 세지 않을 때도 받아 둘 수 있는지는 상한+1행까지만 센다(큰 결과를 받다 버리지 않게)
        count, rows = run(lambda: held.page(querying.search_key(body), source_expr, lambda t: querying.arrange(body, t, held.ROW), limit, offset,
                                            server.state["cancel"], counter, body.get("fresh")))
        if rows is None and not able:
            raise ValueError(querying.PC_TOO_BIG)
        note = TOO_BIG if rows is None else ""
        count = count if rows is not None or (counts and not body.get("calcs")) else None  # 받기 전에 센 수는 계산 전의 행 수(세지 않을 때는 상한까지만 센 수)
    kept = rows is not None
    if pages:
        count, rows = run(lambda: held.server_page(key, con, expr, limit, offset, body.get("fresh")))
        note = SERVER_PAGES
    elif not kept:
        count, rows = db_page(con, body, expr, limit, offset, count)
        if offset and not note and body.get("sorts") and tied(body):  # 이 PC·파일 연결은 쪽마다 다시 정렬한다: 정렬 값이 같은 행이 있을 때만 알린다(둘째 쪽부터, 검색마다 한 번 센다). 원격 DB의 큰 결과(TOO_BIG)는 그 안내 그대로, 원격에 세는 조회를 보내지 않는다
            note = LOCAL_PAGES
    if offset == 0:  # 새로 검색할 때만(쪽 넘기기는 세지 않는다)
        count_virtual([body["base"], *(body.get("union") or {}).get("tables", []), *[line.split()[0] for line in body.get("joins", []) if line.split()]])
    return server.json_reply(rows, count=count, held=kept, server=note == SERVER_PAGES, note=note)


def sent(con, body, expr, limit, offset):
    """/api/query가 DB에 보내는 조회의 (SQL, 받아 두는지). 받아 두면 기간·조건까지의 조회, 아니면 그 쪽의 조회."""
    item = held.store["items"].get(querying.search_key(body))
    if (held.remote(con) or not source.compiles(expr)) and not (item and item[0] is None) and not (held.pager(con) and source.compiles(expr)):
        return result.get_sql(querying.filtered(body)), True
    if offset and not result.can_offset(expr):
        return result.get_sql(querying.numbered(body, limit, offset)), False
    return result.get_sql(expr, limit, offset), False


@app.post("/api/sql")
def sql():
    """/api/query가 실행할 SQL. 실행하지는 않는다. 결과를 받아 두는 연결이면 받아 둘 때의 조회(정렬·쪽은 이 PC에서)를 보여 준다."""
    body = request.json
    expr, limit, offset = paged(body)
    text, kept = sent(get_con(), body, expr, limit, offset)
    return jsonify(sql=("-- 이 조회로 결과를 한 번 받아 두고, 계산·정렬·쪽 넘기기는 이 PC에서 합니다.\n" if kept else "") + text)


def plannable(con):
    """실행 계획을 볼 수 있는 연결인지 본다. 파일 연결은 SQL로 묻지 않아 계획이 없다(ValueError)."""
    if con.name == "polars":
        raise ValueError("파일 연결(Parquet·CSV 등)은 SQL로 묻지 않아 실행 계획이 없습니다. DuckDB 파일이나 DB에서 보세요.")
    return con


def explained(con, text):
    """SQL의 실행 계획(plan.explain)을 연결 스레드에서."""
    return run(lambda: plan.explain(con, text, sql_exec.dialect_of(con)))


@app.post("/api/plan")
def query_plan():
    """탐색이 DB에 보내는 조회의 실행 계획: 인덱스를 타는지, 테이블 전체를 읽는지(full_scans). EXPLAIN만 하고 조회는 돌리지 않는다."""
    body = request.json
    expr, limit, offset = paged(body)
    con = plannable(get_con())
    text = sent(con, body, expr, limit, offset)[0]
    return jsonify(sql=text, **explained(con, text))


@app.post("/api/sql/plan")
def sql_plan():
    """SQL 탭의 SQL(검사를 통과한 것)의 실행 계획. EXPLAIN만 하고 조회는 돌리지 않는다."""
    expr = sql_expr(request.json)[0]
    con = plannable(get_con())
    text = result.get_sql(expr)
    return jsonify(sql=text, **explained(con, text))


def searches_view(found):
    """저장한 검색과 많이 쓴 순의 이름 목록(order). JSON 객체는 순서를 지키지 않아 순서를 따로 준다."""
    return jsonify(searches=found, order=use_counts.order("searches", found))


@app.get("/api/searches")
def searches():
    return searches_view(settings.searches())


@app.post("/api/searches")
def save_search():
    """search가 null이면 그 이름(과 사용 횟수)을 지운다."""
    name, search = request.json.get("name"), request.json.get("search")
    found = settings.save_search(name, search)
    if search is None:
        use_counts.forget("searches", name.strip())
    return searches_view(found)


@app.post("/api/searches/use")
def use_search():
    """저장한 검색을 불러왔다고 센다(목록을 많이 쓴 순으로 보인다)."""
    name = request.json.get("name")
    if name not in settings.searches():
        raise ValueError(f"저장한 검색 '{name}'이 없습니다.")
    use_counts.use("searches", name)
    return searches_view(settings.searches())


@app.post("/api/summary")
@confirms
def summary():
    """컬럼별 집계. 큰 테이블에서는 오래 걸리므로 검색과 따로, 화면에서 요청할 때만 계산한다.
    원격 DB는 컬럼을 고르지 않으면 고유값을 세지 않는다(컬럼마다 COUNT(DISTINCT)가 무겁다). BigQuery는 읽는 양(요금)을 먼저 묻는다."""
    columns = request.json.get("summary_columns") or []  # 화면에서 고른 컬럼. 없으면 표시 컬럼(없으면 전체).
    expr, con = querying.runnable(request.json, select=not columns, ask=request.json), get_con()
    on_db = ibis.get_backend(expr) is con  # 받아 둔 사본에서 하는 요약은 runnable이 이미 물었다
    counted = lambda: expr.aggregate([expr[c].count().name(f"c{i}") for i, c in enumerate(c for c in (columns or expr.columns) if c in expr.columns)])  # 요약이 읽는 컬럼을 집계로: 받는 양이 아니라 읽는 양(요금)을 묻는다
    if con.name == "bigquery" and on_db and (ask := server.scan_gate(request.json, lambda: server.expr_sql(con, counted()))):
        return ask
    rows = run(lambda: result.get_summary(expr, columns, unique=bool(columns) or not (on_db and held.remote(con))))
    return jsonify(summary={"columns": result.SUMMARY_COLS, "rows": rows})


@app.post("/api/pivot")
@confirms
def pivot():
    """피벗(조건부 집계): 검색 결과를 행 기준 컬럼별로 묶고, 열로 펼칠 컬럼의 값마다 집계 칸을 만든다(calc.pivot). more는 행이 calc.MAX_ROWS보다 많은지."""
    expr, spec = querying.runnable(request.json, select=False, ask=request.json), request.json.get("pivot")
    values = lambda e: e.to_pyarrow()["v"].to_pylist()
    table = run(lambda: calc.pivot(expr, spec, values).to_polars())
    return server.json_reply(table.head(calc.MAX_ROWS), more=table.height > calc.MAX_ROWS)


@app.post("/api/export")
def export():
    """조건에 맞는 전체 행을 임시 파일로 만들어 조금씩 내려보내고, 다 보내면 지운다."""
    if ask := explore_gate(request.json, streams=True):
        return ask
    return server.download(querying.runnable(request.json), request.json.get("format", ""))


@app.post("/api/handoff")
def handoff():
    """조건에 맞는 전체 행을 창의 저장 대화상자로 고른 Parquet 파일에 저장하고, 그 파일 하나에 연결한다(전처리로 넘길 때 쓴다).
    확장자가 .parquet이 아니면 붙인다. 지금 읽고 있는 파일에는 덮어쓰지 않는다. 임시 이름으로 쓰고 끝나면 바꿔치기해 실패해도 원래 파일이 남는다."""
    if server.ask_save is None:
        raise ValueError("저장 위치를 물을 수 없습니다. 프로그램 창에서만 쓸 수 있습니다.")
    if ask := explore_gate(request.json, streams=True):
        return ask
    expr = querying.runnable(request.json)
    chosen = server.ask_save(f"{request.json['base']}_search.parquet", ("Parquet (*.parquet)",))
    if not chosen:
        return jsonify(cancelled=True)
    path = Path(chosen if chosen.lower().endswith(".parquet") else chosen + ".parquet")
    if server.reading(path):
        raise ValueError("지금 읽고 있는 파일에는 저장할 수 없습니다. 다른 이름을 고르세요.")
    part = path.with_name(path.stem + ".part.parquet")
    server.run_writing(lambda: result.export(expr, part), lambda: part.unlink(missing_ok=True))
    os.replace(part, path)
    server.switch({"kind": "folder", "path": str(path.parent), "files": [str(path)]})
    return jsonify(path=str(path), table=source.list_tables(get_con())[0])


def image_stem(name):
    """그래프 이름을 파일 이름으로: 쓸 수 없는 글자는 _로, 앞뒤 공백·점은 빼고 80자까지. 비거나 예약된 이름이면 바꾼다."""
    stem = server.BAD_NAME_CHARS.sub("_", name if isinstance(name, str) else "").strip(" .")[:80].strip(" .") or "chart"
    return "_" + stem if stem.split(".")[0].upper() in server.RESERVED else stem


@app.post("/api/images")
def save_images():
    """그래프 그림 여러 개({name, data: PNG·JPG data URL})를 창에서 고른 폴더에 저장한다. 같은 이름이 있으면 " (2)"처럼 번호를 붙인다(덮어쓰지 않는다).
    창 없이 돌 때(ask_folder가 None)는 window=false를 돌려주고, 화면이 하나씩 내려받는다."""
    images = request.json.get("images")
    if not isinstance(images, list) or not 1 <= len(images) <= dash_store.MAX_CARDS:
        raise ValueError(f"저장할 그림은 1개 이상 {dash_store.MAX_CARDS}개 이하입니다.")
    decoded = []
    for item in images:
        data = item.get("data") if isinstance(item, dict) else None
        head = next((h for h in IMAGE_TYPES if isinstance(data, str) and data.startswith(h)), None)
        if head is None:
            raise ValueError("PNG·JPG 그림만 저장할 수 있습니다.")
        try:
            raw = base64.b64decode(data[len(head):], validate=True)
        except binascii.Error:
            raise ValueError("그림 데이터가 올바르지 않습니다.") from None
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError(f"그림 하나는 {MAX_IMAGE_BYTES // 1024 // 1024}MB까지입니다.")
        decoded.append((image_stem(item.get("name")), IMAGE_TYPES[head], raw))
    if server.ask_folder is None:
        return jsonify(window=False)
    chosen = server.ask_folder()
    if not chosen:
        return jsonify(cancelled=True)
    folder, saved = Path(chosen), []
    for stem, ext, raw in decoded:
        for n in range(1, 1000):
            path = folder / (f"{stem}{ext}" if n == 1 else f"{stem} ({n}){ext}")
            try:
                with open(path, "xb") as f:  # x: 있는 파일은 덮어쓰지 않는다
                    f.write(raw)
                break
            except FileExistsError:
                continue
        else:
            raise ValueError(f"같은 이름의 파일이 너무 많습니다: {stem}{ext}")
        saved.append(path.name)
    return jsonify(folder=str(folder), saved=saved)


# ---------- SQL 직접 쓰기와 가상 테이블 ----------

def count_virtual(names):
    """쓴 가상 테이블을 한 번씩 센다(목록을 많이 쓴 순으로)."""
    vts = server.virtual_tables()
    with contextlib.suppress(OSError):  # 사용 횟수를 못 써도 응답은 그대로
        use_counts.use_many("virtual_tables", [f"{server.connection_key()}|{name}" for name in {n for n in names if n in vts}])


def sql_expr(body):
    """요청의 SQL을 검사하고 가상 테이블을 풀어 넣은 식과 쓴 가상 테이블 이름들."""
    con = get_con()
    vts = server.virtual_tables()
    tree = sql_exec.check(body.get("sql"), sql_exec.dialect_of(con))
    return run(lambda: sql_exec.to_expr(con, body["sql"], vts)), [n for n in sql_exec.table_names(tree) if n in vts]


@app.post("/api/sql/check")
def sql_check():
    """SQL을 실행하지 않고 검사한다: 문법(오류면 줄·칸), 바꾸는 문장·여러 문장, 이 연결에 없는 테이블(테이블·가상 테이블·WITH 이름 기준).
    deep이면 컬럼까지: 연결에서 짜 보기만 하고(결과 컬럼 알아보기) 데이터는 읽지 않는다. {ok, message, line, col}."""
    body, con = request.json, get_con()
    try:
        tree = sql_exec.check(body.get("sql"), sql_exec.dialect_of(con))
    except sql_exec.SqlSyntaxError as e:
        return jsonify(ok=False, message=str(e), line=e.line, col=e.col)
    except ValueError as e:
        return jsonify(ok=False, message=str(e))
    vts = server.virtual_tables()
    known = {t.lower() for t in server.table_names(con)} | {n.lower() for n in vts}
    missing = [n for n in dict.fromkeys(sql_exec.table_names(tree)) if n.lower() not in known]
    if missing:
        return jsonify(ok=False, message=f"이 연결에 없는 테이블입니다: {', '.join(missing)}")
    expanded = sql_exec.expand(con, tree, vts).sql(dialect=sql_exec.dialect_of(con))  # 가상 테이블을 풀어 넣은 SQL
    scan = scan_size.message(scan_size.safe(lambda: run(lambda: scan_size.estimate(con, expanded, server.state["opened"]))))
    if body.get("deep") is True:
        try:
            columns = run(lambda: sql_exec.to_expr(con, body["sql"], vts).columns)
        except Exception as e:
            text = source.mask(str(e), server.state["passwords"]).strip().splitlines()
            return jsonify(ok=False, message="연결에서 짜 보지 못했습니다: " + (text[0] if text else type(e).__name__))
        return jsonify(ok=True, message=f"이상 없음 · 결과 컬럼 {len(columns)}개: {', '.join(columns[:12])}{' …' if len(columns) > 12 else ''}", scan=scan)
    return jsonify(ok=True, message="문법 이상 없음", scan=scan)


@app.post("/api/sql/run")
def sql_run():
    """SQL을 검사해 실행하고 offset행 다음부터 rows행(기본 1,000)을 돌려준다. more는 그 뒤에 더 있는지. 읽기만 하는 SELECT 문 하나만 된다.
    주소로 접속한 DB는 결과를 한 번 받아 두고(held, fresh면 새로) 쪽 넘기기를 이 PC에서 한다. 그때는 전체 행 수(count)도 준다."""
    import polars as pl
    body = request.json
    n, offset = sql_exec.rows_limit(body.get("rows")), parse.parse_offset(body.get("offset", 0))
    expr, used = sql_expr(body)
    con, count, rows, note = get_con(), None, None, ""
    pages = held.remote(con) and held.pager(con) is not None  # 서버에 결과를 두는 DB(BigQuery): 받아 두지 않고 server_page 한 번
    if (not offset or (pages and reruns(querying.held_key("server", {"sql": body["sql"]}), body))) and (ask := sql_gate(body)):  # 쪽 넘기기는 묻지 않는다(다시 돌릴 때는 묻는다)
        return ask
    if held.remote(con) and not pages:
        key = querying.held_key("sql", {"sql": body["sql"]})
        count, rows = run(lambda: held.page(key, expr, lambda t: t.order_by(held.ROW).select(*[c for c in t.columns if c != held.ROW]), n + 1, offset, server.state["cancel"], fresh=body.get("fresh")))
        note = TOO_BIG.replace(" 정렬이 없거나 정렬 값이 같은 행은 쪽마다 겹치거나 빠질 수 있으니 고유한 컬럼으로 정렬하세요.", " SQL에 고유한 컬럼의 ORDER BY를 넣어야 쪽이 섞이지 않습니다.") if rows is None else ""
    kept = rows is not None
    if pages:
        count, rows = run(lambda: held.server_page(querying.held_key("server", {"sql": body["sql"]}), con, expr, n + 1, offset, body.get("fresh")))
        note = SERVER_PAGES
    elif not kept:
        if offset and not result.can_offset(expr):
            raise ValueError("이 DB는 SQL 결과에서 앞 행을 건너뛰지 못하고, 결과가 커서 이 PC에 받아 두지도 못했습니다. 조건을 좁히거나 [결과 저장]으로 받으세요.")
        rows = run(lambda: result.get_rows(expr, n + 1, offset))
        if con.name == "polars" and (offset or rows.height > n):  # 파일 연결의 SQL은 Polars SQL이 정렬해 동률 순서를 고정하지 못한다
            note = FILE_SQL_PAGES
    if offset == 0:
        count_virtual(used)
    shown = rows.head(n).with_columns(pl.col(pl.Decimal).cast(pl.Float64))  # SUM 등이 돌려주는 큰 소수 타입은 화면에서 숫자로(파일 저장은 원래 타입)
    return server.json_reply(shown, more=rows.height > n, limit=n, offset=offset, count=count, held=kept, server=note == SERVER_PAGES, note=note)


@app.post("/api/sql/export")
def sql_export():
    """SQL 결과 전체를 파일(csv, xlsx, parquet)로 내려보낸다."""
    expr = sql_expr(request.json)[0]
    if ask := sql_gate(request.json, streams=True):
        return ask
    return server.download(expr, request.json.get("format", ""))


def sqls_view(found):
    return jsonify(sqls=found, order=use_counts.order("sqls", found))


@app.get("/api/sqls")
def sqls():
    return sqls_view(settings.named("sqls.json"))


@app.post("/api/sqls")
def save_sql():
    """이름을 붙여 SQL을 저장한다(연결 정보 없음). sql이 null이면 지운다. 저장할 때 검사한다."""
    name, sql = request.json.get("name"), request.json.get("sql")
    if sql is not None:
        sql_exec.check(sql)
    found = settings.save_named("sqls.json", "SQL 문", sql_exec.MAX_SQL_SIZE + 100, name, None if sql is None else {"sql": sql})
    if sql is None:
        use_counts.forget("sqls", name.strip())
    return sqls_view(found)


@app.post("/api/sqls/use")
def use_sql():
    name = request.json.get("name")
    if name not in settings.named("sqls.json"):
        raise ValueError(f"저장한 SQL '{name}'이 없습니다.")
    use_counts.use("sqls", name)
    return sqls_view(settings.named("sqls.json"))


def virtual_view(vts):
    """지금 연결의 가상 테이블과 많이 쓴 순의 이름 목록."""
    key = server.connection_key()
    return jsonify(virtual=vts, order=[k.rpartition("|")[2] for k in use_counts.order("virtual_tables", [f"{key}|{n}" for n in vts])])


@app.get("/api/virtual")
def virtual():
    return virtual_view(server.virtual_tables())


@app.post("/api/virtual")
def save_virtual():
    """지금 연결의 가상 테이블을 저장한다({name, sql} 또는 {name, parquet}). 둘 다 없으면(null) 지운다. 지금 연결에서 한 행을 읽어 본다."""
    body, con = request.json, get_con()
    name, key = body.get("name"), server.connection_key()
    definition = {"sql": body["sql"]} if body.get("sql") is not None else {"parquet": body["parquet"]} if body.get("parquet") is not None else None
    verify = lambda trial: run(lambda: result.get_rows(sql_exec.virtual_table(con, name.strip(), trial), 1))
    found = sql_exec.save_virtual(key, name, definition, source.list_tables(con), verify)
    if definition is None:
        use_counts.forget("virtual_tables", f"{key}|{name.strip()}")
    server.state["rows"].clear()  # 정의가 바뀌었으면 세어 둔 행 수는 모두 버린다(가상 테이블이 다른 가상 테이블을 쓸 수 있다)
    return virtual_view(found)


@app.post("/api/chart")
@confirms
def chart():
    """x별 집계. series는 [{"y", "agg"}]이고, split을 주면 그 컬럼의 값마다 계열을 나눈다."""
    body = request.json
    expr = querying.runnable(body, select=False, ask=body)
    labels, datasets, more = run(lambda: result.get_chart(
        expr, body["x"], body.get("series") or [], body.get("split") or None, body.get("order", "x")))
    truncated = any([d.pop("truncated") for d in datasets])  # 계열마다 붙은 표시는 응답 맨 위에 한 번만(계열 모양은 그대로)
    return jsonify(labels=[str(v) for v in labels], datasets=datasets, more_splits=more, truncated=truncated,
                   max_groups=result.MAX_GROUPS, max_splits=result.MAX_SPLITS)



# ---------- 사전 설정 지표(metrics.py) ----------

def metrics_view(path, found=None, error=None):
    """지표 파일 경로, 지표 [{name, description, format, text}], 많이 쓴 순의 이름(order). 파일을 읽을 수 없으면 그 이유(error)도."""
    found = found or {}
    listed = [{"name": n, "description": m["description"], "format": m["format"], "text": metric_file.describe(m)} for n, m in found.items()]
    return jsonify(path=path, metrics=listed, order=use_counts.order("metrics", list(found)), max_run=metric_file.MAX_RUN,
                   **({"error": error} if error else {}))


@app.get("/api/metrics")
def metrics_list():
    path = metric_file.file_path()
    try:
        return metrics_view(path, metric_file.registry())
    except ValueError as e:
        return metrics_view(path, error=str(e))


@app.post("/api/metrics")
def save_metrics_file():
    """지표 파일 경로를 읽어 검사한 뒤 저장한다(빈 글자면 쓰지 않음). 다시 읽기도 이것으로 한다."""
    found = metric_file.save_file_path(request.json.get("path"))
    return metrics_view(metric_file.file_path(), found)


@app.post("/api/metrics/run")
@confirms
def metrics_run():
    """탐색의 식(기준 테이블·조인·기간·조건) 위에서 지표들(names)을 구한다. by를 주면 그 컬럼 값마다. 구한 지표는 한 번씩 센다."""
    body = request.json
    names = body.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("구할 지표를 고르세요.")
    found, labels, values, more = querying.metric_values(body, names, body.get("by"), ask=body)
    return jsonify(labels=labels, values=values, formats={n: found[n]["format"] for n in names}, more=more, max_groups=metric_file.MAX_GROUPS)


# ---------- 자연어 질문 → SQL(nl2sql.py, 뼈대) ----------

def nl2sql_context():
    """지금 연결의 NL2SQL 문맥(테이블·컬럼·명세서·캐시 추론·지표 정의, 데이터 값 없음)."""
    con = get_con()
    return con, run(lambda: nl2sql.context(con, server.connection_key(), None, server.virtual_tables()))


@app.post("/api/nl2sql/prompt")
def nl2sql_prompt():
    """LLM에 보낼 내용(시스템 프롬프트와 질문)을 보여 준다. 묻지는 않는다."""
    _, ctx = nl2sql_context()
    question = request.json.get("question") or ""
    try:
        name = nl2sql.llm.client().name
    except ValueError as e:
        name = f"설정 오류({e})"
    return jsonify(system=nl2sql.system_prompt(ctx), question=question.strip()[:nl2sql.MAX_QUESTION], llm=name,
                   tables=len(ctx["tables"]), metrics=len(ctx["metrics"]))


@app.post("/api/nl2sql/ask")
def nl2sql_ask():
    """질문을 LLM에 물어 검사한 SQL을 돌려준다. 실행하지 않는다(화면이 편집기에 넣고 사람이 [실행])."""
    con, ctx = nl2sql_context()
    known = server.table_names(con) + list(server.virtual_tables())
    return jsonify(sql=nl2sql.ask(request.json.get("question"), ctx, sql_exec.dialect_of(con), known))


def llm_view():
    """LLM env 파일 경로와 지금 어댑터 이름(키는 보이지 않는다). 파일을 읽을 수 없으면 error."""
    error = None
    try:
        name = nl2sql.llm.client().name
    except ValueError as e:
        name, error = "없음", str(e)
    return jsonify(env=nl2sql.llm.env_path(), llm=name, **({"error": error} if error else {}))


@app.get("/api/llm")
def llm_state():
    return llm_view()


@app.post("/api/llm")
def save_llm():
    """LLM env 파일 경로를 읽어 검사한 뒤 저장한다(빈 글자면 LLM을 쓰지 않음). 키는 저장하지 않는다."""
    nl2sql.llm.save_env_path(request.json.get("env"))
    return llm_view()
