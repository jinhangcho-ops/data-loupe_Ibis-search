"""SQL 함수 사전: 모든 화면(피벗·집계·차트·지표·전처리·대시보드·문장)이 같은 목록에서 함수를 고른다.
항목 하나 = {id, category, label, help, args, example, kind_in, kind_out, aggregate, window, table, build}.
- 집계(aggregate): 묶음마다 값 하나(count·합계·중앙값·분위수·최빈값·표준편차·이어 붙이기·첫/마지막 값 …), 창(window): 행마다 값 하나(순위·이전 값·누적·이동 …),
  글자·숫자·날짜·조건·형 변환·구조는 행마다 계산, 묶음(table)은 표를 바꾼다(HAVING·QUALIFY·ROLLUP·CUBE·GROUPING SETS·DISTINCT·펼치기).
- 만들기: make(id, t, 인자)가 Ibis 식을 만든다. 인자의 컬럼은 이름 글자(또는 Ibis 값), 조건은 조건 줄(또는 Ibis 값), 정렬은 "컬럼 [asc|desc]" 목록.
  집계는 aggregate(id, t, 컬럼, 조건, 옵션)으로 화면의 {agg, y} 한 칸을 식으로 만든다. 예전 이름(nunique)은 ALIASES로 읽힌다.
- 지원 판정: support(식)이 지금 연결로 source.compiles(실행하지 않음)를 보고 ok / substitute(같은 결과의 대체, 표시) / local(이 PC 사본에서 계산, 표시) /
  unavailable(고를 수 없음 + 이유)로 알려 준다. 대체가 없는 함수는 연결이 못 하면 local이다(core.runnable이 PC 사본으로 돌린다).
- 대표 값 고르기(10-2): REPRESENTATIVE의 선택지(any_value·first·last·min·max·mode·string_agg·warn)를 pick이 집계 식 하나로, conflicts가 값이 둘 이상인 묶음 수로.
- SQL 식 직접 쓰기: raw_expr이 'SELECT *, <식> AS 이름 FROM (<지금 식의 SQL>)'을 sql_exec.check(파일 읽기 막기 포함)로 검사한 뒤 연결의 sql()로 붙인다."""
import re

from lazy import Later, ibis

from core import parse

geo_funcs = Later("geo.funcs")  # 좌표 함수의 식은 geo 패키지가 만든다(좌표 함수를 처음 쓸 때 올라온다)

CATEGORIES = [("aggregate", "집계"), ("window", "창 함수"), ("text", "글자"), ("number", "숫자"), ("date", "날짜"), ("cond", "조건"),
              ("cast", "형 변환"), ("struct", "구조"), ("geo", "좌표"), ("group", "묶음")]
ALIASES = {"nunique": "count_distinct"}  # 저장돼 있는 예전 이름 → 지금 id
KIND_NAMES = {"num": "숫자", "text": "글자", "date": "날짜", "bool": "참거짓", "json": "JSON", "array": "목록", "struct": "구조", "any": "모든"}
UNITS = {"year": "Y", "quarter": "Q", "month": "M", "week": "W", "day": "D", "hour": "h", "minute": "m", "second": "s"}
UNIT_NAMES = {"year": "연", "quarter": "분기", "month": "월", "week": "주", "day": "일", "hour": "시", "minute": "분", "second": "초"}
CASTS = {"int": "int64", "float": "float64", "string": "string", "date": "date", "timestamp": "timestamp", "bool": "boolean"}
CAST_NAMES = {"int": "정수", "float": "실수", "string": "글자", "date": "날짜", "timestamp": "날짜시간", "bool": "참거짓"}
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b, ">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "!=": lambda a, b: a != b}
SUBTOTAL, TOTAL = "(소계)", "(전체)"  # ROLLUP·CUBE·GROUPING SETS의 소계·총계 줄에 보이는 값
NAME = re.compile(r"[A-Za-z_가-힣][A-Za-z0-9_가-힣]{0,62}")
FUNCS = {}  # id → 항목(등록 차례가 화면에 보이는 차례)
SUBS = {}  # id → [(대체 설명, build)] 같은 결과를 내는 대체 식(연결이 원래 식을 못 할 때)
HAS_ARGS = ("where", "order", "group")  # 정해진 자리의 인자: 조건·정렬·묶음


class Args(dict):
    """build가 받는 풀린 인자(점으로 읽는다). t = 표, x·y = 컬럼 식, 나머지는 인자 이름대로."""
    __getattr__ = dict.get


# ---------- 인자 칸 ----------

def col(name="x", label="컬럼", kinds="any", optional=False):
    return {"name": name, "kind": "column", "label": label, "kinds": kinds, "optional": optional}


def num(name, label, default, optional=False, kind="number"):
    return {"name": name, "kind": kind, "label": label, "default": default, "optional": optional}


def text(name, label, default="", optional=False):
    return {"name": name, "kind": "text", "label": label, "default": default, "optional": optional}


def choice(name, label, options, default):
    return {"name": name, "kind": "choice", "label": label, "options": list(options), "default": default, "optional": False}


def cond(name="cond", label="조건", optional=False):
    return {"name": name, "kind": "cond", "label": label, "optional": optional}


def order(optional=True, label="정렬 기준"):
    return {"name": "order", "kind": "order", "label": label, "optional": optional}


def group(label="묶음 기준"):
    return {"name": "group", "kind": "columns", "label": label, "optional": True}


def many(name="columns", label="컬럼들"):
    return {"name": name, "kind": "columns", "label": label, "optional": False}


def value(name, label, default="", optional=False):
    """숫자·글자 값 하나(그대로 쓴다). {"col": 컬럼 이름}이면 그 컬럼."""
    return {"name": name, "kind": "value", "label": label, "default": default, "optional": optional}


def items(name, label, default=None):
    return {"name": name, "kind": "list", "label": label, "default": default, "optional": default is None}


WHERE = cond("where", "조건(선택): 이 조건인 행만", True)


def add(id, category, label, help, args, build, example, kin="any", kout="any", aggregate=False, window=False, table=False, raw=False):
    """사전에 함수 하나를 올린다. 집계는 조건(where) 칸이 저절로 붙는다. raw는 SQL 식 직접 쓰기(식을 만들지 않고 raw_expr로 붙인다)."""
    if id in FUNCS:
        raise ValueError(f"함수 id가 겹칩니다: {id}")
    FUNCS[id] = {"id": id, "category": category, "label": label, "help": help, "args": [*args, WHERE] if aggregate else list(args), "example": example,
                 "kind_in": kin, "kind_out": kout, "aggregate": aggregate, "window": window, "table": table, "raw": raw, "build": build}


# ---------- 인자 풀기 ----------

def kind_of(dtype):
    """Ibis 타입 → 사전의 종류(num·text·date·bool·json·array·struct·other)."""
    if dtype.is_boolean():
        return "bool"
    if dtype.is_numeric():
        return "num"
    if dtype.is_string():
        return "text"
    if dtype.is_temporal():
        return "date"
    return "json" if dtype.is_json() else "array" if dtype.is_array() else "struct" if dtype.is_struct() else "other"


def kinds_text(kinds):
    return "·".join(KIND_NAMES[k] for k in ([kinds] if isinstance(kinds, str) else kinds))


def kind_fits(kinds, dtype):
    """컬럼 종류가 칸이 받는 종류(글자 하나 또는 목록)에 맞는지."""
    if kinds == "any":
        return True
    return kind_of(dtype) in ([kinds] if isinstance(kinds, str) else kinds)


def column_value(t, raw, spec, f):
    if isinstance(raw, ibis.expr.types.Value):
        value = raw
    else:
        if not isinstance(raw, str):
            raise ValueError(f"{f['label']}: '{spec['label']}'에 컬럼 이름을 넣으세요.")
        value = parse.column(t, raw)
    if not kind_fits(spec.get("kinds", "any"), value.type()):
        name = raw if isinstance(raw, str) else value.get_name()
        raise ValueError(f"'{name}'은(는) {kinds_text(spec['kinds'])} 컬럼이 아니라 {f['label']}에 쓸 수 없습니다.")
    return value


def number_value(raw, spec, f):
    try:
        out = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{f['label']}: '{spec['label']}'에는 숫자를 넣으세요.") from None
    return int(out) if spec["kind"] == "int" or isinstance(raw, int) else out


def condition_value(t, raw, spec, f):
    if isinstance(raw, ibis.expr.types.Value):
        return raw
    if isinstance(raw, (str, dict, list)) and raw:
        return parse.predicate(raw, t)
    raise ValueError(f"{f['label']}: '{spec['label']}'을 넣으세요.")


def resolve_one(t, raw, spec, f):
    kind = spec["kind"]
    if kind == "column":
        return column_value(t, raw, spec, f)
    if kind == "columns":
        raw = [raw] if isinstance(raw, (str, ibis.expr.types.Value)) else raw
        if not isinstance(raw, (list, tuple)) or (not raw and not spec.get("optional")):
            raise ValueError(f"{f['label']}: '{spec['label']}'을 하나 이상 넣으세요.")
        return [column_value(t, r, {**spec, "kinds": spec.get("kinds", "any")}, f) for r in raw]
    if kind in ("number", "int"):
        return number_value(raw, spec, f)
    if kind in ("text", "choice"):
        raw = str(raw)
        if kind == "choice" and raw not in spec["options"]:
            raise ValueError(f"{f['label']}: '{spec['label']}'은 {', '.join(spec['options'])} 중 하나입니다.")
        return raw
    if kind == "cond":
        return condition_value(t, raw, spec, f)
    if kind == "value":
        return column_value(t, raw["col"], {"kinds": "any"}, f) if isinstance(raw, dict) and "col" in raw else raw
    if kind == "dict":
        if not isinstance(raw, dict):
            raise ValueError(f"{f['label']}: '{spec['label']}'은 객체여야 합니다.")
        return raw
    if kind == "order":
        raw = [raw] if isinstance(raw, (str, ibis.expr.types.Value)) else raw
        return [parse.parse_sort(r, t) if isinstance(r, str) else r for r in raw]
    if kind == "list":
        if not isinstance(raw, (list, tuple)) or (not raw and not spec.get("optional")):
            raise ValueError(f"{f['label']}: '{spec['label']}'을 하나 이상 넣으세요.")
        return list(raw)
    if kind == "cases":
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError(f"{f['label']}: '{spec['label']}'을 하나 이상 넣으세요.")
        out = []
        for item in raw:
            if not isinstance(item, dict) or "when" not in item or "then" not in item:
                raise ValueError(f"{f['label']}: 각 줄은 {{when: 조건, then: 값}} 모양이어야 합니다.")
            then = item["then"]
            then = column_value(t, then["col"], {"kinds": "any"}, f) if isinstance(then, dict) and "col" in then else then
            out.append((condition_value(t, item["when"], spec, f), then))
        return out
    raise ValueError(f"알 수 없는 칸 종류: {kind}")


def blank(value):
    """칸이 비었는지(None·빈 글자·빈 목록). Ibis 값은 ==로 비교하면 식이 되므로 종류를 먼저 본다."""
    return value is None or (isinstance(value, (str, list, tuple)) and not value)


def resolve(f, t, raw):
    """인자 글(JSON 모양)을 build가 받는 Args로. 빠진 필수 칸·잘못된 종류는 ValueError(칸 이름 포함)."""
    raw = raw or {}
    unknown = set(raw) - {a["name"] for a in f["args"]}
    if unknown:
        raise ValueError(f"{f['label']}: 모르는 항목 {', '.join(sorted(map(str, unknown)))}. {', '.join(a['name'] for a in f['args'])}만 씁니다.")
    out = Args(t=t)
    for spec in f["args"]:
        name = spec["name"]
        value = raw.get(name)
        if blank(value):
            value = spec.get("default")
        if blank(value):
            if not spec.get("optional"):
                raise ValueError(f"{f['label']}: '{spec['label']}'을 넣으세요.")
            out[name] = [] if spec["kind"] in ("order", "columns") and name in HAS_ARGS else None
            continue
        out[name] = resolve_one(t, value, spec, f)
    return out


def needs_column(id):
    """집계 id가 컬럼을 꼭 받아야 하는지(count와 조건만 받는 count_if·all·any는 아님)."""
    return any(a["name"] == "x" and not a.get("optional") for a in get(id)["args"])


def get(id):
    """id(예전 이름 포함)의 항목. 없으면 비슷한 id를 알려 주는 ValueError."""
    id = ALIASES.get(id, id)
    if id not in FUNCS:
        parse.check_name(str(id), list(FUNCS), "함수")
        raise ValueError(f"'{id}' 함수가 없습니다.")
    return FUNCS[id]


def make(id, t, raw=None):
    """함수 id를 인자 raw로 표 t 위의 Ibis 식으로. 집계는 값 하나(Scalar), 창·행 함수는 컬럼 하나. 묶음(table) 함수는 apply로."""
    f = get(id)
    if f["table"] or f["raw"]:
        raise ValueError(f"'{f['label']}'은 {'raw_expr로 붙이는' if f['raw'] else '표를 바꾸는'} 함수입니다.")
    return f["build"](resolve(f, t, raw))


def apply(id, t, raw=None):
    """묶음(table) 함수를 표 t에 건다(새 표)."""
    f = get(id)
    if not f["table"]:
        raise ValueError(f"'{f['label']}'은 표를 바꾸는 함수가 아닙니다.")
    return f["build"](resolve(f, t, raw))


def aggregate(id, t, column=None, where=None, opts=None):
    """화면의 집계 칸 {agg: id, y: 컬럼, 옵션}을 집계 식으로. column이 칸의 첫 컬럼(x), where는 조건 줄이나 Ibis 값, opts는 나머지 인자."""
    f = get(id)
    if not f["aggregate"]:
        raise ValueError(f"'{f['label']}'은 집계 함수가 아닙니다.")
    raw = dict(opts or {})
    if column is not None:
        raw["x"] = column
    if where is not None:
        raw["where"] = where
    c = resolve(f, t, raw)
    return usable(f, t, c, f["build"](c))


def usable(f, t, c, value):
    """집계 식 value를 t의 연결이 SQL(계획)로 바꿀 수 있으면 그대로, 못 하면 같은 결과의 대체 식(SUBS) 중 되는 것으로. 대체도 안 되면 원래 식(부르는 쪽이 이 PC 사본에서 한다).
    대체 식이 없는 함수나 연결이 없는 식은 따져 보지 않는다."""
    if f["id"] not in SUBS:
        return value
    from core import source
    try:
        ibis.get_backend(t)
    except Exception:
        return value
    if source.compiles(t.aggregate(v=value)):
        return value
    for _, build in SUBS[f["id"]]:
        try:
            other = build(c)
        except Exception:
            continue
        if source.compiles(t.aggregate(v=other)):
            return other
    return value


def both(*conds):
    """조건들을 and로(없는 것은 건너뛴다). 모두 없으면 None."""
    found = [c for c in conds if c is not None]
    return ibis.and_(*found) if found else None


def win(c, whole=False, rows=None):
    """창: 묶음(group)·정렬(order). whole이면 묶음 전체, rows면 지금 행 포함 앞 N행(없으면 처음부터 지금까지)."""
    kw = {"group_by": c.group or None, "order_by": c.order or None}
    if whole:
        return ibis.window(**kw, preceding=None, following=None)
    if not c.order:
        return ibis.window(**kw)
    if rows is None:  # 처음부터 지금까지: 정렬 값이 같은 행끼리는 같은 값(RANGE). 행 번호(ROWS)로 하면 같은 값인 행마다 달라진다
        return ibis.range_window(**kw, preceding=None, following=0)
    return ibis.window(**kw, preceding=rows, following=0)


def need_order(c, f):
    if not c.order:
        raise ValueError(f"{FUNCS[f]['label']}에는 정렬 기준이 필요합니다.")
    return c.order


def ordered(c):
    """정렬 기준(없으면 정렬할 수 있는 컬럼 전부: 행 번호가 매번 같게)."""
    return c.order or parse.orderable(c.t)


# ---------- 집계 ----------

def distinct(x, where=None):
    """서로 다른 값 수(빈 값 제외). Polars는 nunique가 빈 값도 세므로 백엔드와 상관없이 빈 값을 걸러 낸 뒤 센다."""
    return x.nunique(where=both(where, x.notnull()))


NUM, TXT, DAT, BOOL = "num", "text", "date", "bool"
ORD = (NUM, TXT, DAT)  # 크기를 비교할 수 있는 종류

add("count", "aggregate", "개수", "행 수를 세요(컬럼을 고르면 빈 값이 아닌 것만).", [col(optional=True)],
    lambda c: c.t.count(where=c.where) if c.x is None else c.x.count(where=c.where), "count(), count(금액)", kout=NUM, aggregate=True)
add("count_distinct", "aggregate", "고유 개수", "서로 다른 값이 몇 개인지 정확하게 세요.", [col()],
    lambda c: distinct(c.x, c.where), "count_distinct(고객)", kout=NUM, aggregate=True)
add("approx_count_distinct", "aggregate", "고유 개수(근사)", "서로 다른 값 수를 빠르게 어림해서 세요(큰 표에서 빠름, 약간의 오차).", [col()],
    lambda c: c.x.approx_nunique(where=both(c.where, c.x.notnull())), "approx_count_distinct(고객)", kout=NUM, aggregate=True)
add("sum", "aggregate", "합계", "더한 값.", [col(kinds=NUM)], lambda c: c.x.sum(where=c.where), "sum(금액)", NUM, NUM, aggregate=True)
add("mean", "aggregate", "평균", "평균 값.", [col(kinds=NUM)], lambda c: c.x.mean(where=c.where), "mean(금액)", NUM, NUM, aggregate=True)
add("min", "aggregate", "최소", "가장 작은(이른) 값.", [col(kinds=ORD)], lambda c: c.x.min(where=c.where), "min(금액)", "any", "any", aggregate=True)
add("max", "aggregate", "최대", "가장 큰(늦은) 값.", [col(kinds=ORD)], lambda c: c.x.max(where=c.where), "max(금액)", "any", "any", aggregate=True)
add("median", "aggregate", "중앙값", "순서대로 세웠을 때 가운데 값.", [col(kinds=NUM)], lambda c: c.x.median(where=c.where), "median(금액)", NUM, NUM, aggregate=True)


def quantile_of(p):
    return lambda c: c.x.quantile(p, where=c.where)


def check_p(c):
    if not 0 <= c.p <= 1:
        raise ValueError("분위수는 0~1 사이로 넣으세요(0.9 = 상위 10% 경계).")
    return c


add("quantile", "aggregate", "분위수", "작은 쪽에서 P 위치의 값(P는 0~1, 0.9 = P90).", [col(kinds=NUM), num("p", "P (0~1)", 0.5)],
    lambda c: check_p(c).x.quantile(c.p, where=c.where), "quantile(금액, 0.9)", NUM, NUM, aggregate=True)
for name, p in (("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99)):
    add(name, "aggregate", name.upper(), f"작은 쪽에서 {int(p * 100)}% 위치의 값.", [col(kinds=NUM)], quantile_of(p), f"{name}(금액)", NUM, NUM, aggregate=True)
add("mode", "aggregate", "최빈값", "가장 많이 나온 값.", [col()], lambda c: c.x.mode(where=c.where), "mode(등급)", aggregate=True)
add("std", "aggregate", "표준편차(표본)", "값이 평균에서 얼마나 퍼졌는지(표본, n-1).", [col(kinds=NUM)], lambda c: c.x.std(where=c.where, how="sample"), "std(금액)", NUM, NUM, aggregate=True)
add("std_pop", "aggregate", "표준편차(모집단)", "값이 평균에서 얼마나 퍼졌는지(모집단, n).", [col(kinds=NUM)], lambda c: c.x.std(where=c.where, how="pop"), "std_pop(금액)", NUM, NUM, aggregate=True)
add("var", "aggregate", "분산(표본)", "표준편차의 제곱(표본, n-1).", [col(kinds=NUM)], lambda c: c.x.var(where=c.where, how="sample"), "var(금액)", NUM, NUM, aggregate=True)
add("var_pop", "aggregate", "분산(모집단)", "표준편차의 제곱(모집단, n).", [col(kinds=NUM)], lambda c: c.x.var(where=c.where, how="pop"), "var_pop(금액)", NUM, NUM, aggregate=True)
add("corr", "aggregate", "상관계수", "두 숫자 컬럼이 함께 움직이는 정도(-1~1).", [col(kinds=NUM), col("y", "둘째 컬럼", NUM)],
    lambda c: c.x.corr(c.y, where=c.where, how="pop"), "corr(금액, 수량)", NUM, NUM, aggregate=True)
add("cov", "aggregate", "공분산(표본)", "두 숫자 컬럼이 함께 움직이는 크기(표본).", [col(kinds=NUM), col("y", "둘째 컬럼", NUM)],
    lambda c: c.x.cov(c.y, where=c.where, how="sample"), "cov(금액, 수량)", NUM, NUM, aggregate=True)
add("cov_pop", "aggregate", "공분산(모집단)", "두 숫자 컬럼이 함께 움직이는 크기(모집단).", [col(kinds=NUM), col("y", "둘째 컬럼", NUM)],
    lambda c: c.x.cov(c.y, where=c.where, how="pop"), "cov_pop(금액, 수량)", NUM, NUM, aggregate=True)


def string_agg(c):
    parts = c.x.cast("string")
    if c.distinct == "yes":  # 중복을 빼면 값 순서로 잇는다(정렬 기준을 따로 둘 수 없다)
        return parts.collect(where=c.where, distinct=True, order_by=parts).join(c.sep)
    return parts.group_concat(c.sep, where=c.where, order_by=c.order or None)


add("string_agg", "aggregate", "이어 붙이기", "값을 구분자로 이어 붙여요(정렬 기준 순, 중복 빼기는 값 순).",
    [col(), text("sep", "구분자", ","), order(label="정렬 기준(선택)"), choice("distinct", "중복", ["no", "yes"], "no")], string_agg, "string_agg(태그, ',')", kout="text", aggregate=True)
add("collect", "aggregate", "목록 모으기", "값을 목록(배열)으로 모아요.", [col(), choice("distinct", "중복", ["no", "yes"], "no")],
    lambda c: c.x.collect(where=c.where, distinct=c.distinct == "yes"), "collect(태그)", kout="array", aggregate=True)
add("all", "aggregate", "모두 참", "묶음의 모든 행이 조건을 만족하면 참.", [cond()], lambda c: c.cond.all(where=c.where), "all(금액 > 0)", kin=BOOL, kout=BOOL, aggregate=True)
add("any", "aggregate", "하나라도 참", "묶음의 행 중 하나라도 조건을 만족하면 참.", [cond()], lambda c: c.cond.any(where=c.where), "any(금액 > 0)", kin=BOOL, kout=BOOL, aggregate=True)
add("count_if", "aggregate", "조건 개수", "조건을 만족하는 행 수.", [cond()], lambda c: c.t.count(where=both(c.cond, c.where)), "count_if(금액 > 0)", kout=NUM, aggregate=True)
add("sum_if", "aggregate", "조건부 합계", "조건을 만족하는 행의 합계.", [col(kinds=NUM), cond()], lambda c: c.x.sum(where=both(c.cond, c.where)), "sum_if(금액, 구분 = A)", NUM, NUM, aggregate=True)
add("mean_if", "aggregate", "조건부 평균", "조건을 만족하는 행의 평균.", [col(kinds=NUM), cond()], lambda c: c.x.mean(where=both(c.cond, c.where)), "mean_if(금액, 구분 = A)", NUM, NUM, aggregate=True)
add("arg_min", "aggregate", "가장 작은 쪽의 값", "다른 컬럼이 가장 작은(이른) 행의 이 컬럼 값.", [col(), col("by", "기준 컬럼", ORD)],
    lambda c: c.x.argmin(c.by, where=c.where), "arg_min(상품, 주문일)", aggregate=True)
add("arg_max", "aggregate", "가장 큰 쪽의 값", "다른 컬럼이 가장 큰(늦은) 행의 이 컬럼 값.", [col(), col("by", "기준 컬럼", ORD)],
    lambda c: c.x.argmax(c.by, where=c.where), "arg_max(상품, 주문일)", aggregate=True)
add("first", "aggregate", "첫 값", "정렬 기준으로 가장 앞 행의 값.", [col(), order(False)], lambda c: c.x.first(where=c.where, order_by=c.order), "first(상품, 주문일)", aggregate=True)
add("last", "aggregate", "마지막 값", "정렬 기준으로 가장 뒤 행의 값.", [col(), order(False)], lambda c: c.x.last(where=c.where, order_by=c.order), "last(상품, 주문일)", aggregate=True)
add("any_value", "aggregate", "아무 값", "묶음에서 아무 값이나 하나(값이 하나뿐일 때 쓴다).", [col()], lambda c: c.x.arbitrary(where=c.where), "any_value(이름)", aggregate=True)
add("range", "aggregate", "범위(최대−최소)", "가장 큰 값에서 가장 작은 값을 뺀 것.", [col(kinds=(NUM,))],
    lambda c: c.x.max(where=c.where) - c.x.min(where=c.where), "range(금액)", NUM, NUM, aggregate=True)
add("unique_or_null", "aggregate", "하나일 때만 값", "묶음의 값이 하나뿐이면 그 값, 둘 이상이면 빈 값(여러 개면 알리기).", [col(kinds=ORD)],
    lambda c: ibis.ifelse(distinct(c.x, c.where) > 1, ibis.null().cast(c.x.type()), c.x.min(where=c.where)), "unique_or_null(이름)", aggregate=True)

# ---------- 창 함수 ----------

N1 = num("n", "N", 1, kind="int")


def shifted(c, how):
    default = None if c.default is None else coerce(c.x, c.default)
    return getattr(c.x, how)(c.n, default=default).over(win(c))


add("row_number", "window", "행 번호", "묶음 안에서 정렬 순서대로 1부터 번호를 매겨요.", [order(), group()],
    lambda c: ibis.row_number().over(ibis.window(group_by=c.group or None, order_by=ordered(c))) + 1, "row_number()", kout=NUM, window=True)
add("rank", "window", "순위", "묶음 안의 순위(같으면 같은 순위, 다음 순위는 건너뜀).", [order(False), group()],
    lambda c: ibis.rank().over(win(c)) + 1, "rank()", kout=NUM, window=True)
add("dense_rank", "window", "밀집 순위", "같은 값은 같은 순위, 다음 순위를 건너뛰지 않아요.", [order(False), group()],
    lambda c: ibis.dense_rank().over(win(c)) + 1, "dense_rank()", kout=NUM, window=True)
add("percent_rank", "window", "백분위 순위", "맨 앞이 0, 맨 뒤가 1인 상대 순위.", [order(False), group()],
    lambda c: ibis.percent_rank().over(win(c)), "percent_rank()", kout=NUM, window=True)
add("cume_dist", "window", "누적 분포", "나와 같거나 앞선 행의 비율(0~1).", [order(False), group()],
    lambda c: ibis.cume_dist().over(win(c)), "cume_dist()", kout=NUM, window=True)
add("ntile", "window", "N등분", "정렬 순서대로 N개 구간으로 나눠 1~N 번호를 매겨요.", [num("n", "구간 수", 4, kind="int"), order(False), group()],
    lambda c: ibis.ntile(c.n).over(win(c)) + 1, "ntile(4)", kout=NUM, window=True)
add("lag", "window", "이전 값", "정렬 순서로 N행 앞의 값.", [col(), N1, value("default", "없을 때 값", None, True), order(False), group()],
    lambda c: shifted(c, "lag"), "lag(금액, 1)", window=True)
add("lead", "window", "다음 값", "정렬 순서로 N행 뒤의 값.", [col(), N1, value("default", "없을 때 값", None, True), order(False), group()],
    lambda c: shifted(c, "lead"), "lead(금액, 1)", window=True)
add("first_value", "window", "묶음의 첫 값", "묶음 안에서 정렬 기준으로 맨 앞 행의 값.", [col(), order(False), group()],
    lambda c: c.x.first().over(win(c, whole=True)), "first_value(금액)", window=True)
add("last_value", "window", "묶음의 마지막 값", "묶음 안에서 정렬 기준으로 맨 뒤 행의 값.", [col(), order(False), group()],
    lambda c: c.x.last().over(win(c, whole=True)), "last_value(금액)", window=True)
add("nth_value", "window", "N번째 값", "묶음 안에서 정렬 기준으로 N번째 행의 값(1부터).", [col(), num("n", "N", 2, kind="int"), order(False), group()],
    lambda c: c.x.nth(c.n - 1).over(win(c, whole=True)), "nth_value(금액, 2)", window=True)
add("cum_sum", "window", "누적 합", "처음부터 지금 행까지의 합.", [col(kinds=NUM), order(False), group()],
    lambda c: c.x.sum().over(win(c, rows=None)), "cum_sum(금액)", NUM, NUM, window=True)
add("cum_mean", "window", "누적 평균", "처음부터 지금 행까지의 평균.", [col(kinds=NUM), order(False), group()],
    lambda c: c.x.mean().over(win(c, rows=None)), "cum_mean(금액)", NUM, NUM, window=True)
add("cum_min", "window", "누적 최소", "처음부터 지금 행까지의 최소.", [col(kinds=NUM), order(False), group()],
    lambda c: c.x.min().over(win(c, rows=None)), "cum_min(금액)", NUM, NUM, window=True)
add("cum_max", "window", "누적 최대", "처음부터 지금 행까지의 최대.", [col(kinds=NUM), order(False), group()],
    lambda c: c.x.max().over(win(c, rows=None)), "cum_max(금액)", NUM, NUM, window=True)


def moving(how):
    def build(c):
        if c.n < 1:
            raise ValueError("이동 크기는 1 이상입니다.")
        if c.unit == "days":
            need_order(c, f"moving_{how}")
            dtype = c.order[0].op().arg.dtype
            if not (dtype.is_date() or dtype.is_timestamp()):
                raise ValueError("'일' 단위는 정렬 기준이 날짜·날짜시간 컬럼이어야 합니다.")
            w = ibis.window(group_by=c.group or None, order_by=c.order, preceding=ibis.interval(days=c.n - 1), following=0)
        else:
            w = win(c, rows=c.n - 1)
        return getattr(c.x, how)().over(w)
    return build


for how, name in (("sum", "합"), ("mean", "평균"), ("min", "최소"), ("max", "최대")):
    add(f"moving_{how}", "window", f"이동 {name}", f"최근 N행(또는 N일)의 {name}.",
        [col(kinds=NUM), num("n", "크기 N", 3, kind="int"), choice("unit", "단위", ["rows", "days"], "rows"), order(False), group()],
        moving(how), f"moving_{how}(금액, 3)", NUM, NUM, window=True)
add("share_of_group", "window", "묶음 안 비율", "값 ÷ 묶음 안 합계(0~1).", [col(kinds=NUM), group()],
    lambda c: c.x.cast("float64") / c.x.sum().over(ibis.window(group_by=c.group or None)).nullif(0).cast("float64"), "share_of_group(금액)", NUM, NUM, window=True)
add("diff", "window", "이전 대비 차이", "지금 값 − N행 앞의 값.", [col(kinds=NUM), N1, order(False), group()],
    lambda c: c.x - c.x.lag(c.n).over(win(c)), "diff(금액)", NUM, NUM, window=True)
add("pct_change", "window", "이전 대비 변화율", "(지금 값 − N행 앞의 값) ÷ N행 앞의 값.", [col(kinds=NUM), N1, order(False), group()],
    lambda c: (c.x - c.x.lag(c.n).over(win(c))).cast("float64") / c.x.lag(c.n).over(win(c)).nullif(0).cast("float64"), "pct_change(금액)", NUM, NUM, window=True)

# ---------- 글자 ----------

T1 = col(kinds=TXT)


def row(id, cat, label, help, args, build, example, kin="any", kout="any"):
    add(id, cat, label, help, args, build, example, kin, kout)


row("length", "text", "글자 수", "글자가 몇 개인지.", [T1], lambda c: c.x.length().cast("int64"), "length(이름)", TXT, NUM)
row("upper", "text", "대문자로", "영문을 대문자로.", [T1], lambda c: c.x.upper(), "upper(이름)", TXT, TXT)
row("lower", "text", "소문자로", "영문을 소문자로.", [T1], lambda c: c.x.lower(), "lower(이름)", TXT, TXT)
row("capitalize", "text", "첫 글자 대문자", "첫 글자만 대문자로.", [T1], lambda c: c.x.capitalize(), "capitalize(이름)", TXT, TXT)
def not_zero(n, label):
    """1부터 세는 위치 칸의 값: 0은 DB마다 뜻이 달라 거부한다."""
    if n == 0:
        raise ValueError(f"'{label}'은 1부터 셉니다. 0은 쓸 수 없습니다.")
    return n


row("trim", "text", "앞뒤 공백 지우기", "양쪽 공백을 지워요.", [T1], lambda c: c.x.strip(), "trim(이름)", TXT, TXT)
row("ltrim", "text", "앞 공백 지우기", "왼쪽 공백을 지워요.", [T1], lambda c: c.x.lstrip(), "ltrim(이름)", TXT, TXT)
row("rtrim", "text", "뒤 공백 지우기", "오른쪽 공백을 지워요.", [T1], lambda c: c.x.rstrip(), "rtrim(이름)", TXT, TXT)
row("substr", "text", "일부 자르기", "시작 위치(1부터)부터 길이만큼.", [T1, num("start", "시작(1부터)", 1, kind="int"), num("length", "길이", 3, True, "int")],
    lambda c: c.x.substr(not_zero(c.start, "시작") - 1, c.length), "substr(이름, 1, 3)", TXT, TXT)
row("left", "text", "앞 N글자", "앞에서 N글자.", [T1, num("n", "글자 수", 3, kind="int")], lambda c: c.x.left(c.n), "left(이름, 3)", TXT, TXT)
row("right", "text", "뒤 N글자", "뒤에서 N글자.", [T1, num("n", "글자 수", 3, kind="int")], lambda c: c.x.right(c.n), "right(이름, 3)", TXT, TXT)
row("replace", "text", "바꾸기", "글자를 다른 글자로 바꿔요.", [T1, text("find", "찾을 글자", "a"), text("to", "바꿀 글자", "", True)],
    lambda c: c.x.replace(c.find, c.to or ""), "replace(이름, 'a', 'b')", TXT, TXT)
row("regex_contains", "text", "패턴 포함", "정규식 패턴이 들어 있으면 참.", [T1, text("pattern", "패턴", "^a")], lambda c: c.x.re_search(c.pattern), "regex_contains(이름, '^a')", TXT, BOOL)
row("regex_extract", "text", "패턴에서 꺼내기", "정규식에 맞는 부분(그룹 N, 0이면 전체). 맞는 것이 없으면 빈 값. \\w는 DuckDB·DB에서는 영문·숫자·_만, 파일(Polars)에서는 한글 같은 글자도 포함해 달라요(한글은 [가-힣]로 쓰세요).", [T1, text("pattern", "패턴", "(\\d+)"), num("group_no", "그룹 번호", 1, kind="int")],
    lambda c: c.x.re_extract(c.pattern, c.group_no).nullif(""), "regex_extract(이름, '(\\d+)', 1)", TXT, TXT)
row("regex_replace", "text", "패턴 바꾸기", "정규식에 맞는 부분을 바꿔요. \\w는 DuckDB·DB에서는 영문·숫자·_만, 파일(Polars)에서는 한글 같은 글자도 포함해 달라요(한글은 [가-힣]로 쓰세요).", [T1, text("pattern", "패턴", "\\s+"), text("to", "바꿀 글자", " ")],
    lambda c: c.x.re_replace(c.pattern, c.to or ""), "regex_replace(이름, '\\s+', ' ')", TXT, TXT)
row("split", "text", "나누기(목록)", "구분자로 나눠 목록으로.", [T1, text("sep", "구분자", ",")], lambda c: c.x.split(c.sep), "split(태그, ',')", TXT, "array")
row("split_part", "text", "나눈 N번째", "구분자로 나눈 N번째 조각(1부터).", [T1, text("sep", "구분자", ","), num("n", "N번째", 1, kind="int")],
    lambda c: c.x.split(c.sep)[not_zero(c.n, "N번째") - 1], "split_part(태그, ',', 1)", TXT, TXT)
def concat_parts(values, sep):
    """빈 값은 건너뛰고(구분자도 붙이지 않는다) 이어 쓴다. 모두 빈 값이면 빈 값. DuckDB·Polars가 같은 결과를 낸다(Polars의 join은 빈 값이 하나라도 있으면 빈 값이다)."""
    parts = [v.cast("string") for v in values]
    found = parts[0]
    for p in parts[1:]:
        found = ibis.ifelse(found.isnull(), p, ibis.ifelse(p.isnull(), found, found + sep + p))
    return found


row("concat", "text", "이어 쓰기", "여러 컬럼·글자를 한 글자로(사이에 구분자). 빈 값은 건너뛰어요.", [many(), text("sep", "구분자", "", True)],
    lambda c: concat_parts(c.columns, c.sep or ""), "concat(성, 이름)", "any", TXT)
row("lpad", "text", "왼쪽 채우기", "길이가 N이 되도록 왼쪽을 채워요.", [T1, num("width", "길이", 5, kind="int"), text("fill", "채울 글자", "0")],
    lambda c: c.x.lpad(c.width, c.fill), "lpad(번호, 5, '0')", TXT, TXT)
row("rpad", "text", "오른쪽 채우기", "길이가 N이 되도록 오른쪽을 채워요.", [T1, num("width", "길이", 5, kind="int"), text("fill", "채울 글자", " ")],
    lambda c: c.x.rpad(c.width, c.fill), "rpad(번호, 5, '0')", TXT, TXT)
row("contains", "text", "포함", "글자가 들어 있으면 참.", [T1, text("find", "찾을 글자", "a")], lambda c: c.x.contains(c.find), "contains(이름, 'a')", TXT, BOOL)
row("startswith", "text", "시작", "글자로 시작하면 참.", [T1, text("find", "찾을 글자", "a")], lambda c: c.x.startswith(c.find), "startswith(이름, 'a')", TXT, BOOL)
row("endswith", "text", "끝", "글자로 끝나면 참.", [T1, text("find", "찾을 글자", "a")], lambda c: c.x.endswith(c.find), "endswith(이름, 'a')", TXT, BOOL)
row("find", "text", "위치 찾기", "글자가 처음 나오는 위치(1부터, 없으면 0).", [T1, text("find", "찾을 글자", "a")], lambda c: c.x.find(c.find) + 1, "find(이름, 'a')", TXT, NUM)
row("reverse", "text", "거꾸로", "글자를 거꾸로.", [T1], lambda c: c.x.reverse(), "reverse(이름)", TXT, TXT)
row("repeat", "text", "반복", "글자를 N번 이어 붙여요.", [T1, num("n", "횟수", 2, kind="int")], lambda c: c.x.repeat(c.n), "repeat(이름, 2)", TXT, TXT)

# ---------- 숫자 ----------

N_ = col(kinds=NUM)


def safe_math(name, x, *args):
    """ln·log10·log·sqrt를 정의역 밖에서 빈 값으로 만든다(DB마다 오류·NaN·무한대로 달라서). 로그는 x가 0 이하, 제곱근은 x가 음수일 때.
    log의 밑(args[0])이 0 이하거나 1이면 모든 행이 빈 값이다. 계산 컬럼(prep_derive)도 같은 함수를 쓴다."""
    if args and (args[0] <= 0 or args[0] == 1):
        return ibis.null().cast("float64")
    return getattr((x >= 0 if name == "sqrt" else x > 0).ifelse(x, ibis.null()), name)(*args)


def half_up(col, digits):
    """반올림: 0.5는 0에서 먼 쪽으로(백엔드마다 달라 직접 정한다. Polars는 짝수 쪽). 정수·소수(Decimal) 컬럼은 그대로 round. 사전 round·계산 줄 반올림·계산 컬럼 round·전처리 반올림이 함께 쓴다.
    2^52 이상(이미 정수)과 nan·inf는 그대로 둔다. floor를 정수로 바꾸는 곳이 넘치지 않게 그런 값은 floor에 넣지 않는다."""
    if not col.type().is_floating():
        return col.round(digits)
    scale = 10.0**digits
    scaled = col.abs() * scale
    big = ~(scaled < 2.0**52)
    done = big.ifelse(0.0, scaled + 0.5).floor() / scale
    return big.ifelse(col, (col < 0).ifelse(-done, done))


def remainder(x, by):
    """나머지: 부호는 나눠지는 수를 따른다(DuckDB·대부분의 DB와 같게. Polars는 나누는 수를 따라 달라서 직접 정한다). 0으로 나누면 빈 값."""
    m = x.abs() % by.nullif(0).abs()
    return (x < 0).ifelse(-m, m)


row("round", "number", "반올림", "소수 자릿수에서 반올림.", [N_, num("digits", "자릿수", 0, kind="int")], lambda c: half_up(c.x, c.digits), "round(금액, 1)", NUM, NUM)
row("ceil", "number", "올림", "정수로 올림.", [N_], lambda c: c.x.ceil(), "ceil(금액)", NUM, NUM)
row("floor", "number", "내림", "정수로 내림.", [N_], lambda c: c.x.floor(), "floor(금액)", NUM, NUM)
row("abs", "number", "절댓값", "부호를 뗀 값.", [N_], lambda c: c.x.abs(), "abs(금액)", NUM, NUM)
row("mod", "number", "나머지", "나눈 나머지(0으로 나누면 빈 값).", [N_, num("by", "나누는 수", 2)], lambda c: remainder(c.x, ibis.literal(c.by)), "mod(번호, 2)", NUM, NUM)
row("power", "number", "거듭제곱", "x의 N제곱.", [N_, num("n", "N", 2)], lambda c: c.x.pow(c.n), "power(금액, 2)", NUM, NUM)
row("sqrt", "number", "제곱근", "제곱근(음수는 빈 값).", [N_], lambda c: safe_math("sqrt", c.x), "sqrt(금액)", NUM, NUM)
row("exp", "number", "지수", "e의 x제곱.", [N_], lambda c: c.x.exp(), "exp(금액)", NUM, NUM)
row("ln", "number", "자연로그", "밑이 e인 로그(0 이하는 빈 값).", [N_], lambda c: safe_math("ln", c.x), "ln(금액)", NUM, NUM)
row("log10", "number", "상용로그", "밑이 10인 로그(0 이하는 빈 값).", [N_], lambda c: safe_math("log10", c.x), "log10(금액)", NUM, NUM)
row("log", "number", "로그(밑 지정)", "밑을 정한 로그(0 이하는 빈 값).", [N_, num("base", "밑", 2)], lambda c: safe_math("log", c.x, c.base), "log(금액, 2)", NUM, NUM)
row("sign", "number", "부호", "음수 -1, 0, 양수 1.", [N_], lambda c: c.x.sign(), "sign(금액)", NUM, NUM)
row("clip", "number", "범위 안으로", "하한보다 작으면 하한, 상한보다 크면 상한으로.", [N_, num("lower", "하한", 0, True), num("upper", "상한", 100, True)],
    lambda c: c.x.clip(lower=c.lower, upper=c.upper), "clip(금액, 0, 100)", NUM, NUM)
row("safe_divide", "number", "나눗셈(0이면 빈 값)", "나누는 값이 0이면 빈 값.", [N_, col("y", "나누는 컬럼", NUM)],
    lambda c: c.x.cast("float64") / c.y.nullif(0).cast("float64"), "safe_divide(금액, 수량)", NUM, NUM)


def bucket(c):
    bounds = c.bounds
    if sorted(bounds) != list(bounds) or len(set(bounds)) != len(bounds):
        raise ValueError("구간 경계는 작은 수부터 겹치지 않게 넣으세요.")
    labels = c.labels or [f"~{bounds[0]}", *[f"{a}~{b}" for a, b in zip(bounds, bounds[1:])], f"{bounds[-1]}~"]
    if len(labels) != len(bounds) + 1:
        raise ValueError(f"라벨은 경계보다 하나 많아야 합니다(경계 {len(bounds)}개 → 라벨 {len(bounds) + 1}개).")
    branches = [(c.x < b, str(label)) for b, label in zip(bounds, labels)]
    return ibis.cases((c.x.isnull(), ibis.null().cast("string")), *branches, else_=str(labels[-1]))


add("bucket", "number", "구간 나누기", "경계 목록으로 구간을 나눠 라벨을 붙여요(경계는 구간의 시작: 10 이상 20 미만).",
    [N_, items("bounds", "경계 목록", [0, 10, 100]), items("labels", "라벨 목록(선택)")], bucket, "bucket(금액, [0, 10, 100])", NUM, TXT)

# ---------- 날짜 ----------

D_ = col(kinds=DAT)
UNIT = choice("unit", "단위", list(UNITS), "day")


def date_part(name):
    return lambda c: getattr(c.x, name)()


def with_time(c):
    """시·분·초 단위는 시각이 있는 컬럼에만 쓴다(날짜 컬럼에 쓰면 Ibis가 영어 오류를 낸다)."""
    if c.unit in ("hour", "minute", "second") and c.x.type().is_date():
        raise ValueError(f"'{UNIT_NAMES[c.unit]}' 단위는 시각이 있는 컬럼(날짜시간)에만 쓸 수 있어요. 날짜 컬럼에는 연·분기·월·주·일을 고르세요.")
    return c


row("truncate", "date", "단위로 자르기", "날짜를 연·분기·월·주·일·시·분·초의 시작으로.", [D_, UNIT], lambda c: with_time(c).x.truncate(UNITS[c.unit]), "truncate(주문일, 'month')", DAT, DAT)
for pid, plabel, method in (("year", "연", "year"), ("quarter", "분기", "quarter"), ("month", "월", "month"), ("week", "주(연중 몇 째 주)", "week_of_year"),
                            ("day", "일", "day"), ("hour", "시", "hour"), ("minute", "분", "minute"), ("second", "초", "second")):
    row(pid, "date", f"{plabel} 뽑기", f"날짜에서 {plabel}만 숫자로.", [D_], date_part(method), f"{pid}(주문일)", DAT, NUM)
row("weekday", "date", "요일 뽑기", "월요일 0 ~ 일요일 6.", [D_], lambda c: c.x.day_of_week.index().cast("int64"), "weekday(주문일)", DAT, NUM)
row("epoch", "date", "epoch(초)", "1970-01-01부터 지난 초.", [D_], lambda c: c.x.cast("timestamp").epoch_seconds(), "epoch(주문일)", DAT, NUM)


def interval(c, sign):
    unit = with_time(c).unit + "s"
    return c.x + ibis.interval(**{unit: sign * c.n})


row("date_add", "date", "더하기", "날짜에 N 단위를 더해요.", [D_, num("n", "N", 1, kind="int"), choice("unit", "단위", list(UNITS), "day")], lambda c: interval(c, 1), "date_add(주문일, 7, 'day')", DAT, DAT)
row("date_sub", "date", "빼기", "날짜에서 N 단위를 빼요.", [D_, num("n", "N", 1, kind="int"), choice("unit", "단위", list(UNITS), "day")], lambda c: interval(c, -1), "date_sub(주문일, 7, 'day')", DAT, DAT)
def passed(start, end, unit):
    """start부터 end까지 지난 단위 수. 연·월은 경계를 센 수가 아니라 가득 찬 단위 수(만 나이: 1990-12-31 → 2026-10-23은 35; 1-31 → 2-28은 0개월). 나머지 단위는 delta 그대로.
    달 수에 months 간격을 더해 보지 않는다(BigQuery의 TIMESTAMP_ADD는 MONTH를 못 하고, 식이 길어진다)."""
    start, end = start.cast("timestamp"), end.cast("timestamp")
    if unit not in ("year", "month"):
        return end.delta(start, unit=unit)
    months = ((end.year() - start.year()) * 12 + (end.month() - start.month())).cast("int64")  # 달력의 달 수 차이
    rest = lambda x: x.epoch_seconds() - x.truncate("M").epoch_seconds()  # 그 달 1일 0시부터 지난 초
    months = months - ((months > 0) & (rest(end) < rest(start))).cast("int64") + ((months < 0) & (rest(end) > rest(start))).cast("int64")  # 달 안의 위치가 모자라면 한 달 모자란 것
    return months if unit == "month" else months.sign() * (months.abs() // 12)


row("date_diff", "date", "두 날짜 차이", "날짜 A에서 날짜 B까지 지난 시간(B−A, 단위로). 연·월은 가득 찬 수.", [D_, col("y", "날짜 B", DAT), choice("unit", "단위", list(UNITS), "day")],
    lambda c: passed(c.x, c.y, c.unit), "date_diff(가입일, 주문일, 'day')", DAT, NUM)
row("strftime", "date", "형식 글자로", "날짜를 형식(%Y-%m-%d 등)의 글자로.", [D_, text("format", "형식", "%Y-%m-%d")], lambda c: c.x.strftime(c.format), "strftime(주문일, '%Y-%m')", DAT, TXT)
row("parse_date", "date", "글자에서 날짜 읽기", "글자를 형식에 맞춰 날짜시간으로.", [T1, text("format", "형식", "%Y-%m-%d")], lambda c: c.x.as_timestamp(c.format).cast("timestamp"), "parse_date(날짜글자, '%Y-%m-%d')", TXT, DAT)
row("to_date", "date", "날짜만", "날짜시간에서 날짜만.", [D_], lambda c: c.x.cast("date"), "to_date(주문시각)", DAT, DAT)
row("to_time", "date", "시각만", "날짜시간에서 시각만.", [D_], lambda c: c.x.cast("timestamp").time(), "to_time(주문시각)", DAT, DAT)
row("diff_now", "date", "지금까지 지난 시간", "그 날짜부터 지금까지 지난 시간(단위로).", [D_, choice("unit", "단위", list(UNITS), "day")],
    lambda c: passed(c.x, ibis.now(), c.unit), "diff_now(가입일, 'day')", DAT, NUM)
row("diff_today", "date", "오늘까지 지난 날", "그 날짜부터 오늘까지 지난 단위 수.", [D_, choice("unit", "단위", ["day", "week", "month", "year"], "day")],
    lambda c: passed(c.x, ibis.today(), c.unit), "diff_today(가입일, 'day')", DAT, NUM)

# ---------- 조건 ----------


def cases(c):
    branches = c.cases
    return ibis.cases(*branches, else_=c.otherwise) if c.otherwise is not None else ibis.cases(*branches)


add("case", "cond", "조건 분류(CASE)", "조건 → 값을 여러 줄 적고, 아무것도 아니면 마지막 값.",
    [{"name": "cases", "kind": "cases", "label": "조건 → 값 목록", "optional": False}, value("otherwise", "아니면", None, True)], cases, "case(금액 >= 100, '고액', 금액 >= 10, '중간', '소액')")
row("coalesce", "cond", "빈 값 채우기(COALESCE)", "앞에서부터 처음으로 빈 값이 아닌 것.", [many()], lambda c: ibis.coalesce(*c.columns), "coalesce(휴대폰, 전화)", "any", "any")
row("nullif", "cond", "같으면 빈 값(NULLIF)", "값이 같으면 빈 값으로.", [col(), value("value", "빈 값으로 볼 값", "", True)], lambda c: c.x.nullif(coerce(c.x, c.value if c.value is not None else "")), "nullif(코드, '')", "any", "any")
add("ifelse", "cond", "참이면·아니면(IF)", "조건이 참이면 앞 값, 아니면 뒤 값.", [cond(), value("then", "참일 때", "1"), value("otherwise", "아닐 때", "0")],
    lambda c: ibis.ifelse(c.cond, c.then, c.otherwise), "iif(금액 >= 100, '고액', '일반')")
row("greatest", "cond", "가장 큰 값(GREATEST)", "여러 컬럼 중 가장 큰 값.", [many()], lambda c: ibis.greatest(*c.columns), "greatest(가격1, 가격2)", "any", "any")
row("least", "cond", "가장 작은 값(LEAST)", "여러 컬럼 중 가장 작은 값.", [many()], lambda c: ibis.least(*c.columns), "least(가격1, 가격2)", "any", "any")
row("isin", "cond", "목록 안에 있나(IN)", "값이 목록 안에 있으면 참.", [col(), items("members", "값 목록", ["a", "b"])], lambda c: c.x.isin([coerce(c.x, v) for v in c.members]), "isin(등급, 'A', 'B')", "any", BOOL)
row("between", "cond", "범위 안인가(BETWEEN)", "하한 이상 상한 이하이면 참.", [col(kinds=ORD), value("low", "하한", "0"), value("high", "상한", "10")],
    lambda c: c.x.between(coerce(c.x, c.low), coerce(c.x, c.high)), "between(금액, 0, 10)", "any", BOOL)
row("isnull", "cond", "빈 값인가", "값이 비었으면 참.", [col()], lambda c: c.x.isnull(), "isnull(휴대폰)", "any", BOOL)
row("notnull", "cond", "값이 있나", "값이 있으면 참.", [col()], lambda c: c.x.notnull(), "notnull(휴대폰)", "any", BOOL)


def coerce(column, value):
    """글자로 받은 값을 컬럼 종류에 맞춘다(숫자 컬럼이면 숫자, 날짜면 날짜). 이미 숫자 등이면 그대로."""
    return value if not isinstance(value, str) or value == "" else parse.convert(column.type(), value)


# ---------- 형 변환 ----------

row("cast", "cast", "형 바꾸기(CAST)", "값을 정수·실수·글자·날짜·날짜시간·참거짓으로. 안 되는 값이 있으면 오류.", [col(), choice("to", "바꿀 형", list(CASTS), "string")],
    lambda c: c.x.cast(CASTS[c.to]), "cast(코드, 'int')")
row("try_cast", "cast", "형 바꾸기(안 되면 빈 값)", "바꿀 수 없는 값은 오류 대신 빈 값으로.", [col(), choice("to", "바꿀 형", list(CASTS), "float")],
    lambda c: c.x.try_cast(CASTS[c.to]), "try_cast(코드, 'int')")

# ---------- 구조 ----------


def json_steps(path):
    found = re.findall(r"\[(\d+)\]|([^.\[\]]+)", path or "")
    if not found:
        raise ValueError("JSON 경로를 넣으세요. 예: user.name, items[0].id")
    return [int(i) if i else k for i, k in found]


def json_get(c):
    value = c.x.cast("json") if c.x.type().is_string() else c.x
    for step in json_steps(c.path):
        value = value[step]
    return value if c.to == "json" else value.unwrap_as(CASTS[c.to])


row("json_get", "struct", "JSON 경로 값", "JSON 컬럼에서 경로(user.name, items[0])의 값.", [col(kinds=("json", TXT)), text("path", "경로", "name"), choice("to", "돌려줄 형", ["string", "int", "float", "bool", "json"], "string")],
    json_get, "json_get(정보, 'user.name')", "json", "any")
def struct_field(c):
    names = c.x.type().names
    if c.field not in names:
        raise ValueError(f"구조에 '{c.field}' 필드가 없습니다. 있는 필드: {', '.join(names)}")
    return c.x[c.field]


row("struct_field", "struct", "구조 필드", "구조 컬럼 안의 필드 하나.", [col(kinds="struct"), text("field", "필드 이름", "name")], struct_field, "struct_field(주소, 'city')", "struct", "any")
row("array_length", "struct", "목록 길이", "목록에 값이 몇 개인지.", [col(kinds="array")], lambda c: c.x.length(), "array_length(태그)", "array", NUM)
row("array_get", "struct", "목록 N번째", "목록의 N번째 값(1부터).", [col(kinds="array"), num("n", "N번째", 1, kind="int")], lambda c: c.x[c.n - 1], "array_get(태그, 1)", "array", "any")
add("unnest", "struct", "목록 펼치기", "목록 컬럼의 값마다 행을 하나씩 만들어요.", [col(kinds="array")], lambda c: c.t.unnest(c.x.get_name()), "unnest(태그)", "array", table=True)

# ---------- 좌표 ----------
# 식은 geo.funcs가 만든다(sin·cos·asin 같은 기본 수식뿐이라 원격 DB에서도 DB 안에서 계산). 위도·경도는 도 단위 숫자 컬럼.

LAT, LON = col("lat", "위도 컬럼", NUM), col("lon", "경도 컬럼", NUM)
SEOUL = (37.5665, 126.978)  # 서울시청: 칸의 기본값(예시)

add("haversine", "geo", "두 점 거리(km)", "두 점(위도·경도)의 큰 원 거리를 km로(haversine). 둘째 점은 숫자 또는 다른 컬럼.",
    [LAT, LON, value("lat2", "상대 위도", SEOUL[0]), value("lon2", "상대 경도", SEOUL[1])], lambda c: geo_funcs.haversine(c.lat, c.lon, c.lat2, c.lon2),
    "haversine(위도, 경도, 37.5665, 126.978)", NUM, NUM)
add("within_km", "geo", "반경 안(km)", "기준점에서 N km 안(경계 포함)이면 참. 조건 줄로는 near 위도 경도 기준위도 기준경도 반경.",
    [LAT, LON, num("clat", "기준 위도", SEOUL[0]), num("clon", "기준 경도", SEOUL[1]), num("km", "반경(km)", 5)], lambda c: geo_funcs.within_km(c.lat, c.lon, c.clat, c.clon, c.km),
    "within_km(위도, 경도, 37.5665, 126.978, 5)", NUM, BOOL)
add("in_box", "geo", "범위 상자 안", "최소·최대 위경도 상자 안(경계 포함)이면 참. 조건 줄로는 box 위도 경도 최소위도 최소경도 최대위도 최대경도.",
    [LAT, LON, num("min_lat", "최소 위도", 33), num("min_lon", "최소 경도", 124), num("max_lat", "최대 위도", 39), num("max_lon", "최대 경도", 132)],
    lambda c: geo_funcs.in_box(c.lat, c.lon, c.min_lat, c.min_lon, c.max_lat, c.max_lon), "in_box(위도, 경도, 33, 124, 39, 132)", NUM, BOOL)
add("grid_id", "geo", "격자 칸 id", "점을 칸 크기(도 단위, 예 0.01 = 약 1.1km)의 격자로 묶는 칸 id(위도칸_경도칸).",
    [LAT, LON, num("size", "칸 크기(도)", 0.01)], lambda c: geo_funcs.grid_id(c.lat, c.lon, c.size), "grid_id(위도, 경도, 0.01)", NUM, TXT)
add("grid_lat", "geo", "격자 칸 중심 위도", "점이 든 격자 칸의 중심 위도.", [col("lat", "위도 컬럼", NUM), num("size", "칸 크기(도)", 0.01)],
    lambda c: geo_funcs.grid_center(c.lat, c.size), "grid_lat(위도, 0.01)", NUM, NUM)
add("grid_lon", "geo", "격자 칸 중심 경도", "점이 든 격자 칸의 중심 경도.", [col("lon", "경도 컬럼", NUM), num("size", "칸 크기(도)", 0.01)],
    lambda c: geo_funcs.grid_center(c.lon, c.size), "grid_lon(경도, 0.01)", NUM, NUM)

# ---------- 묶음 ----------


def having(c):
    return c.t.filter(c.cond)


add("having", "group", "묶은 뒤 거르기(HAVING)", "집계한 결과 표에서 조건에 맞는 줄만.", [cond()], having, "having(합계 > 100)", table=True)


def qualify(c):
    f = get(c.func)
    if not f["window"]:
        raise ValueError("QUALIFY에는 창 함수(순위·행 번호·이전 값 …)를 고르세요.")
    key = "__ibis_q"
    raw = dict(c.func_args or {})
    tt = c.t.mutate(**{key: f["build"](resolve(f, c.t, raw))})
    return tt.filter(OPS[c.op](tt[key], c.value)).select(*c.t.columns)


add("qualify", "group", "창 결과로 거르기(QUALIFY)", "창 함수 결과(예: 순위)가 조건에 맞는 행만.",
    [text("func", "창 함수 id", "rank"), {"name": "func_args", "kind": "dict", "label": "창 함수 인자", "optional": True, "default": None},
     choice("op", "비교", list(OPS), "<="), num("value", "값", 3)], qualify, "qualify(rank, 기준 금액 desc, <= 3)", table=True)


def agg_specs(t, specs):
    """집계 목록 [{agg, y, where, opts, name}]을 {이름: 집계 식}으로."""
    aggs = {}
    for spec in specs:
        name = spec.get("name") or f"{spec['agg']}_{spec.get('y') or ''}".rstrip("_")
        aggs[name] = aggregate(spec["agg"], t, spec.get("y"), spec.get("where"), spec.get("opts"))
    return aggs


def grouping(t, by, aggs, sets):
    """묶음 기준(by)의 조합(sets)마다 aggs({이름: t 위의 집계 식})를 구해 잇는다(Ibis에는 ROLLUP·CUBE·GROUPING SETS가 없다).
    빠진 기준 칸은 '(소계)', 모두 빠지면 '(전체)'. 기준 칸은 글자가 된다."""
    parts = []
    for keep in sets:
        keys = [t[b] for b in by if b in keep]
        grouped = t.group_by(keys).aggregate(**aggs) if keys else t.aggregate(**aggs)
        shown = {b: (grouped[b].cast("string") if b in keep else ibis.literal(SUBTOTAL if keep else TOTAL)) for b in by}
        parts.append(grouped.select(**shown, **{n: grouped[n] for n in aggs}))
    return ibis.union(*parts, distinct=False)


def group_sets(kind, by, sets=None):
    """묶음 기준 조합들. ROLLUP은 뒤에서부터 하나씩 뺀 것, CUBE는 모든 조합, GROUPING SETS(sets)는 받은 조합(기준에 있는 이름만)."""
    if kind == "rollup":
        return [by[:i] for i in range(len(by), -1, -1)]
    if kind == "cube":
        from itertools import combinations
        return [list(s) for n in range(len(by), -1, -1) for s in combinations(by, n)]
    sets = [list(s) for s in sets or []]
    for s in sets:
        for b in s:
            parse.check_name(b, by, "묶음 기준")
    return sets


def levels(c, kind):
    by = [b.get_name() for b in c.columns]
    return by, group_sets(kind, by, c.sets)


AGG_LIST = {"name": "aggs", "kind": "list", "label": "집계 목록 [{agg, y, name}]", "optional": False, "default": None}
for gid, glabel, ghelp in (("rollup", "소계·총계(ROLLUP)", "기준을 뒤에서부터 하나씩 빼며 소계와 총계 줄을 더해요."),
                           ("cube", "모든 조합(CUBE)", "기준의 모든 조합마다 집계해요."),
                           ("grouping_sets", "묶음 조합 고르기(GROUPING SETS)", "고른 조합마다 집계해요.")):
    extra = [items("sets", "조합 목록(기준 이름의 목록들)")] if gid == "grouping_sets" else []
    add(gid, "group", glabel, ghelp, [many("columns", "묶음 기준"), AGG_LIST, *extra],
        (lambda kind: lambda c: grouping(c.t, levels(c, kind)[0], agg_specs(c.t, c.aggs), levels(c, kind)[1]))(gid), f"{gid}(지역, 상품 / 합계 금액)", table=True)
add("distinct", "group", "중복 빼기(DISTINCT)", "같은 줄을 하나로(컬럼을 고르면 그 컬럼들만).", [{"name": "columns", "kind": "columns", "label": "컬럼들(선택)", "optional": True}],
    lambda c: c.t.select(*[v.get_name() for v in c.columns]).distinct() if c.columns else c.t.distinct(), "distinct(고객, 상품)", table=True)


RAW = "sql_expr"  # SQL 식 직접 쓰기의 id


def raw_only(c):
    raise ValueError("SQL 식 직접 쓰기는 raw_expr로 붙입니다.")


add(RAW, "cond", "SQL 식 직접 쓰기", "DB 고유 함수가 필요할 때: SQL 식 한 줄을 새 컬럼으로 붙여요(읽기만 하는 식, 파일 읽기는 막아요).",
    [text("expr", "SQL 식", "upper(name)"), text("name", "새 컬럼 이름", "new_col")], raw_only, "sql_expr(LOWER(name), 소문자)", raw=True)


# ---------- 대표 값 고르기 (10-2) ----------

REPRESENTATIVE = {  # 선택지 → (보이는 이름, 집계 id, 기준 컬럼이 필요한가)
    "any_value": ("아무 값", "any_value", False),
    "first": ("정렬 기준의 첫 값", "first", True),
    "last": ("정렬 기준의 마지막 값", "last", True),
    "min": ("최소", "min", False),
    "max": ("최대", "max", False),
    "mode": ("최빈값", "mode", False),
    "string_agg": ("모두 이어 붙이기", "string_agg", False),
    "warn": ("여러 개면 알리기(그 칸은 빈 값)", "unique_or_null", False),
}


def pick(how, t, column, order=None, sep=",", where=None):
    """겹치는 값 중 하나를 고르는 집계 식 하나(어느 집계 칸·중복 제거·조인 대표 행에서나 같다). how는 REPRESENTATIVE의 선택지.
    first·last는 정렬 기준(order: "컬럼 [asc|desc]" 목록)이 있어야 하고, string_agg는 구분자(sep)로 잇는다."""
    if how not in REPRESENTATIVE:
        raise ValueError(f"대표 값 고르기는 {', '.join(REPRESENTATIVE)} 중 하나입니다.")
    if REPRESENTATIVE[how][2] and not order:
        raise ValueError(f"'{REPRESENTATIVE[how][0]}'에는 정렬 기준이 필요합니다.")
    opts = {"order": order} if how in ("first", "last", "string_agg") and order else {}
    if how == "string_agg":
        opts["sep"] = sep
    return aggregate(REPRESENTATIVE[how][1], t, column, where, opts)


def conflicts(t, by, columns):
    """by가 같은 묶음 중 columns의 값이 둘 이상인 묶음 수(여러 개면 알리기에서 보여 줄 숫자). 묶음마다 서로 다른 값 수(빈 값 제외)가 2 이상인 컬럼이 하나라도 있으면 센다."""
    keys = [t[b] for b in by]
    sizes = t.group_by(keys).aggregate(**{f"n{i}": distinct(t[c]) for i, c in enumerate(columns)})
    many_ = ibis.or_(*[sizes[f"n{i}"] > 1 for i in range(len(columns))])
    return sizes.filter(many_).count()


# ---------- 화면에 주는 목록 ----------

SCREENS = ("pivot", "chart", "metrics", "dash", "calc", "prep", "sentence")  # 함수를 고르는 화면


GROUP_PIVOT = ("rollup", "cube", "grouping_sets", "having")  # 피벗(집계 탭)이 더 해 주는 묶음 함수
GROUP_CALC = ("qualify", "unnest")  # 계산 줄이 하는 묶음 함수(창 결과로 거르기, 펼치기)


def ids_for(screen):
    """화면(SCREENS)에서 고를 수 있는 함수 id들. 집계 칸(pivot·chart·metrics·dash)은 집계 전부(피벗은 소계·HAVING도), 계산 줄(calc)은 창 함수·행 함수·QUALIFY·펼치기,
    전처리(prep)는 행·창·집계(묶음 안 값으로)·DISTINCT, 문장(sentence)은 전부."""
    if screen not in SCREENS:
        raise ValueError(f"화면은 {', '.join(SCREENS)} 중 하나입니다.")
    plain = lambda f: not (f["aggregate"] or f["window"] or f["table"] or f["raw"])
    pick_ = {"pivot": lambda f: f["aggregate"] or f["id"] in GROUP_PIVOT, "chart": lambda f: f["aggregate"], "metrics": lambda f: f["aggregate"], "dash": lambda f: f["aggregate"],
             "calc": lambda f: f["window"] or plain(f) or f["id"] in GROUP_CALC or f["raw"], "prep": lambda f: not (f["table"] or f["raw"]) or f["id"] == "distinct",
             "sentence": lambda f: True}[screen]
    return [i for i, f in FUNCS.items() if pick_(f)]


MERGE = ("count", "sum", "mean", "min", "max")  # 일 값을 주·월·연으로 다시 묶을 수 있는 집계(대시보드 카드). 나머지는 단위마다 서버가 따로 구한다


def agg_names():
    """집계 id(예전 이름 포함) → 한국어 이름. 화면이 작은 목록으로 바로 쓰는 값(server.CONSTANTS)."""
    return {**{i: FUNCS[i]["label"] for i in aggregate_ids()}, **{a: FUNCS[t]["label"] for a, t in ALIASES.items()}}


def version():
    """사전의 내용이 바뀌면 바뀌는 글(화면이 /api/funcs를 다시 받을지 정하는 ETag)."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(catalog(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def public(f):
    """JSON으로 화면에 보낼 항목(build 없음)."""
    return {k: v for k, v in f.items() if k != "build"}


def catalog():
    """화면에 보내는 사전 {categories, funcs: [항목], screens: {화면: [id]}, aliases, representative, units, casts}."""
    return {"categories": [{"id": i, "label": label} for i, label in CATEGORIES], "funcs": [public(f) for f in FUNCS.values()],
            "screens": {s: ids_for(s) for s in SCREENS}, "aliases": ALIASES,
            "representative": {k: {"label": v[0], "func": v[1], "needs_order": v[2]} for k, v in REPRESENTATIVE.items()},
            "units": UNIT_NAMES, "casts": CAST_NAMES}


def aggregate_ids():
    """집계 함수 id 전부(문장의 {집계} 칸이 받는 목록)."""
    return [i for i, f in FUNCS.items() if f["aggregate"]]


def label_of(id):
    return get(id)["label"]


# ---------- 지원 판정과 대체 ----------

def sub(id, note, build):
    """연결이 원래 식을 못 할 때 쓸 같은 결과의 대체 식을 올린다."""
    SUBS.setdefault(id, []).append((note, build))


def moments(c):
    """분산·공분산·상관계수를 평균·개수만으로 구하는 재료. 두 컬럼이면 둘 다 값이 있는 행만 쓴다."""
    y = c.y if c.y is not None else c.x
    w = both(c.x.notnull(), y.notnull(), c.where)
    n = c.t.count(where=w)
    mx, my, mxy = c.x.mean(where=w), y.mean(where=w), (c.x * y).mean(where=w)
    return n, mx, my, mxy, (c.x * c.x).mean(where=w), (y * y).mean(where=w), w


def sample(n, pop):
    """모집단 값 → 표본 값(n/(n-1) 곱하기, n이 1이면 빈 값)."""
    return pop * n.cast("float64") / (n - 1).nullif(0).cast("float64")


def var_pop(c):
    n, mx, _, _, mxx, _, _ = moments(c)
    return mxx - mx * mx


def cov_pop(c):
    n, mx, my, mxy, *_ = moments(c)
    return mxy - mx * my


sub("var_pop", "평균 제곱 − 제곱 평균으로 구해요.", var_pop)
sub("var", "평균 제곱 − 제곱 평균에 n/(n-1)을 곱해 구해요.", lambda c: sample(moments(c)[0], var_pop(c)))
sub("std_pop", "분산의 제곱근으로 구해요.", lambda c: var_pop(c).sqrt())
sub("std", "분산의 제곱근으로 구해요.", lambda c: sample(moments(c)[0], var_pop(c)).sqrt())
sub("cov_pop", "곱의 평균 − 평균의 곱으로 구해요.", cov_pop)
sub("cov", "곱의 평균 − 평균의 곱에 n/(n-1)을 곱해 구해요.", lambda c: sample(moments(c)[0], cov_pop(c)))
sub("corr", "공분산 ÷ (표준편차 × 표준편차)로 구해요.",
    lambda c: cov_pop(c) / ((moments(c)[4] - moments(c)[1] ** 2) * (moments(c)[5] - moments(c)[2] ** 2)).sqrt().nullif(0))
sub("any_value", "아무 값 대신 최솟값을 써요.", lambda c: c.x.min(where=c.where))
sub("approx_count_distinct", "근사 대신 정확한 고유 개수를 세요(큰 표에서는 느릴 수 있어요).", lambda c: distinct(c.x, c.where))
sub("median", "분위수 0.5로 구해요.", lambda c: c.x.quantile(0.5, where=c.where))
sub("p50", "분위수 0.5로 구해요.", lambda c: c.x.quantile(0.5, where=c.where))
sub("string_agg", "값을 목록으로 모아 이어 붙여요.", lambda c: c.x.cast("string").collect(where=c.where, distinct=c.distinct == "yes").join(c.sep))
sub("first", "정렬 기준이 하나면 가장 작은 쪽(내림차순이면 가장 큰 쪽)의 값으로 구해요.",
    lambda c: (c.x.argmax if c.order[0].op().descending else c.x.argmin)(c.order[0].op().arg.to_expr(), where=c.where) if len(c.order) == 1 else c.x.first(where=c.where, order_by=c.order))
sub("arg_min", "정렬 기준의 첫 값으로 구해요.", lambda c: c.x.first(where=c.where, order_by=[c.by.asc()]))
sub("arg_max", "정렬 기준의 마지막 값으로 구해요.", lambda c: c.x.last(where=c.where, order_by=[c.by.asc()]))


def probe_args(f, t):
    """함수 f를 표 t의 컬럼으로 시험해 볼 인자(칸마다 맞는 종류의 컬럼, 나머지는 기본값). 맞는 컬럼이 없으면 None."""
    cols = {n: kind_of(d) for n, d in t.schema().items()}
    used = []

    def pick_col(kinds, avoid=()):
        found = [n for n, k in cols.items() if (kinds == "any" or k in ([kinds] if isinstance(kinds, str) else kinds)) and n not in avoid]
        if kinds == "any":  # 아무 컬럼이나면 글자 컬럼을 먼저(기본값이 글자라서)
            found.sort(key=lambda n: cols[n] != TXT)
        return found[0] if found else None
    raw, missing = {}, []
    for spec in f["args"]:
        kind, name = spec["kind"], spec["name"]
        if kind == "column":
            n = pick_col(spec.get("kinds", "any"), used)
            if n is None:
                n = pick_col(spec.get("kinds", "any"))
            if n is None:
                if spec.get("optional"):
                    continue
                return None
            used.append(n)
            raw[name] = n
        elif kind == "columns":
            if name in HAS_ARGS or spec.get("optional"):
                continue
            first = pick_col("any")
            raw[name] = [first, next((n for n, k in cols.items() if k == cols[first] and n != first), first)]  # 같은 종류의 컬럼 둘
        elif kind == "cond":
            if name == "where":
                continue
            raw[name] = t[pick_col("any")].notnull()
        elif kind == "order":
            n = pick_col((NUM, DAT, TXT))
            if n is None:
                return None
            raw[name] = [t[n].asc()]
        elif kind == "cases":
            raw[name] = [{"when": t[pick_col("any")].notnull(), "then": 1}]
        elif kind == "dict":
            continue
        elif spec.get("default") is not None:
            raw[name] = spec["default"]
        elif not spec.get("optional"):
            missing.append(name)
    if f["id"] == "struct_field":
        raw["field"] = list(t[raw["x"]].type().names)[0]
    if f["id"] == "qualify":
        raw.update({"func": "rank", "func_args": {"order": [t[pick_col((NUM, DAT, TXT))].asc()]}})
    if f["id"] in ("rollup", "cube", "grouping_sets"):
        by = pick_col("any")
        raw.update({"columns": [by], "aggs": [{"agg": "count", "name": "rows__"}]})
        if f["id"] == "grouping_sets":
            raw["sets"] = [[by], []]
    return None if [m for m in missing if m not in raw] else raw


def probe_expr(f, t, build=None):
    """함수 f를 시험 인자로 t 위에 건 식(집계는 t.aggregate, 행·창 함수는 select, 묶음 함수는 새 표). 시험할 컬럼이 없으면 None."""
    raw = probe_args(f, t)
    if raw is None:
        return None
    if f["table"]:
        return f["build"](resolve(f, t, raw))
    value = (build or f["build"])(resolve(f, t, raw))
    return t.aggregate(v=value) if f["aggregate"] else t.select(v=value)


def judge(f, t, works):
    """works(식) → 그 식이 되는지. 원래 식 → 대체 식 → 이 PC 사본 순으로 {status, note}."""
    try:
        expr = probe_expr(f, t)
    except Exception as e:
        return {"status": "unavailable", "note": f"이 표에서는 쓸 수 없어요: {str(e)[:120]}"}
    if expr is None:
        return {"status": "unavailable", "note": f"맞는 종류의 컬럼({kinds_text(f['kind_in'])})이 이 표에 없어요."}
    if works(expr):
        return {"status": "ok", "note": ""}
    for note, build in SUBS.get(f["id"], []):
        try:
            other = probe_expr(f, t, build)
        except Exception:
            continue
        if other is not None and works(other):
            return {"status": "substitute", "note": note}
    return {"status": "local", "note": "이 연결이 못 하는 계산이라 이 PC 사본에서 계산해요."}


def support(t, ids=None):
    """지금 연결(표 t)이 각 함수를 하는지 {id: {status, note}}. source.compiles로 SQL(계획)만 만들어 보고 실행하지 않는다.
    status: ok / substitute(같은 결과의 대체, note에 설명) / local(이 PC 사본에서 계산) / unavailable(note에 이유)."""
    from core import source
    ok = {"status": "ok", "note": ""}  # SQL 식 직접 쓰기는 식을 만들어 시험할 수 없어(파일 연결은 PC 사본) 그대로 둔다
    return {i: ok if FUNCS[i]["raw"] else judge(FUNCS[i], t, source.compiles) for i in (ids or FUNCS)}


def dialect_status(id, dialect, t=None):
    """방언(sqlglot 이름)으로 SQL을 만들 수 있는지(실행하지 않음) {status, note}. 시험 표는 모든 종류의 컬럼을 가진 빈 표."""
    t = t if t is not None else probe_table()

    def works(expr):
        try:
            ibis.to_sql(expr, dialect=dialect)
            return True
        except Exception:
            return False
    return judge(get(id), t, works)


def probe_table():
    """모든 종류의 컬럼을 가진 시험용 빈 표(SQL 만들기 시험에 쓴다)."""
    return ibis.table({"n": "float64", "i": "int64", "s": "string", "d": "timestamp", "dd": "date", "b": "boolean", "j": "json", "a": "array<string>",
                       "st": "struct<k: string>"}, name="probe")


# ---------- SQL 식 직접 쓰기 ----------

class NeedsLocal(ValueError):
    """파일 연결(Polars)은 SQL을 받지 못해, 이 PC 사본(DuckDB)에서 한 뒤에 붙여야 한다."""


MAX_RAW = 1000  # SQL 식 한 줄의 글자 수


def raw_sql(expr, name, text):
    """식 expr 뒤에 SQL 식 text를 새 컬럼 name으로 붙이는 검사된 SQL 글: SELECT *, (<식>) AS 이름 FROM (<지금 식의 SQL>).
    sql_exec.check(읽기만 하는 문장 하나, 파일·환경 읽기 막기)를 거치고, 식이 바깥으로 빠져나가는 모양(컬럼이 둘 이상 생김)이면 거부한다.
    SQL을 만들 수 없는 연결(Polars 파일)은 NeedsLocal."""
    from core import sql_exec
    backend = ibis.get_backend(expr)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("SQL 식을 입력하세요.")
    if len(text) > MAX_RAW:
        raise ValueError(f"SQL 식은 {MAX_RAW}자까지입니다.")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("새 컬럼 이름은 영문·한글·숫자·밑줄로 쓰고 글자로 시작해야 합니다(63자까지).")
    if name in expr.columns:
        raise ValueError(f"'{name}' 컬럼이 이미 있습니다. 다른 이름을 쓰세요.")
    local = backend.name == "polars"  # 파일 연결은 SQL을 못 받는다: DuckDB 방언으로 식만 검사하고 PC 사본(DuckDB)에서 붙인다
    dialect = "duckdb" if local else sql_exec.dialect_of(backend)
    full = f"SELECT *, ({text.strip()}) AS {sql_exec.quote(name)} FROM ({'ibis_base' if local else backend.compile(expr)}) AS ibis_raw"
    tree = sql_exec.check(full, dialect)
    exp = sql_exec.glot()[1]
    frm = tree.args.get("from") or tree.args.get("from_")  # 앞 단계를 감싼 FROM이 그대로 ibis_raw 서브쿼리 하나인지(주석 -- 로 FROM이 바뀌지 않았는지)
    if (not isinstance(tree, exp.Select) or len(tree.expressions) != 2 or tree.expressions[1].alias != name
            or not isinstance(frm and frm.this, exp.Subquery) or frm.this.alias != "ibis_raw" or {k for k, v in tree.args.items() if v} - {"expressions", "from", "from_"}):
        raise sql_exec.SecurityViolationError("SQL 식은 값 하나를 계산하는 식이어야 합니다(괄호를 닫거나 컬럼을 더 만들 수 없어요).")
    if local:
        raise NeedsLocal("파일 연결은 SQL 식을 바로 붙일 수 없어요. 이 PC 사본에서 계산해요.")
    return tree.sql(dialect=dialect)  # 검사한 트리를 다시 만든 글을 실행한다(검사한 것과 실행하는 것이 같다)


def raw_expr(expr, name, text):
    """SQL 식을 새 컬럼으로 붙인 Ibis 식(아직 실행하지 않는다). 연결의 sql()로 만든다. 파일 연결(Polars)은 NeedsLocal."""
    return ibis.get_backend(expr).sql(raw_sql(expr, name, text))


def local_only(expr, name):
    """파일 연결이 못 하는 계산(SQL 식)의 자리 표시: 글자 컬럼 name을 더한 식. 창 함수가 들어 있어 Polars 계획으로 바뀌지 않으므로(source.compiles가 False)
    core.runnable이 이 PC 사본(DuckDB)에서 진짜 식으로 다시 만든다. 컬럼 목록을 보여 줄 때만 쓰이고 값은 계산하지 않는다."""
    return expr.mutate(**{name: ibis.row_number().over(ibis.window()).cast("string")})


def grouped_order(table, by):
    """소계·총계가 든 표의 정렬: 기준 칸마다 (소계·총계 줄은 뒤로, 값 순). 소계는 그 묶음의 세부 줄 바로 뒤, 총계는 맨 끝."""
    keys = []
    for b in by:
        keys += [table[b].isin([SUBTOTAL, TOTAL]).cast("int64"), table[b]]
    return keys
