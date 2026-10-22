"""Ibis 쿼리 화면(탐색·SQL·지표·대시보드)의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다."""
import base64
import binascii
import os
import re
from pathlib import Path

from flask import jsonify, request
from lazy import ibis

import calc
import core
import dash_store
import held
import metrics
import nl2sql
import parse
import plan
import result
import scan_size
import server
import settings
import source
import sql_exec
import use_counts
from core import build, joined
from server import app, get_con, run, to_json

# 그래프 그림 저장(/api/images): 연결을 쓰지 않으므로 차례를 기다리지 않는다.
server.FREE.add("/api/images")
server.CONSTANTS["calc"] = {"guide": calc.GUIDE, "examples": calc.EXAMPLES, "hows": calc.HOWS, "max_pivot": calc.MAX_PIVOT}  # 계산 줄 형식·예시, 피벗 집계
IMAGE_TYPES = {"data:image/png;base64,": ".png", "data:image/jpeg;base64,": ".jpg"}  # 화면이 보내는 그림(data URL 머리) → 확장자
MAX_IMAGE_BYTES = 30 * 1024 * 1024  # 그림 하나의 크기
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}  # Windows가 파일 이름으로 쓰지 못하는 이름


@app.get("/search")
def search_page():
    return server.page("index.html")


@app.post("/api/columns")
def columns():
    """줄을 모두 검사한 식의 [컬럼, 한글 타입], 구조 컬럼 안의 필드(fields: [user.name, 타입]), 컬럼마다 원래 테이블(origins: 머리글 아래 작은 글씨)."""
    expr = build(request.json, select=False)
    return jsonify(columns=result.columns(expr), fields=result.fields(expr), origins=joined(request.json)[1])


@app.post("/api/values")
def values():
    """조건을 만들 때 고를 수 있게 컬럼의 값 목록을 돌려준다. 화면에서 [목록]을 누를 때만 부른다."""
    expr, column = core.runnable(request.json, select=False), request.json.get("column", "")
    found, more = run(lambda: result.get_values(expr, column))
    return jsonify(values=found, more=more)


def paged(body):
    """(검색 식, 행 수, 건너뛸 행 수)"""
    return build(body), parse.parse_limit(body.get("limit", 20)), parse.parse_offset(body.get("offset", 0))


@app.post("/api/scan")
def scan():
    """탐색 식을 실행하지 않고 처리할 용량을 알린다(scan_size). 표시 컬럼까지 적용한 식의 SQL로 센다."""
    con, expr = get_con(), build(request.json)
    files = con.name == "polars"  # 파일 연결은 SQL을 쓰지 않으므로 duckdb 방언으로 만들어 읽는 테이블·컬럼만 고른다
    sql = lambda: str(ibis.to_sql(expr, dialect="duckdb") if files else ibis.to_sql(expr))
    found = scan_size.safe(lambda: run(lambda: scan_size.estimate(con, sql(), server.state["opened"], "duckdb" if files else None)))
    return jsonify(message=scan_size.message(found), **found)


TOO_BIG = f"결과가 커서({held.MAX_ROWS:,}행·{held.MAX_BYTES // 1024 // 1024}MB 넘음) 이 PC에 받아 두지 않고 DB에서 쪽마다 가져옵니다. 정렬이 없거나 정렬 값이 같은 행은 쪽마다 겹치거나 빠질 수 있으니 고유한 컬럼으로 정렬하세요."
SERVER_PAGES = ("결과가 커서 이 PC에 받아 두지 않고, 쿼리를 한 번만 돌려 DB의 결과 임시 테이블(BigQuery는 24시간)에서 쪽마다 읽습니다. "
                "쪽을 넘겨도 다시 조회하지 않아 요금이 다시 나지 않고, 정렬·조건을 바꾸거나 [검색 실행]을 누르면 다시 조회합니다.")


def db_page(con, body, expr, limit, offset, count=None):
    """DB에서 쪽 하나와 전체 행 수(body의 count가 false면 세지 않음, count를 주면 그 값)를 함께 받는다.
    행 수 세기와 쪽 받기는 같은 연결을 따로 읽을 수 있으면(DuckDB·파일) 동시에 돌린다. 건너뛰기를 SQL로 못 쓰는 DB는 행 번호로 거른다."""
    page, skip = (expr, offset) if not offset or result.can_offset(expr) else (core.numbered(body, limit, offset), 0)
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
    if held.remote(con) or not able:
        source_expr = core.filtered(body)
        counter = (lambda: result.get_count(source_expr)) if body.get("count", True) else None
        count, rows = run(lambda: held.page(core.search_key(body), source_expr, lambda t: core.arrange(body, t, held.ROW), limit, offset,
                                            server.state["cancel"], counter, body.get("fresh")))
        if rows is None and not able:
            raise ValueError(core.PC_TOO_BIG)
        note = TOO_BIG if rows is None else ""
        count = count if rows is not None or not body.get("calcs") else None  # 받기 전에 센 수는 계산 전의 행 수
    kept = rows is not None
    if not kept and held.remote(con) and held.pager(con) is not None:  # 결과를 서버에 둔 채 쪽마다 읽을 수 있는 DB(BigQuery)
        key = core.held_key("server", {k: v for k, v in body.items() if k not in ("limit", "offset", "count", "fresh")})
        count, rows = run(lambda: held.server_page(key, con, expr, limit, offset, body.get("fresh")))
        note = SERVER_PAGES
    elif not kept:
        count, rows = db_page(con, body, expr, limit, offset, count)
    if offset == 0:  # 새로 검색할 때만(쪽 넘기기는 세지 않는다)
        count_virtual([body["base"], *(body.get("union") or {}).get("tables", []), *[line.split()[0] for line in body.get("joins", []) if line.split()]])
    return jsonify(count=count, rows=to_json(rows), held=kept, server=note == SERVER_PAGES, note=note)


def sent(con, body, expr, limit, offset):
    """/api/query가 DB에 보내는 조회의 (SQL, 받아 두는지). 받아 두면 기간·조건까지의 조회, 아니면 그 쪽의 조회."""
    item = held.store["items"].get(core.search_key(body))
    if (held.remote(con) or not source.compiles(expr)) and not (item and item[0] is None):
        return result.get_sql(core.filtered(body)), True
    if offset and not result.can_offset(expr):
        return result.get_sql(core.numbered(body, limit, offset)), False
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
def summary():
    """컬럼별 집계. 큰 테이블에서는 오래 걸리므로 검색과 따로, 화면에서 요청할 때만 계산한다."""
    columns = request.json.get("summary_columns") or []  # 화면에서 고른 컬럼. 없으면 표시 컬럼(없으면 전체).
    expr = core.runnable(request.json, select=not columns)
    rows = run(lambda: result.get_summary(expr, columns))
    return jsonify(summary={"columns": result.SUMMARY_COLS, "rows": rows})


@app.post("/api/pivot")
def pivot():
    """피벗(조건부 집계): 검색 결과를 행 기준 컬럼별로 묶고, 열로 펼칠 컬럼의 값마다 집계 칸을 만든다(calc.pivot). more는 행이 calc.MAX_ROWS보다 많은지."""
    expr, spec = core.runnable(request.json, select=False), request.json.get("pivot")
    values = lambda e: e.to_pyarrow()["v"].to_pylist()
    table = run(lambda: calc.pivot(expr, spec, values).to_polars())
    return jsonify(rows=to_json(table.head(calc.MAX_ROWS)), more=table.height > calc.MAX_ROWS)


@app.post("/api/export")
def export():
    """조건에 맞는 전체 행을 임시 파일로 만들어 조금씩 내려보내고, 다 보내면 지운다."""
    return server.download(core.runnable(request.json), request.json.get("format", ""))


@app.post("/api/handoff")
def handoff():
    """조건에 맞는 전체 행을 창의 저장 대화상자로 고른 Parquet 파일에 저장하고, 그 파일 하나에 연결한다(전처리로 넘길 때 쓴다).
    확장자가 .parquet이 아니면 붙인다. 지금 읽고 있는 파일에는 덮어쓰지 않는다. 임시 이름으로 쓰고 끝나면 바꿔치기해 실패해도 원래 파일이 남는다."""
    if server.ask_save is None:
        raise ValueError("저장 위치를 물을 수 없습니다. 프로그램 창에서만 쓸 수 있습니다.")
    expr = core.runnable(request.json)
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
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name if isinstance(name, str) else "").strip(" .")[:80].strip(" .") or "chart"
    return "_" + stem if stem.split(".")[0].upper() in RESERVED else stem


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
    for name in {n for n in names if n in vts}:
        use_counts.use("virtual_tables", f"{server.connection_key()}|{name}")


def sql_expr(body):
    """요청의 SQL을 검사하고 가상 테이블을 풀어 넣은 식과 쓴 가상 테이블 이름들."""
    con = get_con()
    vts = server.virtual_tables()
    tree = sql_exec.check(body.get("sql"), sql_exec.dialect_of(con))
    return run(lambda: sql_exec.to_expr(con, body["sql"], vts)), [n for n in sql_exec.table_names(tree) if n in vts]


KNOWN_TABLES = {}  # 연결마다 한 번 받은 테이블 목록 {"con": 연결, "names": [...]}. 입력하는 동안의 검사가 DB에 자꾸 묻지 않게 한다.


def known_tables(con):
    if KNOWN_TABLES.get("con") is not con:
        KNOWN_TABLES.update(con=con, names=source.list_tables(con))
    return KNOWN_TABLES["names"]


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
    known = {t.lower() for t in known_tables(con)} | {n.lower() for n in vts}
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
    if held.remote(con):
        key = core.held_key("sql", {"sql": body["sql"]})
        count, rows = run(lambda: held.page(key, expr, lambda t: t.order_by(held.ROW).select(*[c for c in t.columns if c != held.ROW]), n + 1, offset, server.state["cancel"], fresh=body.get("fresh")))
        note = TOO_BIG.replace(" 정렬이 없거나 정렬 값이 같은 행은 쪽마다 겹치거나 빠질 수 있으니 고유한 컬럼으로 정렬하세요.", " SQL에 고유한 컬럼의 ORDER BY를 넣어야 쪽이 섞이지 않습니다.") if rows is None else ""
    kept = rows is not None
    if not kept and held.remote(con) and held.pager(con) is not None:
        count, rows = run(lambda: held.server_page(core.held_key("server", {"sql": body["sql"]}), con, expr, n + 1, offset, body.get("fresh")))
        note = SERVER_PAGES
    elif not kept:
        if offset and not result.can_offset(expr):
            raise ValueError("이 DB는 SQL 결과에서 앞 행을 건너뛰지 못하고, 결과가 커서 이 PC에 받아 두지도 못했습니다. 조건을 좁히거나 [결과 저장]으로 받으세요.")
        rows = run(lambda: result.get_rows(expr, n + 1, offset))
    if offset == 0:
        count_virtual(used)
    shown = rows.head(n).with_columns(pl.col(pl.Decimal).cast(pl.Float64))  # SUM 등이 돌려주는 큰 소수 타입은 화면에서 숫자로(파일 저장은 원래 타입)
    return jsonify(rows=to_json(shown), more=rows.height > n, limit=n, offset=offset, count=count, held=kept, server=note == SERVER_PAGES, note=note)


@app.post("/api/sql/export")
def sql_export():
    """SQL 결과 전체를 파일(csv, xlsx, parquet)로 내려보낸다."""
    return server.download(sql_expr(request.json)[0], request.json.get("format", ""))


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
    server.state["rows"].pop((None, name.strip()), None)  # 정의가 바뀌었으면 세어 둔 행 수는 버린다
    return virtual_view(found)


@app.post("/api/chart")
def chart():
    """x별 집계. series는 [{"y", "agg"}]이고, split을 주면 그 컬럼의 값마다 계열을 나눈다."""
    body = request.json
    expr = core.runnable(body, select=False)
    labels, datasets, more = run(lambda: result.get_chart(
        expr, body["x"], body.get("series") or [], body.get("split") or None, body.get("order", "x")))
    return jsonify(labels=[str(v) for v in labels], datasets=datasets, more_splits=more,
                   max_groups=result.MAX_GROUPS, max_splits=result.MAX_SPLITS)



# ---------- 사전 설정 지표(metrics.py) ----------

def metrics_view(path, found=None, error=None):
    """지표 파일 경로, 지표 [{name, description, format, text}], 많이 쓴 순의 이름(order). 파일을 읽을 수 없으면 그 이유(error)도."""
    found = found or {}
    listed = [{"name": n, "description": m["description"], "format": m["format"], "text": metrics.describe(m)} for n, m in found.items()]
    return jsonify(path=path, metrics=listed, order=use_counts.order("metrics", list(found)), max_run=metrics.MAX_RUN,
                   **({"error": error} if error else {}))


@app.get("/api/metrics")
def metrics_list():
    path = metrics.file_path()
    try:
        return metrics_view(path, metrics.registry())
    except ValueError as e:
        return metrics_view(path, error=str(e))


@app.post("/api/metrics")
def save_metrics_file():
    """지표 파일 경로를 읽어 검사한 뒤 저장한다(빈 글자면 쓰지 않음). 다시 읽기도 이것으로 한다."""
    found = metrics.save_file_path(request.json.get("path"))
    return metrics_view(metrics.file_path(), found)


@app.post("/api/metrics/run")
def metrics_run():
    """탐색의 식(기준 테이블·조인·기간·조건) 위에서 지표들(names)을 구한다. by를 주면 그 컬럼 값마다. 구한 지표는 한 번씩 센다."""
    body = request.json
    names = body.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("구할 지표를 고르세요.")
    found, labels, values, more = core.metric_values(body, names, body.get("by"))
    return jsonify(labels=labels, values=values, formats={n: found[n]["format"] for n in names}, more=more, max_groups=metrics.MAX_GROUPS)


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
    known = known_tables(con) + list(server.virtual_tables())
    return jsonify(sql=nl2sql.ask(request.json.get("question"), ctx, sql_exec.dialect_of(con), known))


def llm_view(error=None):
    """LLM env 파일 경로와 지금 어댑터 이름(키는 보이지 않는다). 파일을 읽을 수 없으면 error."""
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
