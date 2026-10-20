"""코어: 화면(Flask 라우트)과 다른 앱(Slack 봇 등)이 함께 쓰는 검색·지표 계산. 요청·응답 모양(Flask, Slack)을 모른다.
검색 body는 {schema, base, union?, joins, periods, conditions, sorts, columns}(화면의 state() 모양)이다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 돈다. 화면 밖(봇)에서 부를 때는 using()으로 화면 요청과 같은 차례(turn)를 잡는다."""
import contextlib

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
    tables = source.list_tables(con, schema) + [n for n in vts]
    parse.check_name(base, tables, "테이블")
    get_table = lambda name: server.any_table(con, name, schema, vts)
    expr = parse.apply_union(get_table(base), base, parse.parse_union(body.get("union"), tables, base), get_table)
    origins = {c: "이어붙인 테이블" if body.get("union") and c == parse.SOURCE else base for c in expr.columns}
    for join in [parse.parse_join(line, tables) for line in body.get("joins", [])]:
        before = set(expr.columns)
        expr = parse.apply_joins(expr, [join], get_table)
        origins.update({c: join[0] for c in expr.columns if c not in before})
    return expr, origins


def build(body, select=True):
    """요청의 테이블·이어붙이기·조인·기간·조건·정렬·표시 컬럼을 Ibis 식 하나로 만든다.
    select=False면 줄은 모두 검사하되 표시 컬럼은 적용하지 않는다(컬럼 목록, 차트)."""
    expr = joined(body)[0]
    preds = [p for line in body.get("periods", []) for p in parse.parse_period(line, expr)]
    preds += [parse.predicate(cond, expr) for cond in body.get("conditions", [])]
    keys = [parse.parse_sort(line, expr) for line in body.get("sorts", [])]
    cols = [c for line in body.get("columns", []) for c in parse.parse_columns(line, expr)]
    return parse.apply(expr, preds, keys, cols if select else [])


def metric_values(body, names, by=None):
    """검색 식 위에서 지표들을 구하고 한 번씩 센다. ({이름: 지표}, 나눈 값들 또는 None, {이름: 값들}, 더 있는지)."""
    found, expr = metrics.registry(), build(body, select=False)
    labels, values, more = run(lambda: metrics.compute(expr, names, found, by or None))
    for name in names:
        use_counts.use("metrics", name)
    return found, labels, values, more


def search_rows(body, limit):
    """검색 결과의 앞 limit행({columns, rows})과 전체 행 수."""
    expr = build(body)
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
            **{k: s.get(k, []) for k in ("joins", "periods", "sorts", "columns")}, "conditions": grouped(s.get("conditions", []))}
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
