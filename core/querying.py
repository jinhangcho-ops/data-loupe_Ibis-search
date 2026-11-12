"""코어: 화면(Flask 라우트)과 다른 앱(Slack 봇 등)이 함께 쓰는 검색·지표 계산. 요청·응답 모양(Flask, Slack)을 모른다.
검색 body는 {schema, base, union?, joins, periods, conditions, sorts, columns}(화면의 state() 모양)이다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 돈다. 화면 밖(봇)에서 부를 때는 using()으로 화면 요청과 같은 차례(turn)를 잡는다."""
import contextlib
import json
from collections.abc import Callable

from lazy import ibis

from core import calc
from core import crosstab
from core import handoff
from core import held
from core import labels
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
MAX_TAB_DEPTH = 3  # @탭 안의 @탭을 푸는 깊이
RESOLVING = []  # 지금 풀고 있는 @이름들(자기 참조·순환·깊이를 본다). 요청은 차례(turn)로 하나씩이다


def tab_table(name):
    """@이름(쿼리 탭·전처리로 넘긴 검색)의 식: 등록부(core/handoff.py)의 검색 요청을 build한 것. 자기 참조·순환이나 MAX_TAB_DEPTH를 넘으면 ValueError."""
    found = handoff.get(name)
    if found is None:
        raise ValueError(f"'{name}' 탭이 없어요. 쿼리 화면에서 그 탭을 다시 여세요.")
    if name in RESOLVING:
        raise ValueError(f"탭이 자기 자신을 참조해요({' → '.join([*RESOLVING, name])}).")
    if len(RESOLVING) >= MAX_TAB_DEPTH:
        raise ValueError(f"탭 안의 탭은 {MAX_TAB_DEPTH}단계까지예요({' → '.join([*RESOLVING, name])}).")
    RESOLVING.append(name)
    try:
        expr = build(found["body"])
    finally:
        RESOLVING.pop()
    if not source.compiles(expr):  # 받아 둔 사본으로 대신 계산하는 길(runnable)은 다른 식 안에 넣을 수 없다
        raise ValueError(f"이 연결은 탭 '{name}'의 계산(창 함수 등)을 못 해서 다른 탭에서 쓸 수 없어요. 그 계산 줄을 빼거나 DuckDB 파일로 옮겨 쓰세요.")
    return expr


def table_of(name, schema=None, vts=None):
    """기준·조인·행 합치기·그룹집계 대상 자리의 이름을 식으로: @이름은 등록한 검색의 식(tab_table), 아니면 연결의 테이블(기본 스키마면 가상 테이블도)."""
    if isinstance(name, str) and name.startswith(handoff.PREFIX):
        return tab_table(name)
    con = get_con()
    vts = ({} if schema else server.virtual_tables()) if vts is None else vts
    parse.check_name(name, server.table_names(con, schema) + list(vts), "테이블")
    return server.any_table(con, name, schema, vts)


def used_tables(body):
    """검색이 테이블 자리에 쓰는 이름들(차례대로): 기준, 이어붙이기 테이블, 조인 줄의 첫 낱말. 탭 사용 검사(dash_store.uses_tab·release)·가상 테이블 세기가 같이 쓴다."""
    union = body.get("union") if isinstance(body.get("union"), dict) else {}
    return [body.get("base"), *(union.get("tables") or []), *[line.split()[0] for line in body.get("joins") or [] if isinstance(line, str) and line.split()]]


def tab_bodies(body, depth=0):
    """요청 전체(기준·행 합치기·조인뿐 아니라 계산 줄의 그룹집계 테이블 등)가 쓰는 @이름들의 검색 요청 {이름: body}(그 안에서 쓰는 것까지 MAX_TAB_DEPTH단계).
    받아 둔 결과·세어 둔 행 수의 열쇠에 넣어 탭이 바뀌면 새로 받는다.
    shortcut: 글에 이름이 들어 있으면 쓴 것으로 본다(@a와 @ab 둘 다 잡혀 열쇠만 조금 더 넓어진다)."""
    text = json.dumps(body, ensure_ascii=False, default=str)
    found = {n: handoff.get(n)["body"] for n in handoff.names() if n in text}
    for inner in list(found.values()) if depth < MAX_TAB_DEPTH else []:
        found.update(tab_bodies(inner, depth + 1))
    return found


def joined(body):
    """기준 테이블에 이어붙이기와 조인을 한 식, 그리고 컬럼마다 원래 테이블 {컬럼: 테이블}. 테이블 자리의 @이름은 그 탭의 가공한 결과(tab_table).
    기준·이어붙인 컬럼은 기준 테이블, 출처 컬럼은 "이어붙인 테이블", 조인으로 들어온 컬럼(이름이 겹쳐 바뀐 것 포함)은 그 테이블."""
    con, schema, base = get_con(), body.get("schema") or None, body["base"]
    vts = {} if schema else server.virtual_tables()  # 가상 테이블은 기본 스키마에서만 고른다
    tables = server.table_names(con, schema) + [n for n in vts] + handoff.names()
    parse.check_name(base, tables, "테이블")
    get_table = lambda name: table_of(name, schema, vts)
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


def staged(body, expr, ask=None):
    """기간·조건까지 건 식(또는 받아 둔 결과)에 계산 줄들(calc)을 건다. 통계 단계는 pinned가 연결 스레드에서 미리 구한 값으로(ask는 처리 용량 확인용 요청 본문)."""
    expr = labels.attached(body, expr)  # 태그를 켠 요청(labels)이면 계산 줄 앞에서 라벨을 붙인다
    return calc.apply(body.get("calcs"), expr, body.get("schema"), pinned(body, expr, ask))


def shown(body, expr):
    """(정렬 키들, 표시 컬럼들)을 expr 위에서 검사해 만든다."""
    keys = [parse.parse_sort(line, expr) for line in body.get("sorts", [])]
    return keys, [c for line in body.get("columns", []) for c in parse.parse_columns(line, expr)]


def sort_keys(body, at):
    """정렬 순서로 at번째와 그다음(0부터) 행의 정렬 키 값만 고른 표(__ibis_k0, __ibis_k1 …, 두 행까지). 쪽 경계의 두 행 값이 같으면 쪽마다 다시 정렬할 때 그 행들의 차례가 바뀌어 쪽이 겹치거나 빠질 수 있다."""
    expr = staged(body, filtered(body))
    keys = shown(body, expr)[0]
    named = {f"__ibis_k{i}": k.op().arg.to_expr() for i, k in enumerate(keys)}
    return expr.mutate(**named).order_by(keys).limit(2, offset=max(at, 0)).select(*named)


def build(body, select=True, ask=None):
    """요청의 테이블·이어붙이기·조인·기간·조건·정렬·표시 컬럼을 Ibis 식 하나로 만든다.
    select=False면 줄은 모두 검사하되 표시 컬럼은 적용하지 않는다(컬럼 목록, 차트)."""
    expr = staged(body, filtered(body), ask)
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
              f"(또는 {held.MAX_BYTES // 1024 // 1024}MB)을 넘어 받지 않았어요. 기간·조건을 좁히거나 DuckDB 파일로 옮겨 쓰세요.")


def held_key(kind, what):
    """받아 둔 결과를 찾는 열쇠: 연결, 종류(search·sql), 가상 테이블 정의, 결과를 정하는 값."""
    return json.dumps([server.connection_key(), kind, server.virtual_tables(), what], sort_keys=True, ensure_ascii=False, default=str)


def search_key(body, keep=None):
    """검색의 열쇠. keep(받아 둘 컬럼들)이 있으면 열쇠에 넣는다(같은 조건이어도 컬럼이 다르면 다른 사본)."""
    tabs = tab_bodies(body)
    return held_key("search", {**{k: body.get(k) for k in STAGE}, **({"keep": sorted(keep)} if keep else {}), **({"tabs": tabs} if tabs else {})})


PINS = {}  # 쿼리 계산 줄의 통계 단계에 미리 구한 값 {stats_key: pin_value 결과}. 넘치면 오래된 것부터 버린다
MAX_PINS = 64  # PINS에 두는 최대 개수
FRESHED = {}  # [새로 받기](fresh)로 다시 구한 통계 {stats_key: 그때의 server.state["turn_no"]}. 같은 차례(요청) 안에서는 열쇠마다 한 번만 다시 구한다


def stats_gate(ask: dict | None, make_sql: Callable[[], tuple[str, str | None]]) -> None:
    """[SPECIFICATION] plans/phase-1.md#stats_gate
    읽는 양을 미리 잴 수 있는 연결에서 통계 조회 전 처리 용량을 확인해, ask가 있으면 Confirm, 없으면 기준을 넘거나 알 수 없을 때 ValueError를 낸다."""
    from core import scan_size  # 늦은 import
    con = get_con()
    if not scan_size.dry_runs(con):  # 읽는 양을 미리 잴 수 없는 연결: 통계는 집계 1행이라 받는 양이 작다
        return
    if ask is not None:
        if response := server.scan_gate(ask, make_sql):
            raise Confirm(response)
        return
    sql, dialect = make_sql()
    found = run(lambda: scan_size.limit_check(con, sql, server.state["opened"], dialect))
    if found["over"] or found["unknown"]:
        raise ValueError(f"표 전체 통계를 구하는 조회가 커서(또는 크기를 알 수 없어서) 확인 없이는 구하지 않아요. {found['text']} 프로그램의 쿼리 화면에서 실행하세요.")


def stats_key(body: dict, upto: int) -> str:
    """[SPECIFICATION] plans/phase-1.md#stats_key
    계산 줄 upto번째 통계 단계의 미리 구한 값을 찾는 열쇠(연결·기간·조건·쓰는 @탭·그 줄까지의 계산 줄·CSV 세대)."""
    from core import prep_ops  # 늦은 import
    from prep import prep_diag  # prep 패키지는 쓸 때만 올라온다
    opts = prep_diag.options(settings.read(prep_diag.OPTIONS_FILE))
    return held_key("stats", {**{k: body.get(k) for k in STAGE}, "tabs": tab_bodies(body),
                              "calcs": (body.get("calcs") or [])[:upto + 1], "csv": dict(getattr(get_con(), "csv_gen", {})),
                              "labels": labels.revision(body), "diag": {name: opts[name] for name in prep_ops.FLAG_K_OPTION.values()}})


def pinned(body: dict, expr: "ibis.expr.types.Table", ask: dict | None = None) -> dict[int, object]:
    """[SPECIFICATION] plans/phase-1.md#pinned
    계산 줄의 통계 단계마다 연결 스레드에서 통계를 구해(캐시) {줄 번호: 미리 구한 값}을 돌려준다."""
    from core import prep_ops  # 늦은 import
    lines, schema, pins, renew = body.get("calcs") or [], body.get("schema"), {}, body.get("fresh") is True
    for i, line in enumerate(lines):
        if not line.strip().startswith("전처리 "):
            continue  # 앞 줄까지의 식(cur)을 만들 필요가 없는 줄
        cur = calc.apply(lines[:i], expr, schema, pins)  # 이 줄 앞까지(앞의 통계 단계도 미리 구한 값으로)
        step = calc.stat_step(line, cur)
        if step is None:
            continue
        op, column, value = step
        key = stats_key(body, i)
        if renew and FRESHED.get(key) != server.state["turn_no"]:  # 이 차례에서 이 열쇠를 아직 다시 구하지 않았으면
            PINS.pop(key, None)
        if key not in PINS:
            stats_gate(ask, lambda: server.expr_sql(get_con(), cur.aggregate(n=cur[column].count())))
            found = run(lambda: prep_ops.pin_value(cur, column, op, value))
            for cache in (PINS, FRESHED):
                if len(cache) >= MAX_PINS:
                    cache.pop(next(iter(cache)))
            PINS[key] = found
            if renew:
                FRESHED[key] = server.state["turn_no"]
        pins[i] = PINS[key]
    return pins


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
    if isinstance(pivot, dict):
        specs += [(v.get("how"), v.get("value") or None, v.get("opts")) for v in pivot.get("values") or [] if isinstance(v, dict)]
        if pivot.get("preset"):
            with contextlib.suppress(ValueError):  # 쓸 수 없는 프리셋은 실행할 때 이유와 함께 알린다
                specs += [(m["how"], m["value"], m["opts"]) for m in crosstab.expand(pivot["preset"], expr)[0]]
    extra_kinds = body.get("summary_extra") or []
    if "gap" in extra_kinds or "quantiles" in extra_kinds:
        numeric = lambda c: c in expr.columns and expr[c].type().is_numeric()
        first = next((c for c in [*(body.get("summary_columns") or []), *expr.columns] if numeric(c)), None)
        if first:
            if "gap" in extra_kinds:
                specs.append(("median", first, None))
            if "quantiles" in extra_kinds:
                specs += [("quantile", first, {"p": p}) for p in crosstab.check_ps(body.get("summary_ps"))]
    for agg, y, opts in specs:
        if agg in sql_funcs.MERGE or not isinstance(agg, str):
            continue
        try:
            made = sql_funcs.aggregate_built(agg, expr, y or None, None, opts if isinstance(opts, dict) else None, True)  # 되는지만 본다: 대체 식의 평균 조회는 실제 식을 만들 때 한 번만
        except ValueError:
            continue  # 쓸 수 없는 인자는 실행할 때 이유와 함께 알린다
        if not source.compiles(expr.aggregate(v=made)):
            return True
    return False


class Confirm(Exception):
    """원격 DB에서 사본을 받기 전에 처리 용량 확인이 필요함. response는 화면에 돌려줄 확인 응답(X-Needs-Confirm)."""

    def __init__(self, response):
        super().__init__("처리 용량 확인이 필요해요.")
        self.response = response


def runnable(body, select=True, keep=None, extra=(), fresh=False, ask=None):
    """build의 식을 연결이 할 수 있으면 그대로, 못 하는 계산이면(파일 연결의 창 함수, 일부 DB의 펼치기 등) 기간·조건까지의 결과를
    이 PC(held의 DuckDB)에 받아 그 위에서 만든 식(요청의 계열·피벗이 고른 집계를 연결이 못 해도 같다: 중앙값 등). 받을 수 없을 만큼 크면 PC_TOO_BIG.
    keep(쓸 컬럼들)을 주면 식을 그 컬럼으로 줄이고, PC에 받는 사본도 조건을 건 뒤 그 컬럼만 받는다(테이블 전체를 받지 않는다). 계산 줄(calcs)이 있으면 줄이지 않는다.
    fresh면 이미 받아 둔 사본을 쓰지 않고 새로 받는다(원본 파일·테이블이 바뀐 뒤에도 옛 사본으로 계산하지 않게).
    ask(요청 본문)를 주면 원격 DB에서 사본을 새로 받기 전에 처리 용량 확인(server.scan_gate)을 하고, 확인이 필요하면 Confirm을 낸다.
    읽는 양을 미리 잴 수 있는 연결(BigQuery 등)은 DB 안에서 집계해도 읽는 양에 요금이 나므로 ask가 있으면 식이 읽는 컬럼을 세는 집계로 dry run 바이트를 먼저 묻는다(/api/summary와 같은 방식)."""
    from core import scan_size  # 늦은 import

    keep = None if body.get("calcs") else keep
    keep = labels.keep_key(body, keep)
    expr = slim(build(body, select, ask), keep) if keep else build(body, select, ask)
    con = get_con() if ask is not None else None
    if source.compiles(expr) and not agg_unsupported(body, expr, extra) and not labels.needs_pc(body):
        if con is not None and scan_size.dry_runs(con) and (response := server.scan_gate(ask, lambda: server.expr_sql(con, expr.aggregate([expr[c].count().name(f"c{i}") for i, c in enumerate(expr.columns)])))):
            raise Confirm(response)
        return expr
    if ask is not None and held.remote(con) and (fresh or search_key(body, keep) not in held.store["items"]):
        if response := server.scan_gate(ask, lambda: server.expr_sql(con, slim(filtered(body), keep) if keep else filtered(body))):
            raise Confirm(response)
    # held.table도 DuckDB에 묻으므로(DESCRIBE) 연결 스레드에서: 요청 스레드에서 부르면 다른 사본 받기와 같은 연결을 겹쳐 써 오류·멈춤
    table = run(lambda: (name := held.ensure(search_key(body, keep), slim(filtered(body), keep) if keep else filtered(body), server.state["cancel"], fresh=fresh)[0]) and held.table(name))
    if table is None:
        raise ValueError(labels.PC_TOO_BIG if labels.needs_pc(body) else PC_TOO_BIG)
    return arrange(body, table, held.ROW, select)


NUMBER = "__ibis_n"  # numbered가 붙이는 행 번호(0부터)


def numbered(body, limit, offset):
    """offset부터 limit행을 행 번호로 거르는 식. LIMIT·OFFSET을 SQL로 못 쓰는 DB(Oracle 등)의 쪽 넘기기에 쓴다.
    번호는 요청의 정렬 순서로 매기고(정렬이 없으면 정렬할 수 있는 컬럼 전부의 순서), 표시 컬럼은 번호로 거른 뒤에 고른다."""
    expr = staged(body, filtered(body))
    keys, cols = shown(body, expr)
    expr = expr.mutate(**{NUMBER: ibis.row_number().over(ibis.window(order_by=keys or parse.orderable(expr)))})  # 정렬이 없으면 모든 컬럼 순(쪽이 섞이지 않게)
    return parse.apply(expr, [expr[NUMBER].between(offset, offset + limit - 1)], [expr[NUMBER]], cols or [c for c in expr.columns if c != NUMBER])


def metric_values(body, names, by=None, ask=None):
    """검색 식 위에서 지표들을 구하고 한 번씩 센다. ({이름: 지표}, 나눈 값들 또는 None, {이름: 값들}, 더 있는지)."""
    found = metric_file.registry()
    parts = [p for n in names if n in found for p in (found[n]["numerator"], found[n]["denominator"]) if p]
    expr = runnable(body, select=False, extra=[(p["agg"], p["column"], p.get("opts")) for p in parts], ask=ask)  # 연결이 못 하는 집계(중앙값 등)면 이 PC 사본에서
    labels, values, more = run(lambda: metric_file.compute(expr, names, found, by or None))
    with contextlib.suppress(OSError):  # 사용 횟수를 못 써도 구한 값은 돌려준다
        use_counts.use_many("metrics", names)
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
def using(fresh=False, renewed=None):
    """화면 밖에서 데이터를 쓸 때: 화면 요청과 같은 차례(turn)를 TURN_WAIT초까지 기다려 잡는다. 연결이 없으면 ValueError.
    fresh면 이 안에서 쓰는 PC 사본을 처음 쓸 때 새로 받는다(Slack 질의: 안의 질의는 새로 받은 사본을 나눠 쓴다). renewed(열쇠 집합)를 주면
    여러 번의 using이 그 집합을 나눠 써 사본을 작업 하나에 한 번만 새로 받는다(새로고침: 카드마다 차례를 다시 잡는다). 쓰지 않는 사본은 건드리지 않는다."""
    if not server.turn.acquire(timeout=TURN_WAIT):
        raise ValueError("프로그램이 다른 작업 중이에요. 잠시 뒤 다시 하세요.")
    server.state["turn_no"] += 1  # 화면 밖 작업도 한 차례
    try:
        if server.state["con"] is None:
            raise ValueError("프로그램에서 먼저 데이터에 연결하세요.")
        with held.renewing(set() if fresh else renewed):
            yield
    finally:
        server.turn.release()
