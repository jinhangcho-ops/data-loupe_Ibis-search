"""전처리 통계의 표본 방식(Phase 2): ③ 층화 + Neyman 최적 할당(기본), ① 블록 → 행 단위(베르누이), ② 키 단위 해시 표본.
파일럿을 따로 뽑아 필요한 표본 수를 공식(Cochran·변동계수·Neyman n0·유한모집단 보정)으로 정하고, 화면의 표본 행 수는 상한으로만 쓴다.
오차(역계산)와 신뢰구간, 단계별로 읽은 양을 표본 정보에 붙인다. 판정은 DB 이름이 아니라 컴파일 결과로 한다. 명세는 plans/phase-2.md."""
import math
from typing import TYPE_CHECKING, Callable

from core import parse, settings, source, sql_exec
from lazy import ibis

if TYPE_CHECKING:  # 구현에서 쓸 import는 plans/phase-2.md 절마다 적었다(polars·pyarrow·profiler·prep_ops는 함수 안에서: 지연 로딩)
    import polars as pl
    import pyarrow as pa

STRATUM = "__stratum"  # 층 번호 컬럼(층화 표본에만 붙는다)
WEIGHT = "__weight"  # Horvitz-Thompson 가중치 Nh/nh 컬럼(층화 표본에만 붙는다)
HELPERS = (STRATUM, WEIGHT)  # 통계·모양 검사·화면에서 빼는 도움 컬럼
WAYS = ("block", "key", "strata")  # 요청 sample_how.way: ① 블록(못 하면 행 단위) ② 키 단위 ③ 층화(기본)
RANDOM_METHODS = ("block", "row")  # 오차·신뢰구간을 붙이는 profiler 방법(무작위 표본). 층화(strata)도 붙인다
BLOCK_OVER = 1.2  # 블록·행 단위 표본 비율을 목표보다 이만큼 크게 잡고 limit으로 자른다(지금 profiler.block의 1.2)
SETTINGS = ("sample_conf", "sample_e_ratio", "sample_e_mean", "pilot_rows", "strata_h", "strata_min", "hash_buckets", "key_over")  # config가 읽는 설정(sample_conf는 신뢰수준 %, config가 Z로 바꾼다)
NOTE_NO_ROWS = "테이블 행 수를 세지 않아 층화 대신 블록 표본으로 뽑았어요."
NOTE_NO_NUMERIC = "숫자 컬럼이 없어 층화 대신 블록 표본으로 뽑았어요."
NOTE_NO_SPREAD = "파일럿에서 값이 퍼진 숫자 컬럼을 찾지 못해 층화 대신 블록 표본으로 뽑았어요."
NOTE_CANNOT = "이 연결은 층마다 다시 뽑기(행 단위 표본)를 하지 못해 층화 대신 블록 표본으로 뽑았어요."
NOTE_KEY_NO_CI = "키 단위 표본은 키마다 행을 묶어 뽑아(군집 표본) 행 단위 공식이 맞지 않아서 오차와 신뢰구간을 붙이지 않아요."
CV_MIN_VALUES = 2  # 변동계수를 구할 빈 값 뺀 값의 최소 개수(표준편차 ddof=1)
CONF_HALF = 200  # 신뢰수준 %를 양쪽 꼬리 분위수로: 0.5 + conf / 200
CEIL_DIGITS = 6  # 부동소수 올림 전 반올림 자릿수: math.ceil(round(x, 6))
WORST_P = 0.5  # 비율 오차가 가장 큰 p(p(1-p) = 0.25): margins의 비율 최악 오차


def config() -> dict:
    """[SPECIFICATION] plans/phase-2.md#config
    표본 설정(SETTINGS)을 저장값 또는 기본값으로 읽어 dict로 돌려준다(신뢰수준 %는 표준정규 역함수로 sample_z가 된다)."""
    from statistics import NormalDist
    found = {name: settings.get(name) for name in SETTINGS}
    found["sample_z"] = NormalDist().inv_cdf(0.5 + found["sample_conf"] / CONF_HALF)  # 양쪽 신뢰수준: 95% → 0.975 분위수
    return found


def how_of(body: dict) -> dict:
    """[SPECIFICATION] plans/phase-2.md#how_of
    요청의 sample_how를 검사해 {way, column}으로 돌려준다(없으면 층화, 기준 컬럼 자동)."""
    how = body.get("sample_how")
    if how is None:
        return {"way": "strata", "column": None}
    column = how.get("column") if isinstance(how, dict) else None
    bad = (not isinstance(how, dict) or how.get("way") not in WAYS
           or (how["way"] == "key" and (not isinstance(column, str) or not column))
           or (how["way"] == "strata" and column is not None and (not isinstance(column, str) or not column)))
    if bad:
        raise ValueError('표본 방식(sample_how)은 {"way": "strata"(기준 컬럼 "column", 비우면 자동)}, {"way": "block"}, {"way": "key", "column": 키 컬럼} 중 하나로 보내세요.')
    return {"way": how["way"], "column": None if how["way"] == "block" else column}


def numeric_columns(t: "ibis.expr.types.Table") -> list[str]:
    """[SPECIFICATION] plans/phase-2.md#numeric_columns
    층화 기준이 될 수 있는 정수·실수 컬럼 이름들(도움 컬럼 제외)."""
    return [n for n, d in t.schema().items() if n not in HELPERS and parse.kind(d) in ("int", "float")]


def can_stratify(t: "ibis.expr.types.Table", column: str) -> bool:
    """[SPECIFICATION] plans/phase-2.md#can_stratify
    층 번호 붙이기와 층별 행 단위 표본을 그 연결이 컴파일할 수 있는지(실행하지 않음)."""
    base = t.mutate(**{STRATUM: (t[column] >= 0).cast("int64")})
    part = base.filter(base[STRATUM] == 0).sample(0.5, method="row").limit(1)
    return source.compiles(ibis.union(part, base.filter(base[STRATUM] == 1)))


def settle(t: "ibis.expr.types.Table", how: dict, rows: int | None) -> dict:
    """[SPECIFICATION] plans/phase-2.md#settle
    층화를 할 수 없으면 블록 표본으로 바꾸고 안내(notes)를 붙인 {way, column, notes}를 돌려준다."""
    if how["way"] == "key":
        parse.check_name(how["column"], t.columns)
        return {**how, "notes": []}
    if how["way"] == "block":
        return {**how, "notes": []}
    if how["column"] is not None:
        parse.check_name(how["column"], t.columns)
        if parse.kind(t[how["column"]].type()) not in ("int", "float"):
            raise ValueError(f"층화 기준 컬럼은 정수·실수 컬럼이어야 해요. '{how['column']}' 컬럼은 {parse.type_info(t[how['column']].type())[0]} 컬럼이에요.")
    numbers = [how["column"]] if how["column"] else numeric_columns(t)
    note = NOTE_NO_ROWS if rows is None else NOTE_NO_NUMERIC if not numbers else NOTE_CANNOT if not can_stratify(t, numbers[0]) else None
    return {"way": "block", "column": None, "notes": [note]} if note else {**how, "notes": []}


def plan(t: "ibis.expr.types.Table", how: dict, size: int, rows: int | None, conf: dict) -> list[tuple[str, object]]:
    """[SPECIFICATION] plans/phase-2.md#plan
    실행 전 확인 창에 보일 단계별 (단계 이름, 읽는 양을 잴 Ibis 식 또는 None=무과금)."""
    def frac(n: int) -> float:
        return 1.0 if not rows else min(1.0, n * BLOCK_OVER / rows)

    pilot = conf["pilot_rows"]
    steps = [("파일럿", t.sample(frac(pilot), method="block").limit(pilot) if settings.get("pilot_first") == "block" else None)]
    if how["way"] == "block":
        steps.append(("표본", None))  # ① 본 표본은 늘 무과금 미리보기 먼저(pilot_first는 파일럿만)
    elif how["way"] == "key":
        steps += [("빈 키 세기", t.aggregate(n=t[how["column"]].isnull().sum())), ("키 단위 뽑기", t)]
    else:
        cols = [how["column"]] if how["column"] else numeric_columns(t)
        steps += [("층 크기 세기", t.aggregate(**{f"n{i}": t[c].count() for i, c in enumerate(cols)})), ("다시 뽑기", t)]
    return steps


def sample_way(t: "ibis.expr.types.Table", fraction: float) -> str:
    """[SPECIFICATION] plans/phase-2.md#sample_way
    연결이 표 t에서 할 수 있는 무작위 표본 방식("block"·"row"·"head")을 컴파일 결과로 판정한다."""
    backend = ibis.get_backend(t)
    if not isinstance(backend.compile(t), str):  # SQL이 아닌 연결(Polars): Table.sample은 행 단위
        return "row" if source.compiles(t.sample(fraction, method="row")) else "head"
    sqlglot, exp = sql_exec.glot()[:2]
    block = t.sample(fraction, method="block")
    if source.compiles(block):
        tree = sqlglot.parse_one(backend.compile(block), read=sql_exec.dialect_of(backend))
        if next(tree.find_all(exp.TableSample), None) is not None:
            return "block"
    return "row" if source.compiles(t.sample(fraction, method="row")) else "head"


def auto_column(pilot: "pa.Table") -> str | None:
    """[SPECIFICATION] plans/phase-2.md#auto_column
    파일럿에서 변동계수(CV)가 가장 큰 숫자 컬럼(층화 기준). 없으면 None."""
    import polars as pl
    cvs = _cvs(pl.from_arrow(bare(pilot)))
    return max(cvs, key=cvs.get) if cvs else None  # 같으면 앞 컬럼


def _cvs(frame: "pl.DataFrame") -> dict[str, float]:
    """숫자 컬럼(Boolean·도움 컬럼 제외, NaN은 빈 값)의 변동계수 sd/|mean|. 값 2개 이상·평균 ≠ 0·표준편차 > 0인 것만, 컬럼 차례대로(plans/phase-2.md 공통 규칙)."""
    import polars as pl
    names = [n for n, d in frame.schema.items() if n not in HELPERS and d.is_numeric() and d != pl.Boolean]
    values = [pl.col(n).cast(pl.Float64).fill_nan(None) for n in names]
    row = frame.select([e for i, v in enumerate(values) for e in (v.count().alias(f"n{i}"), v.mean().alias(f"m{i}"), v.std().alias(f"s{i}"))]).row(0, named=True) if names else {}
    return {n: row[f"s{i}"] / abs(row[f"m{i}"]) for i, n in enumerate(names) if row[f"n{i}"] >= CV_MIN_VALUES and row[f"m{i}"] and row[f"s{i}"]}


READS: list[dict] = []  # 지금 단계에서 measured가 받은 조회들 {rows, bytes, billed}(stage가 비우고 모은다. 연결 스레드에서만 쓴다)


def measured(con: object, expr: "ibis.expr.types.Table", **kwargs: object) -> "pa.Table":
    """[SPECIFICATION] plans/phase-2.md#measured
    식의 결과를 pyarrow 표로 받고 받은 행 수와 (가능하면) 처리·과금 바이트를 READS에 남긴다(profiler.fetch의 본체)."""
    import pyarrow as pa

    from core import scan_size
    if scan_size.dry_runs(con):
        sql, _ = scan_size.sql_of(con, expr)
        job = con.client.query(sql)
        arrow = job.to_arrow().cast(expr.schema().to_pyarrow())
        READS.append({"rows": arrow.num_rows, "bytes": job.total_bytes_processed, "billed": job.total_bytes_billed})
        return arrow
    try:
        arrow = expr.to_pyarrow(**kwargs)
    except pa.ArrowInvalid:  # 드라이버가 빈 값이 든 정수 컬럼을 실수로 거쳐 넘겨 넘치면 DB-API 커서로 원래 타입으로
        cursor = con.raw_sql(ibis.to_sql(expr))
        try:
            names = [d[0] for d in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
        arrow = pa.Table.from_pylist(rows, schema=expr.schema().to_pyarrow())
    READS.append({"rows": arrow.num_rows, "bytes": None, "billed": None})
    return arrow


def stage(name: str, work: Callable[[], object], scanned: int | None = None) -> tuple[object, dict]:
    """[SPECIFICATION] plans/phase-2.md#stage
    work()를 한 단계로 돌려 (결과, {stage, rows, bytes, billed, scanned})를 돌려준다."""
    from core import scan_size
    READS.clear()
    found = work()
    got = list(READS)

    def total(k: str) -> int | None:
        return sum(r[k] for r in got) if got and all(r[k] is not None for r in got) else None

    read = {"stage": name, "rows": sum(r["rows"] for r in got) if got else None, "bytes": total("bytes"), "billed": total("billed"), "scanned": scanned}
    parts = [f"{read['rows']:,}행 받음" if read["rows"] is not None else "조회 없음",
             read["bytes"] is not None and f"처리 {scan_size.size_text(read['bytes'])}",
             read["billed"] is not None and f"과금 {scan_size.size_text(read['billed'])}",
             scanned is not None and f"표 전체 {scanned:,}행을 훑음"]
    read["text"] = f"{name}: " + " · ".join(p for p in parts if p)
    return found, read


def keyed(con: object, t: "ibis.expr.types.Table", key: str, size: int, rows: int | None, conf: dict) -> tuple["pa.Table", dict]:
    """[SPECIFICATION] plans/phase-2.md#keyed
    hash(키) % M < k로 키를 통째로 골라 그 행들을 받는다(키 단위 표본). 키가 빈 행 수와 단계별 읽은 양도 돌려준다."""
    from core import result
    parse.check_name(key, t.columns)
    if rows is None:
        raise ValueError("키 단위 표본은 테이블 행 수가 필요해요. '컬럼 통계와 추천 보기'를 켜 행 수를 센 뒤 다시 구하세요.")
    buckets, over = conf["hash_buckets"], conf["key_over"]
    keyed_rows = t.filter(t[key].notnull())

    def bucket(k: int) -> "ibis.expr.types.BooleanValue":
        return ((keyed_rows[key].hash() % buckets) + buckets) % buckets < k  # 음수 해시(FARM_FINGERPRINT)도 양수 칸으로

    if not source.compiles(keyed_rows.filter(bucket(1))):
        raise ValueError("이 연결은 키 단위 표본(값의 해시)을 만들지 못해요. 표본 방식을 '층화'나 '블록'으로 바꾸세요.")
    nulls, r1 = stage("빈 키 세기", lambda: measured(con, t.aggregate(n=t[key].isnull().sum()))["n"][0].as_py() or 0, scanned=rows)
    left = rows - nulls
    if left <= 0:
        raise ValueError(f"'{key}' 컬럼 값이 모두 비어 키 단위 표본을 뽑을 수 없어요.")
    if left <= size:
        picked = keyed_rows
    else:
        k = min(buckets, math.ceil(round(buckets * size / left, CEIL_DIGITS)))
        picked = keyed_rows.filter(bucket(k))
    cap = size * over
    limited = picked.limit(cap + 1)
    arrow, r2 = stage("키 단위 뽑기", lambda: measured(con, limited, **result.streaming(limited)), scanned=rows)
    if arrow.num_rows > cap:
        raise ValueError(f"고른 키에 행이 몰려 키 단위 표본이 {cap:,}행을 넘어요. 더 잘게 나뉜 키 컬럼을 고르거나 표본 방식을 바꾸세요.")
    if arrow.num_rows == 0:
        raise ValueError(f"'{key}' 컬럼의 해시 범위에 든 행이 없어요. 값 종류가 적은 컬럼은 키 단위 표본에 맞지 않아요.")
    return arrow, {"null_keys": nulls, "reads": [r1, r2], "notes": [f"'{key}' 값이 빈 행 {nulls:,}개는 키 단위 표본에서 빠졌어요."] if nulls else []}


def finite_size(n0: float, rows: int | None) -> int:
    """[SPECIFICATION] plans/phase-2.md#finite_size
    표본 수 n0에 유한모집단 보정 n0/(1+(n0-1)/N)을 걸어 올림한 정수."""
    n = n0 if rows is None else n0 / (1 + (n0 - 1) / rows)
    return math.ceil(round(n, CEIL_DIGITS))


def ratio_size(p: float | None, e: float, rows: int | None, z: float) -> int:
    """[SPECIFICATION] plans/phase-2.md#ratio_size
    비율 추정의 Cochran 표본 수 Z²·p(1-p)/e²(p를 모르면 0.5)에 유한모집단 보정을 건 값."""
    p = 0.5 if p is None else p
    if not 0 < e < 1 or not 0 <= p <= 1:
        raise ValueError("비율의 허용 오차는 0과 1 사이, 비율 p는 0~1이어야 해요.")
    return finite_size(z ** 2 * p * (1 - p) / e ** 2, rows)


def mean_size(cv: float, e_r: float, rows: int | None, z: float) -> int:
    """[SPECIFICATION] plans/phase-2.md#mean_size
    평균·합계 추정의 표본 수 (Z·CV/e_r)²에 유한모집단 보정을 건 값."""
    if cv < 0 or e_r <= 0:
        raise ValueError("변동계수는 0 이상, 상대 허용 오차는 0보다 커야 해요.")
    return finite_size((z * cv / e_r) ** 2, rows)


def needed(pilot: "pa.Table", rows: int | None, conf: dict) -> int:
    """[SPECIFICATION] plans/phase-2.md#needed
    파일럿의 빈 값 비율·변동계수로 비율·평균 오차 목표를 맞출 표본 수(둘 중 큰 것)."""
    import polars as pl
    frame = pl.from_arrow(bare(pilot))
    if frame.height == 0:
        return 0
    nulls = frame.select([((pl.col(n).is_null() | pl.col(n).is_nan()) if d.is_float() else pl.col(n).is_null()).mean().alias(n) for n, d in frame.schema.items()]).row(0)
    p = max(nulls, key=lambda q: q * (1 - q))  # 같으면 앞 컬럼
    ratio = ratio_size(p, conf["sample_e_ratio"], rows, conf["sample_z"]) if p * (1 - p) > 0 else 0
    mean = max((mean_size(cv, conf["sample_e_mean"], rows, conf["sample_z"]) for cv in _cvs(frame).values()), default=0)
    return max(ratio, mean)


def mean_ci(frame: "pl.DataFrame", expr: "pl.Expr", rows: int | None, sizes: dict[int, int] | None, z: float) -> tuple[float | None, float | None]:
    """[SPECIFICATION] plans/phase-2.md#mean_ci
    expr 값 평균의 추정과 신뢰구간 반폭(단순 무작위면 유한모집단 보정, 층화면 층별 분산)."""
    import polars as pl
    if sizes is None:
        y = frame.select(expr.cast(pl.Float64).alias("y"))["y"].fill_nan(None).drop_nulls()
        n = y.len()
        if n == 0:
            return None, None, None
        f = 0.0 if rows is None else min(frame.height / rows, 1.0)
        return y.mean(), z * math.sqrt((1 - f) * (y.var() or 0.0) / n), None if rows is None else rows * n / frame.height
    d = frame.select(expr.cast(pl.Float64).alias("y"), pl.col(STRATUM))
    counted = d.group_by(STRATUM).len()
    alln = dict(zip(counted[STRATUM].to_list(), counted["len"].to_list()))
    g = (d.with_columns(pl.col("y").fill_nan(None)).drop_nulls("y")
         .group_by(STRATUM).agg(n=pl.len(), m=pl.col("y").mean(), v=pl.col("y").var())
         .with_columns(big=pl.col(STRATUM).replace_strict(sizes, return_dtype=pl.Float64),
                       all=pl.col(STRATUM).replace_strict(alln, return_dtype=pl.Float64)))
    g = g.with_columns(nd=pl.col("big") * pl.col("n") / pl.col("all"))
    nd = g["nd"].sum()
    if not nd:
        return None, None, None
    est = (g["nd"] * g["m"]).sum() / nd
    var = ((g["nd"] / nd) ** 2 * (1 - g["all"] / g["big"]) * g["v"].fill_null(0.0) / g["n"]).sum()
    return est, z * math.sqrt(var), nd


def margins(arrow: "pa.Table", rows: int | None, sizes: dict[int, int] | None, conf: dict) -> dict:
    """[SPECIFICATION] plans/phase-2.md#margins
    지금 표본 크기로 달성한 오차(비율 ±, 숫자 컬럼 평균의 상대 ±)와 목표 오차에 필요한 행 수(역계산)."""
    import polars as pl
    frame = pl.from_arrow(arrow)
    n = frame.height
    if n == 0:
        raise ValueError("표본이 비어 오차를 구할 수 없어요.")
    z, e, er = conf["sample_z"], conf["sample_e_ratio"], conf["sample_e_mean"]
    spread = WORST_P * (1 - WORST_P)
    if sizes is None:
        factor = 0.0 if rows is not None and n >= rows else math.sqrt((rows - n) / (n * (rows - 1))) if rows is not None and rows > 1 else math.sqrt(1 / n)
        ratio = z * math.sqrt(spread) * factor
    else:
        total = sum(sizes.values())
        big = pl.col(STRATUM).replace_strict(sizes, return_dtype=pl.Float64)
        got = frame.group_by(STRATUM).len()
        ratio = z * math.sqrt(got.select(((big / total) ** 2 * (1 - pl.col("len") / big) * spread / pl.col("len")).sum()).item())
    means = {}
    for name, cv in _cvs(pl.from_arrow(bare(arrow))).items():
        est, half, _ = mean_ci(frame, pl.col(name), rows, sizes, z)
        if est:
            means[name] = {"cv": cv, "error": half / abs(est), "need": mean_size(cv, er, rows, z)}
    return {"z": z, "conf": conf.get("sample_conf"), "e_ratio": e, "e_mean": er, "ratio": ratio, "ratio_need": ratio_size(None, e, rows, z), "means": means}


def intervals(arrow: "pa.Table", rows: int | None, sizes: dict[int, int] | None, conf: dict) -> dict:
    """[SPECIFICATION] plans/phase-2.md#intervals
    컬럼마다 비율(빈 값·빈 글자·앞뒤 공백)과 평균의 추정값과 신뢰구간 [추정, 아래, 위]."""
    import polars as pl
    frame = pl.from_arrow(arrow)
    out = {}
    for name, dtype in frame.schema.items():
        if name in HELPERS:
            continue
        c = pl.col(name)
        items = {"null": (c.is_null() | c.is_nan()) if dtype.is_float() else c.is_null()}
        if dtype == pl.String:
            items["blank"] = (c == "").fill_null(False)
            items["spaces"] = (c != c.str.strip_chars()).fill_null(False)
        if dtype.is_numeric() and dtype != pl.Boolean:
            items["mean"] = c
        found = {}
        for item, expr in items.items():
            est, half, _ = mean_ci(frame, expr.cast(pl.Float64), rows, sizes, conf["sample_z"])
            if est is None:
                continue
            found[item] = [est, est - half, est + half] if item == "mean" else [est, max(0.0, est - half), min(1.0, est + half)]
        out[name] = found
    return out


def strata_edges(values: "pl.Series", h: int) -> list[float]:
    """[SPECIFICATION] plans/phase-2.md#strata_edges
    파일럿 값의 분위수로 층 경계(오름차순, 겹침·최솟값 제외)를 정한다."""
    import polars as pl
    v = values.cast(pl.Float64).fill_nan(None).drop_nulls()
    if v.len() == 0:
        return []
    low = v.min()
    return [e for e in sorted({float(v.quantile(i / h, interpolation="lower")) for i in range(1, h)}) if e > low]


def strata_size(sizes: list[int], sds: list[float], mean: float, z: float, e_r: float) -> int:
    """[SPECIFICATION] plans/phase-2.md#strata_size
    층화 표본의 전체 표본 수 n = n0/(1+(1/N)·(ΣWhσh²/(ΣWhσh)²)·n0), n0 = (Z·ΣWhσh/(e_r·μ))²."""
    total = sum(sizes)
    if not mean:
        raise ValueError(f"평균이 0이라 상대 오차(±{e_r:.0%})로 표본 수를 정할 수 없어요. 층화 기준 컬럼을 바꾸세요.")
    w = [s / total for s in sizes]
    a = sum(wi * si for wi, si in zip(w, sds))
    if a == 0:
        return 0
    b = sum(wi * si ** 2 for wi, si in zip(w, sds))
    n0 = (z * a / (e_r * abs(mean))) ** 2
    return math.ceil(round(n0 / (1 + (b / a ** 2) * n0 / total), CEIL_DIGITS))


def neyman(sizes: list[int], sds: list[float], n: int, least: int) -> list[int]:
    """[SPECIFICATION] plans/phase-2.md#neyman
    Neyman 최적 할당 nh = n·Nhσh/ΣNiσi(층마다 최소 least). 할당이 층 행 수를 넘는 층은 전수로 고정하고 나머지에 다시 나눈다."""
    fixed, share = set(), {}
    while True:
        rest = [h for h in range(len(sizes)) if h not in fixed]
        if not rest:
            break
        left = max(n - sum(sizes[h] for h in fixed), 0)
        weight = {h: sizes[h] * sds[h] for h in rest}
        total, rest_rows = sum(weight.values()), sum(sizes[h] for h in rest)
        share = {h: left * (weight[h] / total if total else sizes[h] / rest_rows) for h in rest}
        over = [h for h in rest if share[h] > sizes[h]]
        if not over:
            break
        fixed.update(over)  # 할당 > 층 행 수 → 전수, 나머지에 다시 나눈다
    return [sizes[h] if h in fixed else min(sizes[h], max(least, math.ceil(round(share[h], CEIL_DIGITS)))) for h in range(len(sizes))]


def ht_estimate(drawn: "pl.DataFrame", column: str, sizes: dict[int, int], z: float) -> dict:
    """[SPECIFICATION] plans/phase-2.md#ht_estimate
    층별로 뽑은 값에 Horvitz-Thompson 가중치(Nh/nh)를 걸어 그 컬럼의 합계·평균과 신뢰구간을 구한다."""
    import polars as pl
    counted = drawn.group_by(STRATUM).len()
    got = dict(zip(counted[STRATUM].to_list(), counted["len"].to_list()))
    missing = sorted(h for h in sizes if h not in got)
    if missing:
        raise ValueError(f"층 {missing}에서 행을 하나도 뽑지 못했어요. [다시 구하기]로 다시 뽑거나 미리 읽을 행 수를 늘리세요.")
    mean, half, domain = mean_ci(drawn, pl.col(column), None, sizes, z)
    return {"column": column, "mean": mean, "moe_mean": half, "low": mean - half, "high": mean + half,
            "total": mean * domain, "moe_total": half * domain, "z": z, "rows": round(domain), "drawn": drawn.height,
            "strata": [{"stratum": h, "rows": sizes[h], "drawn": got[h], "weight": sizes[h] / got[h]} for h in sorted(sizes)]}


def weighted_quantiles(t: "ibis.expr.types.Table", name: str, qs: list[float], weight: str) -> list[float | None]:
    """[SPECIFICATION] plans/phase-2.md#weighted_quantiles
    가중치 컬럼을 건 분위수(누적 가중치가 q·합계에 처음 닿는 값). 층화 표본의 사분위수에 쓴다."""
    from core import prep_ops
    s = t.select(v=prep_ops.nan_to_null(t[name]).cast("float64"), w=t[weight])
    s = s.filter(s.v.notnull())
    total = s.w.sum().to_pyarrow().as_py()
    if not total:
        return [None] * len(qs)
    c = s.mutate(c=s.w.sum().over(ibis.window(order_by=[s.v], preceding=None, following=0)))
    found = c.aggregate(**{f"q{i}": c.v.min(where=c.c >= q * total) for i, q in enumerate(qs)}).to_pyarrow()
    return [found[f"q{i}"][0].as_py() for i in range(len(qs))]


def stratified(con: object, t: "ibis.expr.types.Table", column: str, pilot: "pa.Table", size: int, rows: int, conf: dict) -> tuple["pa.Table", dict]:
    """[SPECIFICATION] plans/phase-2.md#stratified
    파일럿으로 층·필요 표본 수·Neyman 할당을 정하고 DB에서 층별로 다시 뽑아 가중치를 붙인 표본과 층 정보를 돌려준다."""
    import polars as pl

    from core import prep_ops, result
    h_count, least, z, er = conf["strata_h"], conf["strata_min"], conf["sample_z"], conf["sample_e_mean"]
    values = pl.from_arrow(bare(pilot))[column].cast(pl.Float64).fill_nan(None).drop_nulls()
    if values.len() < CV_MIN_VALUES:
        raise ValueError(f"파일럿 표본에 '{column}' 값이 {values.len():,}개뿐이라 층을 나눌 수 없어요.")
    edges = strata_edges(values, h_count)
    empty = len(edges) + 1  # 빈 값 층
    col = prep_ops.nan_to_null(t[column])
    level = col.isnull().ifelse(empty, sum(((col >= e).cast("int64") for e in edges), ibis.literal(0).cast("int64")))
    base = t.mutate(**{STRATUM: level})
    counted, r1 = stage("층 크기 세기", lambda: measured(con, base.group_by(STRATUM).aggregate(n=base.count())), scanned=rows)
    sizes = dict(zip(counted[STRATUM].to_pylist(), counted["n"].to_pylist()))
    order = sorted(sizes)
    counts = [sizes[h] for h in order]
    y = pl.col("y")
    pilot_level = pl.when(y.is_null()).then(empty).otherwise(pl.sum_horizontal([(y >= e).cast(pl.Int64) for e in edges]) if edges else pl.lit(0))
    spread = (pl.from_arrow(bare(pilot)).select(pl.col(column).cast(pl.Float64).fill_nan(None).alias("y"))
              .group_by(pilot_level.alias(STRATUM)).agg(sd=y.std()))
    sd_of = dict(zip(spread[STRATUM].to_list(), spread["sd"].to_list()))
    gap = values.std()  # 파일럿에 값이 0~1개인 층은 파일럿 전체 σ
    sds = [0.0 if h == empty else (sd_of[h] if sd_of.get(h) is not None else (gap or 0.0)) for h in order]
    n = min(size, rows, max(strata_size(counts, sds, float(values.mean()), z, er), needed(pilot, rows, conf), len(order) * least))
    alloc = neyman(counts, sds, n, least)
    parts = [base.filter(base[STRATUM] == h) if a >= sizes[h] else base.filter(base[STRATUM] == h).sample(min(1.0, a * BLOCK_OVER / sizes[h]), method="row").limit(a) for h, a in zip(order, alloc)]
    redraw = ibis.union(*parts) if len(parts) > 1 else parts[0]
    got, r2 = stage("다시 뽑기", lambda: measured(con, redraw, **result.streaming(redraw)), scanned=rows)
    frame = pl.from_arrow(got)
    est = ht_estimate(frame, column, sizes, z)
    w = {s["stratum"]: s["weight"] for s in est["strata"]}
    arrow = frame.with_columns(pl.col(STRATUM).replace_strict(w, return_dtype=pl.Float64).alias(WEIGHT)).to_arrow()
    bounds = [None, *edges, None]
    strata = [{**s, "planned": alloc[order.index(s["stratum"])], "empty": s["stratum"] == empty,
               "low": None if s["stratum"] == empty else bounds[s["stratum"]],
               "high": None if s["stratum"] == empty else bounds[s["stratum"] + 1]} for s in est["strata"]]
    return arrow, {"column": column, "n": n, "edges": edges, "strata": strata,
                   "estimate": {k: est[k] for k in ("mean", "moe_mean", "low", "high", "total", "moe_total", "rows", "z")}, "reads": [r1, r2]}


def draw(con: object, t: "ibis.expr.types.Table", how: dict, size: int, rows: int | None, fallback: Callable[[int, bool], tuple], conf: dict) -> tuple["pa.Table", "pa.Table", dict]:
    """[SPECIFICATION] plans/phase-2.md#draw
    파일럿 → (자동 기준 컬럼) → 필요 표본 수 → 고른 방식으로 표본을 뽑아 (표본, 파일럿, 표본 정보)를 돌려준다(연결 스레드에서)."""
    (pilot, first, passed), r0 = stage("파일럿", lambda: fallback(conf["pilot_rows"], True))
    notes, reads, way, column = list(how["notes"]), [r0], how["way"], how["column"]
    base = {"pilot": pilot.num_rows, "notes": notes, "reads": reads}
    if first == "whole" and rows is not None and rows <= size:  # 파일럿이 곧 표 전체이고 상한 안: 더 읽지 않는다
        return pilot, pilot, {**base, "rows": pilot.num_rows, "method": "whole", "passed": passed, "way": "whole", "column": None, "n": pilot.num_rows}
    if way == "strata" and column is None:
        column = auto_column(pilot)
        if column is None:
            way = "block"
            notes.append(NOTE_NO_SPREAD)
    n = min(size, rows or size, max(needed(pilot, rows, conf), settings.SIZES["sample"][0]))
    sizes, extra = None, {}
    if way == "key":
        arrow, k = keyed(con, t, column, n, rows, conf)
        method, passed = "key", []
        reads += k["reads"]
        notes += [*k["notes"], NOTE_KEY_NO_CI]  # 구간이 없는 까닭
        extra = {"null_keys": k["null_keys"]}
    elif way == "strata":
        arrow, s = stratified(con, t, column, pilot, size, rows, conf)
        method, passed, n = "strata", [], s["n"]
        reads += s["reads"]
        sizes = {x["stratum"]: x["rows"] for x in s["strata"]}
        extra = {"estimate": s["estimate"], "strata": s["strata"], "edges": s["edges"]}
    else:
        (arrow, method, passed), r = stage("표본", lambda: fallback(n, False))
        reads.append(r)
    info = {**base, "rows": arrow.num_rows, "method": method, "passed": passed, "way": way, "column": column if way != "block" else None, "n": n, **extra}
    if method in RANDOM_METHODS or way == "strata":
        info["margins"] = margins(arrow, rows, sizes, conf)
        info["intervals"] = intervals(arrow, rows, sizes, conf)
    return arrow, pilot, info


def bare(arrow: "pa.Table") -> "pa.Table":
    """[SPECIFICATION] plans/phase-2.md#bare
    도움 컬럼(층 번호·가중치)을 뺀 표."""
    return arrow.drop_columns([c for c in HELPERS if c in arrow.column_names])
