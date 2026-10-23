"""흐름 최적화 제안(6-2): 결과가 집계(지표·집계 단계)인 흐름에서만, 같은 결과를 더 적게 읽고 계산하는 흐름을 제안한다. 제안만 하고 바꾸지는 않는다(적용은 apply를 따로 부른다).
규칙: 1 조건 앞으로(한 테이블 컬럼만 쓰는 조건·기간을 그 테이블 쪽으로: inner는 양쪽, left는 왼쪽·right는 오른쪽(보존되는 쪽)만, semi·anti는 왼쪽, outer·asof는 내리지 않음)
2 조인 빼기(left join의 오른쪽 컬럼을 아래에서 안 쓰고 오른쪽 키가 전체로 확인해 고유일 때만) 3 먼저 집계(차원 테이블 키가 고유하고 집계가 합계·개수·최소·최대·평균일 때 사실 테이블을 키로 먼저 집계한 뒤 조인, 평균은 합의 합 ÷ 개수의 합, 고유 개수·중앙값 등은 안 함)
4 쓰는 컬럼만(테이블 단계에 columns) 5 부풂 경고(양쪽 키가 모두 고유하지 않으면 빨간 경고, 오른쪽만 겹치면 노란 경고; 흐름은 바꾸지 않음).
같음 확인: 규칙을 하나씩 적용할 때마다 원래 흐름과 새 흐름의 집계 결과를 같은 연결에서 실행해 키 정렬 뒤 비교한다(실수는 상대 1e-9). 다르면 그 규칙을 빼고 다음으로 간다.
원격 DB(held.remote)는 고유 확인·같음 확인이 전체를 읽는 조회라 사용자 확인(scan_ok)이 없으면 보내지 않는다: 구조상 같은 규칙 1·4만 '확인 안 됨'으로 제안하고 2·3은 이유와 함께 건너뛴다.
읽는 양은 scan_size.estimate(메타데이터만)로 어림해 '약'으로 보인다. 데이터를 읽는 함수는 server.run으로 연결 스레드에서 부른다."""
import math

from query import flow
from query import join_keys
from core import scan_size
from core import settings
from core import sql_funcs
from query.flow import AFTER_AGG, by_id, inputs, outputs, params

RULES = {1: ("push_filters", "조건을 테이블 쪽으로 내리기"), 2: ("drop_join", "쓰지 않는 조인 빼기"), 3: ("pre_aggregate", "조인 전에 먼저 집계하기"), 4: ("prune_columns", "쓰는 컬럼만 읽기")}
PUSH = {"inner": ("left", "right"), "left": ("left",), "right": ("right",), "semi": ("left",), "anti": ("left",)}  # 조인 종류별로 조건을 내려도 되는 쪽(보존되는 쪽)
DECOMPOSABLE = ("count", "sum", "min", "max", "mean")  # 나눠 집계했다가 다시 합칠 수 있는 집계(mean은 합과 개수로)
REL_TOL, ABS_TOL = 1e-9, 1e-12
REMOTE_NOTE = "원격 DB라 고유 여부·결과 비교는 전체를 읽는 조회라 보내지 않았어요. 확인하고 실행하면 검증해 제안해요."
NOT_AGG = "집계(지표·집계 단계)가 결과인 흐름에서만 최적화를 제안해요."


# ---------- 흐름 고치기 ----------

def new_id(graph, stem="o"):
    """겹치지 않는 새 단계 id."""
    used, n = {x["id"] for x in graph["nodes"]}, 1
    while f"{stem}{n}" in used:
        n += 1
    return f"{stem}{n}"


def edge_at(graph, a, b):
    """이음 [a, b]의 edges 안 자리(없으면 ValueError)."""
    for i, e in enumerate(graph["edges"]):
        if list(e) == [a, b]:
            return i
    raise ValueError(f"이음 {a}→{b}가 없어요.")


def splice_out(graph, nid):
    """한 입력·한 출력 단계를 빼고 앞뒤를 잇는다(이음의 자리는 지킨다). 새 흐름."""
    g = flow.duplicate(graph)
    (before,), (after,) = inputs(g, nid), outputs(g, nid)
    at = edge_at(g, nid, after)
    g["edges"][at] = [before, after]
    g["edges"].remove([before, nid])
    g["nodes"] = [n for n in g["nodes"] if n["id"] != nid]
    return g


def splice_in(graph, node, before, after):
    """이음 before→after 사이에 단계 node를 끼운다(이음의 자리는 지킨다). 새 흐름."""
    g = flow.duplicate(graph)
    at = edge_at(g, before, after)
    g["edges"][at] = [before, node["id"]]
    g["edges"].insert(at + 1, [node["id"], after])
    g["nodes"].append(node)
    return g


def downstream(graph, nid):
    """nid에서 이음을 따라 닿는 모든 단계 id(자기 제외)."""
    seen, stack = set(), list(outputs(graph, nid))
    while stack:
        now = stack.pop()
        if now not in seen:
            seen.add(now)
            stack += outputs(graph, now)
    return seen


def leaves(value):
    """JSON 값 안의 글자 값들(항목 이름은 빼고 값만)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from leaves(v)


def loose(node):
    """단계 params의 글자 값 안 낱말 전부(컬럼 이름일 수 있는 것, 넉넉하게). 쓰는 컬럼을 확신할 수 없을 때 '안 쓴다'를 증명하는 데 쓴다."""
    found = set()
    for text in leaves(params(node)):  # 낱말 조각 + 공백 조각 + 첫 낱말(flow.refs의 기준): '금액(원)' 같은 이름도 빠뜨리지 않는다
        head = flow.COND_HEAD.match(text)
        found |= {*flow.WORD.findall(text), *text.split(), *([head.group(1)] if head else [])}
    return found | {w.split(".")[0] for w in found}


def target_of(graph, target=None):
    """최적화 대상 집계·지표 단계 id: 주어졌거나, 끝 단계에서 거슬러 올라가 처음 만나는 집계·지표 단계. 없으면 None."""
    nodes = by_id(graph)
    if target:
        return target if nodes.get(target, {}).get("kind") in AFTER_AGG else None
    seen = set()
    nid = flow.one_sink(graph)
    while nid not in seen:
        seen.add(nid)
        if nodes[nid]["kind"] in AFTER_AGG:
            return nid
        ins = inputs(graph, nid)
        if not ins:
            return None
        nid = ins[0]
    return None


# ---------- 결과 비교 ----------

def same_result(a, b):
    """두 Polars 표의 집계 결과가 같은지(컬럼 이름 같고, 키 정렬 뒤 행마다 같음, 실수는 상대 1e-9). (같은지, 다른 까닭)."""
    import polars as pl
    if set(a.columns) != set(b.columns):
        return False, "컬럼이 달라요"
    if a.height != b.height:
        return False, f"행 수가 {a.height:,}행과 {b.height:,}행으로 달라요"
    cols = list(a.columns)
    keys = [c for c in cols if a.schema[c] not in (pl.Float32, pl.Float64)]
    a, b = a.select(cols).sort(keys, nulls_last=True) if keys else a.select(cols), b.select(cols).sort(keys, nulls_last=True) if keys else b.select(cols)
    for c in cols:
        for x, y in zip(a[c].to_list(), b[c].to_list()):
            if x is None or y is None:
                if x is not y:
                    return False, f"‘{c}’에 빈 값이 한쪽에만 있어요"
            elif isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
                if not math.isclose(float(x), float(y), rel_tol=REL_TOL, abs_tol=ABS_TOL):
                    return False, f"‘{c}’의 값이 {x}와 {y}로 달라요"
            elif x != y:
                return False, f"‘{c}’의 값이 {x}와 {y}로 달라요"
    return True, ""


def result_of(graph, target, get_table):
    """대상 단계의 결과 표(Polars)."""
    return flow.to_expr(graph, target, get_table).to_polars()


def bytes_of(graph, target, get_table, con, opened=None):
    """대상 식이 읽는 양의 어림 {bytes(없으면 None), exact, how}. 메타데이터만 보고(scan_size.estimate) 데이터를 읽지 않는다."""
    from lazy import ibis

    def estimate():  # 식을 만들다 실패해도(한 규칙의 흐름이 이상해도) 어림만 '알 수 없음'으로 두고 제안 전체는 이어 간다
        expr = flow.to_expr(graph, target, get_table)
        dialect = "duckdb" if con.name == "polars" else None
        sql = str(ibis.to_sql(expr, dialect=dialect) if dialect else ibis.to_sql(expr))
        return scan_size.estimate(con, sql, opened, dialect)
    return scan_size.safe(estimate)


def bytes_text(before, after):
    """읽는 양 변화 글('약' 표시). 모르면 그 이유."""
    if before["bytes"] is None or after["bytes"] is None:
        return "읽는 양을 미리 알 수 없어요(" + (before["how"] if before["bytes"] is None else after["how"]) + ")."
    return f"읽는 양 약 {scan_size.size_text(before['bytes'])} → 약 {scan_size.size_text(after['bytes'])} (추정 · {after['how']})"


# ---------- 규칙 1: 조건 앞으로 ----------

def side_of(graph, jid, refs, schemas):
    """조인 jid 뒤에서 refs(컬럼 이름들)를 쓰는 조건이 한쪽 입력의 컬럼만 쓰면 (쪽 'left'·'right', 그 쪽 입력 id), 아니면 (None, 이유)."""
    left, right = flow.join_sides(graph, jid)
    lc, rc = flow.out_columns(graph, left, schemas), flow.out_columns(graph, right, schemas) if right else None
    if lc is None or rc is None:
        return None, "컬럼을 알 수 없어요"
    sides = set()
    for r in refs:
        if r in lc:
            sides.add("left")
        elif any(r == c + "_right" and c in lc for c in rc):
            return None, "이름이 겹쳐 바뀐 오른쪽 컬럼이라"
        elif r in rc:
            sides.add("right")
        else:
            return None, f"‘{r}’ 컬럼을 앞 단계에서 찾지 못해"
    if len(sides) != 1:
        return None, "양쪽 컬럼을 함께 써서"
    side = sides.pop()
    return side, left if side == "left" else right


def nearest_join(graph, nid):
    """조건·기간 단계 nid 바로 위에서 (조건·기간 단계들을 건너뛰고) 처음 만나는 조인 단계 id. 없으면 None."""
    nodes, seen = by_id(graph), set()
    cur = inputs(graph, nid)[0] if inputs(graph, nid) else None
    while cur is not None and cur not in seen:
        seen.add(cur)
        if nodes[cur]["kind"] == "join":
            return cur
        if nodes[cur]["kind"] not in ("condition", "period") or len(outputs(graph, cur)) != 1:
            return None
        cur = inputs(graph, cur)[0] if inputs(graph, cur) else None
    return None


def push_filters(graph, ctx):
    """규칙 1. (새 흐름 또는 None, {items, refused})."""
    g, items, refused = flow.duplicate(graph), [], []
    for _ in range(len(g["nodes"]) * 2):
        moved = False
        for node in [n for n in g["nodes"] if n["kind"] in ("condition", "period")]:
            nid = node["id"]
            if len(inputs(g, nid)) != 1 or len(outputs(g, nid)) != 1:
                continue
            jid = nearest_join(g, nid)
            if jid is None:
                continue
            how = params(by_id(g)[jid]).get("how", "inner")
            side, where = side_of(g, jid, flow.refs(node), ctx["schemas"])
            text = flow_text(node)
            if side is None or side not in PUSH.get(how, ()):
                if nid not in [r["node"] for r in refused]:
                    why = (f"내리지 않았어요({where})." if side is None else
                           f"{how} 조인의 {'오른쪽' if side == 'right' else '왼쪽'} 컬럼을 써서 내리지 않았어요(내리면 짝 없는 행의 처리가 달라져 결과가 바뀌어요).")
                    refused.append({"node": nid, "text": f"{text}은 " + why})
                continue
            g = splice_in(splice_out(g, nid), node, where, jid)
            items.append({"node": nid, "join": jid, "side": side, "text": f"{text}을 조인 {'왼쪽' if side == 'left' else '오른쪽'} 테이블 쪽으로 내렸어요."})
            refused = [r for r in refused if r["node"] != nid]
            moved = True
            break
        if not moved:
            break
    return (g if items else None), {"items": items, "refused": refused}


def flow_text(node):
    """조건·기간 단계를 문장에 쓸 짧은 글."""
    p = params(node)
    return f"{flow.NAMES[node['kind']]} ‘{p.get('cond') if node['kind'] == 'condition' and isinstance(p.get('cond'), str) else p.get('line') or node['id']}’"


# ---------- 규칙 2: 조인 빼기 ----------

def key_unique(graph, nid, cols, ctx):
    """단계 nid의 결과에서 cols가 고유한지: (True·False·None(확인 못 함), 이유). 전체를 읽으므로 원격은 scan_ok일 때만."""
    if ctx["remote"] and not ctx["scan_ok"]:
        return None, REMOTE_NOTE
    cache = ctx["uniq"]
    key = (nid, tuple(cols))
    if key not in cache:
        cache[key] = join_keys.is_unique(flow.to_expr(graph, nid, ctx["get_table"]), list(cols))
    return cache[key], ""


def drop_join(graph, ctx):
    """규칙 2. (새 흐름 또는 None, {items, refused})."""
    g, items, refused = flow.duplicate(graph), [], []
    for node in [n for n in g["nodes"] if n["kind"] == "join"]:
        jid, p = node["id"], params(node)
        left, right = flow.join_sides(g, jid)
        if p.get("how") != "left" or right is None or p.get("on"):
            continue
        rc = flow.out_columns(g, right, ctx["schemas"])
        lc = flow.out_columns(g, left, ctx["schemas"])
        if rc is None or lc is None:
            continue
        out_names = {c + "_right" if c in lc else c for c in rc}
        used = set().union(*[loose(by_id(g)[i]) for i in downstream(g, jid)]) if downstream(g, jid) else set()
        if used & out_names:
            refused.append({"node": jid, "text": f"{flow.label(node)}의 오른쪽 컬럼({', '.join(sorted(used & out_names)[:4])})을 아래에서 써서 빼지 않았어요."})
            continue
        unique, why = key_unique(g, right, [r for _, r in p.get("keys") or []], ctx)
        if unique is not True:
            refused.append({"node": jid, "text": f"{flow.label(node)}를 빼지 않았어요: " + (why or "오른쪽 키가 고유하지 않아요(빼면 오른쪽 중복만큼 늘던 행 수가 달라져요).")})
            continue
        gone = {right} | exclusive_upstream(g, right, jid, [left])
        (after,) = outputs(g, jid)
        at = edge_at(g, jid, after)
        g["edges"][at] = [left, after]
        g["edges"] = [e for e in g["edges"] if e[1] != jid and not {e[0], e[1]} & (gone | {jid})]
        g["nodes"] = [n for n in g["nodes"] if n["id"] not in gone | {jid}]
        items.append({"node": jid, "text": f"오른쪽 컬럼을 쓰지 않고 오른쪽 키가 고유해서 {flow.label(node)}를 뺐어요."})
    return (g if items else None), {"items": items, "refused": refused}


def exclusive_upstream(graph, nid, stop, keep=()):
    """nid의 앞쪽 단계 중 stop을 지나지 않고는 아무 데도 나가지 않는 것(같이 지워도 되는 가지). 더 늘지 않을 때까지 모은다(순서와 상관없이). keep과 그 앞쪽은 지우지 않는다."""
    safe = set(keep).union(*[flow.upstream(graph, k) for k in keep])
    found, changed = set(), True
    while changed:
        changed = False
        for i in flow.upstream(graph, nid) - safe - found:
            if all(o in found | {nid, stop} for o in outputs(graph, i)):
                found.add(i)
                changed = True
    return found


# ---------- 규칙 3: 먼저 집계 ----------

def pre_aggregate(graph, target, ctx):
    """규칙 3. 대상 집계 바로 앞이 조인(inner·left)이고 오른쪽 키가 고유할 때 왼쪽(사실 테이블)을 키·묶음 컬럼으로 먼저 집계한다. (새 흐름 또는 None, {items, refused})."""
    nodes = by_id(graph)
    agg = nodes[target]
    refused = lambda text: (None, {"items": [], "refused": [{"node": target, "text": text}]})
    ins = inputs(graph, target)
    if agg["kind"] != "aggregate" or len(ins) != 1 or nodes[ins[0]]["kind"] != "join":
        return None, {"items": [], "refused": []}
    jid = ins[0]
    p, jp = params(agg), params(nodes[jid])
    left, right = flow.join_sides(graph, jid)
    if jp.get("how") not in ("inner", "left") or right is None or jp.get("on"):
        return refused("조인 종류가 inner·left가 아니라 먼저 집계하지 않았어요.")
    if len(outputs(graph, jid)) != 1:
        return refused("조인 결과를 다른 곳에서도 써서 먼저 집계하지 않았어요.")
    for s in p.get("series") or []:
        if s.get("ratio") or s.get("agg") not in DECOMPOSABLE or s.get("where") or s.get("opts"):
            name = sql_funcs.label_of(s["agg"]) if s.get("agg") else "이 집계"
            return refused(f"{settings.josa(name, '은', '는')} 나눠서 집계했다가 합칠 수 없어서 먼저 집계하지 않았어요(합계·개수·최소·최대·평균만 돼요).")
    lc, rc = flow.out_columns(graph, left, ctx["schemas"]), flow.out_columns(graph, right, ctx["schemas"])
    if lc is None or rc is None:
        return None, {"items": [], "refused": []}
    ys = [s["y"] for s in p.get("series") or [] if s.get("y")]
    if any(y not in lc for y in ys):
        return refused("집계하는 값 컬럼이 왼쪽 테이블에 없어서 먼저 집계하지 않았어요.")
    by = [c for c in p.get("by") or [] if c]
    if any(c not in lc and c not in {x if x not in lc else x + "_right" for x in rc} for c in by):
        return refused("묶는 컬럼을 앞 단계에서 찾지 못해 먼저 집계하지 않았어요.")
    lkeys = [l for l, _ in jp.get("keys") or []]
    unique, why = key_unique(graph, right, [r for _, r in jp.get("keys") or []], ctx)
    if unique is not True:
        return refused("먼저 집계하지 않았어요: " + (why or "오른쪽(차원) 키가 고유하지 않아 짝이 여러 행일 수 있어요."))
    pre_by = list(dict.fromkeys([*lkeys, *[c for c in by if c in lc]]))
    pre_series, final = [], []
    for i, s in enumerate(p.get("series") or []):
        name, y, agg_id = s.get("name") or f"v{i}", s.get("y"), s["agg"]
        part = lambda tag, agg_, y_=y: {"name": f"__p{i}_{tag}", "agg": agg_, "y": y_}
        if agg_id == "mean":
            pre_series += [part("s", "sum"), part("c", "count")]
            final.append({"name": name, "ratio": [{"agg": "sum", "y": f"__p{i}_s"}, {"agg": "sum", "y": f"__p{i}_c"}]})
        elif agg_id == "count":
            pre_series.append(part("c", "count"))
            final.append({"name": name, "agg": "sum", "y": f"__p{i}_c"})
        else:
            pre_series.append(part("v", agg_id))
            final.append({"name": name, "agg": agg_id, "y": f"__p{i}_v"})
    node = {"id": new_id(graph, "pre"), "kind": "aggregate", "params": {"by": pre_by, "series": pre_series}, "pos": {"x": 120, "y": 20}}
    g = splice_in(graph, node, left, jid)
    by_id(g)[target]["params"] = {**p, "series": final}
    keys = ", ".join(pre_by)
    return g, {"items": [{"node": node["id"], "join": jid, "text": f"조인 전에 왼쪽 테이블을 ‘{keys}’별로 먼저 집계해요(조인할 행이 ‘{keys}’ 종류 수만큼으로 줄어요)."}], "refused": []}


# ---------- 규칙 4: 쓰는 컬럼만 ----------

def prune_columns(graph, ctx):
    """규칙 4. 테이블 단계마다 아래에서 쓰는 컬럼만 읽게 params.columns를 정한다. 쓰는 컬럼을 확신할 수 없으면(타입·패턴 대상 계산, 이어붙이기) 건너뛴다."""
    from core import calc
    g, items = flow.duplicate(graph), []
    for n in g["nodes"]:
        if n["kind"] == "calc" and (str(params(n).get("line", "")).split() or [""])[0] in (*calc.EACH, "세로로"):
            return None, {"items": [], "refused": [{"node": n["id"], "text": "타입·이름 패턴으로 컬럼을 고르는 계산이 있어 쓰는 컬럼만 읽지 않았어요."}]}
    for n in g["nodes"]:
        if n["kind"] != "table" or params(n).get("columns"):
            continue
        cols = flow.table_columns(n, ctx["schemas"])
        outs = downstream(g, n["id"])
        if cols is None or any(by_id(g)[o]["kind"] == "union" for o in outs | set(outputs(g, n["id"]))):
            continue
        used = set().union(*[loose(by_id(g)[o]) for o in outs]) if outs else set()
        need = [c for c in cols if c in used or (c + "_right") in used]
        need = need or list(cols)[:1]
        if len(need) < len(cols):
            n["params"] = {**params(n), "columns": need}
            items.append({"node": n["id"], "text": f"‘{params(n)['table']}’에서 {len(need)}개 컬럼만 읽어요(전체 {len(cols)}개 중, 빼는 컬럼: {', '.join(c for c in cols if c not in need)[:60]})."})
    return (g if items else None), {"items": items, "refused": []}


# ---------- 규칙 5: 부풂 경고 ----------

def fanout_warnings(graph, ctx):
    """규칙 5. 조인마다 양쪽 키가 모두 고유하지 않으면 빨간 경고(합계가 부풂), 오른쪽만 겹치면 노란 경고. 전체를 읽으므로 원격은 scan_ok일 때만(아니면 '확인 못 함')."""
    out = []
    for node in graph["nodes"]:
        if node["kind"] != "join" or params(node).get("how") not in ("inner", "left", "right", "outer"):
            continue
        left, right = flow.join_sides(graph, node["id"])
        if right is None:
            continue
        lk, rk = [l for l, _ in params(node).get("keys") or []], [r for _, r in params(node).get("keys") or []]
        ru, why = key_unique(graph, right, rk, ctx)
        if ru is None:
            out.append({"rule": 5, "node": node["id"], "level": "info", "text": f"{flow.label(node)}의 부풂을 확인하지 못했어요: {why}"})
            continue
        lu, _ = key_unique(graph, left, lk, ctx)
        if ru is False and lu is False:
            out.append({"rule": 5, "node": node["id"], "level": "bad", "text": f"{flow.label(node)}: 양쪽 키가 모두 고유하지 않아 행이 곱해져요. 이대로 합계를 내면 값이 부풀어요."})
        elif ru is False:
            out.append({"rule": 5, "node": node["id"], "level": "warn", "text": f"{flow.label(node)}: 오른쪽 키가 겹쳐서 왼쪽 값이 오른쪽 행 수만큼 반복돼요. 왼쪽 값의 합계는 부풀 수 있어요."})
    return out


# ---------- 제안 ----------

def propose(graph, get_table, con, schemas=None, target=None, remote=False, scan_ok=False, only=None, opened=None, source_key=None, warn=True):
    """최적화를 제안한다(흐름은 바꾸지 않고 새 흐름을 돌려줄 뿐). only = 규칙 번호 목록(없으면 1~4 전부).
    {ok, reason?, target, rules: [{no, id, title, status(kept·dropped·skipped·unverified), items, refused, equal, bytes}], warnings, graph, changed, bytes, verified, remote, note}.
    warn=False면 부풂 경고를 구하지 않는다(적용할 때는 이미 본 경고라 원격 조회를 또 보내지 않는다).
    status: kept(적용하고 결과가 같음을 확인), dropped(결과가 달라서 뺌), unverified(원격이라 비교 안 함), skipped(할 것 없음 또는 못 함)."""
    graph = flow.check_shape(graph)
    target = target_of(graph, target)
    if target is None:
        return {"ok": False, "reason": NOT_AGG, "rules": [], "warnings": [], "changed": False}
    schemas = schemas if schemas is not None else flow.schemas_of(graph, get_table, source_key)
    ctx = {"get_table": get_table, "schemas": schemas, "remote": remote, "scan_ok": scan_ok, "uniq": {}}
    verify = not remote or scan_ok
    original = result_of(graph, target, get_table) if verify else None
    before = bytes_of(graph, target, get_table, con, opened)
    current, rules = graph, []
    for no, (rid, title) in RULES.items():
        if only is not None and no not in only:
            continue
        if no == 3:
            new, info = pre_aggregate(current, target, ctx)
        else:
            new, info = {1: push_filters, 2: drop_join, 4: prune_columns}[no](current, ctx)
        entry = {"no": no, "id": rid, "title": title, "status": "skipped", "equal": None, **info}
        if new is not None:
            if verify:
                try:
                    same, why = same_result(original, result_of(new, target, get_table))
                except Exception as e:
                    same, why = False, f"실행하지 못했어요({str(e)[:80]})"
                entry.update(equal=same, status="kept" if same else "dropped", **({} if same else {"why": f"결과가 달라서 뺐어요({why})."}))
            elif no in (1, 4):
                entry["status"] = "unverified"
            else:
                new, entry["status"] = None, "skipped"
            if entry["status"] in ("kept", "unverified"):
                current = new
                entry["bytes"] = bytes_of(current, target, get_table, con, opened)
        rules.append(entry)
    after = bytes_of(current, target, get_table, con, opened)
    joins = sum(1 for n in graph["nodes"] if n["kind"] == "join")
    note = "" if verify else f"{REMOTE_NOTE} 확인하면 DB에 최대 {1 + len(RULES) + 4 * joins}번 조회해요."  # 원래 결과 1 + 규칙마다 결과 비교 + 조인마다 양쪽 키 고유 확인(키 하나가 고유한지는 행 수·고유 행 수로 2번 조회하니 양쪽 4번)
    return {"ok": True, "target": target, "rules": rules, "warnings": fanout_warnings(graph, ctx) if warn else [], "graph": current, "changed": current is not graph,
            "bytes": {"before": before, "after": after, "text": bytes_text(before, after)}, "verified": verify, "remote": remote, "note": note}


def apply(graph, numbers, get_table, con, **kwargs):
    """고른 규칙 번호들만 다시 제안해(결과 같음을 다시 확인) 확인된 것만 적용한 새 흐름을 돌려준다. {graph, applied, dropped}. 빠진 규칙은 dropped에 이유."""
    found = propose(graph, get_table, con, only=list(numbers), warn=False, **kwargs)
    if not found["ok"]:
        raise ValueError(found["reason"])
    applied = [r["no"] for r in found["rules"] if r["status"] in ("kept", "unverified")]
    dropped = [{"no": r["no"], "why": r.get("why") or "; ".join(x["text"] for x in r["refused"]) or "바꿀 것이 없어요."} for r in found["rules"] if r["no"] in numbers and r["no"] not in applied]
    return {"graph": found["graph"], "applied": applied, "dropped": dropped, "bytes": found["bytes"]}
