"""한 줄 입력(조인·기간·조건·정렬·표시 컬럼)과 이어붙이기를 해석해 Ibis 식으로 바꾼다. 입력·출력은 하지 않는다."""
import datetime as dt
import difflib
import functools
import operator
import re

from lazy import ibis

JOINS = ["inner", "left", "right", "outer"]
UNIONS = ["left", "inner"]  # 이어붙이기: left는 기준 테이블의 컬럼을 모두(없는 컬럼은 빈 값), inner는 모든 테이블에 있는 컬럼만
SOURCE = "출처"  # 이어붙이면 더하는 컬럼: 그 행이 온 테이블 이름
OPS = {"=": operator.eq, "!=": operator.ne, ">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
CONDITION_GUIDE = "컬럼 >= 값 / 컬럼 != 값 / 컬럼 between 값1 값2 / 컬럼 in 값1, 값2 / 컬럼 except 값1, 값2 / 컬럼 like 값 / 컬럼 is [not] null / 조건 or 조건 / 값에 or·쉼표가 있으면 '값'"
SORT_GUIDE = "컬럼 [asc|desc]"

# (판별, 종류, 한글 이름, 입력값 변환). 종류는 전처리가 컬럼을 나눌 때 쓴다.
TYPES = [
    (lambda t: t.is_boolean(), "bool", "참/거짓", lambda s: {"true": True, "false": False}[s.lower()]),
    (lambda t: t.is_integer(), "int", "정수", int),
    (lambda t: t.is_floating() or t.is_decimal(), "float", "실수", float),
    (lambda t: t.is_date(), "date", "날짜", dt.date.fromisoformat),
    (lambda t: t.is_timestamp(), "date", "날짜시간", dt.datetime.fromisoformat),
    (lambda t: t.is_string(), "string", "문자", str),
]

COND_RE = re.compile(r"^(\S+?)\s*(!=|>=|<=|=|>|<)\s*(.+)$")
BETWEEN_RE = re.compile(r"^(\S+)\s+between\s+(\S+)\s+(?:and\s+)?(\S+)$", re.I)
IN_RE = re.compile(r"^(\S+)\s+(in|except)\s+(.+)$", re.I)
LIKE_RE = re.compile(r"^(\S+)\s+like\s+(.+)$", re.I)
NULL_RE = re.compile(r"^(\S+)\s+is\s+(not\s+)?null$", re.I)
OR_RE = re.compile(r"(?<!\S)or(?!\S)", re.I)  # 앞뒤가 공백인 단어 or
COMMA_RE = re.compile(",")
# 따옴표로 감싼 값: 값의 맨 앞(줄 처음이나 공백 , = < > 뒤)에서 열고, 값의 맨 뒤(줄 끝이나 공백 , 앞)에서 닫는다
QUOTE_RE = re.compile(r"""(?<![^\s,=<>])(['"])(?:.*?\1(?![^\s,]))?""")


def type_info(dtype):
    """(한글 이름, 변환 함수). 모르는 타입은 문자로 다룬다."""
    return next(((name, cast) for check, _, name, cast in TYPES if check(dtype)), (str(dtype), str))


def kind(dtype):
    """int, float, string, date(날짜·날짜시간), bool, time, other 중 하나."""
    return next((k for check, k, *_ in TYPES if check(dtype)), "time" if dtype.is_time() else "other")


def convert(dtype, text):
    name, cast = type_info(dtype)
    try:
        return cast(text)
    except (ValueError, KeyError):
        raise ValueError(f"'{text}'은 {name} 값이 아닙니다.")


def split(pattern, text, quotes=True):
    """pattern으로 나눈다. 따옴표로 감싼 부분은 나누지 않는다. quotes가 False면 따옴표도 글자로 본다."""
    if not quotes:
        return pattern.split(text)
    quoted = [m.span() for m in QUOTE_RE.finditer(text)]
    if any(end - start == 1 for start, end in quoted):
        raise ValueError("따옴표가 닫히지 않았습니다. 값에 따옴표가 있으면 다른 따옴표로 감싸세요.")
    parts, pos = [], 0
    for m in pattern.finditer(text):
        if not any(start <= m.start() < end for start, end in quoted):
            parts.append(text[pos:m.start()])
            pos = m.end()
    return parts + [text[pos:]]


def unquote(text, quotes=True):
    """앞뒤 공백을 떼고, 값 전체를 감싼 따옴표 한 쌍이 있으면 벗긴다. quotes가 False면 벗기지 않는다."""
    text = text.strip()
    if not quotes:
        return text
    m = QUOTE_RE.match(text)
    return text[1:-1] if m and len(text) > 1 and m.end() == len(text) else text


def check_name(name, names, what="컬럼"):
    if name not in names:
        close = difflib.get_close_matches(name, list(names), n=1)
        raise ValueError(f"'{name}' {what}이 없습니다." + (f" 비슷한 이름: {close[0]}" if close else ""))


# 이어붙이기: {"tables": [테이블…], "how": "left" | "inner"}. 테이블을 기준 테이블 아래로 잇는다(행이 늘어난다).
def parse_union(union, tables, base):
    """이어붙이기를 검사해 (테이블들, 방식)으로. 없거나 테이블을 고르지 않았으면 None."""
    if not union:
        return None
    names, how = (union.get("tables") or [], union.get("how", "left")) if isinstance(union, dict) else (None, None)
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError('이어붙이기는 {"tables": [테이블…], "how": "left" 또는 "inner"} 모양이어야 합니다.')
    if not names:
        return None
    if how not in UNIONS:
        raise ValueError(f"이어붙이기 방식은 {', '.join(UNIONS)} 중 하나입니다.")
    for name in names:
        check_name(name, tables, "테이블")
    if base in names or len(set(names)) != len(names):
        raise ValueError("이어붙일 테이블은 기준 테이블과 다르고 서로 겹치지 않아야 합니다.")
    return names, how


def apply_union(base, base_name, union, get_table):
    """기준 테이블 아래로 테이블들을 잇고 SOURCE 컬럼에 온 테이블 이름을 넣는다. 타입은 기준 테이블을 따른다(바꿀 수 없는 값은 그 연결의 오류).
    left: 기준 테이블의 컬럼을 모두 남기고 붙이는 테이블에 없는 컬럼은 빈 값(붙이는 테이블에만 있는 컬럼은 뺀다). inner: 모든 테이블에 있는 컬럼만."""
    if union is None:
        return base
    names, how = union
    if SOURCE in base.columns:
        raise ValueError(f"기준 테이블에 '{SOURCE}' 컬럼이 이미 있어 이어붙일 수 없습니다.")
    others = [(name, get_table(name)) for name in names]
    columns = [c for c in base.columns if how == "left" or all(c in t.columns for _, t in others)]
    if not columns:
        raise ValueError("모든 테이블에 같이 있는 컬럼이 없어 inner로 이어붙일 수 없습니다.")
    schema = base.schema()
    shaped = lambda t, name: t.select(*[(t[c] if c in t.columns else ibis.null()).cast(schema[c]).name(c) for c in columns], **{SOURCE: ibis.literal(name)})
    return ibis.union(shaped(base, base_name), *[shaped(t, name) for name, t in others], distinct=False)


# 조인: "테이블 [inner|left|right|outer] 왼쪽=오른쪽, ..."
def parse_join(line, tables):
    name, _, rest = line.partition(" ")
    check_name(name, tables, "테이블")
    first, _, after = rest.strip().partition(" ")
    how, keys = (first.lower(), after) if first.lower() in JOINS else ("inner", rest)
    pairs = []
    for k in keys.split(","):
        left, _, right = (s.strip() for s in k.partition("="))
        if left:
            pairs.append((left, right or left))
    if not pairs:
        raise ValueError("조인 컬럼을 입력하세요. 예: customers left customer_id=id")
    return name, how, pairs


def apply_joins(base, joins, get_table):
    """joins: [(테이블, 종류, [(왼쪽, 오른쪽)])]. 컬럼이 없으면 ValueError."""
    expr = base
    for name, how, pairs in joins:
        right = get_table(name)
        for left, r in pairs:
            check_name(left, expr.columns)
            check_name(r, right.columns)
        expr = expr.join(right, [expr[left] == right[r] for left, r in pairs], how=how)
    return expr


# 기간: "날짜컬럼 시작 ~ 끝" (한쪽은 비워도 됨)
def parse_period(line, expr):
    col, _, rng = line.partition(" ")
    date_cols = [c for c, t in expr.schema().items() if t.is_date() or t.is_timestamp()]
    check_name(col, date_cols, "날짜 컬럼")
    start, sep, end = (s.strip() for s in rng.partition("~"))
    if not sep or not (start or end):
        raise ValueError("'날짜컬럼 시작 ~ 끝' 형식으로 입력하세요.")
    dtype = expr.schema()[col]
    preds = []
    if start:
        preds.append(expr[col] >= convert(dtype, start))
    if end:
        value = convert(dtype, end)
        # 날짜시간 컬럼에 끝을 날짜로만 쓰면 그날 끝까지 포함한다
        after = dtype.is_timestamp() and next_day(end)
        preds.append(expr[col] < after if after else expr[col] <= value)
    return preds


def next_day(text):
    """날짜만 쓴 값이면 다음 날 0시, 시간이 있으면 None."""
    try:
        return dt.datetime.combine(dt.date.fromisoformat(text), dt.time()) + dt.timedelta(days=1)
    except (ValueError, OverflowError):
        return None


# 조건: CONDITION_GUIDE의 형식 중 하나. 한 줄에서 or로 나눈 조건은 하나만 맞아도 된다
# 값을 '값'이나 "값"으로 감싸면 그 안의 or와 쉼표는 값의 일부다
# quotes가 False면 따옴표를 해석하지 않고 글자 그대로 찾는다(줄마다 화면에서 정한다)
def parse_condition(line, expr, quotes=True):
    parts = split(OR_RE, line, quotes)
    if len(parts) == 1:
        return parse_one(line, expr, quotes)
    return functools.reduce(operator.or_, [parse_one(p.strip(), expr, quotes) for p in parts])


def parse_one(line, expr, quotes=True):
    schema = expr.schema()
    if m := NULL_RE.match(line):
        col, negate = m.groups()
        check_name(col, schema.names)
        return expr[col].notnull() if negate else expr[col].isnull()
    if m := BETWEEN_RE.match(line):
        col, lo, hi = m.groups()
        check_name(col, schema.names)
        lo, hi = convert(schema[col], unquote(lo, quotes)), convert(schema[col], unquote(hi, quotes))
        return expr[col].between(lo, hi)
    if m := IN_RE.match(line):  # except는 in의 반대: 적은 값을 뺀다
        col, word, raw = m.groups()
        check_name(col, schema.names)
        values = [convert(schema[col], unquote(v, quotes)) for v in split(COMMA_RE, raw, quotes) if v.strip()]
        return expr[col].isin(values) if word.lower() == "in" else expr[col].notin(values)
    if m := LIKE_RE.match(line):  # 포함 검색. 대소문자는 가리지 않는다
        col, raw = m.groups()
        check_name(col, schema.names)
        if not schema[col].is_string():
            raise ValueError("like는 문자 컬럼에만 쓸 수 있습니다.")
        raw = unquote(raw, quotes)
        return expr[col].lower().contains(raw.lower())
    if m := COND_RE.match(line):
        col, op, raw = m.groups()
        check_name(col, schema.names)
        value = convert(schema[col], unquote(raw, quotes))
        return OPS[op](expr[col], value)
    raise ValueError(f"형식: {CONDITION_GUIDE}")


def predicate(cond, expr):
    """조건 하나를 Ibis 식으로. 문자열, 따옴표 해석을 끈 {"line": ..., "quotes": False},
    화면에서 or로 이은 묶음 {"any": [조건, ...]} 중 하나다. 묶음 안은 or, 조건끼리는 and."""
    if isinstance(cond, str):
        return parse_condition(cond, expr)
    if "any" in cond:
        if not cond["any"]:
            raise ValueError("or로 묶을 조건이 없습니다.")
        return functools.reduce(operator.or_, [predicate(c, expr) for c in cond["any"]])
    return parse_condition(cond["line"], expr, cond.get("quotes", True))


# 정렬: "컬럼 [asc|desc]"
def parse_sort(line, expr):
    col, _, how = line.partition(" ")
    how = how.strip().lower() or "asc"
    check_name(col, expr.columns)
    if how not in ("asc", "desc"):
        raise ValueError(f"형식: {SORT_GUIDE}")
    return expr[col].desc() if how == "desc" else expr[col].asc()


# 표시 컬럼: "컬럼1, 컬럼2"
def parse_columns(line, expr):
    cols = [c.strip() for c in line.split(",") if c.strip()]
    for c in cols:
        check_name(c, expr.columns)
    return cols


def apply(expr, preds, keys, cols):
    """조건 → 정렬 → 표시 컬럼 순으로 적용한다. 빈 목록은 건너뛴다."""
    if preds:
        expr = expr.filter(*preds)
    if keys:
        expr = expr.order_by(keys)
    return expr.select(list(dict.fromkeys(cols))) if cols else expr


MAX_ROWS = 10_000  # 화면에 한 번에 보여 줄 최대 행 수


def whole_number(value, low, high, message):
    """화면에서 JSON 값으로 들어온 수를 low 이상 high 이하(None이면 위 제한 없음)의 정수로. 아니면 message로 ValueError."""
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(message) from None
    if n < low or (high is not None and n > high):
        raise ValueError(message)
    return n


# 행 수: 1 이상 MAX_ROWS 이하의 정수
def parse_limit(value):
    return whole_number(value, 1, MAX_ROWS, f"행 수는 1 이상 {MAX_ROWS:,} 이하의 정수로 입력하세요. 더 많은 행은 저장 버튼으로 내려받으세요.")


# 시작 위치: 건너뛸 행 수. 0 이상의 정수
def parse_offset(value):
    return whole_number(value, 0, None, "시작 위치는 0 이상의 정수여야 합니다.")
