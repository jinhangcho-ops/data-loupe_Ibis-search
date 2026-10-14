"""Ibis 검색 화면의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다."""
import os
from pathlib import Path

from flask import jsonify, request

import parse
import result
import server
import settings
import source
from server import KEY, app, get_con, run, to_json


@app.get("/search")
def search_page():
    return server.page("index.html")


def build(body, select=True):
    """요청의 테이블·조인·기간·조건·정렬·표시 컬럼 줄을 Ibis 식 하나로 만든다.
    select=False면 줄은 모두 검사하되 표시 컬럼은 적용하지 않는다(컬럼 목록, 차트)."""
    con, schema = get_con(), body.get("schema") or None
    tables = source.list_tables(con, schema)
    parse.check_name(body["base"], tables, "테이블")
    get_table = lambda name: source.get_table(con, name, schema)
    expr = parse.apply_joins(
        get_table(body["base"]),
        [parse.parse_join(line, tables) for line in body.get("joins", [])],
        get_table,
    )
    preds = [p for line in body.get("periods", []) for p in parse.parse_period(line, expr)]
    preds += [parse.predicate(cond, expr) for cond in body.get("conditions", [])]
    keys = [parse.parse_sort(line, expr) for line in body.get("sorts", [])]
    cols = [c for line in body.get("columns", []) for c in parse.parse_columns(line, expr)]
    return parse.apply(expr, preds, keys, cols if select else [])



@app.post("/api/columns")
def columns():
    return jsonify(columns=result.columns(build(request.json, select=False)))


@app.post("/api/values")
def values():
    """조건을 만들 때 고를 수 있게 컬럼의 값 목록을 돌려준다. 화면에서 [목록]을 누를 때만 부른다."""
    expr, column = build(request.json, select=False), request.json.get("column", "")
    found, more = run(lambda: result.get_values(expr, column))
    return jsonify(values=found, more=more)


def paged(body):
    """(검색 식, 행 수, 건너뛸 행 수)"""
    return build(body), parse.parse_limit(body.get("limit", 20)), parse.parse_offset(body.get("offset", 0))


@app.post("/api/query")
def query():
    """count를 false로 주면 전체 행 수를 세지 않고 null로 돌려준다. offset을 주면 그만큼 건너뛴 다음 행부터 준다(count는 그대로)."""
    body = request.json
    expr, limit, offset = paged(body)
    count, rows = run(lambda: (result.get_count(expr) if body.get("count", True) else None, result.get_rows(expr, limit, offset)))
    return jsonify(count=count, rows=to_json(rows))


@app.post("/api/sql")
def sql():
    """/api/query가 행을 가져올 때 실행할 SQL. 실행하지는 않는다."""
    return jsonify(sql=result.get_sql(*paged(request.json)))


@app.get("/api/searches")
def searches():
    return jsonify(searches=settings.searches())


@app.post("/api/searches")
def save_search():
    """search가 null이면 그 이름을 지운다."""
    return jsonify(searches=settings.save_search(request.json.get("name"), request.json.get("search")))


@app.post("/api/summary")
def summary():
    """컬럼별 집계. 큰 테이블에서는 오래 걸리므로 검색과 따로, 화면에서 요청할 때만 계산한다."""
    columns = request.json.get("summary_columns") or []  # 화면에서 고른 컬럼. 없으면 표시 컬럼(없으면 전체).
    expr = build(request.json, select=not columns)
    rows = run(lambda: result.get_summary(expr, columns))
    return jsonify(summary={"columns": result.SUMMARY_COLS, "rows": rows})


@app.post("/api/export")
def export():
    """조건에 맞는 전체 행을 임시 파일로 만들어 조금씩 내려보내고, 다 보내면 지운다."""
    return server.download(build(request.json), request.json.get("format", ""))


@app.post("/api/handoff")
def handoff():
    """조건에 맞는 전체 행을 창의 저장 대화상자로 고른 Parquet 파일에 저장하고, 그 파일 하나에 연결한다(전처리로 넘길 때 쓴다).
    확장자가 .parquet이 아니면 붙인다. 지금 읽고 있는 파일에는 덮어쓰지 않는다. 임시 이름으로 쓰고 끝나면 바꿔치기해 실패해도 원래 파일이 남는다."""
    if server.ask_save is None:
        raise ValueError("저장 위치를 물을 수 없습니다. 프로그램 창에서만 쓸 수 있습니다.")
    expr = build(request.json)
    chosen = server.ask_save(f"{request.json['base']}_search.parquet", ("Parquet (*.parquet)",))
    if not chosen:
        return jsonify(cancelled=True)
    path = Path(chosen if chosen.lower().endswith(".parquet") else chosen + ".parquet")
    if server.reading(path):
        raise ValueError("지금 읽고 있는 파일에는 저장할 수 없습니다. 다른 이름을 고르세요.")
    part = path.with_name(path.stem + ".part.parquet")

    def write():
        try:
            result.export(expr, part)
        except Exception:
            part.unlink(missing_ok=True)
            raise

    run(write, lambda done: part.unlink(missing_ok=True))  # 그만둔 뒤에 늦게 다 쓴 파일도 지운다.
    os.replace(part, path)
    server.switch({"kind": "folder", "path": str(path.parent), "files": [str(path)]})
    return jsonify(path=str(path), table=source.list_tables(get_con())[0])


@app.post("/api/chart")
def chart():
    """x별 집계. series는 [{"y", "agg"}]이고, split을 주면 그 컬럼의 값마다 계열을 나눈다."""
    body = request.json
    expr = build(body, select=False)
    labels, datasets, more = run(lambda: result.get_chart(
        expr, body["x"], body.get("series") or [], body.get("split") or None, body.get("order", "x")))
    return jsonify(labels=[str(v) for v in labels], datasets=datasets, more_splits=more,
                   max_groups=result.MAX_GROUPS, max_splits=result.MAX_SPLITS)

