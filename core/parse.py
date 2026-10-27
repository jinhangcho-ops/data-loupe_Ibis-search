"""한 줄 입력(조인·기간·조건·정렬·표시 컬럼)과 이어붙이기를 해석해 Ibis 식으로 바꾼다. 입력·출력은 하지 않는다."""
import bisect
import datetime as dt
import decimal
import difflib
import functools
import operator
import re

from lazy import ibis

JOINS = ["inner", "left", "right", "outer", "semi", "anti", "asof"]  # semi: 짝이 있는 행만, anti: 짝이 없는 행만(둘은 오른쪽 컬럼을 붙이지 않음), asof: 시각이 같거나 앞선 가장 가까운 행
UNIONS = ["left", "inner", "intersect", "except"]  # 이어붙이기: left는 기준 테이블의 컬럼을 모두(없는 컬럼은 빈 값), inner는 모든 테이블에 있는 컬럼만, intersect는 모든 테이블에 있는 행만, except는 기준 테이블에만 있는 행
STACKS = ("left", "inner")  # 아래로 잇는 방식(출처 컬럼을 붙인다). intersect·except는 행을 고르는 방식이라 붙이지 않는다
SOURCE = "출처"  # 이어붙이면 더하는 컬럼: 그 행이 온 테이블 이름
OPS = {"=": operator.eq, "!=": operator.ne, ">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
CONDITION_GUIDE = "컬럼 >= 값 / 컬럼 != 값 / 컬럼 between 값1 값2 / 컬럼 in 값1, 값2 / 컬럼 except 값1, 값2 / 컬럼 like 값 / 컬럼 starts 값 / 컬럼 ends 값 / 컬럼 is [not] null / near 위도컬럼 경도컬럼 기준위도 기준경도 반경km / box 위도컬럼 경도컬럼 최소위도 최소경도 최대위도 최대경도 / 조건 or 조건 / 값에 or·쉼표가 있으면 '값'"
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
LIKE_RE = re.compile(r"^(\S+)\s+(like|starts|ends)\s+(.+)$", re.I)  # like: 포함(대소문자 무시), starts: 이 글자로 시작, ends: 이 글자로 끝
NULL_RE = re.compile(r"^(\S+)\s+is\s+(not\s+)?null$", re.I)
GEO_RE = re.compile(r"^(near|box)\s+(.+)$", re.I)  # 좌표 조건: near 위도컬럼 경도컬럼 기준위도 기준경도 반경km / box 위도컬럼 경도컬럼 최소위도 최소경도 최대위도 최대경도
GEO_COUNTS = {"near": 5, "box": 6}
NOT_GEO = {"in", "except", "like", "starts", "ends", "between", "is", "=", "!=", ">=", "<=", ">", "<"}  # near·box가 컬럼 이름일 때의 조건('near in 1, 2')은 좌표 조건이 아니다
OR_RE = re.compile(r"(?<!\S)or(?!\S)", re.I)  # 앞뒤가 공백인 단어 or
COMMA_RE = re.compile(",")
# 따옴표로 감싼 값: 값의 맨 앞(줄 처음이나 공백 , = < > 뒤)에서 열고, 값의 맨 뒤(줄 끝이나 공백 , 앞)에서 닫는다
QUOTE_RE = re.compile(r"""(?<![^\s,=<>])(['"])(?:.*?\1(?![^\s,]))?""")


def type_name(dtype):
    """한글 타입 이름. 중첩 타입은 구조{필드, …}, 목록<값 타입>, 맵<키 타입, 값 타입>."""
    if dtype.is_struct():
        return "구조{" + ", ".join(dtype.names) + "}"
    if dtype.is_array():
        return f"목록<{type_name(dtype.value_type)}>"
    if dtype.is_map():
        return f"맵<{type_name(dtype.key_type)}, {type_name(dtype.value_type)}>"
    return next((name for check, _, name, _ in TYPES if check(dtype)), str(dtype))


def type_info(dtype):
    """(한글 이름, 변환 함수). 모르는 타입(중첩 포함)은 값을 문자로 다룬다."""
    return next(((name, cast) for check, _, name, cast in TYPES if check(dtype)), (type_name(dtype), str))


MAX_DEPTH = 3  # 구조 안의 구조를 몇 단계까지 필드로 펼칠지


def struct_fields(name, dtype, depth=1):
    """구조 컬럼 name 안의 필드 [(경로 "name.필드", 타입)]. 구조 안의 구조는 MAX_DEPTH 단계까지. 목록 안은 펼치지 않는다."""
    if not dtype.is_struct() or depth > MAX_DEPTH:
        return []
    found = []
    for field, sub in dtype.items():
        path = f"{name}.{field}"
        found += [(path, sub)] + struct_fields(path, sub, depth + 1)
    return found


def nested_names(expr):
    """표의 구조 컬럼 안 필드 경로들(user.name 등)."""
    return [path for name, dtype in expr.schema().items() for path, _ in struct_fields(name, dtype)]


def column(expr, name):
    """컬럼 식. 컬럼 이름에 없고 a.b처럼 점이 있으면 구조 컬럼 a 안의 필드 b(여러 단계 가능). 없으면 비슷한 이름을 알려 주는 ValueError."""
    if name in expr.columns:
        return expr[name]
    head, *rest = name.split(".")
    if rest and head in expr.columns:
        value = expr[head]
        for part in rest:
            dtype = value.type()
            if not dtype.is_struct() or part not in dtype.names:
                break
            value = value[part]
        else:
            return value
    check_name(name, list(expr.columns) + nested_names(expr))
    raise ValueError(f"'{name}' 컬럼이 없습니다.")


def kind(dtype):
    """int, float, string, date(날짜·날짜시간), bool, time, other 중 하나."""
    return next((k for check, k, *_ in TYPES if check(dtype)), "time" if dtype.is_time() else "other")


def convert(dtype, text):
    name, cast = type_info(dtype)
    try:
        if dtype.is_decimal():  # 실수로 바꾸면 큰 소수(decimal(38,0))가 반올림돼 이웃 값과 같아진다. 타입 없는 Decimal은 DECIMAL(18,3)이 되어 넘치므로 자릿수를 정한다
            value = decimal.Decimal(text.strip())
            if not value.is_finite():
                raise ValueError(text)
            scale, width = max(dtype.scale or 0, -value.as_tuple().exponent), max(38, dtype.precision or 0)  # 1E+3은 지수가 양수라 scale 0
            if value.adjusted() + 1 > width - scale:  # 자릿수를 넘는 값(1e40)은 DB가 영어 오류를 낸다
                raise ValueError(text)
            return ibis.literal(format(value, "f")).cast(f"decimal({width}, {scale})")  # 글자를 바꾼다: Ibis는 SQL Server에 1000을 '1E+3'으로 써서 못 읽는다. BIGNUMERIC(76,38)은 38자리보다 넓다
        return cast(text)
    except (ValueError, KeyError, decimal.InvalidOperation):
        raise ValueError(f"'{text}'은 {name} 값이 아닙니다.")


def split(pattern, text, quotes=True):
    """pattern으로 나눈다. 따옴표로 감싼 부분은 나누지 않는다. quotes가 False면 따옴표도 글자로 본다."""
    if not quotes:
        return pattern.split(text)
    quoted = []
    for m in QUOTE_RE.finditer(text):
        if m.end() - m.start() == 1:  # 닫히지 않은 따옴표: 처음 하나에서 바로 알린다
            raise ValueError("따옴표가 닫히지 않았습니다. 값에 따옴표가 있으면 다른 따옴표로 감싸세요.")
        quoted.append(m.span())
    starts, parts, pos = [s for s, _ in quoted], [], 0
    for m in pattern.finditer(text):
        i = bisect.bisect_right(starts, m.start()) - 1  # 따옴표 구간은 겹치지 않고 앞에서부터 차례라 이진 탐색으로 안에 있는지 본다(쉼표가 많아도 느려지지 않게)
        if not (i >= 0 and m.start() < quoted[i][1]):
            parts.append(text[pos:m.start()])
            pos = m.end()
    return parts + [text[pos:]]


SPACED = "\x00col"  # 공백 있는 컬럼 이름 자리에 잠깐 넣는 한 낱말


AFTER_NAME = re.compile(r"\s*(?:!=|>=|<=|=|>|<|(?:is|in|except|like|starts|ends|between)(?!\S))", re.I)  # 조건에서 컬럼 이름 바로 뒤에 오는 연산자


def head_name(text, names, condition=False):
    """글이 공백 있는 알려진 컬럼 이름(names)으로 시작하면 그 이름(가장 긴 것), 아니면 None. 이름 뒤는 끝·공백·비교 기호여야 한다.
    condition이면 이름 뒤에 연산자가 와야 그 이름으로 읽는다('total in'과 'total' 두 컬럼이 있을 때 'total in 1, 2'의 in을 이름으로 먹지 않게)."""
    for name in sorted((c for c in names or () if " " in c), key=len, reverse=True):
        rest = text[len(name):]
        if text.startswith(name) and rest[:1] in ("", " ", "=", "!", "<", ">") and (not condition or AFTER_NAME.match(rest)):
            return name
    return None


def spaced(line, expr, condition=False):
    """줄이 공백 있는 컬럼 이름으로 시작하면 (그 이름, 이름 자리를 SPACED로 바꾼 줄), 아니면 (None, line)."""
    name = head_name(line, expr.columns, condition)
    return (name, SPACED + line[len(name):]) if name else (None, line)


def unquote(text, quotes=True):
    """앞뒤 공백을 떼고, 값 전체를 감싼 따옴표 한 쌍이 있으면 벗긴다. quotes가 False면 벗기지 않는다."""
    text = text.strip()
    if not quotes:
        return text
    m = QUOTE_RE.match(text)
    return text[1:-1] if m and len(text) > 1 and m.end() == len(text) else text


def check_table_name(name, what="테이블"):
    """새로 만들거나 넣을 테이블(스키마) 이름 검사. 전처리(prep_app)와 적재(load_table)가 쓴다."""
    if not isinstance(name, str) or not TABLE_NAME.fullmatch(name):
        raise ValueError(f"{what} 이름은 영문·숫자·밑줄·한글만 쓸 수 있습니다(공백·따옴표·점 없이).")


def check_new_columns(con, columns):
    """PostgreSQL에 새 테이블을 만들 때 63바이트를 넘는 컬럼 이름을 거부한다(PostgreSQL은 말없이 잘라 다음 단계에서 그 컬럼을 찾지 못한다). 다른 DB는 보지 않는다."""
    if getattr(con, "name", "") != "postgres":
        return
    long = [c for c in columns if len(c.encode("utf-8")) > 63]
    if long:
        raise ValueError(f"PostgreSQL에는 63바이트(영문 63자, 한글 21자)를 넘는 컬럼 이름을 만들 수 없어요: {', '.join(long[:5])}. 이름을 줄여 주세요.")


def check_new_length(name, tail=""):
    """새로 만들 테이블 이름(꼬리 tail을 붙인 이름까지)이 63바이트를 넘으면 거부한다: PostgreSQL은 넘는 이름을 말없이 잘라 다음에 찾지 못한다. 이미 있는 테이블에 넣을 때는 부르지 않는다."""
    room = 63 - len(tail)
    if len(name.encode("utf-8")) > room:
        raise ValueError(f"테이블 이름이 너무 깁니다(영문 {room}자, 한글 {room // 3}자까지).")


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
        raise ValueError('이어붙이기는 {"tables": [테이블…], "how": "left"·"inner"·"intersect"·"except"} 모양이어야 합니다.')
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
    left: 기준 테이블의 컬럼을 모두 남기고 붙이는 테이블에 없는 컬럼은 빈 값(붙이는 테이블에만 있는 컬럼은 뺀다). inner: 모든 테이블에 있는 컬럼만.
    intersect: 모든 테이블에 같이 있는 행만(INTERSECT), except: 기준 테이블에만 있는 행(EXCEPT). 둘은 모든 테이블에 있는 컬럼으로 비교하고 SOURCE는 붙이지 않는다(같은 행은 하나로)."""
    if union is None:
        return base
    names, how = union
    if how in STACKS and SOURCE in base.columns:
        raise ValueError(f"기준 테이블에 '{SOURCE}' 컬럼이 이미 있어 이어붙일 수 없습니다.")
    others = [(name, get_table(name)) for name in names]
    columns = [c for c in base.columns if how == "left" or all(c in t.columns for _, t in others)]
    if not columns:
        raise ValueError(f"모든 테이블에 같이 있는 컬럼이 없어 {how}로 이어붙일 수 없습니다.")
    schema = base.schema()
    if how not in STACKS:
        same = lambda t: t.select(*[t[c].cast(schema[c]).name(c) for c in columns])
        return (ibis.intersect if how == "intersect" else ibis.difference)(same(base), *[same(t) for _, t in others])
    shaped = lambda t, name: t.select(*[(t[c] if c in t.columns else ibis.null()).cast(schema[c]).name(c) for c in columns], **{SOURCE: ibis.literal(name)})
    return ibis.union(shaped(base, base_name), *[shaped(t, name) for name, t in others], distinct=False)


# 조인: "테이블 [inner|left|right|outer|semi|anti] 왼쪽=오른쪽, ..." 또는 "테이블 asof 왼쪽시각>=오른쪽시각, 왼쪽=오른쪽, ..."
def parse_join(line, tables):
    """(테이블, 종류, [(왼쪽, 오른쪽)], asof의 (왼쪽 시각, 오른쪽 시각) 또는 None)."""
    name, _, rest = line.partition(" ")
    check_name(name, tables, "테이블")
    first, _, after = rest.strip().partition(" ")
    how, keys = (first.lower(), after) if first.lower() in JOINS else ("inner", rest)
    pairs, on = [], None
    for k in keys.split(","):
        if how == "asof" and ">=" in k and on is None:
            on = tuple(s.strip() for s in k.split(">=", 1))
            continue
        left, _, right = (s.strip() for s in k.partition("="))
        if left:
            pairs.append((left, right or left))
    if how == "asof" and not (on and all(on)):
        raise ValueError("asof 조인은 시각 쌍을 '왼쪽시각>=오른쪽시각'으로 하나 넣으세요. 예: prices asof 주문시각>=가격시각, 상품ID=상품ID")
    if not pairs and how != "asof":
        raise ValueError("조인 컬럼을 입력하세요. 예: customers left customer_id=id")
    return name, how, pairs, on


ASOF_ID, ASOF_RANK = "__ibis_left", "__ibis_near"  # asof를 대신하는 식이 잠깐 쓰는 컬럼


def orderable(expr):
    """정렬할 수 있는 컬럼 전부(목록·구조·맵·JSON 제외). 정렬 없이 행 번호를 매길 때의 순서로 쓴다(ORDER BY 없는 ROW_NUMBER를 막는 DB가 있다, Oracle 등)."""
    return [expr[c] for c, t in expr.schema().items() if not (t.is_nested() or t.is_json())]


def asof(left, right, on, keys, rname="{name}_right"):
    """asof 조인: 왼쪽 행마다 키가 같고 시각이 같거나 앞선 오른쪽 행 중 가장 가까운 하나를 붙인다(없으면 빈 값).
    연결이 ASOF JOIN을 하지 못하면(SQLite·Databricks 등) 왼쪽 조인 + 시각 순 행 번호로 같은 결과를 만든다."""
    from core import source
    if left.get_backend().name == "polars":  # Polars join_asof는 양쪽이 시각 순이라고 믿고 오류 없이 틀린 짝을 낸다
        left, right = left.order_by(left[on[0]]), right.order_by(right[on[1]])
    found = left.asof_join(right, on=left[on[0]] >= right[on[1]], predicates=[left[a] == right[b] for a, b in keys], rname=rname)
    if source.compiles(found):
        return found
    numbered = left.mutate(**{ASOF_ID: ibis.row_number().over(ibis.window(order_by=orderable(left)))})
    joined = numbered.left_join(right, [numbered[on[0]] >= right[on[1]], *[numbered[a] == right[b] for a, b in keys]], rname=rname)
    when = joined[on[1] if on[1] not in left.columns else rname.format(name=on[1])]  # 오른쪽 시각(이름이 겹치면 rname 꼴)
    near = ibis.row_number().over(ibis.window(group_by=joined[ASOF_ID], order_by=ibis.desc(when)))
    ranked = joined.mutate(**{ASOF_RANK: near})
    return ranked.filter(ranked[ASOF_RANK] == 0).select(*[c for c in joined.columns if c != ASOF_ID])  # drop은 SELECT를 한 겹 더 만든다


def rname_for(left, right, name):
    """조인에서 왼쪽과 이름이 겹치는 오른쪽 컬럼의 새 이름 꼴. 첫 겹침은 _right, 그 이름이 양쪽에 이미 있으면(두 번째 조인 등) 테이블 이름을 붙인다."""
    taken = {*left, *right}
    clash = lambda rname: any(c in left and rname.format(name=c) in taken for c in right)
    rname = "{name}_right"
    if clash(rname):
        rname = "{name}_" + re.sub(r"\W", "_", name)
        while clash(rname):
            rname += "_"
    return rname


def apply_joins(base, joins, get_table):
    """joins: [(테이블, 종류, [(왼쪽, 오른쪽)], asof 시각 쌍)]. 컬럼이 없으면 ValueError."""
    expr = base
    for name, how, pairs, on in joins:
        right = get_table(name).view()  # 자기 자신과 조인해도 두 쪽이 구분되게
        for left, r in [*pairs, *([on] if on else [])]:
            check_name(left, expr.columns)
            check_name(r, right.columns)
        merged = {r for left, r in pairs if left == r} if how == "inner" else set()  # inner 조인의 같은 이름 키는 Ibis가 하나로 합친다
        rname = rname_for(expr.columns, [c for c in right.columns if c not in merged], name)
        if how == "asof":
            expr = asof(expr, right, on, pairs, rname)
        else:
            expr = expr.join(right, [expr[left] == right[r] for left, r in pairs], how=how, rname=rname)
    return expr


# 기간: "날짜컬럼 시작 ~ 끝" (한쪽은 비워도 됨)
def parse_period(line, expr):
    name, rest = spaced(line, expr)
    col, _, rng = (name, "", rest[len(SPACED):]) if name else line.partition(" ")
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
        after = end_after(dtype, end, value)
        preds.append(expr[col] < after if after else expr[col] <= value)
    return preds


def end_after(dtype, text, value):
    """날짜시간 컬럼에 끝을 날짜로만 쓰면 다음 날 0시, 초 없이 HH:MM까지 쓰면 다음 분(그 분 끝까지 포함. 대시보드 기간 period.py와 같은 규칙). 아니면 None."""
    if not dtype.is_timestamp():
        return None
    return next_day(text) or (value + dt.timedelta(minutes=1) if re.search(r"[ T]\d{1,2}:\d{2}$", text) else None)


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


def geo_condition(word, rest, expr):
    """좌표 조건(near·box). 값 자리는 쉼표나 공백으로 나눈다. 위도·경도 컬럼은 숫자 컬럼이어야 한다. 식은 geo.funcs가 만든다(DB 안에서 계산)."""
    from geo import funcs
    parts = rest.replace(",", " ").split()
    lat, lon = column(expr, parts[0]), column(expr, parts[1])
    for name, c in ((parts[0], lat), (parts[1], lon)):
        if not c.type().is_numeric():
            raise ValueError(f"'{name}'은 숫자 컬럼이 아니라 위도·경도로 쓸 수 없습니다.")
    values = [convert(ibis.dtype("float64"), v) for v in parts[2:]]
    return funcs.within_km(lat, lon, *values) if word == "near" else funcs.in_box(lat, lon, *values)


def parse_one(line, expr, quotes=True):
    """조건 한 줄. 컬럼 자리에는 구조 안의 필드(user.name)도 쓸 수 있다(column). near·box는 좌표 조건."""
    name, line = spaced(line, expr, True)  # 공백 있는 컬럼 이름은 자리표시로 바꿔 아래 정규식이 한 낱말로 읽게 한다
    get = lambda col: column(expr, name if name and col == SPACED else col)
    if (m := GEO_RE.match(line)) and len(m.group(2).replace(",", " ").split()) == GEO_COUNTS[m.group(1).lower()] and m.group(2).split()[0].lower() not in NOT_GEO:
        return geo_condition(m.group(1).lower(), m.group(2), expr)
    if m := NULL_RE.match(line):
        col, negate = m.groups()
        c = get(col)
        return c.notnull() if negate else c.isnull()
    if m := BETWEEN_RE.match(line):
        col, lo, hi = m.groups()
        c = get(col)
        lo, hi = unquote(lo, quotes), unquote(hi, quotes)
        after = end_after(c.type(), hi, convert(c.type(), hi))  # 끝을 날짜로만 쓰면 그날 끝까지, HH:MM이면 그 분 끝까지(기간과 같은 규칙)
        if after:
            return (c >= convert(c.type(), lo)) & (c < after)
        return c.between(convert(c.type(), lo), convert(c.type(), hi))
    if m := IN_RE.match(line):  # except는 in의 반대: 적은 값을 뺀다
        col, word, raw = m.groups()
        c = get(col)
        values = [convert(c.type(), unquote(v, quotes)) for v in split(COMMA_RE, raw, quotes) if v.strip()]
        return c.isin(values) if word.lower() == "in" else c.notin(values)
    if m := LIKE_RE.match(line):
        col, word, raw = m.groups()
        c = get(col)
        if not c.type().is_string():
            raise ValueError(f"{word.lower()}는 문자 컬럼에만 쓸 수 있습니다.")
        raw = unquote(raw, quotes)
        word = word.lower()
        found = starts(c, raw) if word == "starts" else ends(c, raw) if word == "ends" else contains(c, raw)
        from core import source
        if source.compiles(expr.filter(found)):
            return found
        return c.startswith(raw) if word == "starts" else c.endswith(raw) if word == "ends" else c.lower().contains(raw.lower())  # LIKE를 못 하는 연결(파일)
    if m := COND_RE.match(line):
        col, op, raw = m.groups()
        c = get(col)
        if c.type().is_nested():
            raise ValueError(f"'{name or col}'은 {type_name(c.type())} 컬럼이라 비교할 수 없습니다. 안의 필드(예: {name or col}.이름)나 is null을 쓰세요.")
        value = convert(c.type(), unquote(raw, quotes))
        after = op in ("=", "!=", "<=", ">") and end_after(c.type(), unquote(raw, quotes), value)  # 날짜만 쓴 값은 그날 하루, HH:MM은 그 1분(기간·between과 같은 규칙)
        if after:
            return {"=": (c >= value) & (c < after), "!=": (c < value) | (c >= after), "<=": c < after, ">": c >= after}[op]
        return OPS[op](c, value)
    raise ValueError(f"형식: {CONDITION_GUIDE}")


TABLE_NAME = re.compile(r"[A-Za-z0-9_가-힣]+")  # 새로 만들 테이블·스키마 이름(check_table_name)
WILD = re.compile(r"[%_\\[]")  # LIKE에서 특별한 뜻이 있는 글자(MSSQL은 [도). 값에 있으면 LIKE를 쓰지 않는다


def contains(c, text):
    """포함 검색(대소문자 무시). 모든 DB에서 LOWER(컬럼) LIKE '%값%'로(contains는 MSSQL에서 전문 검색 CONTAINS로 바뀌어 안 된다).
    값에 LIKE 특수 글자가 있으면 와일드카드가 되지 않게 위치 찾기(INSTR 등)로."""
    text = text.lower()
    return c.lower().find(text) >= 0 if WILD.search(text) else c.lower().like(f"%{text}%")


def starts(c, text):
    """이 글자로 시작(대소문자 구분). 컬럼에 함수를 씌우지 않는 LIKE '값%'라 인덱스를 탈 수 있다(startswith는 STARTS_WITH·SUBSTR 등 함수로 바뀐다).
    값에 LIKE 특수 글자가 있으면 위치 찾기로(인덱스는 못 탄다)."""
    return c.find(text) == 0 if WILD.search(text) else c.like(f"{text}%")


def ends(c, text):
    """이 글자로 끝(대소문자 구분): LIKE '%값'. 값에 LIKE 특수 글자가 있으면 endswith로."""
    return c.endswith(text) if WILD.search(text) else c.like(f"%{text}")


def predicate(cond, expr):
    """조건 하나를 Ibis 식으로. 문자열, 따옴표 해석을 끈 {"line": ..., "quotes": False},
    화면에서 or로 이은 묶음 {"any": [조건, ...]}, 조건 목록(모두 and) 중 하나다. 묶음 안은 or, 조건끼리는 and."""
    if isinstance(cond, list):
        if not cond:
            raise ValueError("and로 묶을 조건이 없습니다.")
        return functools.reduce(operator.and_, [predicate(c, expr) for c in cond])
    if isinstance(cond, str):
        return parse_condition(cond, expr)
    if "any" in cond:
        if not cond["any"]:
            raise ValueError("or로 묶을 조건이 없습니다.")
        return functools.reduce(operator.or_, [predicate(c, expr) for c in cond["any"]])
    return parse_condition(cond["line"], expr, cond.get("quotes", True))


# 정렬: "컬럼 [asc|desc]"
def parse_sort(line, expr):
    name, rest = spaced(line, expr)
    col, _, how = (name, "", rest[len(SPACED):]) if name else line.partition(" ")
    how = how.strip().lower() or "asc"
    c = column(expr, col)
    if how not in ("asc", "desc"):
        raise ValueError(f"형식: {SORT_GUIDE}")
    return native_key(expr, c, how == "desc")


def native_key(expr, col, desc, dialect=None):
    """정렬 키. 빈 값의 위치(앞·뒤)는 그 연결의 기본을 따른다: Ibis는 빈 값을 늘 뒤로 맞추려고 DB에 따라 NULLS LAST나
    CASE WHEN … IS NULL을 붙이는데, 그러면 인덱스로 정렬하지 못한다(MSSQL·MySQL의 오름차순, Postgres·Oracle의 내림차순 등).
    두 위치를 SQL로 바꿔 보고(dialect가 없으면 expr의 연결 방언) ORDER BY에 NULLS·CASE가 붙지 않는 쪽을 고른다. SQL로 바꿀 수 없는 연결(파일)은 빈 값을 뒤로."""
    keys = [col.desc(nulls_first=first) if desc else col.asc(nulls_first=first) for first in (False, True)]
    try:
        for key in keys:
            tail = str(ibis.to_sql(expr.order_by(key), dialect=dialect)).upper().rpartition("ORDER BY")[2]
            if "NULLS" not in tail and "CASE" not in tail:
                return key
    except Exception:
        pass
    return keys[0]


# 표시 컬럼: "컬럼1, 컬럼2"
def parse_columns(line, expr):
    cols = [c.strip() for c in line.split(",") if c.strip()]
    for c in cols:
        column(expr, c)  # 구조 안의 필드(user.name)도 된다
    return cols


def apply(expr, preds, keys, cols):
    """조건 → 정렬 → 표시 컬럼 순으로 적용한다. 빈 목록은 건너뛴다."""
    if preds:
        expr = expr.filter(*preds)
    if keys:
        expr = expr.order_by(keys)
    return expr.select([column(expr, c).name(c) for c in dict.fromkeys(cols)]) if cols else expr


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
