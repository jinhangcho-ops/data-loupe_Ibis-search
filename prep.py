"""전처리: recipe(테이블, 행 거르기, 컬럼 복사, 컬럼별 일, 파생 컬럼 빼기, 중복 제거, downcast, 정렬)를 Ibis 식 하나로 만든다.
컬럼 하나에 하는 일은 prep_ops.py의 OPS 표에 있다."""
from lazy import ibis

import parse
import prep_ops
import source
from prep_ops import stats

INT_TYPES = [("int8", -(2**7), 2**7 - 1), ("int16", -(2**15), 2**15 - 1), ("int32", -(2**31), 2**31 - 1), ("int64", -(2**63), 2**63 - 1)]


def check_spec(name, spec):
    if not isinstance(spec, dict) or not set(spec) <= {"keep", "rename", "ops"}:
        raise ValueError(f"'{name}' 컬럼의 설정은 keep, rename, ops만 쓸 수 있습니다.")
    if not isinstance(spec.get("keep", True), bool) or not isinstance(spec.get("rename", ""), str) or not isinstance(spec.get("ops", {}), dict):
        raise ValueError(f"'{name}' 컬럼의 keep은 true/false, rename은 글자, ops는 객체여야 합니다.")


def names_of(recipe, key):
    names = recipe.get(key) or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError(f"'{key}'은 컬럼 이름 목록이어야 합니다.")
    return names


def copy_columns(t, copies):
    """copies의 컬럼을 원본 값으로 복사해 from 바로 뒤(같은 컬럼의 복사는 적은 순서)에 놓는다."""
    order, base = list(t.columns), t.columns
    for i, copy in enumerate(copies):
        if not isinstance(copy, dict) or set(copy) != {"from", "name"} or not isinstance(copy["name"], str):
            raise ValueError('copies의 각 항목은 {"from": "원래 컬럼", "name": "새 이름"} 형식이어야 합니다.')
        parse.check_name(copy["from"], base)
        if not copy["name"] or copy["name"] in order:
            raise ValueError(f"복사한 컬럼의 이름 '{copy['name']}'이 비었거나 이미 있습니다.")
        order.insert(order.index(copy["from"]) + 1 + sum(c["from"] == copy["from"] for c in copies[:i]), copy["name"])
        t = t.mutate(**{copy["name"]: t[copy["from"]]})
    return t.select(order)


def downcast(t):
    """정수 컬럼은 값이 들어가는 가장 작은 타입으로, float64 컬럼은 float32로 바꿔도 값이 모두 같을 때만 float32로. 집계 한 번으로 확인한다."""
    ints = [n for n, d in t.schema().items() if d.is_integer()]
    floats = [n for n, d in t.schema().items() if d.is_float64()]
    metrics = {f"{kind}{i}": expr for i, n in enumerate(ints) for kind, expr in (("lo", t[n].min()), ("hi", t[n].max()))}
    metrics.update({f"diff{i}": (t[n] != t[n].cast("float32").cast("float64")).sum() for i, n in enumerate(floats)})
    if not metrics:
        return t
    found = stats(t, **metrics)
    casts = {}
    for i, n in enumerate(ints):
        lo, hi = found[f"lo{i}"], found[f"hi{i}"]
        if lo is not None:
            small = next(name for name, low, high in INT_TYPES if low <= lo and hi <= high)
            if ibis.dtype(small).nbytes < t[n].type().nbytes:
                casts[n] = t[n].cast(small)
    casts.update({n: t[n].cast("float32") for i, n in enumerate(floats) if found[f"diff{i}"] == 0})  # 전부 빈 값이면 합이 비어 그대로 둔다.
    return t.mutate(**casts) if casts else t


def shape(con, recipe, table=None):
    """filters → copies → 컬럼별 ops → keep·파생 컬럼 배치·rename → drop까지 만든 식과, 결과 컬럼마다 (recipe상 컬럼 이름, 파생 컬럼인지).
    table을 주면 연결의 테이블 대신 그 식(예: 표본을 담은 메모리 표)에서 시작한다."""
    if table is None:
        schema = recipe.get("schema") or None
        parse.check_name(recipe["base"], source.list_tables(con, schema), "테이블")
        table = source.get_table(con, recipe["base"], schema)
    t = table
    filters = recipe.get("filters") or []
    if filters:
        t = t.filter(*[parse.predicate(cond, t) for cond in filters])
    t = copy_columns(t, recipe.get("copies") or [])
    specs = recipe.get("columns", {})
    for name, spec in specs.items():
        parse.check_name(name, t.columns)
        check_spec(name, spec)
    made = {}  # 파생 컬럼 → 그것을 만든 컬럼
    for name in t.columns:
        before = set(t.columns)
        t = prep_ops.apply_ops(t, name, specs.get(name, {}).get("ops", {}))
        made.update({new: name for new in t.columns if new not in before})
    layout = []  # (t의 컬럼, 결과 이름, recipe상 컬럼, 파생 컬럼인지)
    for name in [n for n in t.columns if n not in made]:
        if specs.get(name, {}).get("keep", True):
            layout.append((name, specs.get(name, {}).get("rename") or name, name, False))
        layout += [(new, new, name, True) for new, source_name in made.items() if source_name == name]
    dropped = names_of(recipe, "drop")
    layout = [item for item in layout if not (item[3] and item[1] in dropped)]  # 파생 컬럼만 뺀다. 없는 이름은 무시한다.
    if not layout:
        raise ValueError("남길 컬럼이 없습니다.")
    results = [item[1] for item in layout]
    for new in results:
        if results.count(new) > 1:
            raise ValueError(f"결과에 '{new}' 컬럼이 둘 이상입니다. 이름을 바꾸세요.")
    t = t.select(*[t[old].name(new) for old, new, *_ in layout])
    return t, {new: (src, derived) for _, new, src, derived in layout}


def dedupe_columns(t, recipe):
    columns = names_of(recipe, "dedupe")
    for name in columns:
        parse.check_name(name, t.columns)
    return columns


def build(con, recipe, table=None):
    """recipe를 Ibis 식 하나로 만든다. (식, 결과 컬럼마다 (recipe상 컬럼 이름, 파생 컬럼인지))를 돌려준다. table은 shape와 같다."""
    t, origin = shape(con, recipe, table)
    columns = dedupe_columns(t, recipe)
    if columns:
        t = t.distinct() if set(columns) == set(t.columns) else t.distinct(on=columns, keep="first")
    if recipe.get("downcast") is True:
        t = downcast(t)
    keys = [parse.parse_sort(line, t) for line in recipe.get("sorts", [])]
    return (t.order_by(keys) if keys else t), origin


def dupes(con, recipe):
    """dedupe를 적용하기 직전의 식에서 중복 묶음 수(같은 기준 값이 2행 이상)와 빠질 행 수를 센다."""
    t = shape(con, recipe)[0]
    columns = dedupe_columns(t, recipe)
    if not columns:
        raise ValueError("중복 기준 컬럼을 고르세요.")
    sizes = t.group_by(columns).aggregate(n=t.count())
    big = sizes.filter(sizes.n > 1)
    found = stats(big, groups=big.count(), extra=(big.n - 1).sum())
    return {"groups": found["groups"], "rows": found["extra"] or 0}


def changes_rows(recipe):
    """recipe가 행 수를 바꿀 수 있는 일(filters, fill drop, outlier drop, dedupe)을 하는지."""
    ops = [spec.get("ops", {}) for spec in recipe.get("columns", {}).values()]
    return bool(recipe.get("filters") or recipe.get("dedupe")) or any(
        o.get("fill", {}).get("how") == "drop" or o.get("outlier", {}).get("how") == "drop" for o in ops)


def type_text(dtype):
    return str(dtype).lstrip("!")
