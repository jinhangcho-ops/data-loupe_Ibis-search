"""코어: 화면(Flask 라우트)과 다른 앱(Slack 봇 등)이 함께 쓰는 검색·지표 계산. 요청·응답 모양(Flask, Slack)을 모른다.
검색 body는 {schema, base, union?, joins, periods, conditions, sorts, columns}(화면의 state() 모양)이다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 돈다. 화면 밖(봇)에서 부를 때는 using()으로 화면 요청과 같은 차례(turn)를 잡는다."""
import contextlib
import json

from lazy import ibis

import calc
import held
import metrics
import parse
import result
import server
import settings
import source
import use_counts
from server import get_con, run

TURN_WAIT = 60  # 화면 밖에서 차례를 기다리는 최대 초


def joined(body):
    """기준 테이블에 이어붙이기와 조인을 한 식, 그리고 컬럼마다 원래 테이블 {컬럼: 테이블}.
    기준·이어붙인 컬럼은 기준 테이블, 출처 컬럼은 "이어붙인 테이블", 조인으로 들어온 컬럼(이름이 겹쳐 바뀐 것 포함)은 그 테이블."""
    con, schema, base = get_con(), body.get("schema") or None, body["base"]
    vts = {} if schema else server.virtual_tables()  # 가상 테이블은 기본 스키마에서만 고른다
    tables = server.table_names(con, schema) + [n for n in vts]
    parse.check_name(base, tables, "테이블")
    get_table = lambda name: server.any_table(con, name, schema, vts)
    expr = parse.apply_union(get_table(base), base, parse.parse_union(body.get("union"), tables, base), get_table)
    stacked = (body.get("union") or {}).get("how", "left") in parse.STACKS
    origins = {c: "이어붙인 테이블" if body.get("union") and stacked and c == parse.SOURCE else base for c in expr.columns}
    for join in [parse.parse_join(line, tables) for line in body.get("joins", [])]:
        before = set(expr.columns)
        expr = parse.apply_joins(expr, [join], get_table)
        origins.update({c: join[0] for c in expr.columns if c not in before})
    return expr, origins


def filtered(body):
    """기준 테이블·이어붙이기·조인에 기간·조건까지 건 식(정렬·표시 컬럼 전). 결과 받아 두기(held)는 이것을 받는다."""
    expr = joined(body)[0]
    preds = [p for line in body.get("periods", []) for p in parse.parse_period(line, expr)]
    preds += [parse.predicate(cond, expr) for cond in body.get("conditions", [])]
    return expr.filter(preds) if preds else expr


def staged(body, expr):
    """기간·조건까지 건 식(또는 받아 둔 결과)에 계산 줄들(calc)을 건다."""
    return calc.apply(body.get("calcs"), expr)


def shown(body, expr):
    """(정렬 키들, 표시 컬럼들)을 expr 위에서 검사해 만든다."""
    keys = [parse.parse_sort(line, expr) for line in body.get("sorts", [])]
    return keys, [c for line in body.get("columns", []) for c in parse.parse_columns(line, expr)]


def build(body, select=True):
    """요청의 테이블·이어붙이기·조인·기간·조건·정렬·표시 컬럼을 Ibis 식 하나로 만든다.
    select=False면 줄은 모두 검사하되 표시 컬럼은 적용하지 않는다(컬럼 목록, 차트)."""
    expr = staged(body, filtered(body))
    keys, cols = shown(body, expr)
    return parse.apply(expr, [], keys, cols if select else [])


def arrange(body, table, last, select=True):
    """받아 둔 결과(held 테이블)에 요청의 계산·정렬·표시 컬럼을 건 식. 정렬의 마지막 키는 last(받은 차례)라 같은 값끼리도 순서가 고정되고, last는 보이지 않는다.
    select=False면 표시 컬럼은 적용하지 않는다(build와 같이)."""
    expr = staged(body, table)
    keys, cols = shown(body, expr)
    shown_cols = cols if cols and select else [c for c in expr.columns if c != last]  # drop은 SELECT를 한 겹 더 만들어 남길 컬럼을 고른다
    return parse.apply(expr, [], [*keys, expr[last]], shown_cols)


STAGE = ("schema", "base", "union", "joins", "periods", "conditions")  # 받아 둘 결과(기간·조건까지)를 정하는 요청 값. 계산·정렬·표시 컬럼은 받아 둔 것 위에서 한다
PC_TOO_BIG = (f"이 연결은 이 계산(창 함수·펼치기 등)을 하지 못해 이 PC에서 하려 했지만, 기간·조건까지의 결과가 {held.MAX_ROWS:,}행"
              f"(또는 {held.MAX_BYTES // 1024 // 1024}MB)을 넘어 받지 않았습니다. 기간·조건을 좁히거나 DuckDB 파일로 옮겨 쓰세요.")


def held_key(kind, what):
    """받아 둔 결과를 찾는 열쇠: 연결, 종류(search·sql), 가상 테이블 정의, 결과를 정하는 값."""
    return json.dumps([server.connection_key(), kind, server.virtual_tables(), what], sort_keys=True, ensure_ascii=False, default=str)


def search_key(body):
    return held_key("search", {k: body.get(k) for k in STAGE})


def runnable(body, select=True):
    """build의 식을 연결이 할 수 있으면 그대로, 못 하는 계산이면(파일 연결의 창 함수, 일부 DB의 펼치기 등) 기간·조건까지의 결과를
    이 PC(held의 DuckDB)에 받아 그 위에서 만든 식. 받을 수 없을 만큼 크면 PC_TOO_BIG."""
    expr = build(body, select)
    if source.compiles(expr):
        return expr
    name = run(lambda: held.ensure(search_key(body), filtered(body), server.state["cancel"]))[0]
    if name is None:
        raise ValueError(PC_TOO_BIG)
    return arrange(body, held.table(name), held.ROW, select)


NUMBER = "__ibis_n"  # numbered가 붙이는 행 번호(0부터)


def numbered(body, limit, offset):
    """offset부터 limit행을 행 번호로 거르는 식. LIMIT·OFFSET을 SQL로 못 쓰는 DB(Oracle 등)의 쪽 넘기기에 쓴다.
    번호는 요청의 정렬 순서로 매기고(정렬이 없으면 정렬할 수 있는 컬럼 전부의 순서), 표시 컬럼은 번호로 거른 뒤에 고른다."""
    expr = staged(body, filtered(body))
    keys, cols = shown(body, expr)
    expr = expr.mutate(**{NUMBER: ibis.row_number().over(ibis.window(order_by=keys or parse.orderable(expr)))})  # 정렬이 없으면 모든 컬럼 순(쪽이 섞이지 않게)
    return parse.apply(expr, [expr[NUMBER].between(offset, offset + limit - 1)], [expr[NUMBER]], cols or [c for c in expr.columns if c != NUMBER])


def metric_values(body, names, by=None):
    """검색 식 위에서 지표들을 구하고 한 번씩 센다. ({이름: 지표}, 나눈 값들 또는 None, {이름: 값들}, 더 있는지)."""
    found, expr = metrics.registry(), runnable(body, select=False)
    labels, values, more = run(lambda: metrics.compute(expr, names, found, by or None))
    for name in names:
        use_counts.use("metrics", name)
    return found, labels, values, more


def search_rows(body, limit):
    """검색 결과의 앞 limit행({columns, rows})과 전체 행 수."""
    expr = runnable(body)
    return server.to_json(run(lambda: result.get_rows(expr, limit))), run(lambda: result.get_count(expr))


def grouped(conditions):
    """저장한 검색의 조건 목록(화면의 S.conditions: 글자 또는 {line, quotes?, or?})을 서버가 받는 모양으로: or로 이은 이웃은 {"any": [...]}.
    화면의 grouped()와 같은 규칙."""
    out, now = [], []
    for i, item in enumerate(conditions):
        if isinstance(item, dict):
            now.append({"line": item.get("line", ""), "quotes": False} if item.get("quotes") is False else item.get("line", ""))
            if item.get("or") and i < len(conditions) - 1:
                continue
        else:
            now.append(item)
        out.append({"any": now} if len(now) > 1 else now[0])
        now = []
    return out


def saved_search(name):
    """저장한 검색 name을 검색 body로(행 수 limit 등 화면 값은 뺀다). 없으면 비슷한 이름을 알려 주는 ValueError."""
    found = settings.searches()
    parse.check_name(name, list(found), "저장한 검색")
    s = found[name]
    union = s.get("union") or {}
    body = {"schema": s.get("schema", ""), "base": s["base"], **({"union": union} if union.get("tables") else {}),
            **{k: s.get(k, []) for k in ("joins", "periods", "calcs", "sorts", "columns")}, "conditions": grouped(s.get("conditions", []))}
    return body


@contextlib.contextmanager
def using():
    """화면 밖에서 데이터를 쓸 때: 화면 요청과 같은 차례(turn)를 TURN_WAIT초까지 기다려 잡는다. 연결이 없으면 ValueError."""
    if not server.turn.acquire(timeout=TURN_WAIT):
        raise ValueError("프로그램이 다른 작업 중입니다. 잠시 뒤 다시 하세요.")
    try:
        if server.state["con"] is None:
            raise ValueError("프로그램에서 먼저 데이터에 연결하세요.")
        yield
    finally:
        server.turn.release()
