"""컬럼 하나에 적용하는 전처리(OPS 표)와 그 함수들. 평균·분위수·값 목록처럼 테이블 전체에서 구하는 값은
작은 조회로 먼저 구해 값으로 식에 넣는다(Polars 백엔드는 창 함수와 스칼라 서브쿼리가 없다)."""
import functools
import operator
import re

from lazy import ibis

from core import parse
from core import source

TYPE_TO = {"int": "int64", "float": "float64", "string": "string", "date": "date"}
FLOAT_SHAPE = r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$"  # 12, -1.5, .5, 1e5. inf·nan은 숫자로 보지 않는다.
FILLS = ["drop", "value", "mean", "median", "mode", "prev", "next"]
STRING, NUMBER, DATE = ("string",), ("int", "float"), ("date",)
KIND_NAMES = {"string": "문자", "int": "정수", "float": "실수", "date": "날짜"}
RARE_MAX = 1000  # rare로 바꿀 값 목록의 최대 개수
LABEL_MAX = 1000  # label로 번호를 붙일 서로 다른 값의 최대 개수
ONEHOT_MAX = 50  # onehot으로 펼칠 서로 다른 값의 최대 개수
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
    """파생 컬럼 이름이 이미 있으면 거부한다(mutate는 말없이 덮어쓴다)."""
    for name in names:
        if name in t.columns:
            raise ValueError(f"'{name}' 컬럼이 이미 있어 파생 컬럼을 만들 수 없습니다.")


def add_columns(t, new):
    check_new(t, new)
    return t.mutate(**new)


def replace_values(col, pairs):
    return ibis.cases(*[(col == old, new) for old, new in pairs], else_=col)


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


def date_pattern(fmt):
    """날짜 형식(DATE_PARTS의 글자와 그 사이의 글자)에 맞는 글자만 찾는 정규식. 영문 이름과 AM/PM은 대소문자를 가리지 않는다."""
    pieces = re.split(r"(%.)", fmt)
    if any(p.startswith("%") and p not in DATE_PARTS for p in pieces):
        raise ValueError(f"날짜 형식에는 {' '.join(DATE_PARTS)}만 쓸 수 있습니다. 예: %Y%m%d, %Y-%m-%d %H:%M:%S")
    return "(?i)^" + "".join(DATE_PARTS.get(p) or literal(p) for p in pieces) + "$"


def to_date(col, fmt):
    """글자를 날짜(형식에 시각이 있으면 날짜시간)로. Polars 백엔드는 try_cast("date")가 없고 as_date·as_timestamp는 안 맞는 값에 오류를 내므로,
    형식에 맞는 모양이 아닌 값은 먼저 빈 값으로 만든다(2026-02-30처럼 모양만 맞는 값은 DB의 오류가 난다)."""
    col = col.cast("string")
    col = col.re_search(date_pattern(fmt)).ifelse(col, ibis.null())
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
    if dtype.is_numeric():  # 실수·소수를 정수로: nan·inf와 int64 범위 밖의 값은 빈 값
        return ((col >= -2.0**63) & (col < 2.0**63)).ifelse(col, ibis.null()).cast(TYPE_TO[to])
    col = col.cast("string")  # 날짜·시간처럼 숫자가 아닌 컬럼은 숫자 모양이 아니라 빈 값이 된다.
    return is_number(col, to).ifelse(col, ibis.null()).cast(TYPE_TO[to])


def retype(col, arg):
    to = arg["to"]
    if to == "string":  # 글자로는 값을 바꾸지 않고 타입만 바꾼다(숫자 모양 검사·공백 지우기를 거치지 않는다).
        return col.cast("string")
    if col.type().is_string():  # 앞뒤 공백은 무시한다(백엔드마다 다르게 다루는 것을 맞춘다).
        col = col.strip()
    if arg.get("clean") and to in ("int", "float"):  # 숫자·부호·소수점이 아닌 글자를 먼저 지운다.
        col = col.cast("string").re_replace(r"[^0-9.+\-]", "")
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
            raise ValueError(f"'{how}'은 정수·실수 컬럼에만 쓸 수 있습니다. '{name}' 컬럼은 {parse.type_info(col.type())[0]}입니다.")
        value = getattr(col, how)().to_pyarrow().as_py()
    return t.mutate(**{name: col.fill_null(value)})


def outlier(t, name, arg):
    col, by, k = t[name], arg["by"], arg.get("k")
    if by == "range":
        low, high = arg.get("low"), arg.get("high")
    else:
        s = stats(t, a=col.quantile(0.25), b=col.quantile(0.75)) if by == "iqr" else stats(t, a=col.mean(), b=col.std())
        if None in s.values():
            raise ValueError(f"'{name}' 컬럼에 값이 부족해 범위를 구할 수 없습니다.")
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
            raise ValueError(f"'{name}' 컬럼의 최솟값이 {low}로 -1 이하라 log로 바꿀 수 없습니다.")
        return t.mutate(**{name: (1 + col).ln()})
    if how == "minmax":
        s = stats(t, a=col.min(), b=col.max())
        if s["a"] == s["b"]:
            raise ValueError(f"'{name}' 컬럼은 값이 모두 같거나 비어 있어(범위가 0) minmax로 바꿀 수 없습니다.")
        return t.mutate(**{name: (col - s["a"]) / (s["b"] - s["a"])})
    s = stats(t, a=col.mean(), b=col.std())
    if not s["b"]:
        raise ValueError(f"'{name}' 컬럼은 표준편차가 0이거나 구할 수 없어 standard로 바꿀 수 없습니다.")
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
        raise ValueError(f"'{name}' 컬럼은 서로 다른 값이 {limit:,}개를 넘어 {what}을 만들 수 없습니다.")
    return sorted(found)


def rare(t, name, arg):
    counts = t.group_by(name).aggregate(n=t.count())
    found = counts.filter(counts.n < arg["min"], counts[name].notnull()).select(name).limit(RARE_MAX + 1).to_pyarrow()[name].to_pylist()
    if len(found) > RARE_MAX:
        raise ValueError(f"'{name}' 컬럼은 {arg['min']}번 미만 나온 값이 {RARE_MAX:,}개를 넘어 rare를 쓸 수 없습니다.")
    return t.mutate(**{name: t[name].isin(found).ifelse(arg.get("label", "기타"), t[name])})


def label(t, name, _):
    """값을 정렬한 순서의 0부터의 번호. 빈 값은 빈 값."""
    values = distinct_values(t, name, LABEL_MAX, "label")
    return add_columns(t, {f"{name}_label": ibis.cases(*[(t[name] == v, i) for i, v in enumerate(values)], else_=ibis.null("int64")).cast("int64")})


def date_parts(t, name, parts):
    if "hour" in parts and not t[name].type().is_timestamp():
        raise ValueError(f"'hour'는 날짜시간 컬럼에만 쓸 수 있습니다. '{name}' 컬럼은 {parse.type_info(t[name].type())[0]}입니다.")
    return add_columns(t, {f"{name}_{p}": PARTS[p](t[name]) for p in parts})


def onehot(t, name, _):
    return add_columns(t, {f"{name}_{v}": ibis.coalesce(t[name] == v, False).cast("int8") for v in distinct_values(t, name, ONEHOT_MAX, "onehot")})


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


def is_has(v):
    return (isinstance(v, dict) and set(v) <= {"words", "regex"} and isinstance(v.get("regex", False), bool) and is_strs(v.get("words"))
            and 1 <= len(v["words"]) <= 50 and all(v["words"]) and len(set(v["words"])) == len(v["words"]))


# (이름, 쓸 수 있는 컬럼 종류(None이면 모두), 값이 맞는지, 값의 형식, 적용 함수). 적용 순서는 이 표의 순서다.
# 전처리를 더할 때는 함수를 만들고 여기에 한 줄을 넣는다. 파생 컬럼을 만드는 것(flag, parts, label, onehot)은 add_columns로 컬럼을 더한다.
OPS = [
    ("trim", STRING, lambda v: v is True, "true", column(lambda col, _: col.strip())),
    ("squeeze", STRING, lambda v: v is True, "true", column(lambda col, _: col.re_replace(r"\s{2,}", " "))),
    ("nulls", STRING, is_strs, '["-", "N/A", ""]', column(lambda col, values: col.isin(values).ifelse(ibis.null(), col))),
    ("remove", STRING, lambda v: isinstance(v, str) and v != "", '"지울 글자들"', column(lambda col, text: col.re_replace(literal(text), ""))),
    ("case", STRING, lambda v: v in ("upper", "lower"), '"upper" 또는 "lower"', column(lambda col, how: getattr(col, how)())),
    ("replace", STRING, lambda v: isinstance(v, list) and bool(v) and all(is_strs(p) and len(p) == 2 for p in v),
     '[["찾을 값", "바꿀 값"], ...]', column(replace_values)),
    ("regex", STRING, lambda v: is_strs(v) and len(v) == 2 and v[0] != "", '["패턴", "바꿀 글자"]', column(lambda col, pair: col.re_replace(*pair))),
    ("type", None, lambda v: isinstance(v, dict) and v.get("to") in TYPE_TO and set(v) <= {"to", "clean", "format"}
     and isinstance(v.get("clean", False), bool) and isinstance(v.get("format", ""), str),
     '{"to": "int" | "float" | "string" | "date", "clean": true, "format": "%Y%m%d"}', column(retype)),
    ("flag", None, lambda v: v is True, "true", lambda t, name, _: add_columns(t, {f"{name}_isnull": t[name].isnull()})),
    ("fill", None, lambda v: isinstance(v, dict) and v.get("how") in FILLS and set(v) <= {"how", "value", "by"}
     and (v["how"] != "value" or v.get("value") is not None) and (v["how"] not in ("prev", "next") or isinstance(v.get("by"), str)),
     '{"how": "drop" | "value" | "mean" | "median" | "mode" | "prev" | "next", "value": 채울 값, "by": 순서를 정할 컬럼(prev·next)}', fill),
    ("outlier", NUMBER, is_outlier, '{"how": "drop" | "clip", "by": "range" | "iqr" | "std", "low": 수, "high": 수, "k": 수}', outlier),
    ("scale", NUMBER, lambda v: v in ("standard", "minmax", "log"), '"standard", "minmax" 또는 "log"', scale),
    ("round", NUMBER, lambda v: type(v) is int and v >= 0, "0 이상의 정수", column(lambda col, digits: col.round(digits))),
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
]


OPS_NAMES = [op[0] for op in OPS]


def apply_ops(t, name, ops):
    """컬럼 하나의 ops를 OPS의 순서대로 적용한다. 컬럼 타입은 앞 단계를 적용한 뒤의 것을 따른다."""
    for key in ops:
        parse.check_name(key, OPS_NAMES, "전처리 항목")
    for key, kinds, valid, form, fn in OPS:
        if key not in ops:
            continue
        dtype = t[name].type()
        if kinds and parse.kind(dtype) not in kinds:
            raise ValueError(f"'{key}'은 {'·'.join(KIND_NAMES[k] for k in kinds)} 컬럼에만 쓸 수 있습니다. '{name}' 컬럼은 {parse.type_info(dtype)[0]}입니다.")
        if not valid(ops[key]):
            raise ValueError(f"'{name}' 컬럼의 '{key}' 값은 {form} 형식으로 쓰세요.")
        t = fn(t, name, ops[key])
    return t
