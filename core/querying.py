"""코어: 화면(Flask 라우트)과 다른 앱(Slack 봇 등)이 함께 쓰는 검색·지표 계산. 요청·응답 모양(Flask, Slack)을 모른다.
검색 body는 {schema, base, union?, joins, periods, conditions, sorts, columns}(화면의 state() 모양)이다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 돈다. 화면 밖(봇)에서 부를 때는 using()으로 화면 요청과 같은 차례(turn)를 잡는다."""
import contextlib
import json

from lazy import ibis

from core import calc
from core import held
from core import metric_file
from core import parse
from core import result
from core import server
from core import settings
from core import source
from core import sql_funcs
from core import use_counts
from core.server import get_con, run

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


def search_key(body, keep=None):
    """검색의 열쇠. keep(받아 둘 컬럼들)이 있으면 열쇠에 넣는다(같은 조건이어도 컬럼이 다르면 다른 사본)."""
    return held_key("search", {**{k: body.get(k) for k in STAGE}, **({"keep": sorted(keep)} if keep else {})})


def slim(expr, keep):
    """expr를 keep에 든 컬럼(식에 있는 것만, 식의 컬럼 순서대로)으로 줄인다. 이미 그 컬럼뿐이거나 keep이 비면 그대로. 집계가 읽을 컬럼만 SQL에 나가게 하고 PC 사본도 그 컬럼만 받게 한다."""
    cols = [c for c in expr.columns if c in set(keep or ())]
    return expr.select(*cols) if cols and len(cols) < len(expr.columns) else expr


def agg_unsupported(body, expr, extra=()):
    """요청의 계열(series)·피벗(pivot)과 extra([(집계, 컬럼, 옵션)])가 고른 집계를 연결이 못 하면(같은 결과의 대체 식도 안 되면) True. 쉬운 집계(개수·합계·평균·최소·최대)는 따져 보지 않는다."""
    specs = [(s.get("agg"), s.get("y"), s.get("opts")) for s in body.get("series") or [] if isinstance(s, dict)] + list(extra)
    pivot = body.get("pivot")
    if isinstance(pivot, dict) and pivot.get("how"):
        specs.append((pivot["how"], pivot.get("value") or None, pivot.get("opts")))
    for agg, y, opts in specs:
        if agg in sql_funcs.MERGE or not isinstance(agg, str):
            continue
        try:
            made = sql_funcs.aggregate(agg, expr, y or None, None, opts if isinstance(opts, dict) else None)
        except ValueError:
            continue  # 쓸 수 없는 인자는 실행할 때 이유와 함께 알린다
        if not source.compiles(expr.aggregate(v=made)):
            return True
    return False


def runnable(body, select=True, keep=None, extra=(), fresh=False):
    """build의 식을 연결이 할 수 있으면 그대로, 못 하는 계산이면(파일 연결의 창 함수, 일부 DB의 펼치기 등) 기간·조건까지의 결과를
    이 PC(held의 DuckDB)에 받아 그 위에서 만든 식(요청의 계열·피벗이 고른 집계를 연결이 못 해도 같다: 중앙값 등). 받을 수 없을 만큼 크면 PC_TOO_BIG.
    keep(쓸 컬럼들)을 주면 식을 그 컬럼으로 줄이고, PC에 받는 사본도 조건을 건 뒤 그 컬럼만 받는다(테이블 전체를 받지 않는다). 계산 줄(calcs)이 있으면 줄이지 않는다.
    fresh면 이미 받아 둔 사본을 쓰지 않고 새로 받는다(원본 파일·테이블이 바뀐 뒤에도 옛 사본으로 계산하지 않게)."""
    keep = None if body.get("calcs") else keep
    expr = slim(build(body, select), keep) if keep else build(body, select)
    if source.compiles(expr) and not agg_unsupported(body, expr, extra):
        return expr
    name = run(lambda: held.ensure(search_key(body, keep), slim(filtered(body), keep) if keep else filtered(body), server.state["cancel"], fresh=fresh))[0]
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
    found = metric_file.registry()
    parts = [p for n in names if n in found for p in (found[n]["numerator"], found[n]["denominator"]) if p]
    expr = runnable(body, select=False, extra=[(p["agg"], p["column"], p.get("opts")) for p in parts])  # 연결이 못 하는 집계(중앙값 등)면 이 PC 사본에서
    labels, values, more = run(lambda: metric_file.compute(expr, names, found, by or None))
    for name in names:
        use_counts.use("metrics", name)
    return found, labels, values, more


def search_rows(body, limit):
    """검색 결과의 앞 limit행({columns, rows})과 전체 행 수. limit+1행을 받아 더 없으면 그 수가 전체이고, 더 있을 때만 COUNT를 보낸다."""
    expr = runnable(body)
    rows = run(lambda: result.get_rows(expr, limit + 1))
    count = rows.height if rows.height <= limit else run(lambda: result.get_count(expr))
    return server.to_json(rows.head(limit)), count


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
def using(fresh=False):
    """화면 밖에서 데이터를 쓸 때: 화면 요청과 같은 차례(turn)를 TURN_WAIT초까지 기다려 잡는다. 연결이 없으면 ValueError.
    fresh면 잡은 뒤 PC에 받아 둔 사본을 모두 지운다(최신화·Slack 질의가 시작할 때 한 번: 그 안의 카드·질의는 사본을 새로 받아 나눠 쓴다)."""
    if not server.turn.acquire(timeout=TURN_WAIT):
        raise ValueError("프로그램이 다른 작업 중입니다. 잠시 뒤 다시 하세요.")
    try:
        if server.state["con"] is None:
            raise ValueError("프로그램에서 먼저 데이터에 연결하세요.")
        if fresh:
            run(held.forget_all)
        yield
    finally:
        server.turn.release()
