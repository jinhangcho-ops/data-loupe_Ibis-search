"""컬럼 하나에 적용하는 전처리(OPS 표)와 그 함수들. 평균·분위수·값 목록처럼 테이블 전체에서 구하는 값은
작은 조회로 먼저 구해 값으로 식에 넣는다(Polars 백엔드는 창 함수와 스칼라 서브쿼리가 없다)."""
import functools
import json
import operator
import re

from lazy import ibis

from core import parse
from core import source
from core import sql_funcs

TYPE_TO = {"int": "int64", "float": "float64", "string": "string", "date": "date"}
FLOAT_SHAPE = r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$"  # 12, -1.5, .5, 1e5. inf·nan은 숫자로 보지 않는다.
FILLS = ["drop", "value", "mean", "median", "mode", "prev", "next"]
STRING, NUMBER, DATE = ("string",), ("int", "float"), ("date",)
KIND_NAMES = {"string": "문자", "int": "정수", "float": "실수", "date": "날짜"}
RARE_MAX = 1000  # rare로 바꿀 값 목록의 최대 개수
LABEL_MAX = 1000  # label로 번호를 붙일 서로 다른 값의 최대 개수
BIN_START = re.compile(r"^(-?[0-9.]+(?:e[-+]?[0-9]+)?)–", re.I)  # bins가 만든 구간 글자의 시작 숫자
ONEHOT_MAX = 50  # onehot으로 펼칠 서로 다른 값의 최대 개수
EXPAND_MAX, EXPAND_SCAN = 50, 1000  # expand로 펼칠 맵·JSON 키의 최대 개수, 그 키를 모으려고 읽는 앞 행 수
PARTS = {  # 날짜에서 뽑는 값. 정수는 int64로 맞춘다(백엔드마다 int16·int32로 다르다).
    "year": lambda col: col.year().cast("int64"), "month": lambda col: col.month().cast("int64"), "day": lambda col: col.day().cast("int64"),
    "weekday": lambda col: col.day_of_week.index().cast("int64"), "hour": lambda col: col.hour().cast("int64"), "ym": lambda col: col.cast("date").truncate("M"),
}


def is_strs(value):
    return isinstance(value, list) and bool(value) and all(isinstance(s, str) for s in value)


def is_num(value):
    return type(value) in (int, float)


def stats(t, **metrics):
    """집계 한 번으로 구한 값들 {이름: 값}."""
    return t.aggregate(**metrics).to_polars().row(0, named=True)


def column(fn):
    """컬럼 하나를 새 컬럼 식으로 바꾸는 함수 fn(컬럼, 값)을, 테이블에 적용하는 함수로 바꾼다."""
    return lambda t, name, arg: t.mutate(**{name: fn(t[name], arg)})


def check_new(t, names):
    """파생 컬럼 이름이 이미 있으면 거부한다(mutate는 말없이 덮어쓴다). 대소문자만 달라도 같다(DuckDB·SQLite·SQL Server는 가리지 않아 저장할 때 막힌다)."""
    have = {c.lower() for c in t.columns}
    for name in names:
        if name.lower() in have:
            raise ValueError(f"'{name}' 컬럼이 이미 있어 파생 컬럼을 만들 수 없어요.")
        have.add(name.lower())


def add_columns(t, new):
    check_new(t, new)
    return t.mutate(**new)


REPLACE_HOWS = ("exact", "contains", "prefix", "suffix", "regex", "list", "cond", "null_to", "to_null")
TEXT_HOWS = ("contains", "prefix", "suffix", "regex")  # 글자 컬럼에만 되는 치환 방식


def is_replace(v):
    """치환 값: 옛 꼴 [[찾을, 바꿀], …] 또는 {how, pairs, ignore_case, trim, rest, to_new}(값의 타입·조건 글은 적용할 때 본다)."""
    if isinstance(v, list):
        return bool(v) and all(is_strs(p) and len(p) == 2 for p in v)
    return (isinstance(v, dict) and set(v) <= {"how", "pairs", "ignore_case", "trim", "rest", "to_new"} and v.get("how") in REPLACE_HOWS
            and isinstance(v.get("pairs"), list) and bool(v["pairs"]) and all(isinstance(p, list) and len(p) == 2 for p in v["pairs"])
            and isinstance(v.get("ignore_case", False), bool) and isinstance(v.get("trim", False), bool)
            and isinstance(v.get("rest", "keep"), (str, int, float)) and isinstance(v.get("to_new", ""), str))


def replace(t, name, spec):
    """값 바꿔 쓰기(how별): exact 같은 값, contains 들어 있는 글자를 차례로, prefix·suffix 앞뒤 글자, regex 정규식(\\1 묶음), list 값 목록 → 한 값,
    cond 조건 줄이 맞는 행, null_to 빈 값을 채움, to_null 그 값들을 빈 값으로. ignore_case는 대소문자 무시, trim은 앞뒤 공백을 지운 값으로,
    rest는 맞지 않은 행(keep 그대로·null 빈 값·그 밖은 그 값), to_new가 있으면 원래 컬럼은 두고 그 새 컬럼에 쓴다. 값은 컬럼 타입으로 바꾼다."""
    if isinstance(spec, list):
        spec = {"how": "exact", "pairs": spec}
    how, pairs, col = spec["how"], spec["pairs"], t[name]
    dtype, text = col.type(), col.type().is_string()
    if how in TEXT_HOWS and not text:
        raise ValueError(f"'{how}' 치환은 글자 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(dtype)[0]} 컬럼이에요.")
    value = lambda v: ibis.null(dtype) if v is None else parse.convert(dtype, str(v)) if not text else str(v)  # 바꿀 값은 컬럼 타입으로(못 바꾸면 ValueError)
    if spec.get("trim") and text:
        col = strip(col)
    fold = spec.get("ignore_case") and text
    key = col.lower() if fold else col
    match = lambda v: value(v).lower() if fold else value(v)
    hits, out = [], None
    if how in ("exact", "list", "to_null"):
        hits = [key.isin([match(v) for v in (old if isinstance(old, list) else [old])]) for old, _ in pairs]
        out = ibis.cases(*[(hit, value(None if how == "to_null" else new)) for hit, (_, new) in zip(hits, pairs)], else_=col)
    elif how == "cond":
        hits = [parse.predicate(cond, t) for cond, _ in pairs]
        out = ibis.cases(*[(hit, value(new)) for hit, (_, new) in zip(hits, pairs)], else_=col)
    elif how in ("prefix", "suffix"):
        hits = [key.startswith(match(old)) if how == "prefix" else key.endswith(match(old)) for old, _ in pairs]
        swap = lambda old, new: str(new) + col.substr(len(str(old))) if how == "prefix" else col.re_replace(("(?i)" if fold else "") + literal(str(old)) + "$", plain_text(t, str(new)))  # 끝 글자는 정규식으로(Polars는 길이 식을 substr 인자로 못 받는다)
        out = ibis.cases(*[(hit, swap(old, new)) for hit, (old, new) in zip(hits, pairs)], else_=col)
    elif how == "null_to":
        hits, out = [col.isnull()], col.fill_null(value(pairs[0][1]))
    else:  # contains·regex: 맞는 곳을 차례로 바꾼다
        out = col
        for old, new in pairs:
            pattern = ("(?i)" if fold else "") + (literal(str(old)) if how == "contains" else str(old))
            hits.append(col.re_search(pattern))
            if how == "regex":
                out = re_replace(t, out, pattern, str(new))
            elif fold:  # 대소문자 무시: 정규식으로 찾되 바꿀 글자는 그대로(묶음 번호로 읽지 않게)
                out = out.re_replace(pattern, plain_text(t, str(new)))
            else:
                out = replace_all(t, out, str(old), str(new))
    rest = spec.get("rest", "keep")
    if rest != "keep":
        out = functools.reduce(operator.or_, hits).fill_null(False).ifelse(out, value(None if rest == "null" else rest))
    if spec.get("to_new"):
        return add_columns(t, {spec["to_new"]: out})
    return t.mutate(**{name: out})


def replace_all(t, col, find, to):
    """글자 find를 모두 to로 바꾸는 식. Ibis의 replace가 Polars(파일 연결)에서는 첫 것만 바꿔서 Polars는 글자 그대로 찾는 정규식으로 한다(다른 연결은 정규식이 없는 DB도 있어 replace)."""
    return col.re_replace("(?:" + literal(find) + ")", plain_text(t, to)) if on_polars(t) else col.replace(find, to)  # (?:)로 감싼다: 기호가 없는 패턴이면 Polars가 바꿀 글자를 글자 그대로 써 $$가 남는다


def plain_text(t, text):
    """정규식 바꾸기의 바꿀 글자를 글자 그대로 쓰게 한다(묶음 번호로 읽지 않게: DuckDB는 \\를, Polars는 $를 겹친다)."""
    return text.replace("$", "$$") if t.get_backend().name == "polars" else text.replace("\\", "\\\\")


def literal(text):
    """text를 정규식에서 글자 그대로 찾도록 정규식 특수 문자 앞에 역슬래시를 붙인다(공백 등은 그대로 둔다)."""
    return re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", text)


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
# 날짜 형식에 쓸 수 있는 글자와 그 모양. DuckDB·Polars 양쪽에서 같은 값이 나오는 것만 받는다(%f는 자릿수를 다르게 읽어 뺐다).
DATE_PARTS = {
    "%Y": "[0-9]{4}", "%y": "[0-9]{2}", "%m": "(0[1-9]|1[0-2])", "%d": "(0[1-9]|[12][0-9]|3[01])",
    "%H": "([01][0-9]|2[0-3])", "%M": "[0-5][0-9]", "%S": "[0-5][0-9]", "%I": "(0[1-9]|1[0-2])", "%p": "(AM|PM)",
    "%b": "(" + "|".join(m[:3] for m in MONTHS) + ")", "%B": "(" + "|".join(MONTHS) + ")",
}
TIME_PARTS = ["%H", "%M", "%S", "%I", "%p"]  # 형식에 이 중 하나라도 있으면 결과는 날짜시간이다.


def date_pattern(fmt, variants=(DATE_PARTS,)):
    """날짜 형식(DATE_PARTS의 글자와 그 사이의 글자)에 맞는 글자만 찾는 정규식. 영문 이름과 AM/PM은 대소문자를 가리지 않는다.
    variants: 글자 모양 사전 여럿을 주면 그중 하나에 맞는 글자를 찾는다(real_dates)."""
    pieces = re.split(r"(%.)", fmt)
    if any(p.startswith("%") and p not in DATE_PARTS for p in pieces):
        raise ValueError(f"날짜 형식에는 {' '.join(DATE_PARTS)}만 쓸 수 있어요. 예: %Y%m%d, %Y-%m-%d %H:%M:%S")
    return "(?i)^(" + "|".join("".join(v.get(p) or DATE_PARTS.get(p) or literal(p) for p in pieces) for v in variants) + ")$"


def real_dates(fmt):
    """date_pattern에 더해 없는 날짜(4월 31일, 2월 30일, 평년 2월 29일)는 빼는 정규식. 달마다 될 수 있는 날만 받는 모양을 |로 잇는다."""
    if "%d" not in fmt or not any(p in fmt for p in ("%m", "%b", "%B")):
        return date_pattern(fmt)
    month = lambda nums: {"%m": "(" + "|".join(f"{n:02}" for n in nums) + ")", "%b": "(" + "|".join(MONTHS[n - 1][:3] for n in nums) + ")", "%B": "(" + "|".join(MONTHS[n - 1] for n in nums) + ")"}
    leap = "(0[48]|[2468][048]|[13579][26])"  # 4의 배수인 두 자리. %y의 00은 2000년이라 윤년
    variants = [month([1, 3, 5, 7, 8, 10, 12]), {**month([4, 6, 9, 11]), "%d": "(0[1-9]|[12][0-9]|30)"}, {**month([2]), "%d": "(0[1-9]|1[0-9]|2[0-8])"}]
    if "%Y" in fmt or "%y" in fmt:  # 연도가 없으면 2월 29일은 받지 않는다
        variants.append({**month([2]), "%d": "29", "%Y": f"([0-9]{{2}}{leap}|{leap}00)", "%y": f"(00|{leap})"})
    return date_pattern(fmt, variants)


def to_date(col, fmt):
    """글자를 날짜(형식에 시각이 있으면 날짜시간)로. Polars 백엔드는 try_cast("date")가 없고 as_date·as_timestamp는 안 맞는 값에 오류를 내므로,
    형식에 맞는 모양이 아닌 값과 없는 날짜(2026-02-30, 2025-02-29)는 먼저 빈 값으로 만든다(숫자로 못 바꾸는 값이 빈 값이 되는 것과 같게)."""
    col = col.cast("string")
    col = col.re_search(real_dates(fmt)).ifelse(col, ibis.null())
    return col.as_timestamp(fmt).cast("timestamp") if any(p in fmt for p in TIME_PARTS) else col.as_date(fmt)  # cast: 시간대 없는 날짜시간으로


def int_shape():
    """int64에 들어가는 정수 글자의 정규식(절댓값이 2^63-1 이하). 열아홉 자리는 가장 큰 값보다 작거나 같은 것만 맞는다."""
    top = str(2**63 - 1)
    under = [top[:i] + f"[0-{int(d) - 1}]" + f"[0-9]{{{len(top) - i - 1}}}" * (i < len(top) - 1) for i, d in enumerate(top) if d != "0"]
    return f"^[+-]?0*([0-9]{{1,{len(top) - 1}}}|{'|'.join(under)}|{top})$"


INT_SHAPE = int_shape()


def is_number(col, to):
    """글자 col이 to(int, float)로 바뀌는 모양인지의 조건. 빈 값이면 거짓이다."""
    return ibis.coalesce(col.re_search(INT_SHAPE if to == "int" else FLOAT_SHAPE), False)


def to_number(col, to):
    """정수·실수로. 바꿀 수 없는 값은 빈 값이 된다. try_cast가 없는 연결이 있어, 바뀌는 값만 남긴 뒤 cast한다."""
    dtype = col.type()
    if dtype.is_integer() or dtype.is_boolean() or (dtype.is_numeric() and to == "float"):
        return col.cast(TYPE_TO[to])
    if dtype.is_numeric():  # 실수·소수를 정수로: nan·inf와 int64 범위 밖의 값은 빈 값, 소수점은 버림(0 쪽으로; 백엔드마다 반올림·버림이 달라 직접 정한다)
        col = ((col >= -2.0**63) & (col < 2.0**63)).ifelse(col, ibis.null())
        return (col >= 0).ifelse(col.floor(), col.ceil()).cast(TYPE_TO[to])
    col = col.cast("string")  # 날짜·시간처럼 숫자가 아닌 컬럼은 숫자 모양이 아니라 빈 값이 된다.
    found = is_number(col, to).ifelse(col, ibis.null()).cast(TYPE_TO[to])
    return found.isinf().ifelse(ibis.null(), found) if to == "float" else found  # "1e400"처럼 실수에 안 들어가는 값은 inf 대신 빈 값


SPACES = "\t\n\v\f\r\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"  # 보통 공백 말고 Polars가 strip에서 지우는 공백류(NBSP·전각 공백 등)


def strip(col):
    """앞뒤 공백 지우기. DuckDB의 strip은 보통 공백만 지우므로 다른 공백류를 공백으로 바꾼 뒤 지워 Polars와 맞춘다(re_replace는 다른 함수 안에 넣으면 컴파일 오류가 난다)."""
    return col.translate(SPACES, " " * len(SPACES)).strip() if col.get_backend().name == "duckdb" else col.strip()


def on_polars(t):
    """표가 파일 연결(Polars)의 것인지. 연결이 없는 표(함수 지원 확인용)는 아니다."""
    try:
        return t.get_backend().name == "polars"
    except Exception:
        return False


def re_replace(t, col, pattern, text):
    """정규식으로 바꾸는 식. 바꿀 글자의 \\1은 묶음 번호, $는 글자 그대로(DuckDB 방식). Polars는 반대($1이 묶음)라 $를 $$로 겹친 뒤 \\1을 ${1}로 바꾸고,
    패턴은 (?:)로 감싼다(기호가 없는 패턴이면 Polars가 바꿀 글자를 글자 그대로 써 $$가 남는다). 전처리 regex 단계·치환 regex·함수 사전 regex_replace가 같이 쓴다."""
    if on_polars(t):
        text = re.sub(r"\\(\\|\d)", lambda m: "\\" if m[1] == "\\" else "${" + m[1] + "}", text.replace("$", "$$"))  # \\(역슬래시 한 글자)와 \1(묶음)을 한 번 훑어 함께
        pattern = "(?:" + pattern + ")"
    return col.re_replace(pattern, text)


def regex_replace(t, name, pair):
    """정규식으로 바꾸기(regex 단계)."""
    return t.mutate(**{name: re_replace(t, t[name], *pair)})


def retype(col, arg):
    to = arg["to"]
    if to == "string":  # 글자로는 값을 바꾸지 않고 타입만 바꾼다(숫자 모양 검사·공백 지우기를 거치지 않는다).
        return col.cast("string")
    if col.type().is_string():  # 앞뒤 공백은 무시한다(백엔드마다 다르게 다루는 것을 맞춘다).
        col = strip(col)
    if arg.get("clean") and to in ("int", "float"):  # 숫자·부호·소수점이 아닌 글자를 먼저 지운다. 이미 1e5·2.5E-3 같은 숫자 모양이면 e를 지워 값이 바뀌지 않게 그대로 둔다.
        col = col.cast("string")
        col = col.re_search(FLOAT_SHAPE).ifelse(col, col.re_replace(r"[^0-9.+\-]", ""))
    if to == "date":
        return to_date(col, arg.get("format") or "%Y-%m-%d") if arg.get("format") or col.type().is_string() else col.cast("date")
    return to_number(col, to)


def fill_around(t, name, by, ahead):
    """by 컬럼 순(같은 값끼리는 parse.orderable의 컬럼 순)으로 앞(ahead면 뒤)의 빈 값 아닌 값으로 채운다. 창 함수라 지원하지 않는 연결에서는 그 오류가 난다.
    빈 값이 아닌 값을 셀 때마다 번호가 바뀌는 점을 이용한다: 같은 번호의 행들은 빈 값 아닌 값 하나와 그 뒤(앞)의 빈 값들이다."""
    parse.check_name(by, t.columns)
    run_window = ibis.window(order_by=[t[by], *parse.orderable(t)], rows=(0, None) if ahead else (None, 0))
    t = t.mutate(__group=t[name].notnull().cast("int64").sum().over(run_window))
    t = t.mutate(**{name: t[name].max().over(ibis.window(group_by=t.__group))})
    return t.drop("__group")


def fill(t, name, arg):
    how, col = arg["how"], t[name]
    if how == "drop":
        return t.filter(col.notnull())
    if how in ("prev", "next"):
        return fill_around(t, name, arg["by"], how == "next")
    if how == "value":
        value = parse.convert(col.type(), str(arg["value"]))
    else:
        if how != "mode" and parse.kind(col.type()) not in NUMBER:
            raise ValueError(f"'{how}'은 정수·실수 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(col.type())[0]} 컬럼이에요.")
        value = getattr(t[name], how)().to_pyarrow().as_py()
    return t.mutate(**{name: col.fill_null(value)})


def outlier(t, name, arg, base=None):
    """base를 주면 iqr·std 통계를 t 대신 base(단계 시작 표)로 구한다(앞 컬럼이 행을 줄인 뒤의 표가 아니라)."""
    col, by, k = t[name], arg["by"], arg.get("k")
    src = t if base is None else base
    if by == "range":
        low, high = arg.get("low"), arg.get("high")
    else:
        sc = src[name]
        s = stats(src, a=sc.quantile(0.25), b=sc.quantile(0.75)) if by == "iqr" else stats(src, a=sc.mean(), b=sc.std())
        if None in s.values():
            raise ValueError(f"'{name}' 컬럼에 값이 부족해 범위를 구할 수 없어요.")
        s = {key: float(value) for key, value in s.items()}  # 소수(Decimal) 컬럼은 실수로 계산한다.
        if by == "iqr":
            gap = (k or 1.5) * (s["b"] - s["a"])
            low, high = s["a"] - gap, s["b"] + gap
        else:
            gap = (k or 3) * s["b"]
            low, high = s["a"] - gap, s["a"] + gap
    if col.type().is_integer() and any(b is not None and b != int(b) for b in (low, high)):
        col = col.cast("float64")  # 정수 컬럼을 소수 경계로 자르면 백엔드마다 어긋나므로(오류 또는 소수점 버림) 실수로 바꾼다.
    if arg["how"] == "clip":
        return t.mutate(**{name: col.clip(low, high)})
    inside = [p for p, bound in ((col >= low, low), (col <= high, high)) if bound is not None]
    return t.filter(col.isnull() | functools.reduce(operator.and_, inside))  # 빈 값인 행은 남긴다.


def scale(t, name, how):
    col = t[name]
    if how == "log":
        low = stats(t, a=col.min())["a"]
        if low is not None and low <= -1:
            raise ValueError(f"'{name}' 컬럼의 최솟값이 {low}로 -1 이하라 log로 바꿀 수 없어요.")
        return t.mutate(**{name: (1 + col).ln()})
    if how == "minmax":
        s = stats(t, a=col.min(), b=col.max())
        if s["a"] == s["b"]:
            raise ValueError(f"'{name}' 컬럼은 값이 모두 같거나 비어 있어(범위가 0) minmax로 바꿀 수 없어요.")
        return t.mutate(**{name: (col - s["a"]) / (s["b"] - s["a"])})
    s = stats(t, a=col.mean(), b=col.std())
    if not s["b"]:
        raise ValueError(f"'{name}' 컬럼은 표준편차가 0이거나 구할 수 없어 standard로 바꿀 수 없어요.")
    return t.mutate(**{name: (col - s["a"]) / s["b"]})


def bins(col, edges):
    """edges를 경계로 한 구간 글자. 왼쪽 포함·오른쪽 미포함이고 마지막 구간만 오른쪽도 포함한다."""
    text = lambda e: str(int(e)) if float(e).is_integer() else str(e)
    last = len(edges) - 2
    return ibis.cases(*[((col >= lo) & ((col <= hi) if i == last else (col < hi)), f"{text(lo)}–{text(hi)}")
                        for i, (lo, hi) in enumerate(zip(edges, edges[1:]))], else_=ibis.null())


def distinct_values(t, name, limit, what):
    """컬럼의 서로 다른 값(빈 값 제외)을 정렬해서. limit개를 넘으면 거부한다."""
    found = t.filter(t[name].notnull()).select(name).distinct().limit(limit + 1).to_pyarrow()[name].to_pylist()
    if len(found) > limit:
        raise ValueError(f"'{name}' 컬럼은 서로 다른 값이 {limit:,}개를 넘어 {what}을 만들 수 없어요.")
    return sorted(found)


def rare(t, name, arg):
    counts = t.group_by(name).aggregate(n=t.count())
    found = counts.filter(counts.n < arg["min"], counts[name].notnull()).select(name).limit(RARE_MAX + 1).to_pyarrow()[name].to_pylist()
    if len(found) > RARE_MAX:
        raise ValueError(f"'{name}' 컬럼은 {arg['min']}번 미만 나온 값이 {RARE_MAX:,}개를 넘어 rare를 쓸 수 없어요.")
    return t.mutate(**{name: t[name].isin(found).ifelse(arg.get("label", "기타"), t[name])})


def label(t, name, _):
    """값을 정렬한 순서의 0부터의 번호. 빈 값은 빈 값."""
    values = distinct_values(t, name, LABEL_MAX, "label")
    starts = [BIN_START.match(v) if isinstance(v, str) else None for v in values]
    if all(starts):  # bins로 만든 구간 글자("5–10", "10–20")는 글자순이 아니라 시작 숫자 순
        values = [v for _, v in sorted(zip((float(m[1]) for m in starts), values))]
    return add_columns(t, {f"{name}_label": ibis.cases(*[(t[name] == v, i) for i, v in enumerate(values)], else_=ibis.null("int64")).cast("int64")})


def date_parts(t, name, parts):
    if "hour" in parts and not t[name].type().is_timestamp():
        raise ValueError(f"'hour'는 날짜시간 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(t[name].type())[0]} 컬럼이에요.")
    return add_columns(t, {f"{name}_{p}": PARTS[p](t[name]) for p in parts})


def onehot(t, name, _):
    values = distinct_values(t, name, ONEHOT_MAX, "onehot")
    seen = {}
    for v in map(str, values):
        if (other := seen.setdefault(v.lower(), v)) != v:  # 새 컬럼 이름은 대소문자를 가리지 않아 겹친다(check_new와 같은 lower)
            raise ValueError(f"'{name}'의 값 '{other}'와 '{v}'는 대소문자만 달라요. 먼저 '대문자·소문자로'를 적용하세요.")
    return add_columns(t, {f"{name}_{v}": ibis.coalesce(t[name] == v, False).cast("int8") for v in values})


def is_outlier(v):
    if not (isinstance(v, dict) and set(v) <= {"how", "by", "low", "high", "k"} and v.get("how") in ("drop", "clip") and v.get("by") in ("range", "iqr", "std")):
        return False
    if not all(v.get(key) is None or is_num(v[key]) for key in ("low", "high", "k")) or (v.get("k") is not None and v["k"] <= 0):
        return False
    bounds = [v[key] for key in ("low", "high") if v.get(key) is not None]
    return v["by"] != "range" or (bool(bounds) and bounds == sorted(bounds))


def extract(t, name, arg):
    """처음 맞은 부분(group이 0이면 전체, k면 정규식의 k번째 묶음)을 새 컬럼으로. 전체를 한 번 더 묶어 1번 이상을 뽑으면 연결마다 같다(0번을 못 뽑는 연결이 있다).
    맞는 곳이 없을 때 연결마다 ""나 빈 값이 나오므로 ""는 빈 값으로 맞춘다."""
    found = t[name].re_extract("(" + arg["pattern"] + ")", arg.get("group", 0) + 1)
    return add_columns(t, {f"{name}_match": found.nullif("")})


def has(t, name, arg):
    """단어마다 들어 있으면 1, 없거나 빈 값이면 0인 int8 컬럼."""
    test = (lambda word: t[name].re_search(word)) if arg.get("regex") else (lambda word: t[name].contains(word))
    return add_columns(t, {f"{name}_has_{word}": ibis.coalesce(test(word), False).cast("int8") for word in arg["words"]})


def json_text(col):
    """글자를 JSON 글자 값 안에 넣을 수 있게 \\와 "앞에 \\를 붙인다. 치환 문자열의 \\ 해석이 연결마다 달라, 나눠서 이어 붙이는 방식으로 한다."""
    bs = chr(92)
    return col.split(bs).join(bs + bs).split('"').join(bs + '"')


def counts_in_row(col, sep):
    """행 안에서 배열로 조각별 개수를 센 JSON 객체 글자. 배열의 map·filter를 지원하는 연결에서만 컴파일된다."""
    pieces = col.split(sep) if sep else col.strip().re_replace(r"\s+", " ").split(" ")
    words = pieces.map(lambda piece: piece.strip()).filter(lambda piece: piece != "")
    items = words.unique().sort().map(lambda word: '"' + json_text(word) + '": ' + words.filter(lambda other: other == word).length().cast("string"))
    return (words.length() > 0).ifelse("{" + items.join(", ") + "}", ibis.null("string"))


def counts_by_table(t, name, new, sep):
    """counts를 서로 다른 값마다 조각 수를 센 번호표를 만들어 합쳐서 구한다(배열 안에서 세는 식이 없는 연결용).
    값마다 한 번 계산하므로 값이 거의 다 다른 큰 표에서는 느리고 행 순서가 바뀐다."""
    check_new(t, ["__counts_key"])
    values = t.select(__counts_key=t[name]).distinct()
    values = values.filter(values["__counts_key"].notnull())
    key = values["__counts_key"]
    pieces = key.split(sep) if sep else key.strip().re_replace(r"\s+", " ").split(" ")
    flat = values.mutate(__counts_piece=pieces.unnest())
    flat = flat.mutate(__counts_piece=flat["__counts_piece"].strip())
    flat = flat.filter(flat["__counts_piece"].notnull(), flat["__counts_piece"] != "")
    each = flat.group_by(["__counts_key", "__counts_piece"]).aggregate(__counts_n=flat.count())
    item = '"' + json_text(each["__counts_piece"]) + '": ' + each["__counts_n"].cast("string")
    look = each.group_by("__counts_key").aggregate(__counts_json=item.group_concat(", ", order_by=each["__counts_piece"]))
    look = look.select("__counts_key", **{new: "{" + look["__counts_json"] + "}"})
    return t.left_join(look, t[name] == look["__counts_key"]).drop("__counts_key")


def counts(t, name, arg):
    """행마다 글자를 sep으로 나눈 조각별 개수를 JSON 객체 글자({"서울": 2, "주문": 1})로 새 컬럼에 넣는다. 키는 조각의 정렬 순서.
    sep이 없으면 공백류. 조각의 앞뒤 공백은 지우고 빈 조각은 세지 않는다. 값이 빈 값이거나 조각이 없으면 빈 값.
    행 안에서 세는 식을 먼저 쓰고(빠르고 행 순서가 그대로), 그 연결이 컴파일하지 못하면 번호표 방식으로 간다(실행하지 않고 컴파일만 해 본다)."""
    new, sep = f"{name}_counts", arg.get("sep")
    check_new(t, [new])
    row = counts_in_row(t[name], sep)
    try:
        t.get_backend().compile(t.select(row))
    except source.unsupported_errors():
        return counts_by_table(t, name, new, sep)
    return t.mutate(**{new: row})


def json_keys(text):
    """JSON 글자가 객체면 그 맨 위 키들, 아니면(깨졌거나 배열·숫자) 빈 목록."""
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return list(value) if isinstance(value, dict) else []


def expand_keys(t, name, dtype):
    """맵·JSON 컬럼의 키를 앞 EXPAND_SCAN행에서 모아 정렬해서(맵은 키 값 그대로, JSON은 맨 위 객체의 키 글자). EXPAND_MAX개를 넘으면 거부한다."""
    head = t.limit(EXPAND_SCAN)
    if dtype.is_map():
        found = head.select(k=head[name].keys().unnest()).distinct().to_pyarrow()["k"].to_pylist()
    else:  # JSON 글자(또는 json 타입): 객체가 아니거나 JSON이 아닌 값은 건너뛴다
        texts = head.filter(head[name].notnull()).select(v=head[name].cast("string")).to_pyarrow()["v"].to_pylist()
        found = {k for v in texts for k in json_keys(v)}
    if len(found) > EXPAND_MAX:
        raise ValueError(f"'{name}' 컬럼은 키가 {EXPAND_MAX}개를 넘어 컬럼으로 펼칠 수 없어요(앞 {EXPAND_SCAN:,}행 기준).")
    if not found:
        raise ValueError(f"'{name}' 컬럼에서 펼칠 키를 찾지 못했어요(앞 {EXPAND_SCAN:,}행 기준).")
    return sorted(found, key=str)


def expand(t, name, arg):
    """구조 컬럼은 필드마다, 맵 컬럼은 앞 행들의 키마다, JSON 글자는 맨 위 키마다 '{컬럼}.{키}' 컬럼을 더한다(원래 컬럼은 그대로).
    JSON 값은 글자 컬럼으로(글자는 따옴표 없이, 숫자·참거짓·객체는 JSON 글자 그대로, null은 빈 값).
    arg가 {"keys": [...]}(처음 찾은 키를 저장해 둔 것)면 맵·JSON의 키를 다시 찾지 않는다(식을 만들 때마다 조회가 나가지 않게)."""
    col, dtype = t[name], t[name].type()
    found = lambda: arg["keys"] if isinstance(arg, dict) else expand_keys(t, name, dtype)
    if dtype.is_struct():
        new = {f"{name}.{f}": col[f] for f in dtype.names}
    elif dtype.is_map():
        new = {f"{name}.{k}": col.get(k) for k in found()}
    elif dtype.is_string() or dtype.is_json():
        js = col if dtype.is_json() else col.cast("json")
        new = {f"{name}.{k}": ibis.coalesce(js[k].unwrap_as("string"), js[k].cast("string")).nullif("null") for k in found()}
    else:
        raise ValueError(f"'expand'(컬럼으로 펼치기)는 구조·맵·JSON 글자 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(dtype)[0]} 컬럼이에요. (목록은 unnest로 행으로 펼쳐요)")
    return add_columns(t, new)


def is_has(v):
    return (isinstance(v, dict) and set(v) <= {"words", "regex"} and isinstance(v.get("regex", False), bool) and is_strs(v.get("words"))
            and 1 <= len(v["words"]) <= 50 and all(v["words"]) and len(set(v["words"])) == len(v["words"]))


# (이름, 쓸 수 있는 컬럼 종류(None이면 모두), 값이 맞는지, 값의 형식, 적용 함수). 적용 순서는 이 표의 순서다.
# 전처리를 더할 때는 함수를 만들고 여기에 한 줄을 넣는다. 파생 컬럼을 만드는 것(flag, parts, label, onehot)은 add_columns로 컬럼을 더한다.
OPS = [
    ("trim", STRING, lambda v: v is True, "true", column(lambda col, _: strip(col))),
    ("squeeze", STRING, lambda v: v is True, "true", column(lambda col, _: col.re_replace(r"\s{2,}", " "))),
    ("nulls", STRING, is_strs, '["-", "N/A", ""]', column(lambda col, values: col.isin(values).ifelse(ibis.null(), col))),
    ("remove", STRING, lambda v: isinstance(v, str) and v != "", '"지울 글자들"', column(lambda col, text: col.re_replace(literal(text), ""))),
    ("case", STRING, lambda v: v in ("upper", "lower"), '"upper" 또는 "lower"', column(lambda col, how: getattr(col, how)())),
    ("replace", None, is_replace, '[["찾을 값", "바꿀 값"], ...] 또는 {"how": "exact" | "contains" | "prefix" | "suffix" | "regex" | "list" | "cond" | "null_to" | "to_null", '
     '"pairs": [[찾을, 바꿀], ...], "ignore_case": false, "trim": false, "rest": "keep" | "null" | 값, "to_new": "새 컬럼"}', replace),
    ("regex", STRING, lambda v: is_strs(v) and len(v) == 2 and v[0] != "", '["패턴", "바꿀 글자"]', regex_replace),
    ("type", None, lambda v: isinstance(v, dict) and v.get("to") in TYPE_TO and set(v) <= {"to", "clean", "format"}
     and isinstance(v.get("clean", False), bool) and isinstance(v.get("format", ""), str),
     '{"to": "int" | "float" | "string" | "date", "clean": true, "format": "%Y%m%d"}', column(retype)),
    ("flag", None, lambda v: v is True, "true", lambda t, name, _: add_columns(t, {f"{name}_isnull": t[name].isnull()})),
    ("fill", None, lambda v: isinstance(v, dict) and v.get("how") in FILLS and set(v) <= {"how", "value", "by"}
     and (v["how"] != "value" or v.get("value") is not None) and (v["how"] not in ("prev", "next") or isinstance(v.get("by"), str)),
     '{"how": "drop" | "value" | "mean" | "median" | "mode" | "prev" | "next", "value": 채울 값, "by": 순서를 정할 컬럼(prev·next)}', fill),
    ("outlier", NUMBER, is_outlier, '{"how": "drop" | "clip", "by": "range" | "iqr" | "std", "low": 수, "high": 수, "k": 수}', outlier),
    ("scale", NUMBER, lambda v: v in ("standard", "minmax", "log"), '"standard", "minmax" 또는 "log"', scale),
    ("round", NUMBER, lambda v: type(v) is int and 0 <= v <= 15, "0 이상 15 이하의 정수", column(sql_funcs.half_up)),
    ("bins", NUMBER, lambda v: isinstance(v, list) and len(v) >= 2 and all(is_num(e) for e in v) and all(a < b for a, b in zip(v, v[1:])),
     "오름차순 경계값 2개 이상. 예: [0, 10, 100]", column(bins)),
    ("rare", STRING, lambda v: isinstance(v, dict) and set(v) <= {"min", "label"} and type(v.get("min")) is int and v["min"] >= 2
     and isinstance(v.get("label", ""), str), '{"min": 2 이상의 정수, "label": "기타"}', rare),
    ("parts", DATE, lambda v: is_strs(v) and all(p in PARTS for p in v), f"{list(PARTS)} 중 하나 이상", date_parts),
    ("label", None, lambda v: v is True, "true", label),
    ("onehot", None, lambda v: v is True, "true", onehot),
    ("extract", STRING, lambda v: isinstance(v, dict) and set(v) <= {"pattern", "group"} and isinstance(v.get("pattern"), str) and v["pattern"] != ""
     and type(v.get("group", 0)) is int and v.get("group", 0) >= 0, '{"pattern": "정규식", "group": 0}', extract),
    ("has", STRING, is_has, '{"words": ["서울", "부산"], "regex": false} (words는 비어 있지 않은 서로 다른 글자 1~50개)', has),
    ("counts", STRING, lambda v: isinstance(v, dict) and set(v) <= {"sep"} and isinstance(v.get("sep", ""), str), '{"sep": ","}', counts),
    ("expand", None, lambda v: v is True or (isinstance(v, dict) and set(v) == {"keys"} and isinstance(v["keys"], list) and 1 <= len(v["keys"]) <= EXPAND_MAX
                                             and all(isinstance(k, (str, int)) and not isinstance(k, bool) for k in v["keys"])), 'true 또는 {"keys": ["찾아 둔 키", ...]}', expand),
]


OPS_NAMES = [op[0] for op in OPS]

# 화면(쿼리 우클릭 메뉴·새 컬럼 상자)에 줄 전처리 단계 목록(GET /api/prep/ops). 차례는 OPS와 같고, 라벨·묶음은 static/prep/prep.html의 OPS와 같은 글이다.
# need: 고를 수 있는 컬럼 종류(str·num·date·any·struct), fields: 입력 칸 {name(prep.html 입력 상태의 키), placeholder | check | options}.
_F = lambda name, placeholder: {"name": name, "placeholder": placeholder}
_C = lambda name, check: {"name": name, "check": check}
_O = lambda name, *options: {"name": name, "options": [list(o) for o in options]}
LABELS = [
    {"id": "trim", "label": "앞뒤 공백 지우기", "group": "글자", "need": "str", "fields": []},
    {"id": "squeeze", "label": "연속 공백을 하나로", "group": "글자", "need": "str", "fields": []},
    {"id": "nulls", "label": "이 값들을 빈 값으로", "group": "글자", "need": "str", "fields": [_F("text", "예: -, N/A(쉼표로 구분)"), _C("blank", "빈 문자열도")]},
    {"id": "remove", "label": "글자 찾아 지우기", "group": "글자", "need": "str", "fields": [_F("text", "지울 글자들")]},
    {"id": "case", "label": "대문자·소문자로", "group": "글자", "need": "str", "fields": [_O("how", ("upper", "대문자로"), ("lower", "소문자로"))]},
    {"id": "replace", "label": "값 바꿔 쓰기", "group": "글자", "need": "any", "fields": [_F("from", "찾을 값"), _F("to", "바꿀 값")]},
    {"id": "regex", "label": "정규식으로 바꾸기", "group": "글자", "need": "str", "fields": [_F("pattern", "패턴"), _F("to", "바꿀 글자")]},
    {"id": "type", "label": "타입 바꾸기", "group": "타입", "need": "any",
     "fields": [_O("to", ("int", "정수"), ("float", "실수"), ("string", "글자"), ("date", "날짜")), _C("clean", "쉼표·단위 떼기"), _F("format", "%Y-%m-%d")]},
    {"id": "flag", "label": "빈 값 표시 컬럼 만들기", "group": "새 컬럼", "need": "any", "fields": []},
    {"id": "fill", "label": "빈 값 처리", "group": "빈 값", "need": "any",
     "fields": [_O("how", ("drop", "그 행 빼기"), ("value", "정한 값으로"), ("mean", "평균으로"), ("median", "중앙값으로"), ("mode", "최빈값으로"), ("prev", "앞의 값으로"), ("next", "뒤의 값으로")),
                _F("value", "채울 값"), _F("by", "순서를 정할 컬럼")]},
    {"id": "outlier", "label": "범위 밖 값 처리", "group": "숫자", "need": "num",
     "fields": [_O("how", ("drop", "그 행 빼기"), ("clip", "범위로 잘라 내기")), _O("by", ("range", "직접 범위"), ("iqr", "사분위수(IQR)"), ("std", "표준편차")),
                _F("low", "최소"), _F("high", "최대"), _F("k", "k (비우면 IQR 1.5, 표준편차 3)")]},
    {"id": "scale", "label": "크기 맞추기(스케일링)", "group": "숫자", "need": "num", "fields": [_O("how", ("standard", "표준화"), ("minmax", "0~1 사이로"), ("log", "로그 ln(1+x)"))]},
    {"id": "round", "label": "반올림", "group": "숫자", "need": "num", "fields": [_F("digits", "소수점 아래 자릿수(0 이상)")]},
    {"id": "bins", "label": "구간 나누기", "group": "숫자", "need": "num", "fields": [_F("text", "경계값을 작은 것부터 쉼표로")]},
    {"id": "rare", "label": "드문 값 묶기", "group": "범주", "need": "str", "fields": [_F("min", "이보다 적게 나오면 묶기(2 이상)"), _F("label", "묶은 값의 이름(기본: 기타)")]},
    {"id": "parts", "label": "날짜에서 뽑기", "group": "새 컬럼", "need": "date",
     "fields": [_C("year", "연"), _C("month", "월"), _C("day", "일"), _C("weekday", "요일"), _C("ym", "월 단위"), _C("hour", "시")]},
    {"id": "label", "label": "값을 번호로(라벨 인코딩)", "group": "새 컬럼", "need": "any", "fields": []},
    {"id": "onehot", "label": "값마다 컬럼으로(원-핫 인코딩)", "group": "새 컬럼", "need": "any", "fields": []},
    {"id": "extract", "label": "패턴에 맞는 부분 뽑기", "group": "새 컬럼", "need": "str", "fields": [_F("pattern", "정규식, 예: [A-Z]-[0-9]+"), _F("group", "괄호 묶음 번호(비우면 전체)")]},
    {"id": "has", "label": "글자가 들어 있는지 표시", "group": "새 컬럼", "need": "str", "fields": [_F("words", "찾을 글자 예: 서울, 부산"), _C("regex", "정규식으로")]},
    {"id": "counts", "label": "단어별 개수 세기", "group": "새 컬럼", "need": "str", "fields": [_F("sep", "구분자(비우면 공백)")]},
    {"id": "expand", "label": "컬럼 여러 개로 펼치기(구조·맵·JSON)", "group": "새 컬럼", "need": "struct", "fields": []},
]


def nan_to_null(col):
    """실수 컬럼의 진짜 NaN과 ±inf를 빈 값으로(prep_profile과 같게. NaN·inf가 평균·표준편차·IQR을 NaN으로 만들거나 DuckDB 오류를 내고 빈 값 표시에서 빠지는 것을 막는다). 실수가 아니면 그대로.
    연결이 isnan·isinf를 하지 못하면(SQLite·SQL Server) 그대로 둔다."""
    if not col.type().is_floating():
        return col
    out = (col.isnan() | col.isinf()).ifelse(ibis.null(), col)
    return out if source.compiles(out) else col


NAN_OPS = ("flag", "fill", "outlier", "scale", "label", "onehot")  # 빈 값을 따지거나 통계를 내는 일: 먼저 NaN을 빈 값으로


def drops_rows(key, value):
    return key in ("fill", "outlier") and isinstance(value, dict) and value.get("how") == "drop"


OUTLIER_AT = OPS_NAMES.index("outlier")  # 행을 뺄지 정하는 통계(outlier)는 단계 시작 표로 구하니, 그 앞의 일만 시작 표(base)에도 한다


def fills_stat(key, value):
    """빈칸을 채울 값을 통계(mean·median·mode)로 구하는 fill: 행을 다 뺀 뒤의 표로 구하려고 rest로 미룬다."""
    return key == "fill" and isinstance(value, dict) and value.get("how") in ("mean", "median", "mode")


def apply_ops(t, name, ops, part="all", base=None):
    """컬럼 하나의 ops를 OPS의 순서대로 적용한다. 컬럼 타입은 앞 단계를 적용한 뒤의 것을 따른다.
    part가 rows면 마지막 행 줄이기(fill·outlier의 drop)까지만, rest면 그 뒤 것과 fill의 통계 채우기(mean·median·mode)를 적용한다(행 줄이기가 없으면 모두 rest).
    규칙: 행을 뺄지 정하는 통계(outlier의 경계)는 base(그 단계를 시작하는 표)로 구해 컬럼 순서와 무관하고,
    채울 값(fill mean 등)과 scale 같은 값 통계는 모든 컬럼의 행 줄이기가 끝난 t로 구한다(앞 컬럼이 뺀 행을 빼고 평균을 구함).
    이 컬럼의 outlier 앞선 일(타입 바꾸기 등)은 base에도 같이 해 경계가 같은 값에서 나오게 한다(상수·prev·next 채우기도 값을 바꾸니 base에 한다. 통계 채우기(mean·median·mode)와 drop만 base에 하지 않는다).
    컬럼에 outlier가 없으면 base는 쓰지 않는다."""
    for key in ops:
        parse.check_name(key, OPS_NAMES, "전처리 항목")
    if "outlier" not in ops:
        base = None
    last = max((i for i, op in enumerate(OPS) if op[0] in ops and drops_rows(op[0], ops[op[0]])), default=-1)
    for i, (key, kinds, valid, form, fn) in enumerate(OPS):
        if key not in ops:
            continue
        deferred = fills_stat(key, ops[key])
        mine = (i <= last and not deferred) if part == "rows" else (i > last or deferred) if part == "rest" else True
        src = t if mine or base is None else base  # 이 part의 일이 아니어도 base에 하는 일(outlier 앞)은 base에 하기 전에 검사한다
        if mine or (base is not None and i <= OUTLIER_AT):
            dtype = src[name].type()
            if kinds and parse.kind(dtype) not in kinds:
                raise ValueError(f"'{key}'은 {'·'.join(KIND_NAMES[k] for k in kinds)} 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(dtype)[0]} 컬럼이에요.")
        if not valid(ops[key]):  # 값 형식은 이 part의 일이 아니어도 먼저 본다(base에 하다 엉뚱한 오류가 나지 않게)
            raise ValueError(f"'{name}' 컬럼의 '{key}' 값은 {form} 형식으로 쓰세요.")
        if key in NAN_OPS:
            if mine:
                t = t.mutate(**{name: nan_to_null(t[name])})
            if base is not None and i <= OUTLIER_AT:
                base = base.mutate(**{name: nan_to_null(base[name])})
        if key == "outlier" and base is not None:
            if mine:
                t = fn(t, name, ops[key], base)
            continue
        if mine:
            t = fn(t, name, ops[key])
        if base is not None and i < OUTLIER_AT and not deferred and not drops_rows(key, ops[key]):
            base = fn(base, name, ops[key])
    return t
