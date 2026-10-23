"""흐름 모델(6-1): 단계를 점(node)으로, 이음을 선(edge)으로 둔 그래프. 단계 카드·흐름·문장 보기가 같은 검색 body(core.build가 받는 모양)를 본다.
graph = {nodes: [{id, kind, params, pos}], edges: [[from, to]], extra?}. 줄에 어느 쪽이 먼저인지는 edges의 차례로 정한다(조인의 첫 이음 = 왼쪽, 둘째 = 오른쪽).
kind와 params(단계 종류 RANK 순):
  table {table, schema?, source_key?, label?, columns?(쓰는 컬럼만)} / union {how} / join {how, keys: [[왼쪽, 오른쪽]], on?: [왼쪽 시각, 오른쪽 시각]}
  period {line} / condition {cond(글자·{line, quotes}·{any})} / calc {line, adds?: {새 컬럼: 종류}} / dedupe {keys, order?}
  aggregate {by: [컬럼], series: [{agg, y, opts?, where?, name?} 또는 {name, ratio: [{합 쪽 집계}, {나누는 쪽 집계}]}]} / metric {names, by?, defs?} / chart·card {x, split?, series?, …}
검사 check_edge는 데이터를 읽지 않고 schemas({테이블: {columns: {컬럼: 종류}, source_key}})만 본다. 변환 to_body·from_body는 body로 왕복하고,
body로 표현할 수 없는 흐름(조인 앞 가지에 조건·집계가 있는 최적화 결과)은 to_expr로 식을 만든다. 행 수는 [단계별 행 수 세기]를 눌렀을 때(row_counts)만 센다."""
import copy as _copy
import re

from core import calc
from core import parse
from core import settings
from core import sql_funcs

RANK = {"table": 0, "union": 1, "join": 2, "period": 3, "condition": 4, "calc": 5, "dedupe": 6, "aggregate": 7, "metric": 7, "chart": 8, "card": 8}
NAMES = {"table": "테이블", "union": "이어붙이기", "join": "조인", "period": "기간", "condition": "조건", "calc": "계산", "dedupe": "중복 제거",
         "aggregate": "집계", "metric": "지표", "chart": "차트", "card": "카드"}
ORDER_TEXT = "테이블 → 이어붙이기 → 조인 → 기간 → 조건 → 계산 → 중복 제거 → 집계·지표 → 차트·카드"
SAME_OK = ("join", "period", "condition", "calc")  # 같은 순위끼리 이어도 되는 단계
KIND_TEXT = {"num": "숫자", "text": "글자", "date": "날짜", "bool": "참/거짓", "json": "JSON", "array": "목록", "struct": "구조", "other": "기타"}
JOIN_HOWS = parse.JOINS
STAGES = ("joins", "periods", "conditions", "calcs")
MAX_NODES = 200
AFTER_AGG = ("aggregate", "metric")  # 컬럼이 묶은 컬럼과 결과만 남는 단계
NUMERIC_AGGS = tuple(i for i in sql_funcs.aggregate_ids() if sql_funcs.get(i)["kind_in"] == "num")  # 숫자 컬럼이 있어야 하는 집계
COND_HEAD = re.compile(r"^\s*([^\s=!<>]+)")  # 조건·기간 줄의 첫 낱말(컬럼)
WORD = re.compile(r"[\w가-힣.]+")


def empty():
    """빈 흐름."""
    return {"nodes": [], "edges": []}


def duplicate(graph):
    """흐름의 깊은 사본(고치기 전에)."""
    return _copy.deepcopy(graph)


def by_id(graph):
    """{id: 단계}."""
    return {n["id"]: n for n in graph["nodes"]}


def inputs(graph, nid):
    """단계로 들어오는 이음의 시작 단계 id들(edges 차례)."""
    return [a for a, b in graph["edges"] if b == nid]


def outputs(graph, nid):
    """단계에서 나가는 이음의 끝 단계 id들."""
    return [b for a, b in graph["edges"] if a == nid]


def params(node):
    return node.get("params") or {}


def label(node):
    """화면·문장에 보일 단계 이름: 종류(테이블이면 이름)."""
    p = params(node)
    return f"{NAMES[node['kind']]} ‘{p.get('table')}’" if node["kind"] == "table" and p.get("table") else f"{NAMES[node['kind']]}({node['id']})"


def check_shape(graph):
    """흐름의 모양(단계 id가 겹치지 않고, 종류를 알고, 이음이 있는 단계를 가리키는지)을 검사한 사본. 아니면 ValueError."""
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges", []), list):
        raise ValueError("흐름은 {nodes: [...], edges: [...]} 모양이어야 해요.")
    if len(graph["nodes"]) > MAX_NODES:
        raise ValueError(f"흐름의 단계는 {MAX_NODES}개까지예요.")
    ids = set()
    for n in graph["nodes"]:
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not n["id"] or n.get("kind") not in RANK:
            raise ValueError(f"단계는 {{id, kind}}를 가져야 하고 kind는 {', '.join(RANK)} 중 하나예요.")
        if n["id"] in ids:
            raise ValueError(f"단계 id '{n['id']}'가 겹쳐요.")
        if not isinstance(n.get("params", {}), dict):
            raise ValueError(f"단계 '{n['id']}'의 params는 객체여야 해요.")
        ids.add(n["id"])
    for e in graph.get("edges", []):
        if not (isinstance(e, (list, tuple)) and len(e) == 2 and e[0] in ids and e[1] in ids):
            raise ValueError("이음은 [시작 단계 id, 끝 단계 id]이고 두 단계가 흐름에 있어야 해요.")
    out = duplicate(graph)
    out.setdefault("edges", [])
    out["edges"] = [list(e) for e in out["edges"]]
    return out


def route(graph, start, goal):
    """start에서 이음을 따라 goal까지 가는 단계 id 목록(없으면 None). 순환 검사에 쓴다."""
    seen, queue = {start: None}, [start]
    while queue:
        now = queue.pop(0)
        if now == goal:
            path = []
            while now is not None:
                path.append(now)
                now = seen[now]
            return path[::-1]
        for nxt in outputs(graph, now):
            if nxt not in seen:
                seen[nxt] = now
                queue.append(nxt)
    return None


def upstream(graph, nid):
    """단계 앞쪽(자기 자신 제외)의 모든 단계 id. 순환이 있어도 끝난다."""
    seen, stack = set(), list(inputs(graph, nid))
    while stack:
        now = stack.pop()
        if now not in seen:
            seen.add(now)
            stack += inputs(graph, now)
    return seen - {nid}


def root_table(graph, nid):
    """단계를 거슬러 올라가 처음 만나는 테이블 단계(가지의 뿌리, 첫 이음을 따른다). 없으면 None."""
    nodes, seen = by_id(graph), set()
    while nid not in seen:
        seen.add(nid)
        if nodes[nid]["kind"] == "table":
            return nodes[nid]
        ins = inputs(graph, nid)
        if not ins:
            return None
        nid = ins[0]
    return None


def source_keys(graph, nid, schemas):
    """단계의 앞쪽(자기 포함) 테이블들이 속한 연결 열쇠들(source_key). 모르는 테이블은 건너뛴다."""
    nodes, found = by_id(graph), set()
    for i in upstream(graph, nid) | {nid}:
        n = nodes[i]
        if n["kind"] == "table":
            key = params(n).get("source_key") or (schemas.get(params(n).get("table")) or {}).get("source_key")
            if key:
                found.add(key)
    return found


# ---------- 컬럼 따라가기 ----------

def table_columns(node, schemas):
    """테이블 단계의 {컬럼: 종류}(쓰는 컬럼만 정해 뒀으면 그것만). 모르면 None."""
    cols = (schemas.get(params(node).get("table")) or {}).get("columns")
    if cols is None:
        return None
    keep = params(node).get("columns")
    return {c: k for c, k in cols.items() if c in keep} if keep else dict(cols)


def right_names(left, right, how):
    """조인 결과의 컬럼: 왼쪽 컬럼 + 오른쪽 컬럼(왼쪽과 이름이 겹치면 _right를 붙인다). semi·anti는 왼쪽만."""
    if how in ("semi", "anti"):
        return dict(left)
    return {**left, **{(c + "_right" if c in left else c): k for c, k in right.items()}}


def out_columns(graph, nid, schemas, depth=0):
    """단계가 내보내는 {컬럼: 종류}. 하나라도 모르면(스키마 없음, 순환) None."""
    nodes = by_id(graph)
    if depth > MAX_NODES or nid not in nodes:
        return None
    node, kind, p = nodes[nid], nodes[nid]["kind"], params(nodes[nid])
    if kind == "table":
        return table_columns(node, schemas)
    subs = [out_columns(graph, i, schemas, depth + 1) for i in inputs(graph, nid)]
    if not subs or subs[0] is None:
        return None
    first = subs[0]
    if kind == "union":
        if any(s is None for s in subs):
            return None
        how = p.get("how", "left")
        common = {c: k for c, k in first.items() if all(c in s for s in subs[1:])}
        cols = dict(first) if how == "left" else common
        return {**cols, **({parse.SOURCE: "text"} if how in parse.STACKS else {})}
    if kind == "join":
        if len(subs) < 2:
            return first
        return None if subs[1] is None else right_names(first, subs[1], p.get("how", "inner"))
    if kind == "calc":
        return {**first, **(p.get("adds") or {})}
    if kind in AFTER_AGG:
        by = [c for c in (p.get("by") or []) if c]
        cols = {c: first.get(c, "other") for c in by}
        if kind == "metric":
            return {**cols, **{n: "num" for n in p.get("names") or []}}
        for i, s in enumerate(p.get("series") or []):
            out = "num" if s.get("ratio") else sql_funcs.get(s.get("agg", "count"))["kind_out"]
            cols[s.get("name") or f"v{i}"] = first.get(s.get("y"), "other") if out == "any" else out
        return cols
    return first  # period·condition·dedupe·chart·card


def bare(name):
    """a.b 같은 구조 필드 이름의 컬럼 부분."""
    return str(name).split(".")[0]


def cond_columns(cond):
    """조건(글자·{line}·{any})이 가리키는 컬럼 이름들."""
    if isinstance(cond, str):
        m = COND_HEAD.match(cond)
        return {bare(m.group(1))} if m else set()
    if isinstance(cond, dict):
        if "any" in cond:
            return {c for item in cond["any"] for c in cond_columns(item)}
        return cond_columns(cond.get("line", ""))
    return set()


def refs(node):
    """단계가 앞 단계에서 읽는 컬럼 이름들(알 수 있는 만큼). 조인은 왼쪽 키만(오른쪽 키는 오른쪽 입력의 컬럼이라 join_problem이 본다)."""
    p, kind = params(node), node["kind"]
    if kind == "period":
        return {bare(p["column"])} if p.get("column") else cond_columns(p.get("line", ""))
    if kind == "condition":
        return set(p.get("columns") or ()) or cond_columns(p.get("cond"))
    if kind == "calc":
        if p.get("reads"):
            return set(p["reads"])
        try:
            k, main, opts = calc.split_words(p.get("line", ""))
            return {bare(c) for c in calc.mentions(main, opts)}
        except ValueError:
            return set()
    if kind == "dedupe":
        return {*(p.get("keys") or ()), *({bare(p["order"].split()[0])} if p.get("order") else ())}
    if kind == "join":
        return {l for l, _ in (p.get("keys") or [])} | ({p["on"][0]} if p.get("on") else set())
    if kind in AFTER_AGG:
        found = {c for c in (p.get("by") or []) if c}
        for s in p.get("series") or []:
            found |= ({s["y"]} if s.get("y") else set()) | cond_columns(s.get("where")) | {x["y"] for x in s.get("ratio") or [] if x.get("y")}
        for m in (p.get("defs") or {}).values():
            for part in (m.get("numerator"), m.get("denominator")):
                if part:
                    found |= ({part["column"]} if part.get("column") else set()) | {c for w in part.get("where") or [] for c in cond_columns(w)}
        return {bare(c) for c in found}
    if kind in ("chart", "card"):
        found = {p.get("x"), p.get("split")} | {s.get("y") for s in p.get("series") or [] if isinstance(s, dict)}
        return {bare(c) for c in found if c}
    return set()


def kind_text(kind):
    return KIND_TEXT.get(kind, kind)


def ro(word):
    """낱말 뒤에 '로'/'으로'(받침이 없거나 ㄹ이면 로)."""
    last = word[-1:]
    final = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 not in (0, 8)
    return word + ("으로" if final else "로")


# ---------- 이음 검사 ----------

def with_edge(graph, a, b, at=None):
    """이음 a→b를 더한 사본(at이 있으면 edges의 그 자리에, 아니면 맨 끝에)."""
    out = duplicate(graph)
    out["edges"].insert(len(out["edges"]) if at is None else at, [a, b])
    return out


def problem(code, reason, **extra):
    return {"code": code, "reason": reason, **extra}


def kind_pair_ok(ka, kb):
    """조인 키 두 컬럼의 종류가 그대로 비교되는지(같은 종류, 모르는 종류는 통과)."""
    return ka == kb or "other" in (ka, kb)


def cast_suggestion(left, right, kl, kr):
    """조인 키 타입이 다를 때 바꿔 잇기 제안: 글자 쪽을 상대 종류로(둘 다 글자가 아니면 오른쪽을 왼쪽 종류로). left·right = 키 컬럼 이름, kl·kr = 그 종류."""
    if kl == "text" and kr != "text":
        return {"side": "left", "column": left, "to": kr}
    if kr == "text" and kl != "text":
        return {"side": "right", "column": right, "to": kl}
    return {"side": "right", "column": right, "to": kl}


def edge_problem(graph, a, b, schemas, at=None):
    """이음 a→b를 막는 이유 {code, reason, …} 또는 None. 데이터는 읽지 않는다(schemas = {테이블: {columns: {컬럼: 종류}, source_key}}).
    순서: 모양 → 순위 → 순환 → 다른 연결 → 컬럼(키 타입·이어붙일 컬럼·집계 뒤 키·숫자·날짜)."""
    schemas = schemas or {}
    nodes = by_id(graph)
    if a not in nodes or b not in nodes:
        return problem("shape", "이을 단계가 흐름에 없어요.")
    if a == b:
        return problem("shape", "단계를 자기 자신에게 이을 수 없어요.")
    if [a, b] in [list(e) for e in graph["edges"]]:
        return problem("shape", "이미 이어져 있어요.")
    ka, kb = nodes[a]["kind"], nodes[b]["kind"]
    na, nb = NAMES[ka], NAMES[kb]
    if kb == "table":
        return problem("rank", f"{na} 뒤에 테이블을 이을 수 없어요. 테이블은 흐름의 시작이에요.")
    if ka in ("chart", "card"):
        return problem("rank", f"{na} 뒤에는 다른 단계를 이을 수 없어요. 차트·카드는 흐름의 끝이에요.")
    if kb not in ("join", "union"):  # 조인·이어붙이기는 조건을 건 테이블 같은 가지도 받는다
        if RANK[kb] < RANK[ka] or (RANK[kb] == RANK[ka] and ka not in SAME_OK):
            return problem("rank", f"{na} 뒤에 {nb} 단계를 놓을 수 없어요. 순서는 {ORDER_TEXT}예요.")
    path = route(graph, b, a)
    if path:
        return problem("cycle", "순환이 생겨요: " + " → ".join(label(nodes[i]) for i in [*path, b]) + ".")
    have = inputs(graph, b)
    if kb == "union" and ka != "table":
        return problem("rank", "이어붙이기에는 테이블끼리만 이을 수 있어요.")
    if kb == "join" and len(have) >= 2:
        return problem("shape", "조인에는 왼쪽·오른쪽 두 곳만 이을 수 있어요.")
    if kb not in ("join", "union") and have:
        return problem("shape", f"{nb}에는 이미 앞 단계({label(nodes[have[0]])})가 이어져 있어요. 한 단계에는 하나만 이을 수 있어요.")
    g = with_edge(graph, a, b, at)
    keys = source_keys(g, b, schemas)
    if len(keys) > 1:
        return problem("connection", f"서로 다른 연결의 테이블이라 이을 수 없어요({', '.join(sorted(keys))}). 같은 연결의 테이블끼리만 이을 수 있어요.")
    return column_problem(g, a, b, schemas)


def column_problem(g, a, b, schemas):
    """이음을 더한 흐름 g에서 컬럼 때문에 막히는 이유(없으면 None)."""
    nodes = by_id(g)
    kb, p = nodes[b]["kind"], params(nodes[b])
    cols = out_columns(g, a, schemas)
    if kb == "union":
        subs = [out_columns(g, i, schemas) for i in inputs(g, b)]
        if len(subs) >= 2 and all(s is not None for s in subs):
            common = [c for c in subs[0] if all(c in s for s in subs[1:])]
            if not common:
                return problem("union_columns", "이어붙일 테이블들에 같은 이름의 컬럼이 없어요. 컬럼 이름을 맞춘 뒤 이어 주세요.")
        return None
    if kb == "join":
        subs = [out_columns(g, i, schemas) for i in inputs(g, b)]
        if len(subs) < 2 or any(s is None for s in subs):
            return None
        after = [any(nodes[x]["kind"] in AFTER_AGG for x in upstream(g, i) | {i}) for i in inputs(g, b)]
        return join_problem(p, subs[0], subs[1], after)
    if cols is None:
        return None
    if kb == "period":
        dates = [c for c, k in cols.items() if k == "date"]
        want = refs(nodes[b])
        if not dates:
            return problem("no_date", "앞 단계에 날짜 컬럼이 없어서 기간을 걸 수 없어요. 날짜 컬럼이 있는 단계 뒤에 놓으세요.")
        for c in want:
            if c in cols and cols[c] != "date":
                return problem("no_date", f"‘{c}’은 {kind_text(cols[c])} 컬럼이라 기간을 걸 수 없어요. 날짜 컬럼({', '.join(dates[:3])})을 고르세요.")
    missing = sorted(c for c in refs(nodes[b]) if c not in cols)
    if missing:
        names = ", ".join(f"‘{c}’" for c in missing)
        if any(nodes[i]["kind"] in AFTER_AGG for i in upstream(g, b) | {a}):
            return problem("aggregate_lost", f"집계한 뒤에는 묶은 컬럼({', '.join(list(cols)[:6])})만 남아서 {names}을(를) 쓸 수 없어요. 집계 앞 단계에서 쓰거나 집계에 넣어 주세요.", columns=missing)
        return problem("missing_column", f"앞 단계에 {names} 컬럼이 없어요.", columns=missing)
    if kb in AFTER_AGG or kb in ("chart", "card"):
        for s in p.get("series") or []:
            if isinstance(s, dict) and s.get("y") in cols and s.get("agg") in NUMERIC_AGGS and cols[s["y"]] != "num":
                name = sql_funcs.label_of(s["agg"])
                return problem("need_number", f"‘{s['y']}’은 {kind_text(cols[s['y']])} 컬럼이라 {settings.josa(name, "을", "를")} 구할 수 없어요. 숫자로 바꾸거나(cast) 개수·고유 개수를 쓰세요.",
                               column=s["y"], to="num")
    return None


def join_problem(p, left, right, after=(False, False)):
    """조인 단계 p의 키가 왼쪽·오른쪽 컬럼에 있고 종류가 맞는지. after = (왼쪽, 오른쪽)이 집계한 뒤의 가지인지(그러면 키가 사라졌다고 알린다)."""
    for l, r in p.get("keys") or []:
        for side, name, cols, agg in (("왼쪽", l, left, after[0]), ("오른쪽", r, right, after[1])):
            if name not in cols:
                if agg:
                    return problem("aggregate_lost", f"{side}는 집계한 뒤라 묶은 컬럼({', '.join(list(cols)[:6])})만 남아서 조인 키 ‘{name}’을(를) 쓸 수 없어요. 집계에 그 컬럼을 묶음 기준으로 넣어 주세요.", columns=[name])
                return problem("missing_column", f"{side}에 ‘{name}’ 컬럼이 없어요.", columns=[name])
        if not kind_pair_ok(left[l], right[r]):
            suggest = cast_suggestion(l, r, left[l], right[r])
            side = "왼쪽" if suggest["side"] == "left" else "오른쪽"
            return problem("key_type", f"조인 키 ‘{l}’({kind_text(left[l])})과 ‘{r}’({kind_text(right[r])})의 종류가 달라 이을 수 없어요. "
                                       f"{side} ‘{suggest['column']}’을(를) {ro(kind_text(suggest['to']))} 바꿔(cast) 이어 보세요.", suggest=suggest)
    if p.get("on"):
        lt, rt = p["on"]
        if lt not in left or rt not in right:
            return problem("missing_column", f"asof 시각 컬럼 ‘{lt}’·‘{rt}’이 양쪽에 있어야 해요.", columns=[lt, rt])
    return None


def check_edge(graph, a, b, schemas=None):
    """이음 a→b를 해도 되는지: 막는 이유 문장 또는 None(해도 됨)."""
    found = edge_problem(graph, a, b, schemas)
    return found["reason"] if found else None


def check_graph(graph, schemas=None):
    """흐름의 이음을 하나씩 다시 검사해 [{edge, code, reason}]. 이음을 뺀 흐름에서 그 이음을 제자리에 다시 더해 본다.
    조인처럼 입력이 둘인 단계의 같은 문제는 한 번만(마지막 입력의 이음으로) 알린다."""
    found = {}
    for at, e in enumerate(graph["edges"]):
        rest = duplicate(graph)
        del rest["edges"][at]
        why = edge_problem(rest, e[0], e[1], schemas, at)
        if why:
            found[e[1], why["code"], why["reason"]] = {"edge": list(e), **why}
    return list(found.values())


# ---------- graph → body ----------

def join_sides(graph, jid):
    """조인의 (왼쪽 입력 id, 오른쪽 입력 id 또는 None): 이음 차례대로."""
    ins = inputs(graph, jid)
    return (ins[0] if ins else None), (ins[1] if len(ins) > 1 else None)


def sinks(graph):
    """나가는 이음이 없는 단계 id들."""
    return [n["id"] for n in graph["nodes"] if not outputs(graph, n["id"])]


def main_chain(graph, nid):
    """nid까지의 본 흐름 단계 id들(처음 테이블부터 차례로). 조인은 왼쪽, 이어붙이기는 자기가 시작이다(기준 테이블 = 첫 입력)."""
    nodes, order, seen = by_id(graph), [], set()
    while True:
        if nid in seen:
            raise ValueError("흐름에 순환이 있어요.")
        seen.add(nid)
        order.append(nid)
        node = nodes[nid]
        if node["kind"] in ("table", "union"):
            break
        ins = inputs(graph, nid)
        if not ins:
            raise ValueError(f"{label(node)} 앞에 이어진 단계가 없어요.")
        nid = ins[0]
    return order[::-1]


def plain_table(graph, nid):
    """nid가 조건·집계 없이 바로 쓰는 테이블 단계면 그 단계, 아니면 None."""
    node = by_id(graph).get(nid)
    return node if node and node["kind"] == "table" and not params(node).get("columns") else None


def body_problem(graph, target=None):
    """흐름을 검색 body로 바꿀 수 없는 이유(바꿀 수 있으면 None): 조인·이어붙이기의 입력이 조건을 건 가지거나, 쓰는 컬럼만 정해 둔 테이블이 있을 때."""
    try:
        target = target or one_sink(graph)
        chain = main_chain(graph, target)
    except ValueError as e:
        return str(e)
    nodes, used, stage = by_id(graph), set(chain), 0
    for nid in chain:
        n, p = nodes[nid], params(nodes[nid])
        if n["kind"] in ("period", "condition", "calc", "dedupe"):
            order = 1 if n["kind"] in ("period", "condition") else 2  # body는 조인 → 기간·조건 → 계산 순으로만 적용한다
            if order < stage:
                return "계산 단계 뒤에 기간·조건 단계가 있어 단계 카드(검색 body)로 바꿀 수 없어요. 흐름 보기에서만 쓰는 모양이에요."
            stage = max(stage, order)
        if n["kind"] == "join" and stage:
            return "조인 앞에 기간·조건·계산 단계가 있어 단계 카드(검색 body)로 바꿀 수 없어요. 흐름 보기에서만 쓰는 모양이에요."
        if n["kind"] == "table":
            if p.get("columns"):
                return "쓰는 컬럼만 정해 둔 테이블이 있어 단계 카드로 바꿀 수 없어요."
        if n["kind"] == "union":
            rest = inputs(graph, nid)
            if len(rest) < 2 or any(plain_table(graph, i) is None for i in rest):
                return "이어붙이기에는 테이블만 이어야 단계 카드로 바꿀 수 있어요."
            used |= set(rest)
        if n["kind"] == "join":
            right = join_sides(graph, nid)[1]
            if right is None:
                return f"{label(n)}의 오른쪽에 테이블이 이어지지 않았어요."
            if plain_table(graph, right) is None:
                return "조인 앞 가지에 조건·집계가 있어 단계 카드(검색 body)로 바꿀 수 없어요. 흐름 보기에서만 쓰는 모양이에요."
            used.add(right)
    others = [i for i in upstream(graph, target) if i not in used]
    if others:
        return "본 흐름에서 벗어난 가지가 있어 단계 카드로 바꿀 수 없어요."
    if len({params(nodes[i]).get("schema") or "" for i in used if nodes[i]["kind"] == "table"}) > 1:
        return "스키마가 다른 테이블이 섞여 있어 단계 카드로 바꿀 수 없어요."
    return None


def one_sink(graph):
    """끝 단계가 하나뿐이면 그 id, 아니면 ValueError."""
    found = sinks(graph)
    if len(found) != 1:
        raise ValueError("흐름의 끝이 " + ("없어요." if not found else f"{len(found)}곳이에요. 어느 단계까지 볼지 정하세요."))
    return found[0]


def join_line(rname, p):
    """조인 단계 params를 조인 줄('테이블 how 왼쪽=오른쪽, …')로."""
    how = p.get("how", "inner")
    parts = [f"{l}={r}" for l, r in p.get("keys") or []]
    if how == "asof" and p.get("on"):
        parts.insert(0, f"{p['on'][0]}>={p['on'][1]}")
    return f"{rname} {how} {', '.join(parts)}"


def period_line(p):
    return p["line"] if p.get("line") else f"{p['column']} {p.get('start', '')} ~ {p.get('end', '')}".replace("  ", " ")


def dedupe_line(p):
    """중복 제거 단계를 계산 줄로: 그룹마다 정렬 기준의 첫 행만(창거르기 row_number)."""
    keys = [k for k in p.get("keys") or [] if k]
    if not keys:
        raise ValueError("중복 제거에는 기준 컬럼이 하나 이상 있어야 해요.")
    return f"창거르기 row_number <= 1 기준 {p.get('order') or keys[0]} 그룹 {', '.join(keys)}"


def to_body(graph, target=None):
    """흐름(target 단계까지)을 검색 body로: {schema, base, union?, joins, periods, conditions, calcs, aggregate?, metric?, view?} + graph.extra(정렬·표시 컬럼 등).
    바꿀 수 없는 모양이면 이유를 담은 ValueError."""
    graph = check_shape(graph)
    why = body_problem(graph, target)
    if why:
        raise ValueError(why)
    target = target or one_sink(graph)
    nodes = by_id(graph)
    body = {"schema": "", "base": "", "joins": [], "periods": [], "conditions": [], "calcs": []}
    for nid in main_chain(graph, target):
        n, p = nodes[nid], params(nodes[nid])
        kind = n["kind"]
        if kind == "table":
            body.update(schema=p.get("schema") or "", base=p["table"])
        elif kind == "union":
            tables = [params(nodes[i])["table"] for i in inputs(graph, nid)]
            body.update(schema=params(nodes[inputs(graph, nid)[0]]).get("schema") or "", base=tables[0], union={"tables": tables[1:], "how": p.get("how", "left")})
        elif kind == "join":
            body["joins"].append(join_line(params(nodes[join_sides(graph, nid)[1]])["table"], p))
        elif kind == "period":
            body["periods"].append(period_line(p))
        elif kind == "condition":
            body["conditions"].append(p["cond"])
        elif kind == "calc":
            body["calcs"].append(p["line"])
        elif kind == "dedupe":
            body["calcs"].append(dedupe_line(p))
        elif kind == "aggregate":
            body["aggregate"] = {"by": list(p.get("by") or []), "series": _copy.deepcopy(p.get("series") or [])}
        elif kind == "metric":
            body["metric"] = {k: _copy.deepcopy(p[k]) for k in ("names", "by", "defs") if p.get(k)}
        else:
            body["view"] = {"kind": kind, **_copy.deepcopy(p)}
    return {**body, **_copy.deepcopy(graph.get("extra") or {})}


# ---------- body → graph ----------

def from_body(body):
    """검색 body를 흐름으로: 테이블 → (이어붙이기) → 조인들 → 기간들 → 조건들 → 계산들 → (집계·지표) → (차트·카드). 단계 id는 n1, n2 …
    정렬·표시 컬럼 같은 나머지 값은 graph.extra에 둔다."""
    graph, count = empty(), [0]
    known = {"schema", "base", "union", "joins", "periods", "conditions", "calcs", "aggregate", "metric", "view"}

    def add(kind, **p):
        count[0] += 1
        node = {"id": f"n{count[0]}", "kind": kind, "params": p, "pos": {"x": RANK[kind] * 180, "y": 40 + 90 * (count[0] % 3)}}
        graph["nodes"].append(node)
        return node["id"]

    def table(name):
        return add("table", table=name, **({"schema": body["schema"]} if body.get("schema") else {}))

    base = table(body["base"])
    last, union = base, body.get("union") or {}
    if union.get("tables"):
        last = add("union", how=union.get("how", "left"))
        graph["edges"] += [[base, last]] + [[table(t), last] for t in union["tables"]]
    for line in body.get("joins") or []:
        rname = line.partition(" ")[0]
        _, how, pairs, on = parse.parse_join(line, [rname])
        node = add("join", how=how, keys=[list(p) for p in pairs], **({"on": list(on)} if on else {}))
        graph["edges"] += [[last, node], [table(rname), node]]
        last = node
    for kind, key, name in (("period", "periods", "line"), ("condition", "conditions", "cond"), ("calc", "calcs", "line")):
        for item in body.get(key) or []:
            node = add(kind, **{name: _copy.deepcopy(item)})
            graph["edges"].append([last, node])
            last = node
    if body.get("aggregate"):
        node = add("aggregate", by=list(body["aggregate"].get("by") or []), series=_copy.deepcopy(body["aggregate"].get("series") or []))
        graph["edges"].append([last, node])
        last = node
    if body.get("metric"):
        node = add("metric", **_copy.deepcopy(body["metric"]))
        graph["edges"].append([last, node])
        last = node
    if body.get("view"):
        view = _copy.deepcopy(body["view"])
        node = add(view.pop("kind", "chart"), **view)
        graph["edges"].append([last, node])
    extra = {k: _copy.deepcopy(v) for k, v in body.items() if k not in known}
    if extra:
        graph["extra"] = extra
    return graph


# ---------- graph → Ibis 식 ----------

def table_name(graph, nid):
    """가지의 뿌리 테이블 이름(조인·이어붙이기 줄에 쓴다)."""
    root = root_table(graph, nid)
    if root is None:
        raise ValueError("테이블에서 시작하지 않는 가지가 있어요.")
    return params(root)["table"]


def to_expr(graph, nid, get_table, memo=None):
    """단계 nid까지의 Ibis 식. get_table(이름, 스키마)이 테이블 식을 준다. body로 바꿀 수 없는 흐름(조인 앞 가지의 조건·집계)도 만든다."""
    memo = {} if memo is None else memo
    if nid in memo:
        return memo[nid]
    node = by_id(graph)[nid]
    kind, p = node["kind"], params(node)
    ins = inputs(graph, nid)
    sub = lambda i: to_expr(graph, i, get_table, memo)
    if kind == "table":
        expr = get_table(p["table"], p.get("schema") or None)
        if p.get("columns"):
            expr = expr.select(*[c for c in p["columns"] if c in expr.columns])
    elif kind == "union":
        parts = {table_name(graph, i): sub(i) for i in ins}
        names = list(parts)
        expr = parse.apply_union(parts[names[0]], names[0], (names[1:], p.get("how", "left")), lambda name: parts[name])
    elif kind == "join":
        left, right = join_sides(graph, nid)
        rexpr = sub(right)
        expr = parse.apply_joins(sub(left), [(table_name(graph, right), p.get("how", "inner"), [tuple(k) for k in p.get("keys") or []], tuple(p["on"]) if p.get("on") else None)],
                                 lambda name: rexpr)
    elif not ins:
        raise ValueError(f"{label(node)} 앞에 이어진 단계가 없어요.")
    elif kind == "period":
        expr = sub(ins[0])
        preds = parse.parse_period(period_line(p), expr)
        expr = expr.filter(preds) if preds else expr
    elif kind == "condition":
        expr = sub(ins[0])
        expr = expr.filter(parse.predicate(p["cond"], expr))
    elif kind in ("calc", "dedupe"):
        expr = calc.apply([p["line"] if kind == "calc" else dedupe_line(p)], sub(ins[0]))
    elif kind == "aggregate":
        expr = aggregate_expr(sub(ins[0]), p)
    elif kind == "metric":
        expr = metric_expr(sub(ins[0]), p)
    else:  # chart·card는 앞 단계 그대로
        expr = sub(ins[0])
    memo[nid] = expr
    return expr


def aggregate_expr(expr, p):
    """집계 단계: by별로 series의 집계들(sql_funcs.aggregate). by가 없으면 전체 한 줄."""
    series = p.get("series") or []
    if not series:
        raise ValueError("집계에는 계열이 하나 이상 있어야 해요.")
    made = {}
    for i, s in enumerate(series):
        if s.get("ratio"):  # 먼저 집계한 조각으로 평균을 되살린다: 합의 합 ÷ 개수의 합
            top, bottom = [sql_funcs.aggregate(x.get("agg", "sum"), expr, x.get("y") or None, None, x.get("opts")) for x in s["ratio"]]
            made[s.get("name") or f"v{i}"] = top.cast("float64") / bottom.nullif(0).cast("float64")
            continue
        where = parse.predicate(s["where"], expr) if s.get("where") else None
        made[s.get("name") or f"v{i}"] = sql_funcs.aggregate(s.get("agg", "count"), expr, s.get("y") or None, where, s.get("opts"))
    by = [parse.column(expr, c) for c in p.get("by") or [] if c]
    return expr.group_by(by).aggregate(**made) if by else expr.aggregate(**made)


def metric_expr(expr, p):
    """지표 단계: metrics.py 모양의 정의(defs) 또는 지표 파일의 이름(names)을 by별로 한 번에."""
    from core import metric_file
    found = {n: metric_file.metric(n, m) for n, m in (p.get("defs") or {}).items()} or metric_file.registry()
    made = {n: metric_file.metric_expr(expr, n, found[n]) for n in p.get("names") or []}
    if not made:
        raise ValueError("구할 지표를 고르세요.")
    by = [parse.column(expr, c) for c in p.get("by") or [] if c]
    return expr.group_by(by).aggregate(**made) if by else expr.aggregate(**made)


def schemas_of(graph, get_table, source_key=None):
    """흐름의 테이블 단계마다 {테이블: {columns: {컬럼: 종류}, source_key}}. get_table(이름, 스키마)로 스키마만 읽는다(데이터는 읽지 않는다)."""
    out = {}
    for n in graph["nodes"]:
        if n["kind"] == "table" and params(n).get("table") and params(n)["table"] not in out:
            t = get_table(params(n)["table"], params(n).get("schema") or None)
            out[params(n)["table"]] = {"columns": {c: sql_funcs.kind_of(ty) for c, ty in t.schema().items()}, "source_key": params(n).get("source_key") or source_key}
    return out


# ---------- 단계별 행 수 ----------

def row_counts(graph, target, count_expr, runnable=None, get_table=None, remote=False, confirmed=False):
    """[단계별 행 수 세기]를 눌렀을 때만: target까지의 본 흐름(집계 앞)에서 단계마다 그 단계까지의 식의 행 수.
    count_expr(식) → 행 수(연결 스레드에서 센다). runnable(body)가 있으면 body로 바꿀 수 있는 흐름은 그것으로 식을 만든다(연결이 못 하는 계산은 PC 사본).
    원격 DB는 조회 요금이 들 수 있어 confirmed가 아니면 세지 않고 needs_confirm을 돌려준다.
    [{id, label, rows}] 와 {needs_confirm, note}."""
    graph = check_shape(graph)
    nodes = by_id(graph)
    ids = [i for i in all_steps(graph, target) if nodes[i]["kind"] not in (*AFTER_AGG, "chart", "card")]
    if remote and not confirmed:
        return {"steps": [{"id": i, "label": label(nodes[i]), "rows": None} for i in ids], "needs_confirm": True,
                "note": "원격 DB라 단계마다 행 수를 세면 조회가 여러 번 나가요. 요금이 들 수 있어서 확인을 받은 뒤에 세요."}
    steps = []
    for i in ids:
        if runnable is not None and body_problem(graph, i) is None:
            expr = runnable(to_body(graph, i))
        else:
            expr = to_expr(graph, i, get_table)
        steps.append({"id": i, "label": label(nodes[i]), "rows": count_expr(expr)})
    return {"steps": steps, "needs_confirm": False, "note": ""}


def all_steps(graph, target):
    """target까지의 모든 앞 단계(본 흐름 순서, 가지 포함: 앞쪽 가지 먼저)."""
    order, seen = [], set()

    def visit(i):
        if i in seen:
            return
        seen.add(i)
        for j in inputs(graph, i):
            visit(j)
        order.append(i)
    visit(target)
    return order
