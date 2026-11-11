"""(Phase 5) 데이터 프로파일링·품질 진단: 표본 진단(prep_diag의 Polars 함수), 전체 진단(키 중복·빈 시간 구간·참조 무결성), 진단 기준, 라우트.
데이터는 모두 시험 안에서 지어낸다. xfail(strict)은 (수정) 절이 끝나면 지운다(plans/phase-5.md)."""
import datetime as dt
import hashlib
import math
import uuid

import duckdb
import polars as pl
import pyarrow as pa
import pytest
from lazy import ibis

from prep import prep_diag
from prep import prep_profile

NOW = dt.datetime(2026, 11, 4, 12, 0)


@pytest.fixture
def o():
    return prep_diag.options(None)


def backend_table(kind, name, df):
    """같은 데이터를 메모리(DuckDB memtable) 또는 Polars 연결의 표로."""
    return ibis.memtable(df.to_arrow()) if kind == "memory" else ibis.polars.connect().create_table(name, df)


# ---------- 진단 기준 ----------

def test_options_defaults_and_errors():
    o = prep_diag.options(None)
    assert set(o) == set(prep_diag.OPTION_RULES)
    assert {k: o[k] for k in ("iqr_k", "z_k", "mad_k", "skew_log", "qi_size", "benford_mad")} == {"iqr_k": 1.5, "z_k": 3.0, "mad_k": 3.5, "skew_log": 1.0, "qi_size": 3, "benford_mad": 0.015}
    assert o["percentiles"] == [0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999]
    assert o["sentinels"] == [-1, -99, -999, 999999] and o["heap_bases"] == [10, 50, 100]
    assert o["epoch_dates"] == ["1970-01-01", "1900-01-01", "9999-12-31"] and o["markers_extra"] == []
    assert ["n", "y"] in o["bool_pairs"] and ["아니오", "예"] in o["bool_pairs"] and "가입" in o["time_starts"] and ["소", "중", "대"] in o["ordinal_scales"]
    assert prep_diag.options({"bool_pairs": [["No ", "YES"]]})["bool_pairs"] == [["no", "yes"]]  # 사용자가 바꾼 목록(소문자·앞뒤 공백 뺌)
    assert prep_diag.options({}) == o
    o["percentiles"].append(0.5)
    assert prep_diag.options(None)["percentiles"][-1] == 0.999  # 기본값 목록을 같이 쓰지 않는다
    mine = prep_diag.options({"z_k": 2, "percentiles": [0.9, 0.1, 0.9], "markers_extra": ["모름"]})
    assert mine["z_k"] == 2.0 and isinstance(mine["z_k"], float) and mine["percentiles"] == [0.1, 0.9] and mine["markers_extra"] == ["모름"] and mine["iqr_k"] == 1.5
    for bad in ([], "x", {"nope": 1}, {"z_k": 0.5}, {"z_k": "3"}, {"z_k": True}, {"qi_size": 4}, {"qi_size": 2.5}, {"percentiles": []}, {"percentiles": [0, 0.5]},
                {"percentiles": [1.0]}, {"epoch_dates": ["2026-13-01"]}, {"markers_extra": [""]}, {"markers_extra": ["x" * 51]}, {"ordinal_scales": [["하"]]},
                {"sentinels": [True]}, {"heap_bases": [1]}, {"heap_bases": []}, {"bool_pairs": [["a"]]}, {"bool_pairs": [["a", "A"]]}, {"bool_pairs": [["a", ""]]}):
        with pytest.raises(ValueError):
            prep_diag.options(bad)


def test_stamp():
    a = prep_diag.stamp(prep_diag.options(None))
    assert a == prep_diag.stamp(prep_diag.options({})) and len(a) == 16
    assert a != prep_diag.stamp(prep_diag.options({"z_k": 2.5}))


# ---------- A. 스키마·시맨틱 ----------

def test_formats_mixed():
    values = pl.Series(["2026-01-01"] * 6 + ["01/02/2026"] * 3 + ["20260103", "abc", None, "N/A", " 2026-01-04 "])
    got = prep_diag.formats(values, None, prep_profile.MARKERS)
    assert got["counts"] == {"YYYY-MM-DD": 7, "MM/DD/YYYY 또는 DD/MM/YYYY": 3, "YYYYMMDD": 1}
    assert (got["matched"], got["rest"]) == (11, 12) and got["mixed"] == pytest.approx(4 / 11)
    heavy = prep_diag.formats(values, pl.Series([2.0] * len(values)), prep_profile.MARKERS)
    assert heavy["counts"] == {"YYYY-MM-DD": 14, "MM/DD/YYYY 또는 DD/MM/YYYY": 6, "YYYYMMDD": 2} and (heavy["matched"], heavy["rest"]) == (22, 24)
    assert prep_diag.formats(pl.Series(["2026-01-01", "2026-01-02"]), None, []) == {"counts": {"YYYY-MM-DD": 2}, "matched": 2, "rest": 2, "mixed": 0.0}
    assert prep_diag.formats(pl.Series(["a", "b", "2026-01-01"]), None, []) is None
    assert prep_diag.formats(pl.Series([None, None], dtype=pl.String), None, []) is None


def test_semantic_binary_ordinal_hash(o):
    s = lambda v: prep_diag.semantic(pl.Series(v), None, o)
    assert s(["Y", "N", "Y", None, "Y"])["binary"] == {"values": ["N", "Y"], "true": "Y"}
    assert s([0, 1, 1, 0])["binary"] == {"values": [0, 1], "true": 1}
    assert s(["남", "여", "남"])["binary"] == {"values": ["남", "여"], "true": None}
    assert prep_diag.semantic(pl.Series(["Y", "N"]), None, prep_diag.options({"bool_pairs": []}))["binary"]["true"] is None  # 쌍은 진단 기준(bool_pairs)에서 온다
    assert "binary" not in s(["a", "b", "c"])
    assert s(["Gold", "Bronze", "silver", "Gold"])["ordinal"] == {"scale": "bronze/silver/gold/platinum/diamond", "order": ["Bronze", "silver", "Gold"]}
    assert s(["1단계", "3단계", "2단계", "10단계"])["ordinal"] == {"scale": "번호 순서", "order": ["1단계", "2단계", "3단계", "10단계"]}
    assert "ordinal" not in s(["1단계", "2등급"]) and "ordinal" not in s(["red", "blue"])
    ids = [str(uuid.uuid5(uuid.NAMESPACE_DNS, f"u{i}")) for i in range(19)]
    assert s(ids + ["x"])["hash"] == {"shape": "uuid", "share": pytest.approx(0.95)}
    assert "hash" not in s(ids[:18] + ["x", "y"])
    assert s([hashlib.md5(str(i).encode()).hexdigest() for i in range(10)])["hash"]["shape"] == "md5"
    assert s(["a1B2c3D4e5F6g7H8i9J" + str(i) for i in range(10)])["hash"] == {"shape": "token", "share": 1.0}
    assert "hash" not in s(["abcdefghijklmnopqrstu", "abcdefghijklmnopqrstv"])  # 숫자가 없는 같은 길이 글자는 토큰이 아니다


def test_hashed_columns(o):
    ids = [str(uuid.uuid5(uuid.NAMESPACE_DNS, f"u{i}")) for i in range(20)]
    f = pl.DataFrame({"n": list(range(20)), "uid": ids, "city": ["서울"] * 20, "tok": ["a1B2c3D4e5F6g7H8i9J" + str(i % 10) for i in range(20)]})
    kinds = {"n": "int", "uid": "string", "city": "string", "tok": "string"}
    assert prep_diag.hashed_columns(f, None, kinds, o) == ["uid", "tok"]
    assert prep_diag.hashed_columns(f, None, kinds, prep_diag.options({"hash_share": 1.0})) == ["uid", "tok"]
    assert prep_diag.hashed_columns(f.select("n", "city"), None, {"n": "int", "city": "string"}, o) == []


def test_json_counts():
    t = ibis.memtable(pa.table({"j": ['{"a":1}', "[1,2]", "{bad", "x", None, ' {"b":2} '], "w": [1.0, 1.0, 1.0, 1.0, 1.0, 3.0]}))
    plain = prep_diag.json_counts(t, ["j"], None)["j"]
    assert (plain["objects"], plain["arrays"], plain["rest"]) == (2, 1, 5) and plain["share"] == pytest.approx(0.6)
    heavy = prep_diag.json_counts(t, ["j"], "w")["j"]
    assert (heavy["objects"], heavy["arrays"], heavy["rest"]) == (4, 1, 7) and heavy["share"] == pytest.approx(5 / 7)
    assert prep_diag.json_counts(t, [], None) == {}


# ---------- B·D. 글자 ----------

def test_text_quality():
    o = prep_diag.options({"markers_extra": ["모름"]})
    values = pl.Series(["", " ", "\t", "a\nb", "iOS", "ios", "IOS", "ios", "Android", "x\x00y", "Ã©t", "모름", None])
    got = prep_diag.text_quality(values, None, o)
    assert (got["ws_only"], got["tab_nl"], got["markers"]) == (2, 2, {"모름": 1})
    assert got["length"] == {"mean": pytest.approx(32 / 12), "bytes_min": 0, "bytes_max": 7, "bytes_mean": pytest.approx(38 / 12)}
    assert got["cases"] == [[["ios", 2], ["IOS", 1], ["iOS", 1]]]
    assert got["chars"] == {"control": 1, "broken": 1, "examples": ["x□y", "Ã©t"]}


def test_entropy():
    two = prep_diag.entropy(pl.Series(["a", "a", "b", "b"]), None)
    assert two == {"bits": pytest.approx(1.0), "norm": pytest.approx(1.0), "gini": pytest.approx(0.5), "k": 2}
    heavy = prep_diag.entropy(pl.Series(["a", "a", "b", "b"]), pl.Series([3.0, 3.0, 1.0, 1.0]))
    assert heavy["bits"] == pytest.approx(-(0.75 * math.log2(0.75) + 0.25 * math.log2(0.25))) and heavy["gini"] == pytest.approx(0.375)
    assert prep_diag.entropy(pl.Series(["a"] * 3), None) == {"bits": 0.0, "norm": 0.0, "gini": 0.0, "k": 1}
    assert prep_diag.entropy(pl.Series([None, None], dtype=pl.String), None) is None
    assert prep_diag.entropy(pl.Series([str(i) for i in range(prep_diag.ENTROPY_MAX + 1)]), None) is None


# ---------- B. 숫자 ----------

def test_signs_sentinels_edges(o):
    got = prep_diag.signs(pl.Series([0.0, 0.0, -5.0, 3.0, float("nan"), float("inf"), float("-inf"), -999.0, None, 7.0]), None, o)
    assert {k: got[k] for k in ("zero", "neg", "pos", "nan", "pinf", "ninf")} == {"zero": 2, "neg": 2, "pos": 2, "nan": 1, "pinf": 1, "ninf": 1}
    assert got["sentinels"] == {"-999": 1} and got["edges"] == {"low": [-999.0, 1]}
    ints = prep_diag.signs(pl.Series([1, 2, 2, -1, 999999]), None, o)
    assert ints["sentinels"] == {"-1": 1, "999999": 1} and (ints["nan"], ints["pinf"], ints["neg"]) == (0, 0, 1)
    assert ints["edges"] == {"low": [-1, 1], "high": [999999, 1]}
    heavy = prep_diag.signs(pl.Series([0, 5, 6]), pl.Series([2.0, 1.0, 1.0]), o)
    assert (heavy["zero"], heavy["pos"]) == (2, 2)


def test_percentiles():
    v = pl.Series([float(i) for i in range(1, 101)])
    assert prep_diag.percentiles(v, None, [0.01, 0.5, 0.99]) == {"0.01": pytest.approx(1.99), "0.5": pytest.approx(50.5), "0.99": pytest.approx(99.01)}
    assert prep_diag.percentiles(pl.Series([1.0, 2.0, 3.0, 4.0]), pl.Series([1.0, 1.0, 1.0, 7.0]), [0.1, 0.25, 0.5]) == {"0.1": 1.0, "0.25": 3.0, "0.5": 4.0}
    assert prep_diag.percentiles(pl.Series([1.0, float("nan"), 3.0, float("inf")]), None, [0.5]) == {"0.5": 2.0}
    assert prep_diag.percentiles(pl.Series([None], dtype=pl.Float64), None, [0.5]) == {"0.5": None}


def test_outliers_z_and_mad(o):
    v = pl.Series([float(i) for i in range(1, 21)] + [100.0])
    got = prep_diag.outliers(v, None, o)
    assert (got["z"]["rows"], got["z"]["k"]) == (1, 3.0) and got["z"]["share"] == pytest.approx(1 / 21)
    assert got["z"]["mean"] == pytest.approx(310 / 21) and got["z"]["sd"] == pytest.approx(v.std())
    assert (got["mad"]["rows"], got["mad"]["median"], got["mad"]["mad"], got["mad"]["k"]) == (1, 11.0, 5.0, 3.5)
    assert prep_diag.outliers(pl.Series([5.0] * 10 + [6.0]), None, o)["mad"] == {"k": 3.5, "rows": None, "share": None, "median": 5.0, "mad": 0.0}
    assert prep_diag.outliers(pl.Series([2.0] * 5), None, o)["z"]["rows"] is None
    assert prep_diag.outliers(pl.Series([1.0]), None, o) == {"z": None, "mad": None}


def test_moments():
    v = pl.Series([1.0, 2.0, 3.0, 4.0, 100.0])
    got = prep_diag.moments(v, None)
    assert got["mean"] == pytest.approx(22.0) and got["sd"] == pytest.approx(v.std()) and got["min"] == 1.0
    assert got["skew"] == pytest.approx(v.skew(bias=False)) and got["kurt"] == pytest.approx(v.kurtosis(fisher=True, bias=False)) and got["skew"] > 1
    w = prep_diag.moments(pl.Series([1.0, 2.0, 3.0]), pl.Series([1.0, 1.0, 2.0]))
    assert w["mean"] == pytest.approx(2.25) and w["sd"] == pytest.approx(math.sqrt(0.6875))
    assert w["skew"] == pytest.approx(-0.28125 / 0.6875 ** 1.5) and w["kurt"] is None  # 값 3개: 가중이어도 n < 4면 kurt None(phase-5 moments)
    w4 = prep_diag.moments(pl.Series([1.0, 2.0, 3.0, 6.0]), pl.Series([1.0, 1.0, 2.0, 0.0]))  # 가중치 0인 값도 n에 든다
    assert w4["kurt"] == pytest.approx(0.76953125 / 0.6875 ** 2 - 3)
    assert prep_diag.moments(pl.Series([1.0, 2.0]), None)["skew"] is None


def test_modes(o):
    two = prep_diag.modes(pl.Series([float(10 + i % 5) for i in range(200)] + [float(50 + i % 5) for i in range(200)]), None, o)
    assert (two["count"], two["bins"]) == (2, 10) and len(two["peaks"]) == 2
    assert prep_diag.modes(pl.Series([float(i % 20) for i in range(400)]), None, o)["count"] == 1
    assert prep_diag.modes(pl.Series([1.0, 2.0] * 50), None, o) is None  # 이산(서로 다른 값 2개)
    assert prep_diag.modes(pl.Series([float(i) for i in range(20)]), None, o) is None  # 값이 MODE_MIN_ROWS보다 적다


def test_benford(o):
    good = prep_diag.benford(pl.Series([10 ** (k * 4 / 1000) for k in range(1000)]), None, o)
    assert good["verdict"] == "close" and good["mad"] <= 0.006 and len(good["digits"]) == 9 and good["expected"][0] == pytest.approx(math.log10(2)) and good["rows"] == 1000
    flat = prep_diag.benford(pl.Series([float(d * 10 ** e) for e in range(3) for d in range(1, 10)] * 20), None, o)
    assert flat["verdict"] == "nonconform" and flat["mad"] > 0.015
    assert prep_diag.benford(pl.Series([5.0] * 200), None, o) == {"skipped": prep_diag.BENFORD_NARROW}
    assert prep_diag.benford(pl.Series([float(i) for i in range(1, 51)]), None, o) == {"skipped": prep_diag.BENFORD_FEW}


def test_heaping(o):
    ages = prep_diag.heaping(pl.Series(list(range(20, 80)) + [30, 40, 50, 60] * 15), None, o)
    assert ages == [{"base": 10, "share": pytest.approx(0.55), "expected": 0.1, "ratio": pytest.approx(5.5), "heaped": True}]
    even = prep_diag.heaping(pl.Series(list(range(1000))), None, o)
    assert [h["base"] for h in even] == [10, 50, 100] and not any(h["heaped"] for h in even)
    assert prep_diag.heaping(pl.Series([0.5, 1.5, 22.5] * 10), None, o) == []


# ---------- D. 날짜 ----------

def test_dates_epoch_and_future(o):
    days = prep_diag.dates(pl.Series([dt.date(1970, 1, 1), dt.date(2026, 5, 1), dt.date(2030, 1, 1), dt.date(9999, 12, 31), None]), None, o, NOW)
    assert days == {"epoch": {"1970-01-01": 1, "9999-12-31": 1}, "future": {"rows": 2, "max": dt.date(9999, 12, 31), "after": "2026-11-04"}, "timestamp": False}
    times = prep_diag.dates(pl.Series([dt.datetime(1970, 1, 1), dt.datetime(1970, 1, 1, 5), dt.datetime(2027, 1, 1), None]), None, o, NOW)
    assert times == {"epoch": {"1970-01-01": 1}, "future": {"rows": 1, "max": dt.datetime(2027, 1, 1), "after": "2026-11-04 12:00:00"}, "timestamp": True}
    later = prep_diag.dates(pl.Series([dt.date(2026, 12, 1)]), None, prep_diag.options({"future_days": 30}), NOW)
    assert later["future"] == {"rows": 0, "max": None, "after": "2026-12-04"} and later["epoch"] == {}


def test_reversals(o):
    f = pl.DataFrame({"가입일": [dt.date(2026, 1, 1), dt.date(2026, 1, 5), None], "탈퇴일": [dt.date(2026, 1, 3), dt.date(2026, 1, 2), dt.date(2026, 1, 1)], "x": [1, 2, 3]})
    kinds = {"가입일": "date", "탈퇴일": "date", "x": "int"}
    assert prep_diag.reversals(f, None, kinds, o) == [{"start": "가입일", "end": "탈퇴일", "rows": 1}]
    assert prep_diag.reversals(f, pl.Series([1.0, 4.0, 1.0]), kinds, o) == [{"start": "가입일", "end": "탈퇴일", "rows": 4}]
    assert prep_diag.reversals(f.select("x"), None, {"x": "int"}, o) == []


def test_dup_rows():
    assert prep_diag.dup_rows(pl.DataFrame({"a": [1, 1, 2, 1], "b": ["x", "x", "y", "x"]})) == 2
    assert prep_diag.dup_rows(pl.DataFrame({"a": [1, 2], "b": ["x", "x"]})) == 0


# ---------- 모아서 ----------

def frame40():
    return pl.DataFrame({"amt": [-999.0] * 3 + [float(i * 10) for i in range(3, 40)], "s": ["  "] + ["a"] * 39, "d": [dt.date(1970, 1, 1)] + [dt.date(2026, 1, 1)] * 39})


KINDS40 = {"amt": "float", "s": "string", "d": "date"}
JSONS40 = {"s": {"objects": 0, "arrays": 0, "rest": 40, "share": 0.0}}


def test_diagnose_lines_and_suggest(o):
    d = prep_diag.diagnose(frame40(), None, KINDS40, o, JSONS40, NOW)
    assert d["rows"] == 40 and d["weighted"] is False and set(d["columns"]) == {"amt", "s", "d"}
    assert all("tags" in c and c["kind"] == KINDS40[name] for name, c in d["columns"].items())
    assert d["columns"]["s"]["json"] == JSONS40["s"]
    assert any(x["ops"] == {"replace": {"how": "to_null", "pairs": [[[-999], None]]}} for x in d["suggest"]["amt"])
    assert any(x["ops"] == {"trim": True, "nulls": [""]} for x in d["suggest"]["s"])
    assert any(x["ops"] == {"replace": {"how": "to_null", "pairs": [[["1970-01-01"], None]]}} for x in d["suggest"]["d"])
    assert any("더미" in line for line in d["lines"]["amt"]) and any("기본값" in line for line in d["lines"]["d"])
    assert d["table"]["dup_rows"] == 1 and len(d["table_lines"]) == 1
    heavy = prep_diag.diagnose(frame40(), pl.Series([2.0] * 40), KINDS40, o, JSONS40, NOW)
    assert heavy["rows"] == 80 and heavy["weighted"] is True and heavy["columns"]["amt"]["signs"]["sentinels"] == {"-999": 6}
    assert any(line.startswith("약 ") or "약 " in line for line in heavy["lines"]["amt"])


def test_lines_and_suggest_direct(o):
    found = {"kind": "string", "tags": ["binary", "hash"], "semantic": {"binary": {"values": ["N", "Y"], "true": "Y"}, "hash": {"shape": "uuid", "share": 1.0}}}
    text = prep_diag.lines("c", "string", found, 10, False, o)
    assert any("N / Y" in t and "참: Y" in t for t in text) and any("UUID" in t for t in text)
    skewed = {"kind": "float", "tags": ["skewed"], "moments": {"mean": 5.0, "sd": 9.0, "skew": 3.2, "kurt": 9.0, "min": 0.0}, "signs": {"zero": 0, "neg": 0, "pos": 10, "nan": 0, "pinf": 0, "ninf": 0, "sentinels": {}, "edges": {}}}
    assert any(s["ops"] == {"scale": "log"} for s in prep_diag.suggest("c", "float", skewed, o))
    negative = {**skewed, "moments": {**skewed["moments"], "min": -1.0}, "signs": {**skewed["signs"], "neg": 1}}
    assert not any(s["ops"] == {"scale": "log"} for s in prep_diag.suggest("c", "float", negative, o))
    assert any("음수" in t for t in prep_diag.lines("매출액", "float", negative, 10, False, o))
    assert not any("음수" in t for t in prep_diag.lines("온도", "float", negative, 10, False, o))


# ---------- 조합 키(표본, 이 PC) ----------

def test_quasi_keys(o):
    f = pl.DataFrame({"user": [1, 1, 2, 2, 3, 3], "ts": [10, 20, 10, 20, 10, 20], "v": list("abcdef"), "g": ["x"] * 5 + ["y"], "f": [0.1, 0.1, 0.2, 0.2, 0.3, 0.3]})
    kinds = {"user": "int", "ts": "int", "v": "string", "g": "string", "f": "float"}
    assert prep_diag.quasi_keys(f, kinds, o) == [{"columns": ["user", "ts"], "rows": 6}]
    assert prep_diag.quasi_keys(f, kinds, prep_diag.options({"qi_columns": 2})) == [{"columns": ["user", "ts"], "rows": 6}]
    holes = f.with_columns(pl.Series("user", [1, 1, 2, 2, 3, None]))
    assert prep_diag.quasi_keys(holes, kinds, o) == []


# ---------- 전체 진단(Ibis) ----------

@pytest.mark.parametrize("kind", ["memory", "polars"])
def test_key_dupes(kind):
    t = backend_table(kind, "kd_t", pl.DataFrame({"k": [1, 1, 1, 2, 2, 3], "g": ["a"] * 6, "u": [1, 2, 3, 4, 5, 6]}))
    assert prep_diag.key_dupes(t, ["k"], 10) == {"columns": ["k"], "rows": 6, "null_rows": 0, "groups": 2, "extra": 3, "max": 3,
                                                 "sizes": [{"n": 2, "groups": 1}, {"n": 3, "groups": 1}], "more_sizes": False,
                                                 "top": [{"values": [1], "n": 3}, {"values": [2], "n": 2}], "unique": False}
    one = prep_diag.key_dupes(t, ["u"], 10)
    assert (one["groups"], one["extra"], one["max"], one["sizes"], one["top"], one["unique"]) == (0, 0, None, [], [], True)
    assert prep_diag.key_dupes(t, ["k", "g"], 1)["top"] == [{"values": [1, "a"], "n": 3}]
    for bad in ([], ["nope"], ["k", "k"]):
        with pytest.raises(ValueError):
            prep_diag.key_dupes(t, bad, 10)
    holes = backend_table(kind, "kd_n", pl.DataFrame({"k": [1, None]}, schema={"k": pl.Int64}))
    got = prep_diag.key_dupes(holes, ["k"], 10)
    assert (got["null_rows"], got["groups"], got["unique"]) == (1, 0, False)


@pytest.mark.parametrize("kind", ["memory", "polars"])
def test_time_gaps(kind, o):
    days = [dt.date(2026, 1, d) for d in (1, 2, 5, 6, 10)] + [None]
    t = backend_table(kind, "gap_t", pl.DataFrame({"d": days, "ts": [dt.datetime(2026, 1, 1, 0, 10), dt.datetime(2026, 1, 1, 1, 59), dt.datetime(2026, 1, 1, 4), None, None, None]}))
    assert prep_diag.time_gaps(t, "d", "day", o) == {"column": "d", "unit": "day", "first": dt.date(2026, 1, 1), "last": dt.date(2026, 1, 10), "buckets": 10, "present": 5, "missing": 5,
                                                     "runs": [{"start": dt.date(2026, 1, 7), "end": dt.date(2026, 1, 9), "buckets": 3}, {"start": dt.date(2026, 1, 3), "end": dt.date(2026, 1, 4), "buckets": 2}],
                                                     "more": 0}
    hours = prep_diag.time_gaps(t, "ts", "hour", o)
    assert (hours["first"], hours["last"], hours["buckets"], hours["present"], hours["missing"]) == (dt.datetime(2026, 1, 1, 0), dt.datetime(2026, 1, 1, 4), 5, 3, 2)
    assert hours["runs"] == [{"start": dt.datetime(2026, 1, 1, 2), "end": dt.datetime(2026, 1, 1, 3), "buckets": 2}]
    short = prep_diag.time_gaps(t, "d", "day", prep_diag.options({"gap_list": 1}))
    assert len(short["runs"]) == 1 and short["more"] == 1
    for column, unit, opts in (("d", "hour", o), ("d", "week", o), ("d", "day", prep_diag.options({"gap_max_buckets": 100}) | {"gap_max_buckets": 5}), ("nope", "day", o)):
        with pytest.raises(ValueError):
            prep_diag.time_gaps(t, column, unit, opts)
    empty = backend_table(kind, "gap_e", pl.DataFrame({"d": [None, None]}, schema={"d": pl.Date}))
    with pytest.raises(ValueError):
        prep_diag.time_gaps(empty, "d", "day", o)


@pytest.mark.parametrize("kind", ["memory", "polars"])
def test_orphans(kind):
    fact_df, dim_df = pl.DataFrame({"k": [1, 2, 2, 3, 4, None], "x": list("abcdef")}), pl.DataFrame({"id": [1, 2, 5], "name": ["p", "q", "r"]})
    if kind == "memory":
        fact, dim = ibis.memtable(fact_df.to_arrow()), ibis.memtable(dim_df.to_arrow())
    else:
        con = ibis.polars.connect()
        fact, dim = con.create_table("f", fact_df), con.create_table("d", dim_df)
    assert prep_diag.orphans(fact, dim, [["k", "id"]], 20) == {"pairs": [["k", "id"]], "rows": 6, "null_keys": 1, "key_rows": 5, "keys": 4, "orphan_rows": 2, "orphan_keys": 2,
                                                              "row_share": pytest.approx(0.4), "key_share": pytest.approx(0.5), "top": [{"values": [3], "n": 1}, {"values": [4], "n": 1}]}
    assert prep_diag.orphans(fact, dim, [["k", "id"]], 1)["top"] == [{"values": [3], "n": 1}]
    texts = ibis.memtable(pa.table({"id": ["1", "2"]})) if kind == "memory" else con.create_table("t", pl.DataFrame({"id": ["1", "2"]}))
    for d, pairs in ((texts, [["k", "id"]]), (dim, []), (dim, [["k", "id"]] * 4), (dim, [["k", "nope"]]), (dim, [["nope", "id"]]), (dim, [["k"]])):
        with pytest.raises(ValueError):
            prep_diag.orphans(fact, d, pairs, 20)


# ---------- (수정) 절 ----------

def test_markers_extended():
    assert {"none", "undefined", "미입력", "알수없음"} <= set(prep_profile.MARKERS)


def test_profile_iqr_k_and_split():
    t = ibis.memtable(pa.table({"v": [float(i % 10) for i in range(99)] + [1000.0, -1000.0]}))
    checks = prep_profile.profile(t)["columns"][0]["checks"]
    assert any("IQR" in s and "1.5배" in s and "2개(2.0%: 아래 1 · 위 1)" in s for s in checks)
    assert not any("IQR" in s for s in prep_profile.profile(t, iqr_k=1000.0)["columns"][0]["checks"])


# ---------- 라우트 ----------

BODY = {"base": "t", "sample": 1000, "sample_how": {"way": "block"}}


@pytest.fixture
def api(folder, tmp_path, monkeypatch):
    """12행 표 t와 차원 표 dim이 든 DuckDB 파일에 연결한 클라이언트. 표본은 앞 행(head)만 읽게 해 결정적으로."""
    from core import profiler, server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("""create table t as select i as id, i % 4 as g, i % 5 as k, case when i < 3 then -999.0 else i * 10.0 end as amt,
        case i % 3 when 0 then 'a' when 1 then '모름' else 'b' end as s,
        date '2026-01-01' + (case when i < 6 then i else i + 3 end)::int as d from range(12) r(i)""")
    raw.execute("create table dim as select i as id, 'n' || i as name from range(3) r(i)")
    raw.close()
    monkeypatch.setattr(profiler, "FALLBACK", [("head", profiler.head)])
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_diag_options_route(client):
    got = client.get("/api/profile/diag").get_json()
    assert got["options"] == prep_diag.options(None) and set(got["rules"]) == set(prep_diag.OPTION_RULES) and got["orphan_pairs"] == prep_diag.ORPHAN_MAX_PAIRS
    assert got["rules"]["z_k"] == ["float", 1.0, 10.0, 3.0]
    assert client.post("/api/profile/diag", json={"options": {"z_k": 2.0}}).get_json()["options"]["z_k"] == 2.0
    assert client.get("/api/profile/diag").get_json()["options"]["z_k"] == 2.0
    bad = client.post("/api/profile/diag", json={"options": {"z_k": 99}})
    assert bad.status_code == 400 and "z_k" in bad.get_json()["error"]
    assert client.post("/api/profile/diag", json={"options": {}}).get_json()["options"] == prep_diag.options(None)


def test_diagnose_route(api):
    api.post("/api/profile/diag", json={"options": {"markers_extra": ["모름"]}})
    d = api.post("/api/diagnose", json=BODY).get_json()
    assert d["columns"]["amt"]["signs"]["sentinels"] == {"-999": 3}
    assert any(s["ops"] == {"replace": {"how": "to_null", "pairs": [[[-999], None]]}} for s in d["suggest"]["amt"])
    assert d["columns"]["s"]["text"]["markers"] == {"모름": 4} and any(s["ops"] == {"nulls": ["모름"]} for s in d["suggest"]["s"])
    assert d["stamp"] == prep_diag.stamp(prep_diag.options({"markers_extra": ["모름"]}))
    api.post("/api/profile/diag", json={"options": {}})
    again = api.post("/api/diagnose", json=BODY).get_json()
    assert again["stamp"] == prep_diag.stamp(prep_diag.options(None)) and again["columns"]["s"]["text"]["markers"] == {}  # 기준이 바뀌면 다시 구한다


def test_quasi_route(api):
    got = api.post("/api/diag/quasi", json=BODY).get_json()
    assert got["keys"] == [{"columns": ["k", "g"], "rows": 12}, {"columns": ["k", "s"], "rows": 12}, {"columns": ["g", "s"], "rows": 12}]


def test_diag_full_routes(api):
    g = api.post("/api/diag/dupes", json={"base": "t", "columns": ["g"]}).get_json()
    assert (g["groups"], g["extra"], g["max"], g["sizes"], g["unique"]) == (4, 8, 3, [{"n": 3, "groups": 4}], False)
    assert [x["values"] for x in g["top"]] == [[0], [1], [2], [3]]
    whole = api.post("/api/diag/dupes", json={"base": "t", "columns": []}).get_json()
    assert whole["columns"] == ["id", "g", "k", "amt", "s", "d"] and whole["unique"] is True and whole["rows"] == 12
    gaps = api.post("/api/diag/gaps", json={"base": "t", "column": "d", "unit": "day"}).get_json()
    assert (gaps["buckets"], gaps["present"], gaps["missing"]) == (15, 12, 3) and gaps["runs"] == [{"start": "2026-01-07", "end": "2026-01-09", "buckets": 3}]
    ri = api.post("/api/diag/orphans", json={"base": "t", "dim": "dim", "pairs": [["k", "id"]]}).get_json()
    assert (ri["orphan_rows"], ri["orphan_keys"], ri["keys"], ri["key_rows"]) == (4, 2, 5, 12) and ri["top"] == [{"values": [3], "n": 2}, {"values": [4], "n": 2}]
    assert api.post("/api/diag/orphans", json={"base": "t", "dim": "없는표", "pairs": [["k", "id"]]}).status_code == 400


def test_hash_exclude_setting(folder):
    from core import settings
    assert settings.CHOICES["hash_exclude"] == ["on", "off"] and settings.get("hash_exclude") == "on"


def test_hashed_route(api):
    from core import settings
    got = api.post("/api/diag/hashed", json=BODY).get_json()  # 시험 표 t에는 해시 컬럼이 없어 빈 목록
    assert got == {"on": True, "columns": []}
    settings.save({"hash_exclude": "off"})
    assert api.post("/api/diag/hashed", json=BODY).get_json() == {"on": False, "columns": []}


def test_profile_route_uses_iqr_k(api):
    iqr = lambda: [s for c in api.post("/api/profile", json=BODY).get_json()["columns"] if c["name"] == "amt" for s in c["checks"] if "IQR" in s]
    api.post("/api/profile/diag", json={"options": {"iqr_k": 10.0}})
    assert iqr() == []
    api.post("/api/profile/diag", json={"options": {"iqr_k": 0.5}})
    assert len(iqr()) == 1  # 같은 표본, 기준이 바뀌어 통계를 다시 구한다
