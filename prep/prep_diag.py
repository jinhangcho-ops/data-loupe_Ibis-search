"""데이터 진단(Phase 5): 표본으로 구하는 컬럼 진단(시맨틱 타입·숨은 빈 값·분포·문자열·시계열)과 버튼으로 구하는 무거운 진단(조합 키·키 중복·빈 시간 구간·참조 무결성).
표본 진단은 이 PC의 Polars로 한다(층화 표본이면 가중치 컬럼 sampling.WEIGHT를 건다: Phase 2 규칙). 전체 진단은 연결(또는 이 PC 사본)의 Ibis 식으로 한다.
기준 값은 설정 폴더의 OPTIONS_FILE(사용자가 [진단 기준]에서 바꾼다). 명세는 plans/phase-5.md."""
import datetime as dt
from typing import TYPE_CHECKING

from lazy import ibis

if TYPE_CHECKING:  # polars는 함수 안에서 불러온다(지연 로딩)
    import polars as pl

OPTIONS_FILE = "diag_options.json"  # 진단 기준(settings.read·write). 없으면 OPTION_RULES의 기본값
# 진단 기준: 이름 → (종류, 아래, 위, 기본값). 목록 종류의 아래·위는 항목마다의 범위(None이면 검사 없음). 기본값은 사용자 결정(2026-11-04)으로 확정
OPTION_RULES = {
    "iqr_k": ("float", 0.5, 10.0, 1.5),
    "z_k": ("float", 1.0, 10.0, 3.0),
    "mad_k": ("float", 1.0, 10.0, 3.5),
    "skew_log": ("float", 0.1, 10.0, 1.0),
    "zero_share": ("float", 0.01, 1.0, 0.5),
    "hash_share": ("float", 0.5, 1.0, 0.95),
    "json_share": ("float", 0.5, 1.0, 0.9),
    "sentinel_edge": ("float", 0.001, 0.5, 0.01),
    "sentinel_gap": ("float", 0.1, 100.0, 1.0),
    "mode_share": ("float", 0.01, 0.5, 0.05),
    "mode_dip": ("float", 0.1, 0.95, 0.7),
    "benford_min": ("int", 30, 1_000_000, 100),
    "benford_mad": ("float", 0.001, 0.1, 0.015),  # Nigrini 첫째 자리 '부적합' 경계
    "heap_ratio": ("float", 1.1, 100.0, 2.0),
    "future_days": ("int", 0, 3650, 0),
    "case_groups": ("int", 1, 100, 10),
    "qi_columns": ("int", 2, 30, 10),
    "qi_size": ("int", 2, 3, 3),
    "gap_max_buckets": ("int", 100, 1_000_000, 100_000),
    "gap_list": ("int", 1, 1000, 50),
    "dup_top": ("int", 1, 100, 10),
    "orphan_top": ("int", 1, 100, 20),
    "percentiles": ("floats", 0.0, 1.0, [0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999]),
    "sentinels": ("numbers", None, None, [-1, -99, -999, 999999]),
    "heap_bases": ("ints", 2, 1_000_000, [10, 50, 100]),
    "epoch_dates": ("dates", None, None, ["1970-01-01", "1900-01-01", "9999-12-31"]),
    "markers_extra": ("texts", None, None, []),
    "nonneg_hints": ("texts", None, None, ["매출", "금액", "가격", "체류", "시간", "수량", "나이", "amount", "price", "revenue", "sales", "duration", "qty", "quantity", "count", "cnt", "age"]),
    "time_starts": ("texts", None, None, ["start", "begin", "open", "created", "signup", "join", "시작", "개시", "가입", "입사", "등록", "출발"]),
    "time_ends": ("texts", None, None, ["end", "finish", "until", "close", "updated", "leave", "quit", "종료", "마감", "탈퇴", "퇴사", "해지", "도착"]),
    "ordinal_scales": ("scales", None, None, [["bronze", "silver", "gold", "platinum", "diamond"], ["브론즈", "실버", "골드", "플래티넘", "다이아몬드"], ["소", "중", "대"],
                                              ["하", "중", "상"], ["저", "중", "고"], ["초급", "중급", "고급"], ["low", "medium", "high"], ["xs", "s", "m", "l", "xl", "xxl"]]),
    "bool_pairs": ("pairs", None, None, [["0", "1"], ["n", "y"], ["no", "yes"], ["f", "t"], ["false", "true"], ["off", "on"], ["x", "o"], ["아니오", "예"], ["아니요", "예"], ["미사용", "사용"]]),  # [거짓, 참]
}
LIST_MAX = {"floats": 20, "numbers": 30, "ints": 10, "dates": 30, "texts": 50, "scales": 50, "pairs": 50}  # 목록 종류의 최대 항목 수
LIST_MIN = {"floats": 1, "numbers": 0, "ints": 1, "dates": 0, "texts": 0, "scales": 0, "pairs": 0}  # 목록 종류의 최소 항목 수
TEXT_MAX = 50  # 글자 항목 하나의 최대 글자 수
SCALE_MAX = 20  # 순서 척도 하나의 최대 낱말 수(최소 2)
QUARTILES = (0.25, 0.75)  # IQR을 구하는 분위수(Q1, Q3)
STAMP_LEN = 16 # 진단 기준 지문의 글자 수(sha1 앞부분)
SAMPLE_END = "(표본 기준)."  # lines의 모든 줄 끝

# 날짜 모양(앞의 것이 먼저: 값마다 처음 맞는 모양 하나로 센다). 값은 앞뒤 공백을 지운 글자
FORMAT_SHAPES = [("YYYY-MM-DD HH:MM:SS", r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$"), ("YYYY-MM-DD", r"^\d{4}-\d{2}-\d{2}$"), ("YYYY/MM/DD", r"^\d{4}/\d{1,2}/\d{1,2}$"),
                 ("YYYY.MM.DD", r"^\d{4}\.\d{1,2}\.\d{1,2}\.?$"), ("YYYYMMDD", r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$"),
                 ("MM/DD/YYYY 또는 DD/MM/YYYY", r"^\d{1,2}/\d{1,2}/\d{4}$"), ("DD-MM-YYYY 또는 MM-DD-YYYY", r"^\d{1,2}-\d{1,2}-\d{4}$"), ("YYYY년 M월 D일", r"^\d{4}년\s*\d{1,2}월\s*\d{1,2}일$")]
MIX_MIN_SHARE = 0.5  # 날짜 모양에 맞는 값이 이 비율 이상인 글자 컬럼만 형식 혼재를 본다
ORDINAL_MAX_VALUES = 30  # 서로 다른 값이 이보다 많으면 순서형을 보지 않는다
NUMBERED = r"(?i)^\s*(?:(\d+)\s*(단계|등급|차|급|학년|레벨|level|lv\.?|grade|tier|step|stage)|(level|lv\.?|grade|tier|step|stage)\s*(\d+))\s*$"  # 번호가 붙은 순서 값(1단계, Lv 3)
NUMBERED_LABEL = "번호 순서"
HASH_SHAPES = [("uuid", r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"), ("md5", r"^[0-9a-fA-F]{32}$"), ("sha1", r"^[0-9a-fA-F]{40}$"),
               ("sha256", r"^[0-9a-fA-F]{64}$"), ("token", r"^[A-Za-z0-9_\-]{20,}$")]  # 앞의 것이 먼저. token은 길이가 모두 같고 숫자·영문이 함께 있어야 한다
HASH_LABELS = {"uuid": "UUID", "md5": "32자리 해시(MD5)", "sha1": "40자리 해시(SHA-1)", "sha256": "64자리 해시(SHA-256)", "token": "같은 길이의 토큰"}
# 아래 정규식은 Polars(Rust regex) 문법이다(\x{…}). Python re에 넣지 않는다
CONTROL = r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x{200B}-\x{200F}\x{2028}\x{2029}\x{FEFF}]"  # 보이지 않는 제어 문자(탭·줄바꿈은 LINEBREAK로 따로)
LINEBREAK = r"[\t\r\n]"
BROKEN = r"\x{FFFD}|[ÃÂâìíëêð][\x{80}-\x{BF}]"  # 깨진 글자: 대체 문자, UTF-8을 Latin-1로 읽은 모양
CONTROL_MARK = "□"  # 예시 값에서 제어 문자를 보이게 바꾸는 글자
EXAMPLES = 3  # 예시 값 수
EXAMPLE_LEN = 50  # 예시 값 하나의 최대 글자 수
CASE_VARIANTS = 5  # 대소문자 묶음 하나에 보일 값 수
ENTROPY_MAX = 1000  # 서로 다른 값이 이보다 많으면 엔트로피를 구하지 않는다
MAD_C = 0.6745  # 수정 Z-Score = MAD_C × (x - 중앙값) / MAD (Iglewicz-Hoaglin)
SKEW_MIN_N = 3  # 왜도를 구하는 데 필요한 값 수
KURT_MIN_N = 4  # 첨도를 구하는 데 필요한 값 수
SKEW_POWER = 1.5  # 왜도 = m3 / m2^1.5
KURT_NORMAL = 3  # 정규분포의 (초과 아닌) 첨도: 가중 첨도에서 뺀다
MODE_MIN_ROWS = 50 # 다봉 판정에 필요한 값 수(빈 값 제외, 가중 없이)
MODE_MIN_UNIQUE = 10  # 서로 다른 값이 이보다 적으면(이산) 다봉을 보지 않는다
MODE_BINS = (10, 50)  # 히스토그램 구간 수의 아래·위(Freedman-Diaconis를 이 범위로 자른다)
MODE_CLIP = (0.01, 0.99)  # 히스토그램 범위: P1 ~ P99
MODE_SMOOTH = 3  # 칸 수를 고르게 하는 이동 평균 창 크기
MODE_WIDTH_POWER = -1 / 3  # Freedman-Diaconis 구간 폭 = 2 × IQR × n^(-1/3)
BENFORD_SPAN = 100  # |값|의 P99 / P1이 이보다 작으면(자릿수 폭이 좁으면) 벤포드를 보지 않는다
BENFORD_LEVELS = (0.006, 0.012)  # Nigrini 첫째 자리 MAD: 이하 close, 이하 acceptable, 그 위 benford_mad 이하 marginal, 넘으면 nonconform
BENFORD_FEW = "0이 아닌 값이 적어 벤포드 법칙을 보지 않았어요."
BENFORD_NARROW = "값의 자릿수 폭이 좁아(P99/P1 < 100) 벤포드 법칙을 보지 않았어요."
BENFORD_CLIP = (0.01, 0.99)  # 자릿수 폭을 재는 분위수(P1, P99)
BENFORD_DIGITS = tuple(range(1, 10))  # 첫째 자리 숫자 1~9
BENFORD_BASE = 10.0  # 첫째 자리를 뽑는 진법
BENFORD_EPS = 1e-9  # 부동소수 경계 보정
HEAP_SPAN = 2 # 배수 m의 쏠림은 P99 - P1이 m × 이 값 이상일 때만 본다
TIME_PAIRS_MAX = 20  # 시간 역전을 볼 (시작, 끝) 컬럼 쌍 수
QI_TOP = 20  # 조합 키 후보를 보일 최대 수
QI_KINDS = ("int", "string", "date", "bool")  # 조합 키 후보가 되는 컬럼 종류(실수·time·other 제외)
DUP_SIZES = 20  # 키 중복 횟수 분포의 최대 줄 수
DUP_COUNT = "__n"  # 키 묶음의 행 수 컬럼 이름(사용자 컬럼과 겹치면 안 된다)
GAP_UNITS = {"day": "D", "hour": "h"}  # 빈 시간 구간 단위 → Ibis truncate 단위
GAP_STEPS = {"day": dt.timedelta(days=1), "hour": dt.timedelta(hours=1)}  # 빈 시간 구간 단위 → 구간 하나의 길이
ORPHAN_MAX_PAIRS = 3  # 참조 무결성의 키 쌍 최대 수
PAIRS_ERROR = f"키 쌍은 [팩트 컬럼, 차원 컬럼]으로 1~{ORPHAN_MAX_PAIRS}개예요."  # orphans와 /api/diag/orphans가 같이 쓴다
# 전체 진단이 표를 읽는 단계(확인 창에 단계별·합계로 보인다: 처리 용량 = 단계마다 읽는 양의 합). 참조 무결성은 (단계 이름, 읽는 표 "fact"·"dim")
DUPES_STAGES = ("행 수·빈 키", "중복 묶음", "중복 횟수 분포", "많이 겹친 키")
GAPS_STAGES = ("처음·마지막", "구간 목록")
ORPHAN_STAGES = (("팩트 행·빈 키", "fact"), ("팩트 키 묶음", "fact"), ("미매핑 묶음", "fact"), ("미매핑 묶음", "dim"), ("많은 순 키", "fact"), ("많은 순 키", "dim"))


def _w(values: "pl.Series", weight: "pl.Series | None") -> "pl.Series":
    """가중치(없으면 1.0)의 Float64 Series."""
    import polars as pl

    return pl.repeat(1.0, len(values), eager=True) if weight is None else weight.cast(pl.Float64)


def _count(mask: "pl.Series", w: "pl.Series") -> int:
    """조건이 참인 행의 가중치 합(반올림한 정수)."""
    import polars as pl

    return round(float((mask.fill_null(False).cast(pl.Float64) * w).sum()))


def _finite(values: "pl.Series", weight: "pl.Series | None") -> "tuple[pl.Series, pl.Series | None]":
    """Float64로 바꾼 유한한 값과 같은 행의 가중치(없으면 None)."""
    import polars as pl

    v = values.cast(pl.Float64)
    keep = v.is_finite().fill_null(False)
    return v.filter(keep), None if weight is None else weight.cast(pl.Float64).filter(keep)


def _wq(v: "pl.Series", w: "pl.Series | None", qs: list[float]) -> list[float | None]:
    """분위수들: 가중치 없으면 선형 보간, 있으면 누적 가중치가 q × Σw 이상이 되는 첫 값. 값이 없으면 None."""
    import polars as pl

    if len(v) == 0:
        return [None for _ in qs]
    if w is None:
        return [v.quantile(q, interpolation="linear") for q in qs]
    d = pl.DataFrame({"v": v, "w": w}).sort("v").with_columns(c=pl.col("w").cum_sum())
    total = d["w"].sum()
    return [d.filter(pl.col("c") >= q * total)["v"][0] for q in qs]


def options(raw: object) -> dict:
    """[SPECIFICATION] plans/phase-5.md#options
    저장된 진단 기준(raw, 없으면 None)을 OPTION_RULES로 검사해 기본값과 합친 dict를 돌려준다(틀린 값은 ValueError)."""
    import copy
    import math

    if raw is None:
        raw = {}
    elif not isinstance(raw, dict):
        raise ValueError("진단 기준은 {이름: 값} 모양이어야 해요.")
    unknown = set(raw) - set(OPTION_RULES)
    if unknown:
        raise ValueError(f"모르는 진단 기준이에요: {', '.join(sorted(unknown))}")

    def is_num(x: object) -> bool:
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    def is_text(x: object) -> bool:
        return isinstance(x, str) and 1 <= len(x.strip()) <= TEXT_MAX

    def is_date(x: object) -> bool:
        if not isinstance(x, str) or len(x) != 10:
            return False
        try:
            dt.date.fromisoformat(x)
        except ValueError:
            return False
        return True

    def words(x: object, low: int, high: int) -> list[str] | None:
        """texts 항목 low~high개의 목록이면 앞뒤 공백 뺀 소문자 목록, 아니면 None."""
        if not (isinstance(x, list) and low <= len(x) <= high and all(is_text(w) for w in x)):
            return None
        lowered = [w.strip().lower() for w in x]
        return lowered if len(set(lowered)) == len(lowered) else None

    def check(kind: str, v: object, lo: float | None, hi: float | None) -> object:
        """검사를 통과한 값, 틀리면 None(어떤 종류도 None은 올바른 값이 아니다)."""
        if kind == "float":
            return float(v) if is_num(v) and lo <= v <= hi else None
        if kind == "int":
            return v if type(v) is int and lo <= v <= hi else None
        if not (isinstance(v, list) and LIST_MIN[kind] <= len(v) <= LIST_MAX[kind]):
            return None
        if kind == "floats":
            return sorted(set(map(float, v))) if all(is_num(x) and lo < x < hi for x in v) else None
        if kind == "numbers":
            return list(dict.fromkeys(v)) if all(is_num(x) and math.isfinite(x) for x in v) else None
        if kind == "ints":
            return sorted(set(v)) if all(type(x) is int and lo <= x <= hi for x in v) else None
        if kind == "dates":
            return list(dict.fromkeys(v)) if all(is_date(x) for x in v) else None
        if kind == "texts":
            return list(dict.fromkeys(x.strip() for x in v)) if all(is_text(x) for x in v) else None
        checked = [words(x, 2, SCALE_MAX if kind == "scales" else 2) for x in v]
        if any(c is None for c in checked):
            return None
        return [list(c) for c in dict.fromkeys(map(tuple, checked))] if kind == "pairs" else checked

    described = {
        "floats": "0과 1 사이(양 끝 제외)의 수 1~20개", "numbers": "수 30개까지", "dates": "YYYY-MM-DD 날짜 30개까지",
        "texts": "1~50글자 글자 50개까지", "scales": "2~20개 낱말의 목록 50개까지", "pairs": "[거짓, 참] 서로 다른 두 글자의 목록 50개까지",
    }
    out: dict = {}
    for name, (kind, lo, hi, default) in OPTION_RULES.items():
        if name not in raw:
            out[name] = copy.deepcopy(default)
            continue
        value = check(kind, raw[name], lo, hi)
        if value is None:
            what = {"float": f"{lo}~{hi} 사이의 수", "int": f"{lo}~{hi} 사이의 정수", "ints": f"{lo}~{hi} 사이의 정수 1~10개"}.get(kind) or described[kind]
            raise ValueError(f"진단 기준 '{name}'은(는) {what}이어야 해요.")
        out[name] = value
    return out


def stamp(opts: dict) -> str:
    """[SPECIFICATION] plans/phase-5.md#stamp
    진단 기준 dict의 지문(정렬한 JSON의 sha1 앞 16자). 캐시가 같은 기준으로 구한 것인지 본다."""
    import hashlib
    import json

    return hashlib.sha1(json.dumps(opts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:STAMP_LEN]


def formats(values: "pl.Series", weight: "pl.Series | None", markers: list[str]) -> dict | None:
    """[SPECIFICATION] plans/phase-5.md#formats
    글자 컬럼의 날짜 모양별 값 수와 섞인 비율(복합 포맷 혼재). 날짜 모양이 드문 컬럼은 None."""
    w = _w(values, weight)
    s = values.str.strip_chars()
    rest = (values.is_not_null() & ~values.is_in(markers) & (s != "")).fill_null(False)
    taken = rest & False
    counts = {}
    for label, pattern in FORMAT_SHAPES:
        hit = rest & s.str.contains(pattern).fill_null(False)
        n = _count(hit & ~taken, w)
        if n > 0:
            counts[label] = n
        taken = taken | hit
    n_rest = _count(rest, w)
    matched = sum(counts.values())
    if n_rest == 0 or matched / n_rest < MIX_MIN_SHARE:
        return None
    return {"counts": counts, "matched": matched, "rest": n_rest, "mixed": 1 - max(counts.values()) / matched}


def semantic(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> dict:
    """[SPECIFICATION] plans/phase-5.md#semantic
    이진(binary)·순서형(ordinal)·해시/UUID(hash) 태그 중 맞는 것만 담은 dict."""
    import polars as pl

    out: dict = {}
    is_text = values.dtype == pl.String
    vals = values.drop_nulls().unique().sort()
    if len(vals) == 2:
        listed = vals.to_list()
        low = [str(v).strip().lower() for v in listed]
        true = None
        for false_word, true_word in opts["bool_pairs"]:
            if low == [false_word, true_word]:
                true = listed[1]
            elif low == [true_word, false_word]:
                true = listed[0]
        out["binary"] = {"values": listed, "true": true}
    if is_text and 2 <= len(vals) <= ORDINAL_MAX_VALUES:
        norm = vals.str.strip_chars().str.to_lowercase()
        for scale in opts["ordinal_scales"]:
            if norm.is_in(scale).all():
                order = sorted(vals.to_list(), key=lambda v: (scale.index(v.strip().lower()), v))
                out["ordinal"] = {"scale": "/".join(scale), "order": order}
                break
        else:
            d = (vals.to_frame("v").with_columns(g=pl.col("v").str.extract_groups(NUMBERED)).unnest("g")
                 .with_columns(num=pl.coalesce("1", "4"), word=pl.coalesce("2", "3").str.to_lowercase().str.replace(r"\.$", "")))
            if d["num"].null_count() == 0 and d["word"].n_unique() == 1:
                out["ordinal"] = {"scale": NUMBERED_LABEL, "order": d.sort(pl.col("num").cast(pl.Int64), "v")["v"].to_list()}
    if is_text:
        nn = values.is_not_null()
        w = _w(values, weight)
        n = _count(nn, w)
        if n:
            for shape, pattern in HASH_SHAPES:
                share = _count(nn & values.str.contains(pattern), w) / n
                if share < opts["hash_share"]:
                    continue
                if shape == "token":
                    lengths = values.drop_nulls().str.len_chars()
                    mixed = _count(nn & values.str.contains("[0-9]") & values.str.contains("[A-Za-z]"), w) / n
                    if lengths.min() != lengths.max() or mixed < opts["hash_share"]:
                        break
                out["hash"] = {"shape": shape, "share": share}
                break
    return out


def hashed_columns(frame: "pl.DataFrame", weight: "pl.Series | None", kinds: dict[str, str], opts: dict) -> list[str]:
    """[SPECIFICATION] plans/phase-5.md#hashed_columns
    표본에서 해시·UUID·토큰 모양(semantic의 hash)인 글자 컬럼 이름들(컬럼 차례)."""
    return [n for n in frame.columns if kinds.get(n) == "string" and "hash" in semantic(frame[n], weight, opts)]


def json_counts(t: "ibis.expr.types.Table", names: list[str], weight: str | None) -> dict[str, dict]:
    """[SPECIFICATION] plans/phase-5.md#json_counts
    표본 memtable(이 PC의 DuckDB)에서 글자 컬럼마다 JSON 객체·배열로 읽히는 값 수를 집계 한 번으로 센다."""
    from core import prep_ops

    @ibis.udf.scalar.builtin
    def json_valid(s: str) -> bool:
        """DuckDB 내장 json_valid(이 PC에서만 쓴다: 원격 식에 넣지 않는다)."""

    if not names:
        return {}
    w = ibis.literal(1.0) if weight is None else t[weight].cast("float64")
    metrics = {}
    for i, name in enumerate(names):
        s = t[name].strip()
        ok = ibis.coalesce(json_valid(s), False)
        metrics |= {f"o{i}": ibis.coalesce((ok & s.startswith("{")).ifelse(w, 0).sum(), 0),
                    f"a{i}": ibis.coalesce((ok & s.startswith("[")).ifelse(w, 0).sum(), 0),
                    f"r{i}": ibis.coalesce(t[name].notnull().ifelse(w, 0).sum(), 0)}
    found = prep_ops.stats(t, **metrics)
    return {name: {"objects": round(found[f"o{i}"]), "arrays": round(found[f"a{i}"]), "rest": round(found[f"r{i}"]),
                   "share": (found[f"o{i}"] + found[f"a{i}"]) / found[f"r{i}"] if found[f"r{i}"] else 0.0}
            for i, name in enumerate(names)}


def text_quality(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> dict:
    """[SPECIFICATION] plans/phase-5.md#text_quality
    글자 컬럼의 공백뿐인 값·탭/줄바꿈·더한 빈 값 표시·글자/바이트 길이·대소문자 묶음·제어/깨진 문자."""
    import polars as pl

    from prep import prep_profile

    w = _w(values, weight)
    nn = values.is_not_null()
    extra = [m for m in opts["markers_extra"] if m not in prep_profile.MARKERS]
    markers = {m: n for m in extra if (n := _count(values == m, w))}

    wn = w.filter(nn)
    total = wn.sum()
    chars = values.filter(nn).str.len_chars()
    sizes = values.filter(nn).str.len_bytes()
    if len(wn):
        length = {"mean": float((wn * chars).sum() / total), "bytes_min": int(sizes.min()), "bytes_max": int(sizes.max()),
                  "bytes_mean": float((wn * sizes).sum() / total)}
    else:
        length = {"mean": None, "bytes_min": None, "bytes_max": None, "bytes_mean": None}

    d = pl.DataFrame({"v": values, "w": w}).drop_nulls("v").with_columns(low=pl.col("v").str.to_lowercase())
    per = d.group_by("low", "v").agg(n=pl.col("w").sum())
    top = (per.group_by("low").agg(total=pl.col("n").sum(), k=pl.len()).filter(pl.col("k") >= 2)
           .sort(["total", "low"], descending=[True, False]).head(opts["case_groups"]))
    cases = [[[v, round(n)] for v, n in per.filter(pl.col("low") == low).sort(["n", "v"], descending=[True, False]).head(CASE_VARIANTS).select("v", "n").iter_rows()]
             for low in top["low"]]

    ctl = values.str.contains(CONTROL)
    bad = values.str.contains(BROKEN)
    chars_found = {"control": _count(ctl, w), "broken": _count(bad, w),
                   "examples": values.filter(ctl | bad).unique(maintain_order=True).head(EXAMPLES).str.replace_all(CONTROL, CONTROL_MARK)
                   .str.slice(0, EXAMPLE_LEN).to_list()}
    return {"ws_only": _count(values.str.contains(r"^\s+$"), w), "tab_nl": _count(values.str.contains(LINEBREAK), w), "markers": markers,
            "length": length, "cases": cases, "chars": chars_found}


def entropy(values: "pl.Series", weight: "pl.Series | None") -> dict | None:
    """[SPECIFICATION] plans/phase-5.md#entropy
    범주 값의 섀넌 엔트로피(비트)·정규화 엔트로피·지니 불순도. 값이 없거나 너무 많으면 None."""
    import math

    import polars as pl

    d = pl.DataFrame({"v": values, "w": _w(values, weight)}).drop_nulls("v").group_by("v").agg(n=pl.col("w").sum())
    k = d.height
    if k == 0 or k > ENTROPY_MAX:
        return None
    p = d["n"] / d["n"].sum()
    bits = float(-(p * p.log(2)).sum())
    norm = bits / math.log2(k) if k > 1 else 0.0
    gini = float(1 - (p ** 2).sum())
    return {"bits": bits, "norm": norm, "gini": gini, "k": k}


def signs(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> dict:
    """[SPECIFICATION] plans/phase-5.md#signs
    숫자 컬럼의 0·음수·양수·NaN·무한대 수, 더미 숫자(sentinel) 수, 끝값에 몰린 더미 의심 값."""
    import polars as pl

    v = values.cast(pl.Float64)
    w = _w(values, weight)
    fin = v.is_finite().fill_null(False)
    is_int = values.dtype.is_integer()
    inf = v.is_infinite()
    sentinels = {}
    for s in opts["sentinels"]:
        if is_int and not isinstance(s, int):
            continue
        if n := _count(fin & (v == s), w):
            sentinels[str(s)] = n

    edges = {}
    fv, fw = _finite(values, weight)
    if len(fv) >= 2:
        q1, q3 = _wq(fv, fw, list(QUARTILES))
        iqr = q3 - q1
        if iqr > 0:
            fwe = _w(fv, fw)
            total = fwe.sum()
            for key, edge, other in (("low", fv.min(), fv.filter(fv > fv.min()).min()), ("high", fv.max(), fv.filter(fv < fv.max()).max())):
                n = _count(fv == edge, fwe)
                if n / total >= opts["sentinel_edge"] and other is not None and abs(other - edge) >= opts["sentinel_gap"] * iqr:
                    edges[key] = [int(edge) if is_int else float(edge), n]
    return {"zero": _count(fin & (v == 0), w), "neg": _count(fin & (v < 0), w), "pos": _count(fin & (v > 0), w),
            "nan": _count(v.is_nan(), w), "pinf": _count(inf & (v > 0), w), "ninf": _count(inf & (v < 0), w),
            "sentinels": sentinels, "edges": edges}


def percentiles(values: "pl.Series", weight: "pl.Series | None", qs: list[float]) -> dict[str, float | None]:
    """[SPECIFICATION] plans/phase-5.md#percentiles
    유한한 값의 분위수들 {str(q): 값}(가중치가 있으면 누적 가중치 규칙, 없으면 선형 보간)."""
    fv, fw = _finite(values, weight)
    if len(fv) == 0:
        return {str(q): None for q in qs}
    return dict(zip(map(str, qs), map(float, _wq(fv, fw, qs))))


def outliers(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> dict:
    """[SPECIFICATION] plans/phase-5.md#outliers
    Z-Score(±z_k σ)와 MAD 기반 수정 Z-Score(> mad_k) 이상치 수와 비중."""
    import polars as pl

    fv, fw = _finite(values, weight)
    if len(fv) < 2:
        return {"z": None, "mad": None}
    wf = _w(fv, fw)
    total = float(wf.sum())

    def hits(mask: "pl.Series") -> tuple[int, float]:
        raw = float((mask.cast(pl.Float64) * wf).sum())
        return round(raw), raw / total

    m = moments(values, weight)
    if not m["sd"]:
        z = {"k": opts["z_k"], "rows": None, "share": None, "mean": m["mean"], "sd": m["sd"]}
    else:
        rows, share = hits((fv - m["mean"]).abs() / m["sd"] > opts["z_k"])
        z = {"k": opts["z_k"], "rows": rows, "share": share, "mean": m["mean"], "sd": m["sd"]}

    med = _wq(fv, fw, [0.5])[0]
    dev = (fv - med).abs()
    mad = _wq(dev, fw, [0.5])[0]
    if mad == 0:
        mad_out = {"k": opts["mad_k"], "rows": None, "share": None, "median": med, "mad": 0.0}
    else:
        rows, share = hits(MAD_C * dev / mad > opts["mad_k"])
        mad_out = {"k": opts["mad_k"], "rows": rows, "share": share, "median": med, "mad": mad}
    return {"z": z, "mad": mad_out}


def moments(values: "pl.Series", weight: "pl.Series | None") -> dict:
    """[SPECIFICATION] plans/phase-5.md#moments
    평균·표준편차·왜도·첨도(초과 첨도)·최솟값."""
    import math

    fv, fw = _finite(values, weight)
    n = len(fv)
    if n == 0:
        return {"mean": None, "sd": None, "skew": None, "kurt": None, "min": None}

    def clean(x: float | None) -> float | None:
        return None if x is None or math.isnan(x) else float(x)

    if fw is None:
        skew = fv.skew(bias=False) if n >= SKEW_MIN_N else None
        kurt = fv.kurtosis(fisher=True, bias=False) if n >= KURT_MIN_N else None
        return {"mean": clean(fv.mean()), "sd": clean(fv.std()) if n > 1 else None, "skew": clean(skew), "kurt": clean(kurt), "min": clean(fv.min())}
    total = float(fw.sum())
    mean = float((fw * fv).sum()) / total
    m2, m3, m4 = (float((fw * (fv - mean) ** k).sum()) / total for k in range(2, KURT_MIN_N + 1))
    skew = m3 / m2 ** SKEW_POWER if m2 != 0 and n >= SKEW_MIN_N else None
    kurt = m4 / m2 ** 2 - KURT_NORMAL if m2 != 0 and n >= KURT_MIN_N else None
    return {"mean": clean(mean), "sd": clean(math.sqrt(m2)), "skew": clean(skew), "kurt": clean(kurt), "min": clean(fv.min())}


def modes(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> dict | None:
    """[SPECIFICATION] plans/phase-5.md#modes
    P1~P99 히스토그램의 봉우리 수(다봉 분포 감지). 값이 적거나 이산이면 None."""
    import math

    import polars as pl

    fv, fw = _finite(values, weight)
    if len(fv) < MODE_MIN_ROWS or fv.n_unique() < MODE_MIN_UNIQUE:
        return None
    lo, hi = _wq(fv, fw, list(MODE_CLIP))
    if hi <= lo:
        return None
    q1, q3 = _wq(fv, fw, list(QUARTILES))
    width = 2 * (q3 - q1) * len(fv) ** MODE_WIDTH_POWER
    bins = MODE_BINS[0] if width <= 0 else min(max(math.ceil((hi - lo) / width), MODE_BINS[0]), MODE_BINS[1])

    hist = (pl.DataFrame({"x": fv, "w": _w(fv, fw)}).filter(pl.col("x").is_between(lo, hi))
            .with_columns(idx=((pl.col("x") - lo) / (hi - lo) * bins).floor().clip(0, bins - 1).cast(pl.Int64))
            .group_by("idx").agg(n=pl.col("w").sum()))
    counts = pl.DataFrame({"idx": pl.int_range(0, bins, eager=True)}).join(hist, on="idx", how="left").sort("idx")["n"].fill_null(0)
    s = counts.rolling_mean(window_size=MODE_SMOOTH, center=True, min_samples=1).to_list()
    total = sum(s)

    peaks = [i for i in range(bins)
             if (i == 0 or s[i] > s[i - 1]) and (i == bins - 1 or s[i] >= s[i + 1]) and s[i] / total >= opts["mode_share"]]
    kept: list[int] = []
    for c in peaks:
        if not kept:
            kept.append(c)
        elif min(s[kept[-1]:c + 1]) <= opts["mode_dip"] * min(s[kept[-1]], s[c]):
            kept.append(c)
        elif s[c] > s[kept[-1]]:
            kept[-1] = c
    return {"count": len(kept), "bins": bins, "peaks": [lo + (i + 0.5) * (hi - lo) / bins for i in kept]}


def benford(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> dict:
    """[SPECIFICATION] plans/phase-5.md#benford
    첫째 자리 숫자 분포와 벤포드 기대 분포의 MAD·카이제곱·판정. 보지 않으면 {"skipped": 까닭}."""
    import math

    import polars as pl

    fv, fw = _finite(values, weight)
    nonzero = fv != 0
    a = fv.filter(nonzero).abs()
    fw = None if fw is None else fw.filter(nonzero)
    if len(a) < opts["benford_min"]:
        return {"skipped": BENFORD_FEW}
    p1, p99 = _wq(a, fw, list(BENFORD_CLIP))
    if p99 / p1 < BENFORD_SPAN:
        return {"skipped": BENFORD_NARROW}

    first = ((pl.col("a") / pl.lit(BENFORD_BASE).pow(pl.col("a").log10().floor()) + BENFORD_EPS).floor()
             .clip(BENFORD_DIGITS[0], BENFORD_DIGITS[-1]).cast(pl.Int64))
    sums = dict(pl.DataFrame({"a": a, "w": _w(a, fw)}).with_columns(d=first).group_by("d").agg(n=pl.col("w").sum()).iter_rows())
    total = float(sum(sums.values()))
    obs = [sums.get(k, 0.0) / total for k in BENFORD_DIGITS]
    expected = [math.log10(1 + 1 / k) for k in BENFORD_DIGITS]
    mad = sum(abs(o - e) for o, e in zip(obs, expected)) / len(BENFORD_DIGITS)
    chi2 = total * sum((o - e) ** 2 / e for o, e in zip(obs, expected))
    if mad <= BENFORD_LEVELS[0]:
        verdict = "close"
    elif mad <= BENFORD_LEVELS[1]:
        verdict = "acceptable"
    elif mad <= opts["benford_mad"]:
        verdict = "marginal"
    else:
        verdict = "nonconform"
    return {"rows": round(total), "digits": obs, "expected": expected, "mad": mad, "chi2": chi2, "verdict": verdict}


def heaping(values: "pl.Series", weight: "pl.Series | None", opts: dict) -> list[dict]:
    """[SPECIFICATION] plans/phase-5.md#heaping
    정수 모양 값이 heap_bases의 배수에 몰린 정도(관측 비중 / 고르면 나올 비중)."""
    import polars as pl

    fv, fw = _finite(values, weight)
    if len(fv) == 0 or (fv != fv.round(0)).any():
        return []
    p1, p99 = _wq(fv, fw, list(BENFORD_CLIP))
    w = _w(fv, fw)
    nz = fv != 0
    total = float((nz.cast(pl.Float64) * w).sum())
    if total == 0:
        return []
    out = []
    for m in opts["heap_bases"]:
        if p99 - p1 < m * HEAP_SPAN:
            continue
        share = float(((nz & (fv % m == 0)).cast(pl.Float64) * w).sum()) / total
        expected = 1 / m
        ratio = share / expected
        out.append({"base": m, "share": share, "expected": expected, "ratio": ratio, "heaped": ratio >= opts["heap_ratio"]})
    return out


def dates(values: "pl.Series", weight: "pl.Series | None", opts: dict, now: dt.datetime) -> dict:
    """[SPECIFICATION] plans/phase-5.md#dates
    날짜·시각 컬럼의 기본값 날짜(epoch_dates) 수와 지금보다 뒤 시점 수."""
    import zoneinfo

    import polars as pl

    w = _w(values, weight)
    timestamp = values.dtype == pl.Datetime
    zone = zoneinfo.ZoneInfo(values.dtype.time_zone) if timestamp and values.dtype.time_zone else None

    epoch = {}
    for e in opts["epoch_dates"]:
        day = dt.date.fromisoformat(e)
        target = dt.datetime.combine(day, dt.time(), tzinfo=zone) if timestamp else day
        if n := _count(values == target, w):
            epoch[e] = n

    if timestamp:
        after = now.astimezone(zone) + dt.timedelta(days=opts["future_days"]) if zone else now + dt.timedelta(days=opts["future_days"])
        label = after.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    else:
        after = now.date() + dt.timedelta(days=opts["future_days"])
        label = after.isoformat()
    hit = values > after
    future = {"rows": _count(hit, w), "max": values.filter(hit.fill_null(False)).max(), "after": label}
    return {"epoch": epoch, "future": future, "timestamp": timestamp}


def reversals(frame: "pl.DataFrame", weight: "pl.Series | None", kinds: dict[str, str], opts: dict) -> list[dict]:
    """[SPECIFICATION] plans/phase-5.md#reversals
    이름 단서로 짝지은 (시작, 끝) 날짜 컬럼마다 끝 < 시작인 행 수(시간 역전)."""
    import polars as pl

    names = [n for n in frame.columns if kinds.get(n) == "date"]
    starts = [n for n in names if any(h in n.lower() for h in opts["time_starts"])]
    ends = [n for n in names if any(h in n.lower() for h in opts["time_ends"])]
    pairs = [(s, e) for s in starts for e in ends if s != e][:TIME_PAIRS_MAX]
    w = _w(frame, weight)
    out = []
    for s, e in pairs:
        first, last = frame[s], frame[e]
        if first.dtype != last.dtype:
            first, last = first.cast(pl.Datetime), last.cast(pl.Datetime)
        out.append({"start": s, "end": e, "rows": _count(last < first, w)})
    return out


def dup_rows(frame: "pl.DataFrame") -> int:
    """[SPECIFICATION] plans/phase-5.md#dup_rows
    표본 안에서 모든 컬럼 값이 앞 행과 같은(빠질) 행 수."""
    cols = [n for n, d in frame.schema.items() if not d.is_nested()]
    return frame.height - frame.select(cols).unique().height if cols else 0


def lines(name: str, kind: str, found: dict, rows: float, weighted: bool, opts: dict) -> list[str]:
    """[SPECIFICATION] plans/phase-5.md#lines
    컬럼 하나의 진단 결과(found)를 품질 점검 줄(글자)들로 만든다."""
    from core import result
    from prep import prep_profile

    pre = "약 " if weighted else ""
    ratio = rows > 0  # rows가 0이면 비율이 든 줄은 내지 않는다
    out: list[str] = []

    if (fm := found.get("formats")) and len(fm["counts"]) >= 2 and ratio:
        parts = ", ".join(f"{label} {n:,}개" for label, n in fm["counts"].items())
        out.append(f"날짜 형식이 {len(fm['counts'])}가지 섞여 있어요: {parts}(섞인 비율 {fm['mixed']:.1%}){SAMPLE_END}")

    sem = found.get("semantic", {})
    if "binary" in sem:
        a, b = sem["binary"]["values"]
        true = sem["binary"]["true"]
        out.append(f"값이 두 가지뿐인 이진 컬럼이에요: {a} / {b}" + (f"(참: {true})" if true is not None else "") + SAMPLE_END)
    if "ordinal" in sem:
        out.append(f"순서가 있는 값으로 보여요: {' < '.join(sem['ordinal']['order'])}{SAMPLE_END}")
    if "hash" in sem and ratio:
        out.append(f"{HASH_LABELS[sem['hash']['shape']]} 모양의 값이 {sem['hash']['share']:.1%}예요. 묶기·집계 대상에서 빼요{SAMPLE_END}")

    if (js := found.get("json")) and js["objects"] + js["arrays"] > 0 and ratio:
        out.append(f"JSON으로 읽히는 값이 {pre}{js['objects'] + js['arrays']:,}개({js['share']:.1%}: 객체 {js['objects']:,} · 배열 {js['arrays']:,})예요{SAMPLE_END}")

    if tx := found.get("text"):
        if tx["ws_only"] > 0:
            out.append(f"공백·탭·줄바꿈만 있는 값이 {pre}{tx['ws_only']:,}개예요{SAMPLE_END}")
        if tx["tab_nl"] > 0:
            out.append(f"탭·줄바꿈이 든 값이 {pre}{tx['tab_nl']:,}개예요{SAMPLE_END}")
        if tx["markers"]:
            out.append("더한 빈 값 표시가 있어요: " + ", ".join(f'"{m}" {n:,}개' for m, n in tx["markers"].items()) + SAMPLE_END)
        if tx["cases"]:
            out.append("대소문자만 다른 값: " + "; ".join("·".join(f"{v}({n:,})" for v, n in group) for group in tx["cases"]) + SAMPLE_END)
        ch = tx["chars"]
        if ch["control"] > 0:
            out.append(f"보이지 않는 제어 문자가 든 값이 {pre}{ch['control']:,}개예요{SAMPLE_END}")
        if ch["broken"] > 0:
            out.append(f"깨진 글자(인코딩)로 보이는 값이 {pre}{ch['broken']:,}개예요{SAMPLE_END}")
        if ch["control"] > 0 or ch["broken"] > 0:
            out[-1] += f" 예: {' | '.join(ch['examples'])}"

    if sg := found.get("signs"):
        if ratio and sg["zero"] / rows >= opts["zero_share"]:
            out.append(f"0이 {pre}{sg['zero']:,}개({sg['zero'] / rows:.1%})로 많아요. 0이 많은(zero-inflated) 분포예요{SAMPLE_END}")
        if sg["neg"] > 0 and (any(h in name.lower() for h in opts["nonneg_hints"]) or prep_profile.money(name)):
            out.append(f"음수가 나올 수 없어 보이는 컬럼인데 음수가 {pre}{sg['neg']:,}개예요{SAMPLE_END}")
        if sg["nan"] + sg["pinf"] + sg["ninf"] > 0:
            out.append(f"NaN {sg['nan']:,}개, 무한대 +{sg['pinf']:,}개 · −{sg['ninf']:,}개가 있어요. 빈 값 수에 들어 있어요{SAMPLE_END}")
        if sg["sentinels"]:
            out.append("더미 값으로 보이는 숫자가 있어요: " + ", ".join(f"{v} {n:,}개" for v, n in sg["sentinels"].items()) + SAMPLE_END)
        for key, word in (("low", "작은"), ("high", "큰")):
            if key in sg["edges"] and ratio:
                v, n = sg["edges"][key]
                out.append(f"가장 {word} 값 {v}에 {pre}{n:,}개({n / rows:.1%})가 몰려 있고 다음 값과 멀리 떨어져 있어요. 더미 값인지 보세요{SAMPLE_END}")

    if ol := found.get("outliers"):
        if (z := ol["z"]) and z["rows"] and ratio:
            out.append(f"평균에서 표준편차의 {z['k']:g}배 밖 값이 {pre}{z['rows']:,}개({z['share']:.1%})예요(Z-Score, 표본 기준).")
        if (mad := ol["mad"]) and mad["rows"] and ratio:
            out.append(f"수정 Z-Score(MAD)가 {mad['k']:g}보다 큰 값이 {pre}{mad['rows']:,}개({mad['share']:.1%})예요{SAMPLE_END}")

    if (skew := found.get("moments", {}).get("skew")) is not None:
        if skew > opts["skew_log"]:
            out.append(f"오른쪽 꼬리가 길어요(왜도 {skew:.2f}){SAMPLE_END}")
        elif skew < -opts["skew_log"]:
            out.append(f"왼쪽 꼬리가 길어요(왜도 {skew:.2f}){SAMPLE_END}")

    if (md := found.get("modes")) and md["count"] >= 2:
        out.append(f"봉우리가 {md['count']}개인 분포예요. 성격이 다른 집단이 섞였을 수 있어요{SAMPLE_END}")

    if (bf := found.get("benford")) and bf.get("verdict") == "nonconform":
        out.append(f"첫째 자리 숫자 분포가 벤포드 법칙과 달라요(MAD {bf['mad']:.4f} > {opts['benford_mad']}). 사람이 만든 값이나 조작이 섞였는지 보세요{SAMPLE_END}")

    for h in found.get("heaping", []):
        if h["heaped"] and ratio:
            out.append(f"{h['base']}의 배수에 값이 몰려 있어요({h['share']:.1%}, 고르게 퍼졌다면 {h['expected']:.1%}){SAMPLE_END}")

    if dd := found.get("dates"):
        if dd["epoch"]:
            out.append("기본값으로 채운 듯한 날짜가 있어요: " + ", ".join(f"{e} {pre}{n:,}개" for e, n in dd["epoch"].items()) + SAMPLE_END)
        if (fu := dd["future"])["rows"] > 0:
            out.append(f"{fu['after']} 뒤 시점이 {pre}{fu['rows']:,}개예요(가장 늦은 값 {result.plain(fu['max'])}){SAMPLE_END}")
    return out


def suggest(name: str, kind: str, found: dict, opts: dict) -> list[dict]:
    """[SPECIFICATION] plans/phase-5.md#suggest
    컬럼 하나의 진단 결과로 전처리 추천 [{text, why, ops}]을 만든다."""
    out: list[dict] = []
    replace = lambda pairs: {"replace": {"how": "to_null", "pairs": pairs}}  # noqa: E731

    if tx := found.get("text"):
        if tx["ws_only"] > 0:
            out.append({"text": "공백만 있는 값을 빈 값으로", "why": f"공백·탭·줄바꿈만 있는 값이 {tx['ws_only']:,}개 있어요.", "ops": {"trim": True, "nulls": [""]}})
        if tx["markers"]:
            why = "빈 값으로 볼 표시가 있어요: " + ", ".join(f'"{m}" {n:,}개' for m, n in tx["markers"].items())
            out.append({"text": "더한 빈 값 표시를 빈 값으로", "why": why, "ops": {"nulls": list(tx["markers"])}})

    if (js := found.get("json")) and js["share"] >= opts["json_share"]:
        if js["arrays"] > js["objects"]:  # 배열이 대부분이면 행으로, 아니면(객체) 컬럼으로
            out.append({"text": "목록을 행으로 펼치기", "why": f"값의 {js['share']:.0%}가 JSON이고 대부분 목록(배열)이에요.", "ops": {"unnest": True}})
        else:
            out.append({"text": "JSON 펼치기", "why": f"값의 {js['share']:.0%}가 JSON이에요.", "ops": {"expand": True}})

    if kind in ("int", "float"):
        sg = found["signs"]
        dummies = [s for s in opts["sentinels"] if str(s) in sg["sentinels"]] + [sg["edges"][k][0] for k in ("low", "high") if k in sg["edges"]]
        dummies = [v for i, v in enumerate(dummies) if v not in dummies[:i]]
        if dummies:
            out.append({"text": "더미 값을 빈 값으로", "why": "더미로 보이는 값이 있어요: " + ", ".join(str(v) for v in dummies) + ".", "ops": replace([[dummies, None]])})
        mo = found["moments"]
        if mo["skew"] is not None and mo["skew"] > opts["skew_log"] and mo["min"] is not None and mo["min"] >= 0 and sg["neg"] == 0:
            out.append({"text": "로그 변환", "why": f"왜도가 {mo['skew']:.2f}로 오른쪽 꼬리가 길어요.", "ops": {"scale": "log"}})

    if kind == "date" and (epoch := list(found["dates"]["epoch"])):
        listed = [f"{e} 00:00:00" for e in epoch] if found["dates"]["timestamp"] else epoch
        out.append({"text": "기본값 날짜를 빈 값으로", "why": "기본값으로 보이는 날짜가 있어요: " + ", ".join(epoch) + ".", "ops": replace([[listed, None]])})
    return out


def diagnose(frame: "pl.DataFrame", weight: "pl.Series | None", kinds: dict[str, str], opts: dict, jsons: dict[str, dict], now: dt.datetime) -> dict:
    """[SPECIFICATION] plans/phase-5.md#diagnose
    표본(frame, 도움 컬럼 없음) 전체의 표본 진단을 컬럼 종류별로 모아 {rows, weighted, columns, lines, suggest, table, table_lines}로 돌려준다."""
    from core import result
    from prep import prep_profile

    rows = float(weight.cast(float).sum()) if weight is not None else frame.height
    weighted = weight is not None
    pre = "약 " if weighted else ""
    markers = [*prep_profile.MARKERS, *opts["markers_extra"]]
    columns: dict = {}
    for name in frame.columns:
        kind = kinds[name]
        v = frame[name]
        found: dict = {"kind": kind}
        if kind == "string":
            if (fm := formats(v, weight, markers)) is not None:
                found["formats"] = fm
            found["semantic"] = semantic(v, weight, opts)
            if (js := jsons.get(name)) is not None:
                found["json"] = js
            found["text"] = text_quality(v, weight, opts)
            found["entropy"] = entropy(v, weight)
        elif kind in ("int", "float"):
            if kind == "int":
                found["semantic"] = semantic(v, weight, opts)
            found |= {"signs": signs(v, weight, opts), "percentiles": percentiles(v, weight, opts["percentiles"]),
                      "outliers": outliers(v, weight, opts), "moments": moments(v, weight), "modes": modes(v, weight, opts),
                      "benford": benford(v, weight, opts), "heaping": heaping(v, weight, opts)}
            if kind == "int":
                found["entropy"] = entropy(v, weight)
        elif kind == "bool":
            found["entropy"] = entropy(v, weight)
        elif kind == "date":
            dd = dates(v, weight, opts, now)
            dd["future"]["max"] = result.plain(dd["future"]["max"])
            found["dates"] = dd

        tags = list(found.get("semantic", {}))
        if "json" in found and found["json"]["share"] >= opts["json_share"]:
            tags.append("json")
        if "signs" in found and rows and found["signs"]["zero"] / rows >= opts["zero_share"]:
            tags.append("zero_inflated")
        if (skew := found.get("moments", {}).get("skew")) is not None and abs(skew) > opts["skew_log"]:
            tags.append("skewed")
        if (md := found.get("modes")) and md["count"] >= 2:
            tags.append("multimodal")
        if found.get("benford", {}).get("verdict") == "nonconform":
            tags.append("benford")
        if any(h["heaped"] for h in found.get("heaping", [])):
            tags.append("heaping")
        found["tags"] = tags
        columns[name] = found

    table = {"dup_rows": dup_rows(frame), "reversals": reversals(frame, weight, kinds, opts)}
    table_lines = []
    if table["dup_rows"] > 0:
        table_lines.append(f"모든 컬럼 값이 같은 행이 표본 안에 {table['dup_rows']:,}개 더 있어요(표본 기준). 전체는 [전체 중복 행 세기]로 세요.")
    table_lines += [f"'{r['end']}'가 '{r['start']}'보다 앞선 행이 {pre}{r['rows']:,}개예요(시간 역전, 표본 기준)." for r in table["reversals"] if r["rows"] > 0]
    return {"rows": round(rows), "weighted": weighted, "columns": columns,
            "lines": {n: lines(n, f["kind"], f, rows, weighted, opts) for n, f in columns.items()},
            "suggest": {n: suggest(n, f["kind"], f, opts) for n, f in columns.items()},
            "table": table, "table_lines": table_lines}


def quasi_keys(frame: "pl.DataFrame", kinds: dict[str, str], opts: dict) -> list[dict]:
    """[SPECIFICATION] plans/phase-5.md#quasi_keys
    혼자서는 중복이 있지만 2~qi_size개를 묶으면 표본에서 유일한 컬럼 조합(의사 식별자 후보)."""
    import itertools

    sizes = {c: frame[c].drop_nulls().n_unique() for c in frame.columns if kinds.get(c) in QI_KINDS}
    cands = [c for c, n in sizes.items() if n >= 2 and not (frame[c].null_count() == 0 and frame[c].n_unique() == frame.height)]
    cands = sorted(cands, key=lambda c: -sizes[c])[:opts["qi_columns"]]  # 안정 정렬: 같으면 컬럼 차례
    found: list[dict] = []
    for size in range(2, opts["qi_size"] + 1):
        for combo in itertools.combinations(cands, size):
            if any(set(f["columns"]) <= set(combo) for f in found):
                continue
            sub = frame.select(combo)
            if sub.null_count().sum_horizontal()[0] == 0 and sub.n_unique() == frame.height:
                found.append({"columns": list(combo), "rows": frame.height})
                if len(found) >= QI_TOP:
                    return found
    return found


def key_dupes(t: "ibis.expr.types.Table", columns: list[str], top: int) -> dict:
    """[SPECIFICATION] plans/phase-5.md#key_dupes
    표 전체에서 columns 기준 중복 묶음 수·빠질 행 수·중복 횟수 분포·가장 많이 겹친 키(조합 키 확인·키 중복·완전 중복 행)."""
    from core import parse, prep_ops, result
    from prep import prep_profile

    if not columns or len(set(columns)) != len(columns):
        raise ValueError("중복을 셀 컬럼을 하나 이상, 겹치지 않게 고르세요.")
    for c in columns:
        parse.check_name(c, t.columns)
    if DUP_COUNT in columns:
        raise ValueError(f"'{DUP_COUNT}'는 중복을 셀 때 쓰는 이름이라 컬럼으로 고를 수 없어요.")

    q1 = prep_ops.stats(t, rows=t.count(), null_rows=prep_profile.count(ibis.or_(*[t[c].isnull() for c in columns])))
    g = t.group_by(columns).aggregate(**{DUP_COUNT: t.count()})
    big = g.filter(g[DUP_COUNT] > 1)
    q2 = prep_ops.stats(big, groups=big.count(), extra=(big[DUP_COUNT] - 1).sum(), max=big[DUP_COUNT].max())

    sizes = big.group_by(DUP_COUNT).aggregate(groups=big.count()).order_by(DUP_COUNT).limit(DUP_SIZES + 1).to_pyarrow().to_pylist()
    top_rows = big.order_by([ibis.desc(DUP_COUNT), *columns]).limit(top).to_pyarrow().to_pylist()

    groups = q2["groups"] or 0
    null_rows = q1["null_rows"] or 0
    return {"columns": columns, "rows": q1["rows"], "null_rows": null_rows, "groups": groups, "extra": q2["extra"] or 0, "max": q2["max"],
            "sizes": [{"n": r[DUP_COUNT], "groups": r["groups"]} for r in sizes[:DUP_SIZES]], "more_sizes": len(sizes) > DUP_SIZES,
            "top": [{"values": [result.plain(r[c]) for c in columns], "n": r[DUP_COUNT]} for r in top_rows],
            "unique": groups == 0 and null_rows == 0}


def time_gaps(t: "ibis.expr.types.Table", column: str, unit: str, opts: dict) -> dict:
    """[SPECIFICATION] plans/phase-5.md#time_gaps
    날짜·시각 컬럼의 처음~마지막 사이에서 값이 하나도 없는 일·시간 구간을 이어진 묶음으로 찾는다."""
    import polars as pl

    from core import parse, prep_ops, result, source

    parse.check_name(column, t.columns)
    if unit not in GAP_UNITS:
        raise ValueError("단위(unit)는 day 또는 hour예요.")
    dtype = t[column].type()
    if not (dtype.is_date() or dtype.is_timestamp()):
        raise ValueError(f"'{column}'은 날짜 컬럼이 아니에요.")
    if dtype.is_date() and unit == "hour":
        raise ValueError("날짜(시간 없음) 컬럼은 시간 단위로 볼 수 없어요.")

    col = t[column]
    b = col if dtype.is_date() and unit == "day" else result.local_time(col).truncate(GAP_UNITS[unit])  # 분석·지표처럼 설정 시간대의 날짜로 자른다(UTC 날짜로 묶지 않게)
    if not source.compiles(t.select(b=b)):
        raise ValueError("이 연결은 날짜를 일·시간 단위로 자르지 못해요.")

    ends = prep_ops.stats(t, lo=b.min(), hi=b.max())
    lo, hi = ends["lo"], ends["hi"]
    if lo is None or hi is None:
        raise ValueError(f"'{column}' 컬럼에 값이 없어요.")
    step = GAP_STEPS[unit]
    buckets = (hi - lo) // step + 1
    if buckets > opts["gap_max_buckets"]:
        raise ValueError(f"구간이 {buckets:,}개로 너무 많아요({opts['gap_max_buckets']:,}개까지). 단위를 일로 바꾸거나 진단 기준을 늘리세요.")

    found = pl.from_arrow(t.filter(col.notnull()).select(b=b).distinct().to_pyarrow())
    if dtype.is_date() and unit == "day":
        full = pl.date_range(lo, hi, step, eager=True)
    else:
        full = pl.datetime_range(lo, hi, step, eager=True, time_unit=found["b"].dtype.time_unit, time_zone=found["b"].dtype.time_zone)
    missing = full.filter(~full.is_in(found["b"]))

    runs = (missing.to_frame("d").with_columns(run=(pl.col("d").diff() != step).fill_null(True).cum_sum())
            .group_by("run").agg(start=pl.col("d").min(), end=pl.col("d").max(), buckets=pl.len())
            .sort(["buckets", "start"], descending=[True, False]))
    return {"column": column, "unit": unit, "first": lo, "last": hi, "buckets": buckets, "present": found.height, "missing": missing.len(),
            "runs": runs.head(opts["gap_list"]).select("start", "end", "buckets").to_dicts(), "more": max(runs.height - opts["gap_list"], 0)}


def orphans(fact: "ibis.expr.types.Table", dim: "ibis.expr.types.Table", pairs: list[list[str]], top: int) -> dict:
    """[SPECIFICATION] plans/phase-5.md#orphans
    팩트 표의 키 중 차원 표에 없는 키(미매핑)의 행 수·키 수·비율과 많은 순 키 목록(anti join)."""
    from core import parse, prep_ops, result
    from prep import prep_profile

    if not (isinstance(pairs, list) and 1 <= len(pairs) <= ORPHAN_MAX_PAIRS
            and all(isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in pairs)):
        raise ValueError(PAIRS_ERROR)
    for a, b in pairs:
        parse.check_name(a, fact.columns)
        parse.check_name(b, dim.columns)
        ka, kb = parse.kind(fact[a].type()), parse.kind(dim[b].type())
        if ka != kb and not (ka in ("int", "float") and kb in ("int", "float")):
            raise ValueError(f"'{a}'({parse.type_info(fact[a].type())[0]})와 '{b}'({parse.type_info(dim[b].type())[0]})의 타입이 달라 견줄 수 없어요.")

    keys = [a for a, _ in pairs]
    has = fact.filter([fact[a].notnull() for a in keys])
    lost = has.anti_join(dim, [has[a] == dim[b] for a, b in pairs])

    q1 = prep_ops.stats(fact, rows=fact.count(), null_keys=prep_profile.count(ibis.or_(*[fact[a].isnull() for a in keys])))
    gf = has.group_by(keys).aggregate(**{DUP_COUNT: has.count()})
    q2 = prep_ops.stats(gf, keys=gf.count(), key_rows=gf[DUP_COUNT].sum())
    gl = lost.group_by(keys).aggregate(**{DUP_COUNT: lost.count()})
    q3 = prep_ops.stats(gl, orphan_keys=gl.count(), orphan_rows=gl[DUP_COUNT].sum())
    top_rows = gl.order_by([ibis.desc(DUP_COUNT), *keys]).limit(top).to_pyarrow().to_pylist()

    key_rows, n_keys = q2["key_rows"] or 0, q2["keys"] or 0
    orphan_rows, orphan_keys = q3["orphan_rows"] or 0, q3["orphan_keys"] or 0
    return {"pairs": pairs, "rows": q1["rows"], "null_keys": q1["null_keys"] or 0, "key_rows": key_rows, "keys": n_keys,
            "orphan_rows": orphan_rows, "orphan_keys": orphan_keys,
            "row_share": orphan_rows / key_rows if key_rows else None, "key_share": orphan_keys / n_keys if n_keys else None,
            "top": [{"values": [result.plain(r[a]) for a in keys], "n": r[DUP_COUNT]} for r in top_rows]}
