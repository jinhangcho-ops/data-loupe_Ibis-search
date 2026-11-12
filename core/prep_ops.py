"""컬럼 하나에 적용하는 전처리(OPS 표)와 그 함수들. 평균·분위수·값 목록처럼 테이블 전체에서 구하는 값은
작은 조회로 먼저 구해 값으로 식에 넣는다(Polars 백엔드는 창 함수와 스칼라 서브쿼리가 없다)."""
import functools
import json
import math
import operator
import re
from typing import Callable

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
BYTES_PER_MB = 1024 * 1024
LABEL_MAX = 1000  # label로 번호를 붙일 서로 다른 값의 최대 개수
BIN_START = re.compile(r"^(-?[0-9][0-9,]*\.?[0-9]*(?:e[-+]?[0-9]+)?)(?:–| ~ )", re.I)  # bins가 만든 구간 글자의 시작 숫자
ONEHOT_MAX = 50  # onehot으로 펼칠 서로 다른 값의 최대 개수
EXPAND_MAX, EXPAND_SCAN = 50, 1000  # expand로 펼칠 맵·JSON 키의 최대 개수, 그 키를 모으려고 읽는 앞 행 수
PARTS = {  # 날짜에서 뽑는 값. 정수는 int64로 맞춘다(백엔드마다 int16·int32로 다르다).
    "year": lambda col: col.year().cast("int64"), "month": lambda col: col.month().cast("int64"), "day": lambda col: col.day().cast("int64"),
    "weekday": lambda col: col.day_of_week.index().cast("int64"), "hour": lambda col: col.hour().cast("int64"), "ym": lambda col: col.cast("date").truncate("M"),
    "weekday_num": lambda col: sql_funcs.weekday_num(sql_funcs.Args(x=col)), "weekday_name": lambda col: sql_funcs.weekday_name(sql_funcs.Args(x=col)),
    "is_weekend": lambda col: sql_funcs.is_weekend(sql_funcs.Args(x=col, days=None)), "is_month_end": lambda col: sql_funcs.is_month_end(sql_funcs.Args(x=col)),
    "time_of_day": lambda col: sql_funcs.time_of_day(sql_funcs.Args(x=col, bounds=list(sql_funcs.DAY_BOUNDS), labels=None)),
    "month_phase": lambda col: sql_funcs.month_phase(sql_funcs.Args(x=col, bounds=list(sql_funcs.MONTH_BOUNDS), labels=None)),
    "work_hours": lambda col: sql_funcs.work_hours(sql_funcs.Args(x=col, days="weekdays", start=None, end=None)),  # 시작·끝·주말 요일은 설정을 따른다
}
CLOCK_PARTS = ("hour", "time_of_day", "work_hours")  # 날짜시간 컬럼에만 쓰는 parts


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
    """표(또는 컬럼)가 파일 연결(Polars)의 것인지. 연결이 없는 표(함수 지원 확인용)는 아니다."""
    try:
        return t.get_backend().name == "polars"
    except Exception:
        return False


def chars(col):
    """글자 수(정수). Polars의 length()는 UTF-8 바이트 수라('가나' 6) 글자마다 한 바이트로 바꾼 뒤 센다(감사51). 함수 사전 length와 계산 컬럼 length가 쓴다."""
    return (col.re_replace("(?s).", "a") if on_polars(col) else col).length().cast("int64")


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
        value = arg["known"] if isinstance(arg, Known) else known_stats(t, name, "fill", arg)
    return t.mutate(**{name: col.fill_null(value)})


def outlier(t, name, arg, base=None):
    """base를 주면 iqr·std 통계를 t 대신 base(단계 시작 표)로 구한다(앞 컬럼이 행을 줄인 뒤의 표가 아니라)."""
    col, by = t[name], arg["by"]
    src = t if base is None else base
    if by == "range":
        low, high = arg.get("low"), arg.get("high")
    else:
        bounds = known_stats(src, name, "outlier", arg)
        low, high = bounds["low"], bounds["high"]
    if col.type().is_integer() and any(b is not None and b != int(b) for b in (low, high)):
        col = col.cast("float64")  # 정수 컬럼을 소수 경계로 자르면 백엔드마다 어긋나므로(오류 또는 소수점 버림) 실수로 바꾼다.
    if arg["how"] == "clip":
        return t.mutate(**{name: col.clip(low, high)})
    inside = [p for p, bound in ((col >= low, low), (col <= high, high)) if bound is not None]
    return t.filter(col.isnull() | functools.reduce(operator.and_, inside))  # 빈 값인 행은 남긴다.


def scale(t, name, how):
    col = t[name]
    kind, s = (how["how"], how["known"]) if isinstance(how, Known) else (how, known_stats(t, name, "scale", how))
    if kind in ("log", "log10"):
        return t.mutate(**{name: (1 + col).ln() if kind == "log" else (1 + col).log10()})
    if kind in ("minmax", "minmax100"):
        return t.mutate(**{name: (col - s["a"]) / (s["b"] - s["a"]) * (100 if kind == "minmax100" else 1)})
    return t.mutate(**{name: (col - s["a"]) / s["b"]})  # standard·robust((x − 중앙값) / IQR)


def bins(col, edges):
    """edges를 경계로 한 구간 글자. 왼쪽 포함·오른쪽 미포함이고 마지막 구간만 오른쪽도 포함한다.
    dict면 경계 포함 방향(closed)·표시(label), 글자는 '1,000 ~ 10,000' 꼴."""
    if isinstance(edges, dict):
        return labeled_bins(col, edges["edges"], edges.get("closed", "left"), edges.get("label", "range"))
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
    found = arg["known"] if isinstance(arg, Known) else known_stats(t, name, "rare", arg)
    return t.mutate(**{name: t[name].isin(found).ifelse(arg.get("label", "기타"), t[name])})


def label(t, name, arg):
    """값을 정렬한 순서의 0부터의 번호. 빈 값은 빈 값."""
    values = arg["known"] if isinstance(arg, Known) else known_stats(t, name, "label", arg)
    if not values:  # 값이 모두 빈 컬럼: 번호도 모두 빈 값(ibis.cases는 갈래가 하나는 있어야 한다)
        return add_columns(t, {f"{name}_label": ibis.null("int64")})
    return add_columns(t, {f"{name}_label": ibis.cases(*[(t[name] == v, i) for i, v in enumerate(values)], else_=ibis.null("int64")).cast("int64")})


def date_parts(t, name, parts):
    if (bad := [p for p in parts if p in CLOCK_PARTS]) and not t[name].type().is_timestamp():
        raise ValueError(f"'{bad[0]}'는 날짜시간 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(t[name].type())[0]} 컬럼이에요.")
    return add_columns(t, {f"{name}_{p}": PARTS[p](t[name]) for p in parts})


def onehot(t, name, arg):
    """값이 {"top": K}면 많이 나온 K개 값만 컬럼으로, others면 나머지 값이면 1인 {컬럼}_기타도."""
    values = arg["known"] if isinstance(arg, Known) else known_stats(t, name, "onehot", arg)
    seen = {}
    for v in map(str, values):
        if (other := seen.setdefault(v.lower(), v)) != v:  # 새 컬럼 이름은 대소문자를 가리지 않아 겹친다(check_new와 같은 lower)
            raise ValueError(f"'{name}'의 값 '{other}'와 '{v}'는 대소문자만 달라요. 먼저 '대문자·소문자로'를 적용하세요.")
    return add_columns(t, {f"{name}_{v}": ibis.coalesce(t[name] == v, False).cast("int8") for v in values}
                       | ({f"{name}_{ONEHOT_OTHERS}": ibis.coalesce(t[name].notnull() & ~t[name].isin(values), False).cast("int8")} if isinstance(arg, dict) and arg.get("others") is True else {}))


def is_outlier(v):
    if not (isinstance(v, dict) and set(v) <= {"how", "by", "low", "high", "k", "p_low", "p_high"} and v.get("how") in ("drop", "clip") and v.get("by") in ("range", "iqr", "std", "pct")):
        return False
    if not all(v.get(key) is None or is_num(v[key]) for key in ("low", "high", "k", "p_low", "p_high")) or (v.get("k") is not None and v["k"] <= 0):
        return False
    if v["by"] == "pct":
        p_low, p_high = (OUTLIER_PCT[i] if v.get(key) is None else v[key] for i, key in enumerate(("p_low", "p_high")))
        return all(v.get(key) is None for key in ("low", "high", "k")) and 0 <= p_low < p_high <= 100
    if v.get("p_low") is not None or v.get("p_high") is not None:
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


IQR_QS = [0.25, 0.75]  # outlier iqr의 사분위수
ROBUST_QS = [0.25, 0.5, 0.75]  # scale robust의 사분위수(Q1·중앙값·Q3)
IQR_K, STD_K = 1.5, 3  # outlier iqr·std의 k 기본값
KNOWN_REFUSED = "'known'은 프로그램이 미리 구한 통계를 넣는 칸이라 직접 쓸 수 없어요. 그 칸을 빼고 쓰세요."


class Known(dict):
    """[SPECIFICATION] plans/phase-1.md#Known
    pin_value가 만든 미리 구한 통계 값(JSON에서 온 값은 이 타입이 될 수 없어 사용자가 쓴 known을 가려낸다)."""


def quantiles(t: "ibis.expr.types.Table", name: str, qs: list[float]) -> list[float | None]:
    """[SPECIFICATION] plans/phase-1.md#quantiles
    컬럼의 분위수들(0.5는 중앙값)을 연결이 구하면 연결에서, 못 구하면 그 컬럼만 이 PC로 받아 Polars로 구해 돌려준다."""
    exprs = {f"q{i}": (t[name].median() if q == 0.5 else t[name].quantile(q)) for i, q in enumerate(qs)}
    if source.compiles(t.aggregate(**exprs)):
        s = stats(t, **exprs)
        return [s[f"q{i}"] for i in range(len(qs))]
    from core import held  # 늦은 import(지연 로딩·순환 import)
    from core import settings
    import polars as pl
    limit = settings.get("pc_stat_rows")
    frame = t.select(name).limit(limit + 1).to_polars()
    if frame.height > limit or frame.estimated_size() > held.MAX_BYTES:
        raise ValueError(f"이 연결은 '{name}' 컬럼의 중앙값·사분위수를 구하지 못해 이 PC에서 구하려 했지만 {limit:,}행(또는 {held.MAX_BYTES // BYTES_PER_MB}MB)을 넘어 받지 않았어요. 기간·조건을 좁히거나, 왼쪽 메뉴 [설정]의 'PC에서 구할 통계의 최대 행 수'를 늘리거나, DuckDB 파일로 옮겨 쓰세요.")
    col = frame[name].cast(pl.Float64).drop_nulls()
    return [col.median() if q == 0.5 else col.quantile(q, interpolation="linear") for q in qs]


def known_stats(t: "ibis.expr.types.Table", name: str, op: str, value: object) -> object:
    """[SPECIFICATION] plans/phase-1.md#known_stats
    통계 단계(fill·outlier·scale·label·onehot·rare·expand·Phase 6(more_stats))가 표에서 먼저 구해야 하는 값을 작은 조회로 구해 돌려준다."""
    col = t[name]
    if op == "fill" and value["how"] in ("mean", "median", "mode"):
        how = value["how"]
        if how != "mode" and parse.kind(col.type()) not in NUMBER:
            raise ValueError(f"'{how}'은 정수·실수 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(col.type())[0]} 컬럼이에요.")
        if how == "median":
            return quantiles(t, name, [0.5])[0]
        if how == "mode" and not stats(t, n=col.count())["n"]:
            return None  # 값이 하나도 없음(Polars의 최빈값은 이때 오류를 낸다, 감사51)
        return getattr(col, how)().to_pyarrow().as_py()
    if op == "outlier" and value["by"] in ("iqr", "std", "pct"):
        if value["by"] in ("iqr", "pct"):
            a, b = quantiles(t, name, IQR_QS if value["by"] == "iqr" else [value.get("p_low", OUTLIER_PCT[0]) / 100, value.get("p_high", OUTLIER_PCT[1]) / 100])
            s = {"a": a, "b": b}
        else:
            s = stats(t, a=col.mean(), b=col.std())
        if None in s.values():
            raise ValueError(f"'{name}' 컬럼에 값이 부족해 범위를 구할 수 없어요.")
        if value["by"] == "pct":
            return {"low": float(s["a"]), "high": float(s["b"])}
        a, b, k = float(s["a"]), float(s["b"]), value.get("k")
        if value["by"] == "iqr":
            gap = (k or IQR_K) * (b - a)
            return {"low": a - gap, "high": b + gap}
        gap = (k or STD_K) * b
        return {"low": a - gap, "high": a + gap}
    if op == "scale":
        how = value if isinstance(value, str) else value["how"]
        if how in ("log", "log10"):
            low = stats(t, a=col.min())["a"]
            if low is not None and low <= -1:
                raise ValueError(f"'{name}' 컬럼의 최솟값이 {low}로 -1 이하라 log로 바꿀 수 없어요.")
            return {"a": low}
        if how in ("minmax", "minmax100"):
            s = stats(t, a=col.min(), b=col.max())
            if s["a"] == s["b"]:
                raise ValueError(f"'{name}' 컬럼은 값이 모두 같거나 비어 있어(범위가 0) minmax로 바꿀 수 없어요.")
            return {"a": s["a"], "b": s["b"]}
        if how == "robust":
            q1, med, q3 = quantiles(t, name, ROBUST_QS)
            if None in (q1, med, q3) or q3 == q1:
                raise ValueError(f"'{name}' 컬럼은 IQR이 0이거나 값이 없어 robust로 바꿀 수 없어요.")
            return {"a": float(med), "b": float(q3 - q1)}
        s = stats(t, a=col.mean(), b=col.std())
        if not s["b"]:
            raise ValueError(f"'{name}' 컬럼은 표준편차가 0이거나 구할 수 없어 standard로 바꿀 수 없어요.")
        return {"a": s["a"], "b": s["b"]}
    if op == "label":
        values = distinct_values(t, name, LABEL_MAX, "label")
        starts = [BIN_START.match(v) if isinstance(v, str) else None for v in values]
        if all(starts):  # bins로 만든 구간 글자("5–10", "10–20")는 글자순이 아니라 시작 숫자 순
            values = [v for _, v in sorted(zip((float(m[1].replace(",", "")) for m in starts), values))]
        return values
    if op == "onehot":
        if isinstance(value, dict):
            f = t.filter(col.notnull())
            return f.group_by(name).aggregate(__n=f.count()).order_by([ibis.desc("__n"), name]).limit(value["top"]).to_pyarrow()[name].to_pylist()
        return distinct_values(t, name, ONEHOT_MAX, "onehot")
    if op == "rare":
        counts = t.group_by(name).aggregate(n=t.count())
        found = counts.filter(counts.n < value["min"], counts[name].notnull()).select(name).limit(RARE_MAX + 1).to_pyarrow()[name].to_pylist()
        if len(found) > RARE_MAX:
            raise ValueError(f"'{name}' 컬럼은 {value['min']}번 미만 나온 값이 {RARE_MAX:,}개를 넘어 rare를 쓸 수 없어요.")
        return found
    if op == "expand" and not col.type().is_struct():
        return expand_keys(t, name, col.type())
    if op in STAT_OPS6:
        return more_stats(t, name, op, value)
    raise ValueError(f"'{op}'은 미리 구할 통계가 없어요.")


def pin_value(t: "ibis.expr.types.Table", name: str, op: str, value: object) -> object:
    """[SPECIFICATION] plans/phase-1.md#pin_value
    통계 단계의 값을 미리 구한 통계를 넣어 다시 조회하지 않는 값으로 바꿔 돌려준다."""
    parse.check_name(op, OPS_NAMES, "전처리 항목")
    key, kinds, valid, form, fn = OPS[OPS_NAMES.index(op)]
    dtype = t[name].type()
    if kinds and parse.kind(dtype) not in kinds:
        raise ValueError(f"'{op}'은 {'·'.join(KIND_NAMES[k] for k in kinds)} 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(dtype)[0]} 컬럼이에요.")
    if not valid(value):
        raise ValueError(f"'{name}' 컬럼의 '{op}' 값은 {form} 형식으로 쓰세요.")
    if op in NAN_OPS:
        t = t.mutate(**{name: nan_to_null(t[name])})
    known = known_stats(t, name, op, value)
    if op == "expand":
        return Known({"keys": known})
    if op == "outlier":
        return Known({"how": value["how"], "by": "range", "low": known["low"], "high": known["high"]})
    if op == "scale":
        return Known({"how": value if isinstance(value, str) else value["how"], "known": known})
    if op == "label" or (op == "onehot" and not isinstance(value, dict)):
        return Known({"known": known})
    return Known({**value, "known": known})


# ---- Phase 6: 전처리·파생변수 전용 단계(plans/phase-6.md). 원문 수치·사용자 결정(2026-11-04)이 기본값이고 값에 적으면 바뀐다. 모든 수치는 사용자가 확정했다 ----
STAT_OPS6 = ("flags", "gfill", "power", "cut", "others", "freq", "target")  # 표 전체 통계를 먼저 구할 수 있는 새 단계(값마다 needs_stats가 판정, known_stats → more_stats)
FLAG_CMPS = (">=", ">", "<=", "<", "==", "!=")  # 단일 임계치 표시의 비교
FLAG_TOP = [1, 5, 10]  # 상위 % 표시의 기본(원문: 상위 1%·5%·10%)
FLAG_K_OPTION = {"iqr": "iqr_k", "std": "z_k", "mad": "mad_k"}  # 이상치 표시의 k를 비우면 쓰는 Phase 5 진단 기준 이름(사용자가 저장한 값, prep_diag.options)
MAD_C = 0.6745  # 수정 Z-Score 상수: prep_diag.MAD_C와 같다(시험이 대조)
ISIN_MAX = 1000  # 집합 포함 표시의 최대 값 수
GROUP_MAX = 3  # 그룹별 빈 값 채우기(gfill)의 묶음 컬럼 수(newcol 그룹 칸과 같다)
GFILL_HOWS = ("mean", "median", "mode")
POWER_HOWS = ("power", "boxcox", "yeojohnson")
LAMBDA_GRID = (-3.0, 3.0, 121)  # Box-Cox·Yeo-Johnson λ 후보: 아래, 위, 개수(0.05 간격)
LAMBDA_EPS = 1e-9  # |λ|(Yeo-Johnson 음수 쪽은 |λ - 2|)가 이보다 작으면 로그 꼴(부동소수 비교용)
LAMBDA_MIN_N = 3  # λ를 구할 최소 값 수
CUT_HOWS = ("width", "quantile", "zero", "log", "floor", "tails", "best")
CUT_N = {"width": 10, "quantile": 4, "zero": 4, "tails": 10, "best": 5}  # 방식별 구간 수 기본(quantile 4는 원문 4분위)
CUT_N_MAX = 100  # 구간 수 n의 위
CUT_MAX_BINS = 1000  # floor·log가 만들 수 있는 구간 수의 위
CUT_TAIL = 5  # 양끝 클리핑 구간의 %(원문 5%)
CUT_TAIL_MAX = 50  # 양끝 % 값의 위(이 값 미만)
CUT_DIGITS = 6  # width·tails의 안쪽 경계를 유효 숫자 이 자리로 반올림(구간 글자가 길어지지 않게)
CUT_CLOSED = ("left", "right")  # [a, b) / (a, b]
CUT_LABELS = ("range", "mid", "index")  # 구간 글자 '10,000 ~ 50,000' / 구간 가운데 값 / 구간 번호
RANGE_SEP = " ~ "  # 구간 글자의 사이(원문 예시 '10,000 ~ 50,000', 숫자는 천 단위 쉼표)
ZERO_LABEL = "0 (무활동)"  # Zero-Separated의 0 구간 글자(원문)
NEG_LABEL = "0 미만 (음수)"  # Zero-Separated의 음수 구간 글자(사용자 결정: 빈 값으로 만들지 않고 따로 한 구간)
NONPOS_LABEL = "0 이하"  # 로그 구간의 0 이하 구간 글자(사용자 결정: 따로 한 구간)
SPECIAL_INDEX = -1  # 음수·0 이하 구간의 구간 번호(label index). 다른 구간은 0부터
TAIL_LABELS = ("~하위{p:g}%", "상위{p:g}%~")  # 양끝 묶음 구간 글자(원문)
BEST_METHODS = ("iv", "chi")  # 최적 구간화: IV 최대(위에서 나누기) / 카이제곱 병합(ChiMerge)
BEST_FINE = 20  # 최적 구간화의 처음 잘게 나눈 분위 구간 수
BEST_MIN_SHARE = 0.05  # IV 나누기에서 한 구간이 가질 최소 행 비율
BEST_SMOOTH = 0.5  # IV 계산에서 0인 칸에 더하는 수
OTHERS_HOWS = ("top", "share", "pareto")
OTHERS_DEFAULT = {"k": 10, "share": 1, "cum": 80}  # Top-K 10·비중 1%·누적 비중 80%(원문)
OTHERS_MAX = 1000  # 남길 값 목록의 최대 수(rare의 RARE_MAX와 같은 크기)
ENCODE_MAX = 1000  # 빈도·타겟 인코딩·그룹별 채우기의 서로 다른 값(그룹) 최대 수(label의 LABEL_MAX와 같은 크기)
ONEHOT_OTHERS = "기타"  # 원-핫 Top-K의 나머지 컬럼 이름 끝({컬럼}_기타, 옵션 others, 기본 끔)
FREQ_HOWS = ("count", "pct")  # 등장 횟수 / 비율(%)
ORDINAL_MAX = 1000  # 순서형 매핑의 최대 항목 수
SCALES = ("standard", "minmax", "minmax100", "robust", "log", "log10")  # scale 단계의 방식(Phase 6에서 minmax100·robust·log10을 더함)
OUTLIER_PCT = (1, 99)  # outlier by pct(윈저라이징)의 기본 하위·상위 백분위(원문 P1·P99)
TARGET_LEAK_NOTE = ("타겟 인코딩은 같은 표의 타겟 평균을 넣어요. 이 컬럼으로 그 타겟을 맞히는 모델을 만들면 점수가 실제보다 좋게 나올 수 있어요(누수). "
                    "학습·평가 표를 먼저 나누고 학습 표로 만든 값만 쓰세요.")  # 타겟 인코딩 칸에 늘 보이는 안내(누수 방지는 하지 않는다: 사용자 결정)
SCREEN6 = {"flag_top": FLAG_TOP, "cut_n": CUT_N, "cut_tail": CUT_TAIL, "others": OTHERS_DEFAULT, "outlier_pct": list(OUTLIER_PCT), "target_note": TARGET_LEAK_NOTE}  # 화면(SERVER.prep6) 칸의 기본값 안내


def with_known(check: "Callable[[object], bool]") -> "Callable[[object], bool]":
    """[SPECIFICATION] plans/phase-6.md#with_known
    값 검사 check에 '미리 구한 값(Known)이면 known 칸을 뺀 나머지로 검사'를 더한 검사를 돌려준다."""
    return lambda v: check(v) or (isinstance(v, Known) and "known" in v and check({k: x for k, x in v.items() if k != "known"}))


def needs_stats(op: str, value: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#needs_stats
    Phase 6 통계 단계(STAT_OPS6)의 값이 표 전체 통계를 먼저 구해야 하는지 돌려준다."""
    if op == "flags":
        return any(k in value for k in ("top", "above", "outlier"))
    if op == "power":
        return value["how"] in ("boxcox", "yeojohnson") and value.get("lambda") is None
    if op in ("gfill", "cut", "others", "freq", "target"):
        return True
    raise ValueError(f"'{op}'은 Phase 6 통계 단계가 아니에요.")


def more_stats(t: "ibis.expr.types.Table", name: str, op: str, value: object) -> dict:
    """[SPECIFICATION] plans/phase-6.md#more_stats
    Phase 6 통계 단계(flags·gfill·power·cut·others·freq·target)가 먼저 구할 값을 작은 조회로 구해 돌려준다."""
    col = t[name]
    if not needs_stats(op, value):
        raise ValueError(f"'{op}'은 미리 구할 통계가 없어요.")
    if op in ("flags", "power", "cut") and parse.kind(col.type()) not in NUMBER:
        raise ValueError(f"'{op}' 단계의 이 방식은 정수·실수 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(col.type())[0]} 컬럼이에요.")
    if op == "flags":
        return flag_stats(t, name, value)
    if op == "gfill":
        how, group = value["how"], value["group"]
        for g in group:
            parse.check_name(g, t.columns, "묶음 기준")
        if name in group:
            raise ValueError("묶음 컬럼에 채울 컬럼을 넣을 수 없어요.")
        if how in ("mean", "median") and parse.kind(col.type()) not in NUMBER:
            raise ValueError(f"'{how}'은 정수·실수 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(col.type())[0]} 컬럼이에요.")
        f = t.filter(col.notnull(), *[t[g].notnull() for g in group])
        value_expr = getattr(f[name], how)()  # how는 mean·median·mode: 고른 집계만 만든다(글자 컬럼에는 mean이 없다)
        check_new(f, ["__gfill_v"])
        agg = f.group_by(group).aggregate(__gfill_v=value_expr)
        if not source.compiles(agg) and not (how == "mode" and on_polars(t)):  # Polars 최빈값은 빈 묶음에서만 오류인데 f는 빈 값을 뺀 표라 묶음마다 값이 있다
            raise ValueError(f"이 연결은 그룹별 {'평균' if how == 'mean' else '중앙값' if how == 'median' else '최빈값'}을 구하지 못해요. 그룹 없이 빈 값 처리(fill)를 쓰거나 DuckDB 파일로 옮겨 쓰세요.")
        got = agg.limit(ENCODE_MAX + 1).to_polars()
        if got.height > ENCODE_MAX:
            raise ValueError(f"'{name}' 컬럼의 묶음이 {ENCODE_MAX:,}개를 넘어 그룹별로 채울 수 없어요. 묶음 컬럼을 줄이세요.")
        return {"groups": [list(r) for r in got.select(group).rows()], "values": got["__gfill_v"].to_list()}
    if op == "power":
        return {"lambda": boxcox_lambda(t, name, value["how"])}
    if op == "cut":
        return cut_stats(t, name, value)
    if op == "others":
        return {"keep": others_keep(t, name, value)}
    f = t.filter(col.notnull())
    if op == "freq":
        c = f.group_by(name).aggregate(__n=f.count()).limit(ENCODE_MAX + 1).to_polars()
        if c.height > ENCODE_MAX:
            raise ValueError(f"'{name}' 컬럼은 서로 다른 값이 {ENCODE_MAX:,}개를 넘어 빈도 인코딩을 할 수 없어요.")
        return {"values": c[name].to_list(), "counts": c["__n"].to_list(), "total": int(c["__n"].sum())}
    import polars as pl
    tg = value["target"]
    parse.check_name(tg, t.columns)
    if tg == name:
        raise ValueError("타겟 컬럼은 인코딩할 컬럼과 달라야 해요.")
    if not (parse.kind(t[tg].type()) in NUMBER or t[tg].type().is_boolean()):
        raise ValueError(f"타겟 '{tg}' 컬럼은 숫자(또는 참거짓) 컬럼이어야 해요.")
    v = t.filter(t[tg].notnull())
    y = v[tg].cast("float64")
    s = stats(v, n=v.count(), s=y.sum())
    if s["n"] == 0:
        raise ValueError(f"타겟 '{tg}' 컬럼에 값이 없어요.")
    gm = s["s"] / s["n"]
    g = v.group_by(name).aggregate(__n=v.count(), __s=y.sum()).limit(ENCODE_MAX + 2).to_polars()
    nulls, known = g.filter(pl.col(name).is_null()), g.filter(pl.col(name).is_not_null())
    if known.height > ENCODE_MAX:
        raise ValueError(f"'{name}' 컬럼은 서로 다른 값이 {ENCODE_MAX:,}개를 넘어 타겟 인코딩을 할 수 없어요.")
    m = value.get("smooth") or 0
    mean = (pl.col("__s") + m * gm) / (pl.col("__n") + m)
    return {"values": known[name].to_list(), "means": known.select(mean)["__s"].to_list(),
            "null": float(nulls.select(mean)["__s"][0]) if nulls.height else None, "global": float(gm)}


def flag_stats(t: "ibis.expr.types.Table", name: str, value: dict) -> dict:
    """more_stats의 flags 갈래: 상위 % 임계값·평균/중앙값·이상치 경계(값에 있는 키만)."""
    from core import settings
    from prep import prep_diag
    col, out = t[name], {}
    if "top" in value:
        ps = FLAG_TOP if value["top"] is True else value["top"]
        qs = quantiles(t, name, [1 - p / 100 for p in ps])
        if None in qs:
            raise ValueError(f"'{name}' 컬럼에 값이 없어 상위 %를 구할 수 없어요.")
        out["top"] = [float(q) for q in qs]
    if "above" in value:
        a = stats(t, a=col.mean())["a"] if value["above"] == "mean" else quantiles(t, name, [0.5])[0]
        if a is None:
            raise ValueError(f"'{name}' 컬럼에 값이 없어 평균·중앙값을 구할 수 없어요.")
        out["above"] = float(a)
    if "outlier" in value:
        by, k = value["outlier"]["by"], value["outlier"].get("k")
        if k is None:
            k = prep_diag.options(settings.read(prep_diag.OPTIONS_FILE))[FLAG_K_OPTION[by]]
        short = ValueError(f"'{name}' 컬럼에 값이 부족해 범위를 구할 수 없어요.")
        if by == "iqr":
            a, b = quantiles(t, name, IQR_QS)
            if a is None or b is None:
                raise short
            low, high = a - k * (b - a), b + k * (b - a)
        elif by == "std":
            s = stats(t, a=col.mean(), b=col.std())
            if s["a"] is None:
                raise short
            if not s["b"]:
                raise ValueError(f"'{name}' 컬럼은 표준편차가 0이거나 구할 수 없어 Z-Score 이상치를 표시할 수 없어요.")
            low, high = s["a"] - k * s["b"], s["a"] + k * s["b"]
        else:
            med = quantiles(t, name, [0.5])[0]
            if med is None:
                raise short
            check_new(t, ["__flags_dev"])
            mad = quantiles(t.mutate(__flags_dev=(col - med).abs()), "__flags_dev", [0.5])[0]
            if mad == 0:
                raise ValueError(f"'{name}' 컬럼은 MAD가 0이라 수정 Z-Score 이상치를 표시할 수 없어요.")
            gap = k * mad / MAD_C
            low, high = med - gap, med + gap
        out["outlier"] = {"low": float(low), "high": float(high)}
    return out


def cut_stats(t: "ibis.expr.types.Table", name: str, value: dict) -> dict:
    """more_stats의 cut 갈래: 방식별 경계와 최솟값·최댓값."""
    col, how = t[name], value["how"]
    s = stats(t, lo=col.min(), hi=col.max())
    if s["lo"] is None:
        raise ValueError(f"'{name}' 컬럼에 값이 없어 구간을 나눌 수 없어요.")
    lo, hi = float(s["lo"]), float(s["hi"])
    n = value.get("n") or CUT_N.get(how)
    inner = lambda a, b: [float(f"{a + (b - a) * i / n:.{CUT_DIGITS}g}") for i in range(1, n)]
    too_many = ValueError(f"'{name}' 컬럼은 구간이 {CUT_MAX_BINS:,}개를 넘어요. 폭을 크게 하세요.")
    if how == "width":
        edges = [lo, hi] if lo == hi else [lo, *inner(lo, hi), hi]
    elif how == "quantile":
        edges = quantiles(t, name, [i / n for i in range(n + 1)])
    elif how == "zero":
        qs = quantiles(t.filter(col > 0), name, [i / n for i in range(n + 1)])
        edges = [] if None in qs else qs
    elif how == "log":
        p = stats(t, a=col.min(where=col > 0))["a"]
        if p is None:
            edges = []
        else:
            a = math.floor(math.log10(p))
            b = max(math.ceil(math.log10(hi)), a + 1)
            if b - a > CUT_MAX_BINS:
                raise too_many
            edges = [10.0 ** k for k in range(a, b + 1)]
    elif how == "floor":
        w = value["width"]
        start = w * math.floor(lo / w)
        m = math.floor(hi / w) - math.floor(lo / w) + 1
        if m > CUT_MAX_BINS:
            raise too_many
        edges = [start + w * i for i in range(m + 1)]
    elif how == "tails":
        p = value.get("tail") or CUT_TAIL
        a, b = quantiles(t, name, [p / 100, 1 - p / 100])
        if a == b:
            raise ValueError(f"'{name}' 컬럼은 하위·상위 {p:g}% 값이 같아 가운데를 나눌 수 없어요.")
        edges = [a, *inner(a, b), b]
    else:
        edges = best_cut(t, name, value, n)
    edges = [float(e) for e in sorted(set(edges))]
    return {"edges": edges * 2 if len(edges) == 1 else edges, "min": lo, "max": hi}


def best_cut(t: "ibis.expr.types.Table", name: str, value: dict, n: int) -> list[float]:
    """cut best: 잘게 나눈 분위 구간별 행 수·타겟 1의 수를 집계 한 번으로 받아 best_edges로 경계를 고른다."""
    import numpy as np
    tg = value["target"]
    parse.check_name(tg, t.columns)
    if tg == name:
        raise ValueError("타겟 컬럼은 구간을 나눌 컬럼과 달라야 해요.")
    not_binary = ValueError(f"타겟 '{tg}' 컬럼은 0/1(또는 참거짓) 컬럼이어야 해요.")
    if not (parse.kind(t[tg].type()) in NUMBER or t[tg].type().is_boolean()):
        raise not_binary
    y = t[tg].cast("float64")
    v = t.filter(t[name].notnull(), y.notnull())
    yv = v[tg].cast("float64")
    if stats(v, b=((yv != 0) & (yv != 1)).sum())["b"] > 0:
        raise not_binary
    fine = sorted(set(quantiles(v, name, [i / BEST_FINE for i in range(BEST_FINE + 1)])))
    if len(fine) == 1:
        fine = fine * 2
    check_new(v, ["__cut_bin"])
    w = v.mutate(__cut_bin=labeled_bins(v[name], fine, "left", "index"))
    g = w.group_by("__cut_bin").aggregate(__n=w.count(), __p=w[tg].cast("float64").sum()).to_polars()
    m = len(fine) - 1
    pos, cnt = np.zeros(m), np.zeros(m)
    idx = g["__cut_bin"].to_numpy()
    pos[idx], cnt[idx] = g["__p"].to_numpy(), g["__n"].to_numpy()
    return best_edges(fine, pos.tolist(), (cnt - pos).tolist(), n, value.get("method") or "iv")


def others_keep(t: "ibis.expr.types.Table", name: str, value: dict) -> list:
    """more_stats의 others 갈래: 남길 값 목록(Top-K·비중 X% 이상·누적 X%까지)."""
    col, how = t[name], value["how"]
    f = t.filter(col.notnull())
    c = f.group_by(name).aggregate(__n=f.count())
    order = [ibis.desc("__n"), name]
    if how == "top":
        keep = c.order_by(order).limit(value.get("k") or OTHERS_DEFAULT["k"]).to_pyarrow()[name].to_pylist()
    else:
        total = stats(f, n=f.count())["n"]
        if how == "share":
            x = value.get("share") or OTHERS_DEFAULT["share"]
            keep = c.filter(c["__n"] >= x / 100 * total).order_by(order).limit(OTHERS_MAX + 1).to_pyarrow()[name].to_pylist()
            if len(keep) > OTHERS_MAX:
                raise ValueError(f"'{name}' 컬럼은 남길 값이 {OTHERS_MAX:,}개를 넘어요.")
        else:
            x = value.get("cum") or OTHERS_DEFAULT["cum"]
            top = c.order_by(order).limit(OTHERS_MAX + 1).to_polars()
            upto = (top["__n"].cum_sum() / total * 100 >= x).arg_true()
            if upto.is_empty() and top.height:
                raise ValueError(f"'{name}' 컬럼은 누적 {x:g}%까지 남길 값이 {OTHERS_MAX:,}개를 넘어요.")
            keep = top[name][: upto[0] + 1].to_list() if top.height else []
    if not keep:
        raise ValueError(f"'{name}' 컬럼에 값이 없어 묶을 수 없어요.")
    return keep


def boxcox_lambda(t: "ibis.expr.types.Table", name: str, how: str) -> float:
    """[SPECIFICATION] plans/phase-6.md#boxcox_lambda
    Box-Cox·Yeo-Johnson의 λ를 후보 격자의 집계 세 번으로 최대우도에 가장 가까운 값으로 고른다(scipy 없이)."""
    import numpy as np
    grid = np.round(np.linspace(*LAMBDA_GRID), 10)
    v = t.filter(t[name].notnull())
    x = v[name].cast("float64")
    s = stats(v, n=v.count(), lo=x.min(), hi=x.max(), lg=sql_funcs.safe_math("ln", x).mean(), sj=(x.sign() * (x.abs() + 1).ln()).sum())
    n = s["n"]
    if n < LAMBDA_MIN_N:
        raise ValueError(f"'{name}' 컬럼은 값이 3개보다 적어 λ를 구할 수 없어요.")
    if s["lo"] == s["hi"]:  # 분산이 0이라 λ가 정해지지 않는데, 부동소수 오차로 후보 분산이 0보다 커 보일 수 있다
        raise ValueError(f"'{name}' 컬럼은 값이 모두 같거나 변환 값이 너무 커서 λ를 구할 수 없어요.")
    if how == "boxcox":
        if s["lo"] <= 0:
            raise ValueError(f"'{name}' 컬럼에 0 이하 값이 있어 Box-Cox를 쓸 수 없어요. Yeo-Johnson을 쓰세요.")
        z = x / math.exp(s["lg"])  # 기하평균으로 나눔: λ 추정은 같고 큰 값의 거듭제곱이 넘치지 않는다

        def candidate(lam):
            return z.ln() if abs(lam) < LAMBDA_EPS else (z.pow(lam) - 1) / lam
    else:
        def candidate(lam):
            pos = (x + 1).ln() if abs(lam) < LAMBDA_EPS else ((x + 1).pow(lam) - 1) / lam
            neg = -(1 - x).ln() if abs(lam - 2) < LAMBDA_EPS else -((1 - x).pow(2 - lam) - 1) / (2 - lam)
            return ibis.ifelse(x >= 0, pos, neg)
    ys = [candidate(float(lam)) for lam in grid]
    m = stats(v, **{f"m{j}": y.mean() for j, y in enumerate(ys)})
    finite = [j for j in range(len(ys)) if m[f"m{j}"] is not None and math.isfinite(m[f"m{j}"])]
    d = stats(v, **{f"d{j}": ((ys[j] - m[f"m{j}"]) * (ys[j] - m[f"m{j}"])).mean() for j in finite}) if finite else {}
    var = np.array([np.nan if d.get(f"d{j}") is None else d[f"d{j}"] for j in range(len(ys))], dtype="float64")
    ok = np.isfinite(var) & (var > 0)
    if not ok.any():
        raise ValueError(f"'{name}' 컬럼은 값이 모두 같거나 변환 값이 너무 커서 λ를 구할 수 없어요.")
    llf = -(n / 2) * np.log(np.where(ok, var, np.nan))
    if how == "yeojohnson":
        llf = llf + (grid - 1) * s["sj"]
    return float(grid[np.nanargmax(llf)])


def best_edges(edges: list[float], pos: list[float], neg: list[float], n: int, method: str) -> list[float]:
    """[SPECIFICATION] plans/phase-6.md#best_edges
    잘게 나눈 구간별 타겟 1·0 개수로 IV 최대 나누기 또는 카이제곱 병합을 해 n개 이하 구간의 경계를 돌려준다."""
    import numpy as np
    pos, neg = np.asarray(pos, dtype="float64"), np.asarray(neg, dtype="float64")
    P, N = pos.sum(), neg.sum()
    if P == 0 or N == 0:
        raise ValueError("타겟이 한 값뿐이라 최적 구간을 찾을 수 없어요.")
    m = len(edges) - 1
    if m == 1 or n <= 1:
        return [edges[0], edges[-1]]
    cp, cn = np.concatenate([[0.0], np.cumsum(pos)]), np.concatenate([[0.0], np.cumsum(neg)])
    sums = lambda cuts: (np.diff(cp[cuts]), np.diff(cn[cuts]))  # 묶음(cuts[i] ~ cuts[i+1])별 타겟 1·0의 수
    if method == "iv":
        def iv(cuts):
            gp, gn = sums(cuts)
            b = len(cuts) - 1
            p, q = (gp + BEST_SMOOTH) / (P + BEST_SMOOTH * b), (gn + BEST_SMOOTH) / (N + BEST_SMOOTH * b)
            return float(((p - q) * np.log(p / q)).sum())
        cuts = [0, m]
        while len(cuts) - 1 < n:
            best, gain = None, iv(cuts)
            for c in range(1, m):  # 왼쪽 자리부터: 같은 IV면 왼쪽
                if c in cuts:
                    continue
                new = sorted([*cuts, c])
                a, b = new[new.index(c) - 1], new[new.index(c) + 1]
                if min(cp[c] - cp[a] + cn[c] - cn[a], cp[b] - cp[c] + cn[b] - cn[c]) / (P + N) < BEST_MIN_SHARE:
                    continue
                if (v := iv(new)) > gain:
                    best, gain = new, v
            if best is None:
                break
            cuts = best
        return [edges[c] for c in cuts]
    cuts = list(range(m + 1))
    while len(cuts) > 2:  # 행이 0인 묶음은 오른쪽(마지막이면 왼쪽) 이웃과 먼저 합친다
        gp, gn = sums(cuts)
        empty = np.flatnonzero(gp + gn == 0)
        if not len(empty):
            break
        i = int(empty[0])
        del cuts[i + 1 if i < len(cuts) - 2 else i]
    while len(cuts) - 1 > n:
        gp, gn = sums(cuts)
        ra, rb = gp[:-1] + gn[:-1], gp[1:] + gn[1:]
        tot, colp, coln = ra + rb, gp[:-1] + gp[1:], gn[:-1] + gn[1:]
        chi = np.zeros(len(ra))
        for o, e in ((gp[:-1], ra * colp / tot), (gn[:-1], ra * coln / tot), (gp[1:], rb * colp / tot), (gn[1:], rb * coln / tot)):
            chi += np.divide((o - e) ** 2, e, out=np.zeros_like(e), where=e > 0)  # 기대값 0인 칸은 0
        del cuts[int(np.argmin(chi)) + 1]  # 같으면 왼쪽 쌍
    return [edges[c] for c in cuts]


def labeled_bins(col: "ibis.expr.types.NumericValue", edges: list[float], closed: str, label: str, start: int = 0) -> "ibis.expr.types.Value":
    """[SPECIFICATION] plans/phase-6.md#labeled_bins
    경계값 구간을 포함 방향(closed)과 표시(label: 구간 글자·가운데 값·번호)대로 나타내는 식을 돌려준다."""
    text = lambda e: f"{int(e):,}" if float(e).is_integer() else f"{e:,}"
    value = {"range": lambda i, lo, hi: f"{text(lo)}{RANGE_SEP}{text(hi)}", "mid": lambda i, lo, hi: (lo + hi) / 2, "index": lambda i, lo, hi: start + i}
    dtype = {"range": "string", "mid": "float64", "index": "int64"}[label]
    last = len(edges) - 2
    if closed == "left":
        cond = lambda i, lo, hi: (col >= lo) & ((col <= hi) if i == last else (col < hi))
    else:
        cond = lambda i, lo, hi: ((col >= lo) if i == 0 else (col > lo)) & (col <= hi)
    return ibis.cases(*[(cond(i, lo, hi), ibis.literal(value[label](i, lo, hi), dtype)) for i, (lo, hi) in enumerate(zip(edges, edges[1:]))], else_=ibis.null(dtype))


def flags(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#flags
    0·임계치·상위 %·평균/중앙값 초과·범위·집합·이상치 표시를 0/1(int8) 새 컬럼들로 더한다."""
    col = t[name]
    needs_number = [key for key in ("zero", "top", "above", "outlier") if key in arg]
    if needs_number and parse.kind(col.type()) not in NUMBER:
        raise ValueError(f"'{needs_number[0]}' 표시는 정수·실수 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(col.type())[0]} 컬럼이에요.")
    s = arg["known"] if isinstance(arg, Known) else (more_stats(t, name, "flags", arg) if needs_stats("flags", arg) else {})
    value = lambda v: v if parse.kind(col.type()) in NUMBER else parse.convert(col.type(), str(v))
    new = {}
    if "zero" in arg:
        new[f"{name}_is_zero"] = col == 0
    if "cmp" in arg:
        new[f"{name}_thr"] = sql_funcs.OPS[arg["cmp"][0]](col, value(arg["cmp"][1]))
    if "top" in arg:
        for p, limit in zip(FLAG_TOP if arg["top"] is True else arg["top"], s["top"]):
            new[f"{name}_top{p:g}".replace(".", "_")] = col >= limit
    if "above" in arg:
        new[f"{name}_above_{arg['above']}"] = col > s["above"]
    if "between" in arg:
        low, high = (value(v) for v in arg["between"])
        new[f"{name}_between"] = (col >= low) & (col <= high)
    if "isin" in arg:
        new[f"{name}_isin"] = col.isin([value(v) for v in arg["isin"]])
    if "outlier" in arg:
        new[f"{name}_is_outlier"] = (col < s["outlier"]["low"]) | (col > s["outlier"]["high"])
    return add_columns(t, {key: ibis.coalesce(cond, False).cast("int8") for key, cond in new.items()})


def group_fill(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#group_fill
    빈 값을 묶음 컬럼 값이 같은 행들의 평균·중앙값·최빈값(미리 구한 묶음별 값)으로 채운다(행 차례는 그대로)."""
    col, how, group = t[name], arg["how"], arg["group"]
    if name in group:
        raise ValueError("묶음 컬럼에 채울 컬럼을 넣을 수 없어요.")
    if how in ("mean", "median") and parse.kind(col.type()) not in NUMBER and not (isinstance(arg, Known) and col.type().is_null()):  # 미리 구한 값이 있으면 전부 빈(타입 없는) 컬럼도 채운다
        raise ValueError(f"'{how}'은 정수·실수 컬럼에만 쓸 수 있어요. '{name}' 컬럼은 {parse.type_info(col.type())[0]} 컬럼이에요.")
    s = arg["known"] if isinstance(arg, Known) else more_stats(t, name, "gfill", arg)
    dtype = col.type() if how == "mode" else "float64"
    if how != "mode":
        col = col.cast("float64")
    cases = [(ibis.and_(*[t[g] == v for g, v in zip(group, key)]), ibis.literal(value, dtype)) for key, value in zip(s["groups"], s["values"])]
    fill = ibis.cases(*cases, else_=ibis.null(dtype)) if cases else ibis.null(dtype)
    return t.mutate(**{name: ibis.coalesce(col, fill)})


def power(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#power
    거듭제곱·Box-Cox·Yeo-Johnson 변환으로 컬럼 값을 바꾼다(λ가 없으면 boxcox_lambda로 구한 값)."""
    col, how, null = t[name].cast("float64"), arg["how"], ibis.null("float64")
    if how == "power":
        p = arg["p"]
        out = col.pow(p)
        if not float(p).is_integer():
            out = ibis.ifelse(col < 0, null, out)
        if p < 0:
            out = ibis.ifelse(col == 0, null, out)
        return t.mutate(**{name: out})
    lam = arg["lambda"] if arg.get("lambda") is not None else (arg["known"]["lambda"] if isinstance(arg, Known) else more_stats(t, name, "power", arg)["lambda"])
    if how == "boxcox":
        out = ibis.ifelse(col > 0, col.ln() if abs(lam) < LAMBDA_EPS else (col.pow(lam) - 1) / lam, null)
    else:
        pos = (col + 1).ln() if abs(lam) < LAMBDA_EPS else ((col + 1).pow(lam) - 1) / lam
        neg = -(1 - col).ln() if abs(lam - 2) < LAMBDA_EPS else -((1 - col).pow(2 - lam) - 1) / (2 - lam)
        out = ibis.ifelse(col >= 0, pos, neg)
    return t.mutate(**{name: out})


def cut(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#cut
    등간격·등빈도·0 따로·로그·단위 내림·양끝 묶음·타겟 최적 구간으로 컬럼 값을 구간 표시로 바꾼다."""
    s = arg["known"] if isinstance(arg, Known) else more_stats(t, name, "cut", arg)
    edges, closed, label, how, col = s["edges"], arg.get("closed", "left"), arg.get("label", "range"), arg["how"], t[name]
    dtype = {"range": "string", "mid": "float64", "index": "int64"}[label]
    lit = lambda text, mid, index: ibis.literal({"range": text, "mid": mid, "index": index}[label], dtype)
    start = 1 if how in ("zero", "tails") else 0
    middle = labeled_bins(col, edges, closed, label, start) if edges else ibis.null(dtype)
    extra = []
    if how == "zero":
        extra = [(col < 0, lit(NEG_LABEL, s["min"] / 2, SPECIAL_INDEX)), (col == 0, lit(ZERO_LABEL, 0.0, 0))]
    elif how == "tails":
        p = arg.get("tail") or CUT_TAIL
        extra = [(col < edges[0], lit(TAIL_LABELS[0].format(p=p), (s["min"] + edges[0]) / 2, 0)),
                 (col > edges[-1], lit(TAIL_LABELS[1].format(p=p), (edges[-1] + s["max"]) / 2, len(edges)))]
    elif how == "log":
        extra = [(col <= 0, lit(NONPOS_LABEL, s["min"] / 2, SPECIAL_INDEX))]
    out = ibis.cases(*extra, else_=middle) if extra else middle
    return t.mutate(**{name: out})


def cut_special(t: "ibis.expr.types.Table", name: str, arg: dict) -> int | None:
    """[SPECIFICATION] plans/phase-6.md#cut_special
    cut을 건 뒤의 표에서 음수(0 분리)·0 이하(로그) 구간에 들어간 행 수를 센다(그 밖의 방식이면 None)."""
    how, label, col = arg["how"], arg.get("label", "range"), t[name]
    if how not in ("zero", "log"):
        return None
    if label == "range":  # 표시 방식마다 컬럼 타입이 달라 고른 방식의 비교만 만든다
        special = col == (NEG_LABEL if how == "zero" else NONPOS_LABEL)
    elif label == "index":
        special = col == SPECIAL_INDEX
    else:
        special = col < 0 if how == "zero" else col <= 0
    return int(stats(t, n=special.sum())["n"] or 0)


def others(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#others
    Top-K·비중 X% 이상·누적 비중 X%까지의 값만 남기고 나머지를 묶은 값 이름으로 바꾼다."""
    keep = arg["known"]["keep"] if isinstance(arg, Known) else more_stats(t, name, "others", arg)["keep"]
    col = t[name]
    return t.mutate(**{name: (col.isnull() | col.isin(keep)).ifelse(col, arg.get("label", "기타"))})


def freq(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#freq
    값마다 등장 횟수 또는 비율(%)을 {컬럼}_freq 새 컬럼으로 더한다."""
    s = arg["known"] if isinstance(arg, Known) else more_stats(t, name, "freq", arg)
    col, pct = t[name], arg.get("how", "count") == "pct"
    dtype = "float64" if pct else "int64"
    found = ibis.cases(*[(col == v, n / s["total"] * 100 if pct else n) for v, n in zip(s["values"], s["counts"])], else_=0.0 if pct else 0).cast(dtype)
    return add_columns(t, {f"{name}_freq": col.isnull().ifelse(ibis.null(dtype), found)})


def target_encode(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#target_encode
    값마다 타겟 컬럼의 (평활) 평균을 {컬럼}_te 새 컬럼으로 더한다."""
    s = arg["known"] if isinstance(arg, Known) else more_stats(t, name, "target", arg)
    col = t[name]
    out = ibis.cases(*[(col == v, m) for v, m in zip(s["values"], s["means"])], *([(col.isnull(), s["null"])] if s["null"] is not None else []), else_=s["global"]).cast("float64")
    return add_columns(t, {f"{name}_te": out})


def ordinal(t: "ibis.expr.types.Table", name: str, arg: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-6.md#ordinal
    사용자가 정한 값 → 번호 사전으로 {컬럼}_ord 새 컬럼을 더한다."""
    pairs, other, col = arg["map"], arg.get("else"), t[name]
    dtype = "int64" if all(isinstance(v, int) for v in [*pairs.values(), *([] if other is None else [other])]) else "float64"
    null = ibis.null(dtype)
    out = ibis.cases(*[(col == k, v) for k, v in pairs.items()], else_=null if other is None else other).cast(dtype)
    return add_columns(t, {f"{name}_ord": col.isnull().ifelse(null, out)})


def is_edges(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_edges
    오름차순 경계값 2개 이상(bins 단계의 옛 꼴)인지 돌려준다."""
    return isinstance(v, list) and len(v) >= 2 and all(is_num(e) for e in v) and all(a < b for a, b in zip(v, v[1:]))


def is_flags(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_flags
    flags 단계 값의 키·종류·범위가 맞는지 돌려준다."""
    if not (isinstance(v, dict) and v and set(v) <= {"zero", "cmp", "top", "above", "between", "isin", "outlier"}):
        return False
    num_or_text = lambda x: is_num(x) or isinstance(x, str)
    pair = lambda x: isinstance(x, list) and len(x) == 2
    checks = {
        "zero": lambda x: x is True,
        "cmp": lambda x: pair(x) and x[0] in FLAG_CMPS and (is_num(x[1]) or (isinstance(x[1], str) and bool(x[1]))),
        "top": lambda x: x is True or (isinstance(x, list) and 1 <= len(x) <= 10 and len(set(x)) == len(x) and all(is_num(p) and 0 < p < 100 for p in x)),
        "above": lambda x: x in ("mean", "median"),
        "between": lambda x: pair(x) and all(num_or_text(e) for e in x) and not (all(is_num(e) for e in x) and x[0] > x[1]),
        "isin": lambda x: isinstance(x, list) and 1 <= len(x) <= ISIN_MAX and all(num_or_text(e) for e in x),
        "outlier": lambda x: isinstance(x, dict) and set(x) <= {"by", "k"} and x.get("by") in FLAG_K_OPTION and (x.get("k") is None or (is_num(x["k"]) and x["k"] > 0)),
    }
    return all(checks[key](v[key]) for key in v)


def is_gfill(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_gfill
    gfill 단계 값(how·group)이 맞는지 돌려준다."""
    return (isinstance(v, dict) and set(v) == {"how", "group"} and v["how"] in GFILL_HOWS and isinstance(v["group"], list)
            and 1 <= len(v["group"]) <= GROUP_MAX and len(set(v["group"])) == len(v["group"]) and all(isinstance(g, str) and bool(g) for g in v["group"]))


def is_power(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_power
    power 단계 값(how·p·lambda)이 맞는지 돌려준다."""
    if not (isinstance(v, dict) and v.get("how") in POWER_HOWS):
        return False
    if v["how"] == "power":
        return set(v) == {"how", "p"} and is_num(v["p"]) and v["p"] != 0
    return set(v) <= {"how", "lambda"} and (v.get("lambda") is None or is_num(v["lambda"]))


def is_cut(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_cut
    cut 단계 값(방식별로 쓸 수 있는 키와 범위)이 맞는지 돌려준다."""
    if not (isinstance(v, dict) and v.get("how") in CUT_HOWS and set(v) <= {"how", "n", "width", "tail", "target", "method", "closed", "label"}):
        return False
    how = v["how"]
    only = {"n": how not in ("floor", "log"), "width": how == "floor", "tail": how == "tails", "target": how == "best", "method": how == "best"}
    if any(key in v and not allowed for key, allowed in only.items()) or (how == "floor" and "width" not in v) or (how == "best" and "target" not in v):
        return False
    checks = {
        "n": lambda x: type(x) is int and 2 <= x <= CUT_N_MAX,
        "width": lambda x: is_num(x) and x > 0,
        "tail": lambda x: is_num(x) and 0 < x < CUT_TAIL_MAX,
        "target": lambda x: isinstance(x, str) and bool(x),
        "method": lambda x: x in BEST_METHODS,
        "closed": lambda x: x in CUT_CLOSED,
        "label": lambda x: x in CUT_LABELS,
        "how": lambda x: True,
    }
    return all(checks[key](v[key]) for key in v)


def is_others(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_others
    others 단계 값(방식별 k·share·cum과 label)이 맞는지 돌려준다."""
    if not (isinstance(v, dict) and v.get("how") in OTHERS_HOWS and set(v) <= {"how", "k", "share", "cum", "label"}):
        return False
    own = {"k": "top", "share": "share", "cum": "pareto"}
    if any(key in v and v["how"] != how for key, how in own.items()):
        return False
    pct = lambda x: is_num(x) and 0 < x < 100
    checks = {"how": lambda x: True, "k": lambda x: type(x) is int and 1 <= x <= OTHERS_MAX, "share": pct, "cum": pct, "label": lambda x: isinstance(x, str)}
    return all(checks[key](v[key]) for key in v)


def is_target(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_target
    target 단계 값(target·smooth)이 맞는지 돌려준다."""
    return (isinstance(v, dict) and set(v) <= {"target", "smooth"} and isinstance(v.get("target"), str) and bool(v["target"])
            and (v.get("smooth") is None or (is_num(v["smooth"]) and v["smooth"] >= 0)))


def is_ordinal(v: object) -> bool:
    """[SPECIFICATION] plans/phase-6.md#is_ordinal
    ordinal 단계 값(map·else)이 맞는지 돌려준다."""
    mapping = v.get("map") if isinstance(v, dict) else None
    return (isinstance(v, dict) and set(v) <= {"map", "else"} and isinstance(mapping, dict) and 1 <= len(mapping) <= ORDINAL_MAX
            and all(isinstance(k, str) and bool(k) and is_num(x) for k, x in mapping.items()) and (v.get("else") is None or is_num(v["else"])))


# (이름, 쓸 수 있는 컬럼 종류(None이면 모두), 값이 맞는지, 값의 형식, 적용 함수). 적용 순서는 이 표의 순서다.
# 전처리를 더할 때는 함수를 만들고 여기에 한 줄을 넣는다. 파생 컬럼을 만드는 것(flag, parts, label, onehot, flags, freq, target, ordinal)은 add_columns로 컬럼을 더한다.
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
    ("flags", None, with_known(is_flags), '{"zero": true, "cmp": [">=", 60], "top": [1, 5, 10] 또는 true, "above": "mean" | "median", "between": [최소, 최대], "isin": [값, ...], "outlier": {"by": "iqr" | "std" | "mad", "k": 수}} 중 하나 이상', flags),
    ("gfill", None, with_known(is_gfill), '{"how": "mean" | "median" | "mode", "group": ["묶을 컬럼", ...](1~3개)}', group_fill),
    ("fill", None, lambda v: isinstance(v, dict) and v.get("how") in FILLS
     and (set(v) <= {"how", "value", "by"} or (isinstance(v, Known) and set(v) == {"how", "known"} and v["how"] in ("mean", "median", "mode")))
     and (v["how"] != "value" or v.get("value") is not None) and (v["how"] not in ("prev", "next") or isinstance(v.get("by"), str)),
     '{"how": "drop" | "value" | "mean" | "median" | "mode" | "prev" | "next", "value": 채울 값, "by": 순서를 정할 컬럼(prev·next)}', fill),
    ("outlier", NUMBER, is_outlier, '{"how": "drop" | "clip", "by": "range" | "iqr" | "std", "low": 수, "high": 수, "k": 수}', outlier),
    ("power", NUMBER, with_known(is_power), '{"how": "power", "p": 0이 아닌 수} 또는 {"how": "boxcox" | "yeojohnson", "lambda": 수(비우면 자동)}', power),
    ("scale", NUMBER, lambda v: v in SCALES or (isinstance(v, Known) and set(v) == {"how", "known"} and v["how"] in SCALES and isinstance(v["known"], dict)), '"standard", "minmax", "minmax100", "robust", "log" 또는 "log10"', scale),
    ("round", NUMBER, lambda v: type(v) is int and 0 <= v <= 15, "0 이상 15 이하의 정수", column(sql_funcs.half_up)),
    ("bins", NUMBER, lambda v: is_edges(v) or (isinstance(v, dict) and set(v) <= {"edges", "closed", "label"} and is_edges(v.get("edges"))
                                               and v.get("closed", "left") in CUT_CLOSED and v.get("label", "range") in CUT_LABELS),
     "오름차순 경계값 2개 이상. 예: [0, 10, 100] 또는 {\"edges\": [0, 10, 100], \"closed\": \"left\" | \"right\", \"label\": \"range\" | \"mid\" | \"index\"}", column(bins)),
    ("cut", NUMBER, with_known(is_cut), '{"how": "width" | "quantile" | "zero" | "log" | "floor" | "tails" | "best", "n": 구간 수, "width": 폭(floor), "tail": %(tails), "target": "0/1 컬럼"(best), "method": "iv" | "chi", "closed": "left" | "right", "label": "range" | "mid" | "index"}', cut),
    ("rare", STRING, lambda v: isinstance(v, dict) and (set(v) <= {"min", "label"} or (isinstance(v, Known) and set(v) <= {"min", "label", "known"} and isinstance(v["known"], list))) and type(v.get("min")) is int and v["min"] >= 2
     and isinstance(v.get("label", ""), str), '{"min": 2 이상의 정수, "label": "기타"}', rare),
    ("others", STRING, with_known(is_others), '{"how": "top" | "share" | "pareto", "k": 수(top), "share": %(share), "cum": %(pareto), "label": "기타"}', others),
    ("parts", DATE, lambda v: is_strs(v) and all(p in PARTS for p in v), f"{list(PARTS)} 중 하나 이상", date_parts),
    ("label", None, lambda v: v is True or (isinstance(v, Known) and set(v) == {"known"} and isinstance(v["known"], list)), "true", label),
    ("onehot", None, lambda v: v is True or (isinstance(v, Known) and set(v) == {"known"} and isinstance(v["known"], list))
     or with_known(lambda x: isinstance(x, dict) and "top" in x and set(x) <= {"top", "others"} and type(x["top"]) is int and 1 <= x["top"] <= ONEHOT_MAX and isinstance(x.get("others", False), bool))(v),
     'true 또는 {"top": 많이 나온 값 수, "others": true(나머지 컬럼, 기본 false)}', onehot),
    ("freq", None, with_known(lambda v: isinstance(v, dict) and set(v) <= {"how"} and v.get("how", "count") in FREQ_HOWS), '{"how": "count" | "pct"}', freq),
    ("target", None, with_known(is_target), '{"target": "타겟 컬럼", "smooth": 0 이상의 수}', target_encode),
    ("ordinal", STRING, is_ordinal, '{"map": {"하": 1, "중": 2, "상": 3}, "else": 수(없으면 빈 값)}', ordinal),
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
    {"id": "flags", "label": "0·임계치·상위 % 같은 표시 컬럼 만들기", "group": "새 컬럼", "need": "any",
     "fields": [_C("zero", "0이면 1"), _O("cmp_op", ("", "임계치 비교 안 함"), *[(o, o) for o in FLAG_CMPS]), _F("cmp", "임계치 X"), _C("top", "상위 % 표시"),
                _F("top_pct", f"상위 %들(비우면 {', '.join(map(str, FLAG_TOP))})"), _O("above", ("", "평균·중앙값 비교 안 함"), ("mean", "평균보다 크면"), ("median", "중앙값보다 크면")),
                _F("low", "범위 최소(A ≤ X)"), _F("high", "범위 최대(X ≤ B)"), _F("isin", "값 목록(쉼표로), 예: KR, US, JP"),
                _O("out_by", ("", "이상치 표시 안 함"), ("iqr", "사분위수(IQR)"), ("std", "Z-Score(σ)"), ("mad", "수정 Z-Score(MAD)")),
                _F("out_k", "k (비우면 저장한 진단 기준의 배수)")]},
    {"id": "gfill", "label": "그룹별 값으로 빈 값 채우기", "group": "빈 값", "need": "any",
     "fields": [_O("how", ("mean", "그룹 평균으로"), ("median", "그룹 중앙값으로"), ("mode", "그룹 최빈값으로")), _F("g1", "묶을 컬럼 1"), _F("g2", "묶을 컬럼 2(선택)"), _F("g3", "묶을 컬럼 3(선택)")]},
    {"id": "fill", "label": "빈 값 처리", "group": "빈 값", "need": "any",
     "fields": [_O("how", ("drop", "그 행 빼기"), ("value", "정한 값으로"), ("mean", "평균으로"), ("median", "중앙값으로"), ("mode", "최빈값으로"), ("prev", "앞의 값으로"), ("next", "뒤의 값으로")),
                _F("value", "채울 값"), _F("by", "순서를 정할 컬럼")]},
    {"id": "outlier", "label": "범위 밖 값 처리", "group": "숫자", "need": "num",
     "fields": [_O("how", ("drop", "그 행 빼기"), ("clip", "범위로 잘라 내기")), _O("by", ("range", "직접 범위"), ("iqr", "사분위수(IQR)"), ("std", "표준편차"), ("pct", "백분위로(윈저라이징)")),
                _F("low", "최소"), _F("high", "최대"), _F("k", "k (비우면 IQR 1.5, 표준편차 3)"),
                _F("p_low", f"하위 %(비우면 {OUTLIER_PCT[0]})"), _F("p_high", f"상위 %(비우면 {OUTLIER_PCT[1]})")]},
    {"id": "power", "label": "거듭제곱·Box-Cox·Yeo-Johnson", "group": "숫자", "need": "num",
     "fields": [_O("how", ("boxcox", "Box-Cox(양수만)"), ("yeojohnson", "Yeo-Johnson"), ("power", "거듭제곱 x^p")), _F("p", "p (거듭제곱)"), _F("lambda", "λ (비우면 자동으로 찾기)")]},
    {"id": "scale", "label": "크기 맞추기(스케일링)", "group": "숫자", "need": "num",
     "fields": [_O("how", ("standard", "표준화"), ("minmax", "0~1 사이로"), ("minmax100", "0~100 사이로"), ("robust", "로버스트 (x−중앙값)/IQR"), ("log", "로그 ln(1+x)"), ("log10", "로그 log10(1+x)"))]},
    {"id": "round", "label": "반올림", "group": "숫자", "need": "num", "fields": [_F("digits", "소수점 아래 자릿수(0 이상)")]},
    {"id": "bins", "label": "구간 나누기", "group": "숫자", "need": "num",
     "fields": [_F("text", "경계값을 작은 것부터 쉼표로"), _O("closed", ("left", "[a, b) 왼쪽 포함"), ("right", "(a, b] 오른쪽 포함")), _O("label", ("range", "구간 글자"), ("mid", "구간 가운데 값"), ("index", "구간 번호"))]},
    {"id": "cut", "label": "자동으로 구간 나누기", "group": "숫자", "need": "num",
     "fields": [_O("how", ("width", "등간격"), ("quantile", "등빈도(분위)"), ("zero", "0 따로 + 양수 등빈도"), ("log", "로그 스케일(10배씩)"), ("floor", "단위로 내림(10대·20대)"),
                   ("tails", "양끝 묶고 가운데 등간격"), ("best", "타겟으로 최적 구간")),
                _F("n", "구간 수(비우면 " + ", ".join(f"{k} {v}" for k, v in CUT_N.items()) + ")"), _F("width", "단위 폭, 예: 10"), _F("tail", f"양끝 %(비우면 {CUT_TAIL})"),
                _F("target", "타겟 컬럼(0/1)"), _O("method", ("iv", "IV 최대"), ("chi", "카이제곱 병합")),
                _O("closed", ("left", "[a, b) 왼쪽 포함"), ("right", "(a, b] 오른쪽 포함")), _O("label", ("range", "구간 글자"), ("mid", "구간 가운데 값"), ("index", "구간 번호"))]},
    {"id": "rare", "label": "드문 값 묶기", "group": "범주", "need": "str", "fields": [_F("min", "이보다 적게 나오면 묶기(2 이상)"), _F("label", "묶은 값의 이름(기본: 기타)")]},
    {"id": "others", "label": "많은 값만 남기고 묶기", "group": "범주", "need": "str",
     "fields": [_O("how", ("top", "많이 나온 K개만"), ("share", "비중 X% 미만 묶기"), ("pareto", "누적 비중 X%까지만")), _F("k", f"K(비우면 {OTHERS_DEFAULT['k']})"),
                _F("share", f"X %(비우면 {OTHERS_DEFAULT['share']:g})"), _F("cum", f"누적 %(비우면 {OTHERS_DEFAULT['cum']:g})"), _F("label", "묶은 값의 이름(기본: 기타)")]},
    {"id": "parts", "label": "날짜에서 뽑기", "group": "새 컬럼", "need": "date",
     "fields": [_C("year", "연"), _C("month", "월"), _C("day", "일"), _C("weekday", "요일"), _C("ym", "월 단위"), _C("hour", "시"),
                _C("weekday_num", "요일 번호"), _C("weekday_name", "요일 이름"), _C("is_weekend", "주말"), _C("time_of_day", "시간대"), _C("work_hours", "업무시간"),
                _C("month_phase", "월초·중·말"), _C("is_month_end", "월말 날")]},
    {"id": "label", "label": "값을 번호로(라벨 인코딩)", "group": "새 컬럼", "need": "any", "fields": []},
    {"id": "onehot", "label": "값마다 컬럼으로(원-핫 인코딩)", "group": "새 컬럼", "need": "any", "fields": [_F("top", "많이 나온 K개만(비우면 모두)"), _C("others", "나머지 값은 기타 컬럼으로")]},
    {"id": "freq", "label": "값을 등장 횟수로(빈도 인코딩)", "group": "새 컬럼", "need": "any", "fields": [_O("how", ("count", "등장 횟수"), ("pct", "비율(%)"))]},
    {"id": "target", "label": "값을 타겟 평균으로(타겟 인코딩)", "group": "새 컬럼", "need": "any", "fields": [_F("target", "타겟 컬럼(숫자·0/1)"), _F("smooth", "평활 m(비우면 0)")], "note": TARGET_LEAK_NOTE},
    {"id": "ordinal", "label": "정한 순서의 번호로(순서형)", "group": "새 컬럼", "need": "str", "fields": [_F("map", "값=번호, 예: 하=1, 중=2, 상=3"), _F("else", "목록에 없는 값의 번호(비우면 빈 값)")]},
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


NAN_OPS = ("flag", "flags", "gfill", "fill", "outlier", "power", "scale", "cut", "label", "onehot")  # 빈 값을 따지거나 통계를 내는 일: 먼저 NaN을 빈 값으로


def drops_rows(key, value):
    return key in ("fill", "outlier") and isinstance(value, dict) and value.get("how") == "drop"


OUTLIER_AT = OPS_NAMES.index("outlier")  # 행을 뺄지 정하는 통계(outlier)는 단계 시작 표로 구하니, 그 앞의 일만 시작 표(base)에도 한다


def fills_stat(key, value):
    """통계로 채우는 fill·gfill과 통계를 쓰는 flags: 행을 다 뺀 뒤의 표로 구하려고 rest로 미룬다(outlier 경계의 base에도 하지 않는다)."""
    return ((key in ("fill", "gfill") and isinstance(value, dict) and value.get("how") in ("mean", "median", "mode"))
            or (key == "flags" and isinstance(value, dict) and needs_stats("flags", value)))


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
        if isinstance(ops[key], dict) and "known" in ops[key] and not isinstance(ops[key], Known):
            raise ValueError(KNOWN_REFUSED)
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
