"""테이블의 모양(shape), 컬럼별 통계(profile)와 모양 검사가 필요 없는 추천, 글자 컬럼의 타입 추천(patterns).
큰 테이블에서 느린 것(글자 수 세기, 모양 검사)은 앞 sample행으로 먼저 걸러 낸다."""
from lazy import ibis

from core import parse
from core import result
from prep.prep_expr import type_text
from core.prep_ops import date_pattern, is_number, nan_to_null, stats

# 빈 값을 뜻하는 글자로 흔한 것. ""는 blank로 센다.
MARKERS = ["", "-", "N/A", "n/a", "NA", "null", "NULL", "None", "없음", "?"]
# 기호·숫자·단위 순서의 글자(₩1,200, 1,200원, 45%, 3 kg). 이런 모양일 때만 글자를 지우고 숫자로 바꾸라고 추천한다(a1b2 같은 코드는 아니다).
UNIT_SHAPE = r"^[^0-9A-Za-z가-힣]{0,3}[0-9][0-9,]*(\.[0-9]+)?\s*[^0-9]{0,5}$"
DATE_FORMATS = [("%Y%m%d", "YYYYMMDD"), ("%Y-%m-%d %H:%M:%S", "YYYY-MM-DD HH:MM:SS"), ("%Y-%m-%d", "YYYY-MM-DD")]  # 앞의 것이 우선
SHAPE_KEYS = [f"date{j}" for j in range(len(DATE_FORMATS))] + ["num", "int", "clean_num", "clean_int"]
# 우선순위 순서의 묶음. 묶음의 첫 모양이 맞아야 그 추천이 나온다(뒤의 것은 정수인지를 가른다).
SHAPE_GROUPS = [*([f"date{j}"] for j in range(len(DATE_FORMATS))), ["num", "int"], ["clean_num", "clean_int"]]
FEW_NULLS = 0.05  # 빈 값이 이 비율보다 적으면 그 행을 빼라고 추천한다.


def count(cond):
    """참인 행의 수를 세는 집계 식."""
    return cond.sum()


def shape(t, rows):
    """행 수와 컬럼의 이름·타입·종류."""
    return {"rows": rows, "columns": [{"name": name, "type": type_text(dtype), "kind": parse.kind(dtype)} for name, dtype in t.schema().items()]}


def text_metrics(col, i):
    """문자 컬럼의 통계 식(글자 수는 따로)."""
    stripped = col.strip()
    metrics = {f"blank{i}": count(col == ""), f"spaces{i}": count(col != stripped), f"numeric{i}": count(is_number(stripped, "float")),
               f"lower{i}": col.lower().nunique(where=col.notnull())}
    metrics.update({f"mark{j}_{i}": count(col == m) for j, m in enumerate(MARKERS) if m})
    return metrics


def length_metrics(table, names):
    """names({컬럼 번호: 컬럼 이름})의 글자 수 최소·최대 식."""
    return {f"{key}_len{i}": getattr(table[name].length(), key)() for i, name in names.items() for key in ("min", "max")}


def suggestions(info, found, i, rows):
    """컬럼 하나의 추천 [{"text", "why", "ops" 또는 "keep": False}] 중 모양 검사가 필요 없는 것. found는 집계 결과, i는 컬럼 번호."""
    out = []
    add = lambda text, why, **what: out.append({"text": text, "why": why, **what})
    if info["kind"] == "string":
        if info["spaces"]:
            add("앞뒤 공백 지우기", f"앞뒤에 공백이 있는 값이 {info['spaces']:,}개 있습니다.", ops={"trim": True})
        marks = {m: (info["blank"] if m == "" else found[f"mark{j}_{i}"]) for j, m in enumerate(MARKERS)}
        marks = {m: n for m, n in marks.items() if n}
        if marks:
            add("빈 값 표시를 빈 값으로", "빈 값을 뜻하는 글자가 있습니다: " + ", ".join(f'"{m}" {n:,}개' for m, n in marks.items()), ops={"nulls": list(marks)})
        if found[f"lower{i}"] < info["unique"]:
            add("소문자로 통일", f"대소문자만 다른 값이 있어 서로 다른 값이 {info['unique']:,}개에서 {found[f'lower{i}']:,}개로 줄어듭니다.", ops={"case": "lower"})
    if info["nulls"] and info["unique"]:  # 채울 값이 하나도 없는 컬럼은 채우기를 추천하지 않는다.
        share = info["nulls"] / rows
        why = f"빈 값이 {info['nulls']:,}개({share:.1%})입니다."
        if share < FEW_NULLS:
            add("빈 값인 행 빼기", why + " 5% 미만이라 그 행을 빼도 됩니다.", ops={"fill": {"how": "drop"}})
        elif info["kind"] in ("int", "float"):
            add("빈 값을 중앙값으로 채우기", why, ops={"fill": {"how": "median"}})
        else:
            add("빈 값을 최빈값으로 채우기", why, ops={"fill": {"how": "mode"}})
    if info["unique"] <= 1:
        add("컬럼 빼기", f"서로 다른 값이 {info['unique']}개뿐입니다.", keep=False)
    return out


def profile(t):
    """행 수와 컬럼별 통계, 모양 검사가 필요 없는 추천을 집계 조회 한 번으로 구한다. unique는 빈 값을 뺀 서로 다른 값 수다.
    min·max는 숫자·날짜·시간 컬럼만(나머지는 null), 글자 수·blank·spaces·numeric은 문자 컬럼에만 있다.
    t는 표본을 이 PC에서 계산하는 표(DuckDB)라 length()가 글자 수를 센다."""
    kinds = {name: parse.kind(dtype) for name, dtype in t.schema().items()}
    texts = {i: name for i, (name, kind) in enumerate(kinds.items()) if kind == "string"}
    metrics = {"rows": t.count()}
    for i, (name, kind) in enumerate(kinds.items()):
        col = nan_to_null(t[name])  # 실수의 NaN은 빈 값으로(빈 값 수에 들고 min·max가 NaN이 되지 않게)
        metrics.update({f"nulls{i}": count(col.isnull()), f"unique{i}": col.nunique(where=col.notnull())})
        if kind in ("int", "float", "date", "time"):
            metrics.update({f"min{i}": col.min(), f"max{i}": col.max()})
        if kind == "string":
            metrics.update(text_metrics(col, i))
    metrics.update(length_metrics(t, texts))
    found = stats(t, **metrics)
    columns = []
    for i, (name, kind) in enumerate(kinds.items()):
        info = {"name": name, "type": type_text(t[name].type()), "kind": kind, "nulls": found[f"nulls{i}"] or 0, "unique": found[f"unique{i}"],
                "min": result.plain(found.get(f"min{i}")), "max": result.plain(found.get(f"max{i}"))}
        if kind == "string":
            info.update({key: found[f"{key}{i}"] or 0 for key in ("blank", "spaces", "numeric")})
            info.update({key: found[f"{key}{i}"] for key in ("min_len", "max_len")})
        columns.append({**info, "suggest": suggestions(info, found, i, found["rows"])})
    return {"rows": found["rows"], "columns": columns}


def shapes(col):
    """문자 컬럼의 모양 검사. (빈 값도 빈 값 표시도 아닌 값인 조건, {모양 이름: 그 모양인 조건})."""
    stripped = col.strip()
    cleaned = stripped.re_replace(r"[^0-9.+\-]", "")
    unit = ibis.coalesce(stripped.re_search(UNIT_SHAPE), False)
    conds = {f"date{j}": ibis.coalesce(stripped.re_search(date_pattern(fmt)), False) for j, (fmt, _) in enumerate(DATE_FORMATS)}
    conds.update(num=is_number(stripped, "float"), int=is_number(stripped, "int"), clean_num=unit & is_number(cleaned, "float"), clean_int=unit & is_number(cleaned, "int"))
    return col.notnull() & ~col.isin(MARKERS), conds


def measure(t, wanted):
    """wanted({컬럼: [모양 이름]})의 컬럼마다 빈 값·빈 값 표시가 아닌 값 수(rest)와 모양별로 맞는 값 수를 집계 한 번으로 센다."""
    metrics = {}
    for i, (name, keys) in enumerate(wanted.items()):
        rest, conds = shapes(t[name])
        metrics[f"{i}_rest"] = count(rest)
        metrics.update({f"{i}_{key}": count(rest & conds[key]) for key in keys})
    found = stats(t, **metrics)
    return {name: {key: found[f"{i}_{key}"] or 0 for key in ["rest", *keys]} for i, (name, keys) in enumerate(wanted.items())}


def type_suggestions(counts):
    """모양별로 맞는 값 수(rest 포함, 전체 기준)에서 타입 추천. 날짜 → 숫자 → 글자를 지우고 숫자 순으로 하나만."""
    rest, out = counts["rest"], []
    full = lambda key: rest and counts.get(key) == rest
    add = lambda text, why, to, **more: out.append({"text": text, "why": why, "ops": {"type": {"to": to, **more}}})
    date = next(((fmt, name) for j, (fmt, name) in enumerate(DATE_FORMATS) if full(f"date{j}")), None)
    if date:
        add("날짜로 바꾸기", f"빈 값과 빈 값 표시를 뺀 {rest:,}개가 모두 {date[1]} 모양입니다.", "date", **({"format": date[0]} if date[0] != "%Y-%m-%d" else {}))
    elif full("num"):
        add("숫자로 바꾸기", f"빈 값과 빈 값 표시를 뺀 {rest:,}개가 모두 숫자로 바뀝니다.", "int" if full("int") else "float")
    elif full("clean_num"):
        add("글자를 지우고 숫자로 바꾸기", f"숫자·부호·소수점이 아닌 글자를 지우면 {rest:,}개가 모두 숫자가 됩니다.", "int" if full("clean_int") else "float", clean=True)
    return out


def patterns(t, sample):
    """문자 컬럼의 타입 추천 {컬럼: [추천]}(추천이 없는 컬럼은 없다). 앞 sample행에서 모두 맞는 모양만 전체에서 다시 확인한다.
    한 컬럼에 맞는 모양이 여럿이면 우선순위가 높은 것부터 확인하고, 전체에서 맞지 않을 때만 다음 것을 확인한다.
    앞 sample행에 확인할 값이 없으면(모두 빈 값) 모든 모양을 전체에서 한꺼번에 확인한다. 확인할 것이 없으면 전체 조회를 하지 않는다."""
    texts = [name for name, dtype in t.schema().items() if parse.kind(dtype) == "string"]
    if not texts:
        return {}
    head = measure(t.limit(sample), {name: SHAPE_KEYS for name in texts})
    passed = lambda counts, keys: [key for key in keys if counts[key] == counts["rest"]]
    queue = {name: [SHAPE_KEYS] if counts["rest"] == 0 else [keys for group in SHAPE_GROUPS if (keys := passed(counts, group)) and keys[0] == group[0]]
             for name, counts in head.items()}
    out = {}
    while queue := {name: groups for name, groups in queue.items() if groups}:
        whole = measure(t, {name: groups[0] for name, groups in queue.items()})
        for name, counts in whole.items():
            if found := type_suggestions(counts):
                out[name] = found
        queue = {name: groups[1:] for name, groups in queue.items() if name not in out}
    return {name: out[name] for name in texts if name in out}


PRUNE_TOP = 0.99  # 한 값이 이 비율 이상이면 빼라고 제안한다(빈 값 포함 전체 행 기준)
PRUNE_CORR = 0.95  # 두 숫자 컬럼의 상관(절댓값)이 이 이상이면 하나를 빼라고 제안한다
PRUNE_MIN_PAIRS = 30  # 상관을 구할 때 두 컬럼이 모두 있는 행이 이보다 적으면 보지 않는다
PRUNE_MAX_NUMERIC = 200  # 상관을 볼 숫자 컬럼 수(앞에서부터)


def prune(arrow):
    """표본(pyarrow 표)으로 뺄 만한 컬럼을 고른다: 값이 모두 비었거나 하나뿐이거나 한 값이 PRUNE_TOP 이상인 컬럼,
    상관이 PRUNE_CORR 이상인 숫자 컬럼 쌍에서는 빈 값이 더 많은 쪽(같으면 뒤쪽). 이 PC의 표본만 쓰고 DB에 묻지 않는다.
    [{column, reason, with(상관이면 남길 컬럼), r}]"""
    import numpy as np
    import polars as pl
    df = pl.from_arrow(arrow)
    df = df.with_columns(pl.col(n).fill_nan(None) for n, d in df.schema.items() if d.is_float())  # NaN은 빈 값으로(빈 값 수·상관에 같이 쓴다)
    rows, found, picked = df.height, [], set()
    if not rows:
        return found
    for name, dtype in df.schema.items():
        if dtype.is_nested():
            continue
        col = df[name]
        filled = rows - col.null_count()
        if not filled:
            found.append({"column": name, "reason": "값이 모두 비어 있습니다"})
        elif col.n_unique() - (1 if col.null_count() else 0) == 1:
            found.append({"column": name, "reason": f"값이 하나뿐입니다({col.drop_nulls()[0]})" + (" (나머지는 빈 값)" if col.null_count() else "")})
        else:
            counts = col.value_counts(sort=True, name="__n")  # 컬럼 이름이 count여도 겹치지 않게
            top = counts["__n"][0] / rows
            if top < PRUNE_TOP:
                continue
            found.append({"column": name, "reason": f"빈 값이 {top:.1%}입니다" if counts[name][0] is None else f"한 값이 {top:.1%}입니다({counts[name][0]})"})
        picked.add(name)
    numeric = [n for n, d in df.schema.items() if d.is_numeric() and n not in picked][:PRUNE_MAX_NUMERIC]
    if len(numeric) < 2:
        return found
    values = df.select(pl.col(n).cast(pl.Float64) for n in numeric).to_numpy()
    nulls = {n: df[n].null_count() for n in numeric}
    full = [k for k, n in enumerate(numeric) if not nulls[n] and rows >= PRUNE_MIN_PAIRS and np.isfinite(values[:, k]).all()]  # 빈 값 없는 컬럼끼리는 상관 행렬을 한 번에 구한다
    with np.errstate(all="ignore"):
        matrix = np.corrcoef(values[:, full].T) if len(full) > 1 else None
    at = {k: m for m, k in enumerate(full)}
    for i in range(len(numeric)):
        for j in range(i + 1, len(numeric)):
            a, b = numeric[i], numeric[j]
            if a in picked or b in picked:
                continue
            if matrix is not None and i in at and j in at:
                r = float(matrix[at[i], at[j]])
            else:
                both = np.isfinite(values[:, i]) & np.isfinite(values[:, j])
                if both.sum() < PRUNE_MIN_PAIRS:
                    continue
                x, y = values[both, i], values[both, j]
                if x.std() == 0 or y.std() == 0:
                    continue
                r = float(np.corrcoef(x, y)[0, 1])
            if np.isnan(r):
                continue
            if abs(r) >= PRUNE_CORR:
                drop, keep = (a, b) if nulls[a] > nulls[b] else (b, a)
                found.append({"column": drop, "reason": f"'{keep}'와 상관이 {r:.3f}로 거의 같은 정보입니다", "with": keep, "r": round(r, 4)})
                picked.add(drop)
    return found
