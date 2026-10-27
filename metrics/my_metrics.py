"""내 지표(6-5): 지표 틀(sentences.METRICS, scope 없음)에 빈칸을 채우고 테이블에 묶어 둔 것. 정의만 설정 my_metrics.json에 평문으로 둔다(값·계정·비밀번호 없음).
지표 = {id, name, scope: {key, label, schema, table}(예전 흐름 보기의 지표는 table 없이 flow_id: 남겨 두지만 FLOW_GONE으로 쓸 수 없다), template, slots, deps, def_hash, unit, better, cols, align?, created, updated}. template은 지표 틀 id 또는 지표 사전 항목('catalog:id', metric_catalog).
- scope 안에서 이름이 겹칠 수 없다(다른 scope는 같은 이름 가능, 화면은 '이름 · 테이블 (연결)').
- 다른 scope의 지표는 쓸 수 없다. [이 테이블에 맞춰 복사]로 컬럼 짝을 골라 새 id로 복사한다(copy_to). 예외: align(‘{기간 단위}로 맞춰’ 또는 ‘{공통 키}로 맞춰’)을 넣은 조합.
- 의존(deps)은 순환할 수 없다(DFS). def_hash는 자기 정의 + 의존하는 지표의 def_hash를 합쳐 만들어, 의존 지표의 정의가 바뀌면 쓰는 쪽도 바뀐다.
  정의를 고치면 쓰는 지표·카드·목표를 알리고(update), 카드가 저장해 둔 def_hash와 다르면 '정의가 바뀌었어요'(stale).
- cols는 만들 때 쓴 컬럼의 종류 기록이다. 테이블 구조가 바뀌어(컬럼 없음·타입 바뀜) 맞지 않으면 status가 '쓸 수 없음 + 이유'를 준다.
- 예전 metrics.py 파일의 지표는 scope 없는 '공통 정의'(legacy_list)이고, 지표 목록에 '(지표 파일)'로 보이고 이름으로 쓴다.
- 데이터를 읽지 않는다."""
import json
import re
import secrets
import threading

from core import dash_store
from query import flow
from metrics import metric_catalog
from core import metric_file
from query import sentence_run
from query import sentences
from core import settings
from core import sql_funcs
from core import use_counts

FILE = "my_metrics.json"
MAX_METRICS = 500
MAX_NAME = 100
BETTER = ("", "up", "down")
LEGACY = "legacy"  # metrics.py 파일 지표를 묶은 지표의 틀 이름
ALIGN_UNITS = metric_catalog.UNITS  # 지표 사전 계산이 받는 단위와 같다(year는 계산 때 거부되므로 저장도 막는다)
SCOPE_TEXT = "{name} · {table} ({label})"
FLOW_GONE = "흐름 보기가 없어져 이 지표는 다시 구할 수 없어요. 탐색에서 다시 만들어 주세요."  # 예전 흐름(scope.flow_id)에 묶은 지표. 저장한 정의는 지우지 않는다
ORDER_WORDS = re.compile(r"^\s*([^\s,]+)")
LOCK = threading.RLock()  # 저장 파일을 읽고 고쳐 쓰는 일(만들기·고치기·지우기·복사)은 한 번에 하나씩


# ---------- 저장 ----------

def load():
    """저장된 내 지표 목록(없거나 깨졌으면 빈 목록)."""
    found = (settings.read(FILE) or {}).get("metrics")
    return [m for m in found if isinstance(m, dict) and m.get("id")] if isinstance(found, list) else []


def save(items):
    if len(items) > MAX_METRICS:
        raise ValueError(f"내 지표는 {MAX_METRICS}개까지 저장해요.")
    settings.write(FILE, {"version": 1, "metrics": items})


def get(metric_id, items=None):
    """id의 지표. 없으면 ValueError."""
    for m in items if items is not None else load():
        if m["id"] == metric_id:
            return m
    raise ValueError("그 지표를 찾지 못했어요. 지워졌거나 다른 PC에서 만든 지표일 수 있어요.")


# ---------- scope ----------

def scope_id(scope):
    """scope를 알아보는 글: 연결 열쇠 + 스키마 + 테이블(또는 흐름 id)."""
    s = scope or {}
    return json.dumps([s.get("key", ""), s.get("schema") or "", s.get("table") or "", s.get("flow_id") or ""], ensure_ascii=False)


def same_scope(a, b):
    """두 scope가 같은 테이블인지. 연결 열쇠는 예전 열쇠(폴더만·query 없던 Databricks·Athena)도 같은 연결이면 같다고 본다(use_counts.same_source, 화면 inScope와 같은 기준)."""
    ka, kb = (a or {}).get("key", ""), (b or {}).get("key", "")
    keys = ka == kb or (isinstance(ka, str) and isinstance(kb, str) and ka and kb and (use_counts.same_source(ka, kb) or use_counts.same_source(kb, ka)))
    return bool(keys) and scope_id({**(a or {}), "key": ""}) == scope_id({**(b or {}), "key": ""})


def check_scope(scope):
    """scope 모양 검사(정리한 사본). 연결 key와 table이 있어야 한다(table 없이 flow_id만 있으면 FLOW_GONE). 계정·비밀번호 항목은 거부한다."""
    if not isinstance(scope, dict) or not isinstance(scope.get("key"), str) or not scope["key"]:
        raise ValueError("지표가 속한 연결(scope.key)이 필요해요.")
    if not scope.get("table"):
        raise ValueError(FLOW_GONE if scope.get("flow_id") else "지표가 속한 테이블(scope.table)이 필요해요.")
    dash_store.no_secrets(scope, "지표의 scope")
    return {"key": scope["key"], "label": str(scope.get("label") or ""), "schema": str(scope.get("schema") or ""),
            **({"table": str(scope["table"])} if scope.get("table") else {}), **({"flow_id": str(scope["flow_id"])} if scope.get("flow_id") else {})}


def is_flow_only(metric):
    """예전 흐름 보기에 묶여 테이블이 없는 지표인지."""
    s = metric.get("scope") or {}
    return bool(s.get("flow_id")) and not s.get("table")


def scope_text(metric):
    """화면에 보일 이름: '이름 · 테이블 (연결)'."""
    s = metric.get("scope") or {}
    return SCOPE_TEXT.format(name=metric["name"], table=s.get("table") or s.get("flow_id") or "공통", label=s.get("label") or "")


def check_align(align):
    """‘{기간 단위}로 맞춰’({kind: period, unit}) 또는 ‘{공통 키}로 맞춰’({kind: key, keys: [[a, b]]}) 구절 검사. 없으면 None."""
    if not align:
        return None
    if not isinstance(align, dict) or align.get("kind") not in ("period", "key"):
        raise ValueError("맞추는 구절은 {kind: period, unit} 또는 {kind: key, keys: [[A 컬럼, B 컬럼]]} 모양이어야 해요.")
    if align["kind"] == "period":
        if align.get("unit") not in ALIGN_UNITS:
            raise ValueError(f"기간 단위는 {', '.join(ALIGN_UNITS)} 중 하나예요.")
        return {"kind": "period", "unit": align["unit"]}
    keys = align.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, (list, tuple)) and len(k) == 2 and all(isinstance(x, str) and x for x in k) for k in keys):
        raise ValueError("공통 키는 [[A 컬럼, B 컬럼], …] 모양으로 하나 이상 넣어 주세요.")
    return {"kind": "key", "keys": [list(k) for k in keys]}


# ---------- 빈칸 ----------

def order_column(text, columns=None):
    """정렬 글('컬럼 [asc|desc]')의 컬럼 이름(columns를 주면 공백 있는 이름도). 컬럼이 없는 글이면 None."""
    found = ORDER_WORDS.match(text) if isinstance(text, str) else None
    return (flow.head_name(text, columns) or found.group(1)) if found else None


def columns_in_text(text, columns=None):
    """조건 글이 가리키는 컬럼 이름들(columns를 주면 공백 있는 이름도)."""
    return flow.cond_columns(text, columns) if isinstance(text, str) else set()


def check_slot(slot, raw, columns, items):
    """빈칸 하나의 값을 검사해 정리한다. 이유가 있으면 ValueError(빈칸 이름 포함). columns = {컬럼: 종류}(모르면 None), items = 지금 저장된 지표들."""
    where = f"‘{slot['name']}’ 칸"
    kind = slot["type"]
    if raw in (None, ""):
        if slot.get("optional"):
            return None
        if slot.get("default") is not None:
            return slot["default"]
        raise ValueError(f"{where}을 채워 주세요.")
    if kind in ("column", "columns"):
        names = [raw] if kind == "column" else raw
        if kind == "columns" and not isinstance(raw, list):
            raise ValueError(f"{where}에는 컬럼 목록을 넣어 주세요.")
        for item in names:
            name = sentence_run.name_of(item)  # 컬럼 이름 또는 {column, prep}(가공은 문장 실행 때 계산 줄로 바뀐다)
            if not isinstance(name, str) or not name:
                raise ValueError(f"{where}에는 컬럼 이름을 넣어 주세요.")
            if isinstance(item, dict) and item.get("prep") not in (None, *sentence_run.PREPS):
                raise ValueError(f"{where}: 컬럼 가공은 {', '.join(sentence_run.PREPS.values())} 중 하나예요.")
            if columns is not None:
                if name not in columns:
                    raise ValueError(f"{where}: ‘{name}’ 컬럼이 이 테이블에 없어요.")
                if not sentence_run.fits(slot.get("kinds"), columns[name]):
                    raise ValueError(f"{where}: ‘{name}’은 {flow.kind_text(columns[name])} 컬럼이라 쓸 수 없어요({sql_funcs.kinds_text(slot['kinds'])} 컬럼이 필요해요).")
        return raw
    if kind in ("cond", "order", "text"):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{where}에는 글을 넣어 주세요.")
        if columns is not None and kind in ("cond", "order"):
            found = columns_in_text(raw, columns) if kind == "cond" else {order_column(raw, columns)}
            if None in found:
                raise ValueError(f"{where}에는 ‘컬럼 [asc|desc]’ 모양으로 넣어 주세요.")
            missing = sorted(c for c in found if c not in columns)
            if missing:
                raise ValueError(f"{where}: ‘{missing[0]}’ 컬럼이 이 테이블에 없어요.")
        return raw.strip()
    if kind == "agg":
        name = raw.get("agg") if isinstance(raw, dict) else raw  # 집계 이름 또는 {agg, opts}
        if not isinstance(name, str) or (isinstance(raw, dict) and raw.get("opts") is not None and not isinstance(raw["opts"], dict)):
            raise ValueError(f"{where}에는 집계 이름을 넣어 주세요.")
        if not sql_funcs.get(name)["aggregate"]:  # 모르는 집계면 비슷한 이름을 알려 주는 ValueError
            raise ValueError(f"{where}: ‘{name}’은(는) 집계 함수가 아니에요.")
        name = sql_funcs.ALIASES.get(name, name)
        return {**raw, "agg": name} if isinstance(raw, dict) else name
    if kind == "n":
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ValueError(f"{where}에는 1 이상의 정수를 넣어 주세요.")
        return raw
    if kind == "unit":
        if raw not in slot["options"]:
            raise ValueError(f"{where}은 {', '.join(slot['options'])} 중 하나예요.")
        return raw
    if kind == "metric":
        get(raw, items)
        return raw
    if kind in ("value", "number"):
        if isinstance(raw, (dict, list)) or isinstance(raw, bool):
            raise ValueError(f"{where}에는 글자나 숫자 하나를 넣어 주세요.")
        return raw
    return raw


def used_columns(template, slots, columns=None):
    """빈칸 값에서 컬럼 이름들(컬럼 칸 + 조건·정렬 글의 첫 낱말)."""
    found = set()
    for s in template["slots"]:
        v = slots.get(s["name"])
        if s["type"] == "column" and isinstance(sentence_run.name_of(v), str):
            found.add(sentence_run.name_of(v))
        elif s["type"] == "columns" and isinstance(v, list):
            found |= {sentence_run.name_of(x) for x in v if isinstance(sentence_run.name_of(x), str)}
        elif s["type"] == "cond":
            found |= columns_in_text(v, columns)
        elif s["type"] == "order" and order_column(v):
            found.add(order_column(v, columns))
    return found


def deps_of(template, slots):
    """빈칸에 든 지표 id들(의존)."""
    return [slots[s["name"]] for s in template["slots"] if s["type"] == "metric" and slots.get(s["name"])]


# ---------- 의존·지문 ----------

def find_cycle(items, start, deps):
    """start 지표가 deps를 의존하게 하면 순환이 생기는지: 순환하는 id 목록(없으면 None). items는 저장된 지표들."""
    graph = {m["id"]: list(m.get("deps") or []) for m in items}
    graph[start] = list(deps)
    stack = [(d, [start, d]) for d in deps]
    while stack:
        now, path = stack.pop()
        if now == start:
            return path
        for nxt in graph.get(now, []):
            if nxt not in path or nxt == start:
                stack.append((nxt, [*path, nxt]))
    return None


def effective_hash(metric, items, seen=()):
    """자기 정의 + 의존 지표의 def_hash를 합친 지문(의존 지표의 정의가 바뀌면 달라진다)."""
    if metric["id"] in seen:
        return ""
    by = {m["id"]: m for m in items}
    parts = {d: effective_hash(by[d], items, (*seen, metric["id"])) if d in by else "missing" for d in metric.get("deps") or []}
    return dash_store.def_hash({"template": metric["template"], "slots": metric["slots"], "align": metric.get("align"), "scope": scope_id(metric.get("scope")), "deps": parts})


def dependents(metric_id, items=None):
    """metric_id를 (바로 또는 건너서) 의존하는 지표들."""
    items = load() if items is None else items
    found, queue = {}, [metric_id]
    while queue:
        now = queue.pop()
        for m in items:
            if now in (m.get("deps") or []) and m["id"] not in found:
                found[m["id"]] = m
                queue.append(m["id"])
    return list(found.values())


def refresh_hashes(items):
    """모든 지표의 def_hash를 다시 만든다. 바뀐 지표 id들을 돌려준다."""
    changed = []
    for m in items:
        new = effective_hash(m, items)
        if m.get("def_hash") != new:
            m["def_hash"] = new
            changed.append(m["id"])
    return changed


# ---------- 만들기·고치기 ----------

def build(defn, columns, items, metric_id=None):
    """정의 defn({name, scope, template, slots, unit?, better?, align?})을 검사해 저장할 모양으로(id·시각은 부르는 쪽이). columns = {컬럼: 종류}(모르면 None)."""
    if not isinstance(defn, dict):
        raise ValueError("지표 정의는 객체여야 해요.")
    name = str(defn.get("name") or "").strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise ValueError(f"지표 이름은 1~{MAX_NAME}자로 정해 주세요.")
    scope = check_scope(defn.get("scope"))
    same = [m for m in items if m["id"] != metric_id and same_scope(m.get("scope"), scope) and m["name"] == name]
    if same:
        raise ValueError(f"이 테이블에 ‘{name}’ 지표가 이미 있어요. 같은 곳에서는 이름이 겹칠 수 없어요.")
    tid = defn.get("template")
    try:
        template = template_of(tid) if isinstance(tid, str) else None
    except ValueError:
        template = None
    if template is None or template["kind"] != "metric":
        raise ValueError(f"지표 틀을 골라 주세요{f'(‘{tid}’ 틀은 지표 틀이 아니에요)' if tid else ''}.")
    given = defn.get("slots") if isinstance(defn.get("slots"), dict) else {}
    unknown = set(given) - {s["name"] for s in template["slots"]}
    if unknown:
        raise ValueError(f"틀에 없는 칸이에요: {', '.join(sorted(unknown))}")
    slots = {}
    for s in template["slots"]:
        value = check_slot(s, given.get(s["name"]), columns, items)
        if value is not None:
            slots[s["name"]] = value
    used = used_columns(template, slots, columns)
    agg, value = slots.get("집계"), sentence_run.name_of(slots.get("값"))
    agg = sentence_run.agg_of(agg)[0] if agg else None
    if columns and agg and value in columns and sql_funcs.get(agg)["kind_in"] == "num" and columns[value] != "num":
        raise ValueError(f"‘{value}’은 {flow.kind_text(columns[value])} 컬럼이라 {sql_funcs.label_of(agg)}을(를) 구할 수 없어요. 숫자 컬럼을 고르거나 개수·고유 개수를 쓰세요.")
    deps = list(dict.fromkeys(deps_of(template, slots)))
    cycle = find_cycle(items, metric_id or "", deps) if deps else None
    if cycle:
        by = {m["id"]: m["name"] for m in items}
        raise ValueError("지표가 서로를 계산에 써서 순환해요: " + " → ".join(by.get(i, name) for i in cycle) + ".")
    align = check_align(defn.get("align"))
    foreign = [m for m in items if m["id"] in deps and not same_scope(m.get("scope"), scope)]
    if foreign and not align:
        raise ValueError(f"‘{foreign[0]['name']}’은(는) 다른 테이블의 지표라 그대로 쓸 수 없어요. [이 테이블에 맞춰 복사]하거나 "
                         f"‘기간 단위로 맞춰’ 또는 ‘공통 키로 맞춰’ 구절을 넣어 주세요.")
    if not 0 <= len(str(defn.get("unit") or "")) <= 20:
        raise ValueError("단위는 20자까지예요.")
    if defn.get("better", "") not in BETTER:
        raise ValueError("좋은 방향은 up(클수록)·down(작을수록) 또는 비워 두세요.")
    cols = {c: columns[c] for c in sorted(used) if columns and c in columns}
    dash_store.no_secrets(slots, "지표의 빈칸")
    return {"name": name, "scope": scope, "template": tid, "slots": slots, "deps": deps, "unit": str(defn.get("unit") or ""), "better": defn.get("better", ""),
            "cols": cols, **({"align": align} if align else {})}


def locked(work):
    """저장 파일을 읽고 고쳐 쓰는 일이 겹쳐 서로의 변경을 덮어쓰지 않게 LOCK을 잡고 한다."""
    def inner(*args, **kwargs):
        with LOCK:
            return work(*args, **kwargs)
    inner.__name__, inner.__doc__ = work.__name__, work.__doc__
    return inner


def template_of(tid):
    """지표 틀(sentences.METRICS) 또는 지표 사전 항목('catalog:id')의 틀. 없으면 ValueError."""
    return metric_catalog.template(tid) if metric_catalog.is_catalog(tid) else sentences.get(tid)


@locked
def create(defn, columns=None):
    """내 지표를 만든다. 저장한 지표를 돌려준다."""
    items = load()
    made = build(defn, columns, items)
    metric = {"id": secrets.token_hex(8), **made, "def_hash": "", "created": dash_store.now_text(), "updated": dash_store.now_text()}
    items.append(metric)
    refresh_hashes(items)
    save(items)
    return get(metric["id"], items)


@locked
def update(metric_id, patch, columns=None):
    """지표를 고친다(이름·scope·틀·빈칸·단위·좋은 방향·맞춤 구절). {metric, changed(정의가 바뀌었는지), dependents(영향받는 지표들), users(쓰는 카드·목표)}."""
    items = load()
    old = get(metric_id, items)
    base = {k: old.get(k) for k in ("name", "scope", "template", "slots", "unit", "better", "align")}
    made = build({**base, **(patch or {})}, columns, items, metric_id)
    if scope_id(made["scope"]) != scope_id(old.get("scope")):  # 테이블을 옮기면 이 지표를 쓰던 지표가 남의 테이블 지표를 쓰게 되니 거부한다
        users = [m for m in items if metric_id in (m.get("deps") or []) and not m.get("align") and not same_scope(m.get("scope"), made["scope"])]
        if users:
            raise ValueError(f"‘{old['name']}’을(를) 쓰는 지표({', '.join(m['name'] for m in users[:5])})가 있어 다른 테이블로 옮길 수 없어요. 쓰는 지표를 먼저 바꾸세요.")
    before = old.get("def_hash")
    old.pop("align", None)
    old.update(made, updated=dash_store.now_text())
    refresh_hashes(items)
    save(items)
    now = get(metric_id, items)
    deps = dependents(metric_id, items)
    return {"metric": now, "changed": now["def_hash"] != before, "dependents": [{"id": m["id"], "name": scope_text(m)} for m in deps], "users": used_by(metric_id)}


@locked
def remove(metric_id):
    """지표를 지운다. 다른 지표가 쓰고 있으면 거부한다."""
    items = load()
    metric = get(metric_id, items)
    using = dependents(metric_id, items)
    if using:
        raise ValueError(f"‘{metric['name']}’을(를) 쓰는 지표가 있어 지울 수 없어요: " + ", ".join(m["name"] for m in using[:5]))
    save([m for m in items if m["id"] != metric_id])
    return {"removed": metric_id, "users": used_by(metric_id)}


# ---------- 복사 ----------

def rename_in(text, mapping):
    """조건·정렬 글 안의 컬럼 이름을 짝(mapping)대로 바꾼다. 긴 이름부터(공백 있는 'order date'도), 낱말 경계에서만, 따옴표 안의 값은 그대로."""
    names = sorted((k for k in mapping if k), key=len, reverse=True)
    if not names:
        return text
    found = re.compile(r"""(?<![^\s,=<>])('.*?'(?![^\s,])|".*?"(?![^\s,]))|(?<![\w.])(""" + "|".join(map(re.escape, names)) + r")(?![\w.])")
    return found.sub(lambda m: m.group(1) or mapping[m.group(2)], text)


def rename_column(value, mapping):
    """컬럼 빈칸 값(이름 또는 {column, prep})의 컬럼을 짝(mapping)대로 바꾼다."""
    if isinstance(value, dict):
        return {**value, "column": mapping.get(value.get("column"), value.get("column"))}
    return mapping.get(value, value)


def copy_chain(metric_id, items):
    """metric_id와 그것이 (건너서) 의존하는 지표들을 의존이 먼저 오게 한 목록."""
    order, seen = [], set()

    def visit(i):
        if i in seen:
            return
        seen.add(i)
        m = get(i, items)
        for d in m.get("deps") or []:
            visit(d)
        order.append(m)
    visit(metric_id)
    return order


@locked
def copy_to(metric_id, scope, mapping=None, columns=None, name=None):
    """[이 테이블에 맞춰 복사]: 지표(와 의존하는 지표들)를 다른 scope로 새 id로 복사한다. mapping = {원래 컬럼: 새 컬럼}(같은 이름 컬럼이 새 테이블에 있으면 생략 가능).
    새 테이블에 없는 컬럼이 남으면 짝을 골라 달라고 알린다. {metrics: [새 지표들], id_map: {원래 id: 새 id}}."""
    items = load()
    scope = check_scope(scope)
    mapping = dict(mapping or {})
    chain = copy_chain(metric_id, items)
    unmapped = sorted({c for m in chain for c in (m.get("cols") or {}) if mapping.get(c, c) not in (columns or {}) and columns is not None})
    if unmapped:
        raise ValueError("새 테이블에서 컬럼 짝을 골라 주세요: " + ", ".join(unmapped))
    whole = {**{c: c for m in chain for c in (m.get("cols") or {})}, **mapping}  # 글 바꾸기용: 그대로 둘 긴 이름('order date')이 먼저 맞아 짧은 짝('date')이 그 조각을 바꾸지 않게
    id_map, out = {}, []
    for m in chain:
        tid = m["template"]
        slots = json.loads(json.dumps(m["slots"]))
        template = template_of(tid)
        for s in template["slots"]:
            v = slots.get(s["name"])
            if s["type"] == "column" and v:
                slots[s["name"]] = rename_column(v, mapping)
            elif s["type"] == "columns" and isinstance(v, list):
                slots[s["name"]] = [rename_column(x, mapping) for x in v]
            elif s["type"] in ("cond", "order") and isinstance(v, str):
                slots[s["name"]] = rename_in(v, whole)
            elif s["type"] == "metric" and v in id_map:
                slots[s["name"]] = id_map[v]
        pool = [*items, *out]
        title = (name if m["id"] == metric_id and name else m["name"])
        if any(x for x in pool if same_scope(x.get("scope"), scope) and x["name"] == title):
            title += " (복사)"
        made = build({"name": title, "scope": scope, "template": tid, "slots": slots, "unit": m.get("unit"), "better": m.get("better")}, columns, pool)
        new = {"id": secrets.token_hex(8), **made, "def_hash": "", "created": dash_store.now_text(), "updated": dash_store.now_text(), "copied_from": m["id"]}
        id_map[m["id"]] = new["id"]
        out.append(new)
    items += out
    refresh_hashes(items)
    save(items)
    return {"metrics": [get(m["id"], items) for m in out], "id_map": id_map}


# ---------- 쓸 수 있는지 ----------

def status(metric, columns, items=None, seen=()):
    """테이블 구조가 바뀌었는지 본 지표의 상태 {usable, reason}. columns = 지금 테이블의 {컬럼: 종류}. 컬럼이 없어졌거나 종류가 바뀌었거나 의존하는 지표를 쓸 수 없으면 쓸 수 없음 + 이유."""
    items = load() if items is None else items
    for c, k in (metric.get("cols") or {}).items():
        if c not in columns:
            return {"usable": False, "reason": f"‘{c}’ 컬럼이 테이블에서 없어졌어요."}
        if columns[c] != k:
            return {"usable": False, "reason": f"‘{c}’의 타입이 {flow.kind_text(k)}에서 {flow.kind_text(columns[c])}(으)로 바뀌었어요."}
    by = {m["id"]: m for m in items}
    for d in metric.get("deps") or []:
        if d in seen:
            continue
        dep = by.get(d)
        if dep is None:
            return {"usable": False, "reason": "의존하는 지표가 지워졌어요."}
        if same_scope(dep.get("scope"), metric.get("scope")):
            inner = status(dep, columns, items, (*seen, metric["id"]))
            if not inner["usable"]:
                return {"usable": False, "reason": f"의존하는 지표 ‘{dep['name']}’을(를) 쓸 수 없어요({inner['reason'].rstrip('.')})."}
    return {"usable": True, "reason": ""}


def is_stale(metric, saved_hash):
    """카드·목표가 저장해 둔 def_hash가 지금과 다르면 True('정의가 바뀌었어요')."""
    return bool(saved_hash) and saved_hash != metric.get("def_hash")


def used_by(metric_id, dashboards=None):
    """정의를 고칠 때 보일 쓰는 곳: 저장한 대시보드의 카드(JSON에 metric_id가 든 카드; stale = 카드가 저장한 def_hash가 지금과 다름).
    dashboards = [대시보드](없으면 dash_store에서 읽는다, 풀 수 없으면 빈 목록)."""
    items = load()
    try:
        metric = get(metric_id, items)
    except ValueError:
        metric = None
    if dashboards is None:
        try:
            dashboards = [dash_store.get_dashboard(d["id"]) for d in dash_store.listing().get("dashboards", [])]
        except Exception:
            dashboards = []
    cards = []
    for d in dashboards:
        for c in d.get("cards") or []:
            if f'"{metric_id}"' in json.dumps(c, ensure_ascii=False):
                saved = (c.get("data") or {}).get("def_hash") or (c.get("source") or {}).get("def_hash")
                cards.append({"dashboard": d.get("id"), "dashboard_name": d.get("name"), "card": c.get("id"), "title": c.get("title", ""),
                              "stale": bool(metric and is_stale(metric, saved))})
    return {"cards": cards}


# ---------- 예전 지표 파일의 공통 정의 ----------

def legacy_list():
    """metrics.py 지표 파일의 지표들을 scope 없는 '공통 정의'로: [{id: legacy:이름, name, scope: None, text, legacy: True}]. 파일이 없거나 읽을 수 없으면 빈 목록."""
    try:
        found = metric_file.registry()
    except ValueError:
        return []
    return [{"id": f"{LEGACY}:{name}", "name": name, "scope": None, "legacy": True, "text": metric_file.describe(m), "description": m["description"], "format": m["format"]}
            for name, m in found.items()]
