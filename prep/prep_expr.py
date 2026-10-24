"""전처리: recipe(테이블, 행 거르기, 컬럼 복사, 컬럼별 일, 계산 컬럼·PCA, 파생 컬럼 빼기, 중복 제거, downcast, 정렬)를 Ibis 식 하나로 만든다.
컬럼 하나에 하는 일은 prep_ops.py의 OPS 표에, 계산 컬럼·PCA는 prep_derive.py에 있다."""
from lazy import ibis

from core import parse
from core import prep_derive
from core import prep_ops
from core import source
from core import sql_funcs
from core.prep_ops import stats

FLOAT32_MAX = 3.4e38  # float32로 바꿔도 되는 절댓값의 상한(실제 최댓값 3.4028e38보다 조금 작게)
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
    for i, n in enumerate(floats):  # float32에 안 들어가는 큰 값(1e300)은 cast가 DB 오류를 내므로 cast 전에 범위 안의 값만 남겨 견주고, 밖의 값 수는 따로 센다
        big = (t[n].abs() > FLOAT32_MAX) & ~t[n].isinf() & ~t[n].isnan()
        safe = (~big).ifelse(t[n], 0.0)
        metrics.update({f"diff{i}": (safe != safe.cast("float32").cast("float64")).sum(), f"big{i}": big.cast("int64").sum()})
    if not metrics:
        return t
    found = stats(t, **metrics)
    casts = {}
    for i, n in enumerate(ints):
        lo, hi = found[f"lo{i}"], found[f"hi{i}"]
        if lo is not None:
            small = next((name for name, low, high in INT_TYPES if low <= lo and hi <= high), None)  # uint64의 int64 밖 값은 건너뛴다.
            if small and ibis.dtype(small).nbytes < t[n].type().nbytes:
                casts[n] = t[n].cast(small)
    casts.update({n: t[n].cast("float32") for i, n in enumerate(floats) if found[f"diff{i}"] == 0 and not found[f"big{i}"]})  # 전부 빈 값이면 합이 비어 그대로 둔다.
    return t.mutate(**casts) if casts else t


def shape(con, recipe, table=None, info=None, refs=None):
    """filters → copies → 컬럼별 ops → 계산 컬럼(formulas) → PCA → keep·파생 컬럼 배치·rename → drop까지 만든 식과,
    결과 컬럼마다 (recipe상 컬럼 이름, 파생 컬럼인지). 계산 컬럼·PCA 성분은 원래 컬럼이 없어 이름이 ""이고 맨 뒤에 놓인다.
    계산 식은 이름을 바꾸기 전의 컬럼 이름을 쓴다. table을 주면 연결의 테이블 대신 그 식(예: 표본을 담은 메모리 표)에서 시작한다.
    info(dict)를 주면 PCA의 설명된 분산 비율 등을 남긴다. 좌표 단계(recipe.geo, 이 PC에서 계산)는 PCA 뒤에 붙고, 지점·영역 테이블은 refs(없으면 con)에서 읽는다."""
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
    names = list(t.columns)  # 앞 단계가 만든 파생 컬럼에는 ops를 적용하지 않는다
    by = {fill["by"] for spec in specs.values() if isinstance(fill := (spec.get("ops") or {}).get("fill"), dict) and fill.get("how") in ("prev", "next") and fill.get("by") in names}
    names = [n for n in names if n in by] + [n for n in names if n not in by]  # fill prev·next의 by 컬럼을 먼저 처리해, 타입 변환 등을 받은 뒤의 값으로 순서를 정하게(컬럼 순서로 결과가 달라지지 않게)
    start = t  # 행을 뺄지 정하는 통계(outlier 경계)는 이 표로 구한다(컬럼 순서와 무관). 채울 값(fill mean 등)은 rest에서 행을 다 뺀 뒤의 표로 구한다
    for part in ("rows", "rest"):  # 행을 줄이는 일(fill·outlier drop)을 모든 컬럼에 먼저 하고 나서 통계를 쓰는 일을 한다(컬럼 순서로 결과가 달라지지 않게)
        for name in names:
            before = set(t.columns)
            t = prep_ops.apply_ops(t, name, specs.get(name, {}).get("ops", {}), part, start)
            made.update({new: name for new in t.columns if new not in before})
    t, new = prep_derive.apply_formulas(t, recipe.get("formulas") or [])
    if recipe.get("pca"):
        t, components = prep_derive.pca(t, recipe["pca"], {} if info is None else info)
        new += components
    if recipe.get("geo"):
        from geo import prep_geo  # 좌표 단계를 쓸 때만 geo 패키지가 올라온다
        t, made_geo = prep_geo.apply(t, recipe["geo"], info.setdefault("geo", []) if info is not None else None, con if refs is None else refs)
        new += made_geo
    layout = []  # (t의 컬럼, 결과 이름, recipe상 컬럼, 파생 컬럼인지)
    for name in [n for n in t.columns if n not in made and n not in new]:
        if specs.get(name, {}).get("keep", True):
            layout.append((name, specs.get(name, {}).get("rename") or name, name, False))
        layout += [(new, new, name, True) for new, source_name in made.items() if source_name == name]
    layout += [(name, name, "", True) for name in new]
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


FIRST = "__ibis_first"  # first_rows가 잠깐 쓰는 행 번호


def first_rows(t, columns):
    """columns가 같은 행 중 하나만 남긴다(어느 행인지는 정하지 않는다). 연결이 DISTINCT ON(keep first)을 못 하면(MSSQL·Oracle 등) 행 번호로 대신한다."""
    found = t.distinct(on=columns, keep="first")
    if source.compiles(found):
        return found
    numbered = t.mutate(**{FIRST: ibis.row_number().over(ibis.window(group_by=[t[c] for c in columns], order_by=parse.orderable(t)))})
    return numbered.filter(numbered[FIRST] == 0).select(*t.columns)


KEEP_ROWS = ("any", "any_value", "first", "last")  # 겹치는 행 중 행 하나를 남기는 선택지(나머지는 겹치는 값을 대표 값 하나로 합친다)


def keep_choice(recipe):
    """dedupe_keep(겹치는 행에서 무엇을 남길지)을 검사해 (선택지, 정렬 기준 목록, 구분자)로. 없으면 아무 행 하나(예전 recipe와 같다).
    값은 글자("first" …) 또는 {how, order, sep}. how는 any(아무 행)·first·last(정렬 기준의 첫·마지막 행)와 합치기(min·max·mode·string_agg·warn …, sql_funcs.REPRESENTATIVE)."""
    keep = recipe.get("dedupe_keep")
    keep = {"how": keep} if isinstance(keep, str) and keep else keep or {}
    if not isinstance(keep, dict) or not set(keep) <= {"how", "order", "sep"}:
        raise ValueError('dedupe_keep은 글자나 {"how": …, "order": ["컬럼 [desc]"], "sep": ","} 모양이어야 합니다.')
    how, order, sep = keep.get("how") or "any", keep.get("order") or [], keep.get("sep", ",")
    if how not in (*KEEP_ROWS, *sql_funcs.REPRESENTATIVE):
        raise ValueError(f"dedupe_keep의 how는 {', '.join(dict.fromkeys((*KEEP_ROWS, *sql_funcs.REPRESENTATIVE)))} 중 하나입니다.")
    if not isinstance(order, list) or not all(isinstance(o, str) for o in order) or not isinstance(sep, str):
        raise ValueError("dedupe_keep의 order는 '컬럼 [asc|desc]' 글자의 목록, sep는 글자여야 합니다.")
    return how, order, sep


def flipped(line):
    """정렬 줄 '컬럼 [asc|desc]'의 방향을 뒤집는다(마지막 행을 고를 때)."""
    col, _, how = line.partition(" ")
    return f"{col} {'asc' if how.strip().lower() == 'desc' else 'desc'}"


def nth_row(t, columns, order, last):
    """columns가 같은 행 중 정렬 기준(order)으로 첫(last면 마지막) 행 하나만 남긴다(행 번호 0)."""
    keys = [parse.parse_sort(flipped(line) if last else line, t).op().copy(nulls_first=False).to_expr() for line in order]  # 빈 값은 DB 기본 위치에 맡기지 않고 늘 뒤로
    numbered = t.mutate(**{FIRST: ibis.row_number().over(ibis.window(group_by=[t[c] for c in columns], order_by=keys))})
    return numbered.filter(numbered[FIRST] == 0).select(*t.columns)


def merged(t, columns, how, order, sep):
    """columns가 같은 행들을 한 줄로 합친다: 나머지 컬럼마다 대표 값 고르기(sql_funcs.pick)를 건다. 컬럼 순서는 그대로."""
    for line in order:
        parse.check_name(line.partition(" ")[0], t.columns)
    aggs = {}
    for name in [c for c in t.columns if c not in columns]:
        try:
            aggs[name] = sql_funcs.pick(how, t, name, order, sep)
        except ValueError as e:
            raise ValueError(f"'{name}' 컬럼에 '{sql_funcs.REPRESENTATIVE[how][0]}'을(를) 쓸 수 없습니다: {e}") from None
    found = t.group_by([t[c] for c in columns]).aggregate(**aggs)
    return found.select(*t.columns)


def dedupe_rows(t, columns, recipe):
    """중복 제거. 모든 컬럼이 기준이면 DISTINCT, 아니면 dedupe_keep대로: 아무 행·정렬 기준의 첫/마지막 행·합치기."""
    how, order, sep = keep_choice(recipe)
    if set(columns) == set(t.columns):
        return t.distinct()
    if how in ("any", "any_value") or (how == "first" and not order):  # 정렬 기준 없는 first는 예전처럼 아무 행
        return first_rows(t, columns)
    if how in ("first", "last"):
        if not order:
            raise ValueError("마지막 행을 남기려면 dedupe_keep의 order(정렬 기준)가 필요합니다.")
        return nth_row(t, columns, order, how == "last")
    return merged(t, columns, how, order, sep)


def build(con, recipe, table=None, info=None, refs=None):
    """recipe를 Ibis 식 하나로 만든다. (식, 결과 컬럼마다 (recipe상 컬럼 이름, 파생 컬럼인지))를 돌려준다. table·info·refs는 shape와 같다."""
    t, origin = shape(con, recipe, table, info, refs)
    columns = dedupe_columns(t, recipe)
    if columns:
        t = dedupe_rows(t, columns, recipe)
    if recipe.get("downcast") is True:
        t = downcast(t)
    keys = [parse.parse_sort(line, t) for line in recipe.get("sorts", [])]
    return (t.order_by(keys) if keys else t), origin


def dupes(con, recipe, table=None):
    """dedupe를 적용하기 직전의 식에서 중복 묶음 수(같은 기준 값이 2행 이상)와 빠질 행 수를 센다. table은 shape와 같다."""
    t = shape(con, recipe, table)[0]
    columns = dedupe_columns(t, recipe)
    if not columns:
        raise ValueError("중복 기준 컬럼을 고르세요.")
    sizes = t.group_by(columns).aggregate(n=t.count())
    big = sizes.filter(sizes.n > 1)
    found = stats(big, groups=big.count(), extra=(big.n - 1).sum())
    out = {"groups": found["groups"], "rows": found["extra"] or 0}
    others = [c for c in t.columns if c not in columns]
    if keep_choice(recipe)[0] == "warn" and others:  # 여러 개면 알리기: 같은 기준인데 값이 둘 이상인 묶음 수(그 칸은 빈 값이 된다)
        out["conflicts"] = sql_funcs.conflicts(t, columns, others).to_pyarrow().as_py()
    return out


def changes_rows(recipe):
    """recipe가 행 수를 바꿀 수 있는 일(filters, fill drop, outlier drop, dedupe)을 하는지."""
    ops = [spec.get("ops", {}) for spec in recipe.get("columns", {}).values()]
    return bool(recipe.get("filters") or recipe.get("dedupe")) or any(
        o.get("fill", {}).get("how") == "drop" or o.get("outlier", {}).get("how") == "drop" for o in ops)


def type_text(dtype):
    return str(dtype).lstrip("!")
