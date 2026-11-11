"""Phase 2 표본 방식(plans/phase-2.md): ③ 층화 + Neyman(기본, 기준 컬럼 자동), ① 블록 → 행 단위, ② 키 단위 해시, 파일럿·자동 표본 수, 오차(역계산)·신뢰구간, 단계별 읽은 양.
데이터는 시험 안에서 지어낸다(DuckDB 메모리·SQLite 파일·Parquet 파일). 새 함수는 스켈레톤이라 NotImplementedError로 실패하는 것이 정상이고,
기존 함수 수정(plans/phase-2.md의 (수정) 절)이 끝나야 통과하는 시험에는 xfail(strict)을 붙였다. 설정 기본값에 기대지 않게 CONF를 직접 넘긴다."""
import math
import sqlite3
import types

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lazy import ibis

from core import sampling

ROWS = 20_000
KEYS = 2_000  # 키마다 ROWS / KEYS = 10행
CONF = {"sample_z": 1.96, "sample_e_ratio": 0.01, "sample_e_mean": 0.05, "pilot_rows": 5000, "strata_h": 5, "strata_min": 30,
        "hash_buckets": 10_000, "key_over": 3}  # sample_z는 config가 신뢰수준 %(sample_conf)에서 구하는 값. 시험은 1.96으로 고정


def made(n=ROWS):
    """id, k(키, 키마다 10행, 1000번째마다 빈 키), v(값: 100행마다 아주 큰 값, 97행마다 빈 값), s(글자)."""
    i = pl.int_range(n, eager=True, dtype=pl.Int64).alias("id")
    return pl.DataFrame(i).with_columns(
        k=pl.when(pl.col("id") % 1000 == 999).then(None).otherwise(pl.col("id") % KEYS),
        v=pl.when(pl.col("id") % 97 == 0).then(None).when(pl.col("id") % 100 == 0).then(pl.col("id") * 100.0).otherwise((pl.col("id") % 50).cast(pl.Float64)),
        s=pl.lit("a")).to_arrow()


@pytest.fixture
def duck():
    con = ibis.duckdb.connect()
    t = con.create_table("t", ibis.memtable(made()))
    yield con, t
    con.disconnect()


@pytest.fixture
def lite(tmp_path):
    path = tmp_path / "x.sqlite"
    raw = sqlite3.connect(path)
    raw.execute(f"create table t as with recursive r(i) as (select 0 union all select i + 1 from r where i < {ROWS - 1}) select i as id, i % {KEYS} as k, cast(i % 50 as real) as v from r")
    raw.commit()
    raw.close()
    con = ibis.sqlite.connect(str(path))
    yield con, con.table("t")
    con.disconnect()


@pytest.fixture
def flat(tmp_path):
    from core import source
    path = tmp_path / "t.parquet"
    pq.write_table(made(), path)
    con = source.open_flat_files([path])
    yield con, con.table("t")


def fallback(con, t, rows):
    """draw에 넘길 ① 표본 읽기(지금의 profiler.sample 차례)."""
    from core import profiler
    return lambda n, pilot=False: profiler.sample(con, t, "", "t", n, rows, None)  # pilot(파일럿이면 참)은 BigQuery 블록 먼저 설정용이라 여기서는 쓰지 않는다


# ---------- 설정·방식 ----------

def test_config_defaults_and_saved(folder):
    from core import settings
    got = sampling.config()
    assert got["sample_conf"] == 95 and got["sample_z"] == pytest.approx(1.959964, abs=1e-6)  # 신뢰수준 % → 표준정규 역함수(statistics.NormalDist)
    assert {k: got[k] for k in CONF if k != "sample_z"} == {k: v for k, v in CONF.items() if k != "sample_z"}
    settings.save({"strata_h": 7, "sample_e_ratio": 0.02, "sample_conf": 99, "pilot_first": "block"})
    got = sampling.config()
    assert (got["strata_h"], got["sample_e_ratio"], got["sample_z"]) == (7, 0.02, pytest.approx(2.575829, abs=1e-6)) and settings.get("pilot_first") == "block"
    assert "strata_gap_sd" not in got and "sample_z" not in settings.SIZES and "sample_z" not in settings.FLOATS
    for bad in ({"strata_h": 1}, {"strata_h": 21}, {"pilot_rows": 999}, {"sample_conf": 49.9}, {"sample_conf": 100}, {"pilot_first": "x"}, {"strata_gap_sd": "overall"}):
        with pytest.raises(ValueError):
            settings.save(bad)


def test_how_of():
    assert sampling.how_of({}) == {"way": "strata", "column": None}  # 기본: 층화, 기준 컬럼 자동
    assert sampling.how_of({"sample_how": None}) == {"way": "strata", "column": None}
    assert sampling.how_of({"sample_how": {"way": "strata", "column": "v"}}) == {"way": "strata", "column": "v"}
    assert sampling.how_of({"sample_how": {"way": "block", "column": "v"}}) == {"way": "block", "column": None}
    assert sampling.how_of({"sample_how": {"way": "key", "column": "k", "extra": 1}}) == {"way": "key", "column": "k"}
    for bad in ({"way": "key"}, {"way": "key", "column": ""}, {"way": "random"}, "key", {"way": "strata", "column": 3}, {"way": "auto"}):
        with pytest.raises(ValueError, match="표본 방식"):
            sampling.how_of({"sample_how": bad})


def test_numeric_and_can_stratify(duck, flat):
    assert sampling.numeric_columns(duck[1]) == ["id", "k", "v"]
    assert sampling.can_stratify(duck[1], "v") is True
    assert sampling.can_stratify(flat[1], "v") is True  # Polars도 Table.sample(행 단위)이 된다


def test_settle(duck):
    con, t = duck
    auto = {"way": "strata", "column": None}
    assert sampling.settle(t, auto, ROWS) == {"way": "strata", "column": None, "notes": []}
    assert sampling.settle(t, auto, None) == {"way": "block", "column": None, "notes": [sampling.NOTE_NO_ROWS]}
    words = con.create_table("w", ibis.memtable({"s": ["a", "b"]}))
    assert sampling.settle(words, auto, 2) == {"way": "block", "column": None, "notes": [sampling.NOTE_NO_NUMERIC]}
    with pytest.raises(ValueError, match="정수·실수"):
        sampling.settle(t, {"way": "strata", "column": "s"}, ROWS)
    with pytest.raises(ValueError):
        sampling.settle(t, {"way": "key", "column": "없음"}, ROWS)
    assert sampling.settle(t, {"way": "block", "column": None}, ROWS)["way"] == "block"


def test_plan(duck, folder):
    t = duck[1]
    names = lambda steps: [name for name, _ in steps]
    strata = sampling.plan(t, {"way": "strata", "column": None, "notes": []}, 5000, ROWS, CONF)
    assert names(strata) == ["파일럿", "층 크기 세기", "다시 뽑기"] and strata[0][1] is None  # 기본: 파일럿은 무과금 미리보기 자리(읽는 양 0)
    assert names(sampling.plan(t, {"way": "key", "column": "k", "notes": []}, 5000, ROWS, CONF)) == ["파일럿", "빈 키 세기", "키 단위 뽑기"]
    assert names(sampling.plan(t, {"way": "block", "column": None, "notes": []}, 5000, ROWS, CONF)) == ["파일럿", "표본"]


def test_sample_way(duck, lite, flat):
    assert sampling.sample_way(duck[1], 0.1) == "block"
    assert sampling.sample_way(lite[1], 0.1) == "row"  # SQLite의 block은 random() 조건으로 바뀐다(TABLESAMPLE 없음) → 행 단위
    assert sampling.sample_way(flat[1], 0.1) == "row"  # SQL이 아닌 연결(Polars): Table.sample 행 단위


def test_auto_column_and_bare():
    pilot = pa.table({"same": [5.0] * 10, "small": [10.0, 11.0] * 5, "big": [1.0, 100.0] * 5, "s": ["x"] * 10, sampling.WEIGHT: [1.0] * 10})
    assert sampling.auto_column(pilot) == "big"  # 변동계수가 가장 큰 숫자 컬럼(도움 컬럼 제외)
    assert sampling.auto_column(pa.table({"s": ["x", "y"], "same": [1, 1]})) is None
    assert sampling.bare(pa.table({"a": [1], sampling.WEIGHT: [1.0], sampling.STRATUM: [0]})).column_names == ["a"]


# ---------- 읽은 양 ----------

def test_measured_and_stage(monkeypatch, duck):
    from core import scan_size

    class Job:
        total_bytes_processed, total_bytes_billed = 1234, 10 * 1024 * 1024

        def to_arrow(self):
            return pa.table({"a": [1, 2, 3]})

    fake = types.SimpleNamespace(client=types.SimpleNamespace(query=lambda sql: Job()))
    monkeypatch.setattr(scan_size, "dry_runs", lambda con: con is fake)
    monkeypatch.setattr(scan_size, "sql_of", lambda con, expr: ("select 1", None))
    expr = ibis.table({"a": "int64"}, name="t")
    got, read = sampling.stage("다시 뽑기", lambda: sampling.measured(fake, expr), scanned=ROWS)
    assert got.num_rows == 3 and got.schema.field("a").type == pa.int64()
    assert {k: read[k] for k in ("stage", "rows", "bytes", "billed", "scanned")} == {"stage": "다시 뽑기", "rows": 3, "bytes": 1234, "billed": 10 * 1024 * 1024, "scanned": ROWS}
    assert "다시 뽑기" in read["text"] and "3행" in read["text"]
    local, read = sampling.stage("표본", lambda: sampling.measured(duck[0], duck[1].limit(7)))
    assert local.num_rows == 7 and (read["rows"], read["bytes"], read["billed"]) == (7, None, None)
    _, read = sampling.stage("파일럿", lambda: pa.table({"a": [1]}))  # 조회를 거치지 않은 단계(무과금 미리보기 등)
    assert read["rows"] is None and read["bytes"] is None


# ---------- 크기 공식 ----------

def test_size_formulas():
    assert sampling.ratio_size(None, 0.01, None, 1.96) == 9604  # 1.96²·0.25/0.01²
    assert sampling.ratio_size(0.1, 0.02, None, 1.96) == 865  # 864.36 올림
    assert sampling.mean_size(0.5, 0.05, None, 1.96) == 385  # (1.96·0.5/0.05)² = 384.16
    assert sampling.finite_size(9604, 20_000) == 6489  # 9604 / (1 + 9603/20000)
    assert sampling.finite_size(10, 5) == 4
    assert sampling.finite_size(384.16, None) == 385
    assert sampling.ratio_size(None, 0.01, 20_000, 1.96) == 6489
    assert sampling.ratio_size(None, 0.01, None, 2.576) == math.ceil(round(2.576 ** 2 * 0.25 / 0.0001, 6))  # 99%
    for bad in ((None, 0, None, 1.96), (1.5, 0.01, None, 1.96)):
        with pytest.raises(ValueError):
            sampling.ratio_size(*bad)


def test_needed():
    pilot = pa.table({"half": [None, 1.0] * 500, "x": [1.0, 3.0] * 500})
    p_need = sampling.ratio_size(0.5, 0.01, 100_000, 1.96)  # half 컬럼의 빈 값 비율 0.5가 가장 큰 p(1-p)
    cv = pl.Series([1.0, 3.0] * 500).std() / 2.0
    assert sampling.needed(pilot, 100_000, CONF) == max(p_need, sampling.mean_size(cv, 0.05, 100_000, 1.96))
    assert sampling.needed(pa.table({"x": [1.0, 1.0]}), 100, CONF) == 0  # 빈 값도 퍼짐도 없으면 0(부르는 쪽이 최소 행 수로 올린다)


# ---------- 오차·신뢰구간 ----------

def test_mean_ci_simple_and_strata():
    frame = pl.DataFrame({"y": [1.0, 2.0, 3.0, 4.0] * 250})
    est, half, domain = sampling.mean_ci(frame, pl.col("y"), None, None, 1.96)
    assert est == pytest.approx(2.5) and half == pytest.approx(1.96 * math.sqrt(frame["y"].var() / 1000)) and domain is None
    assert sampling.mean_ci(frame, pl.col("y"), 1000, None, 1.96)[1] == 0  # 표본이 곧 전체
    drawn = pl.DataFrame({"y": [1.0, 2.0, 3.0, 4.0] + [50.0] * 10, sampling.STRATUM: [0] * 4 + [1] * 10})
    est, half, domain = sampling.mean_ci(drawn, pl.col("y"), None, {0: 100, 1: 10}, 1.96)
    assert est == pytest.approx(750 / 110) and domain == pytest.approx(110)
    assert half == pytest.approx(1.96 * math.sqrt((100 / 110) ** 2 * (1 - 4 / 100) * (5 / 3) / 4))


def test_margins():
    arrow = pa.table({"x": [1.0, 2.0, 3.0, 4.0] * 250, "s": ["a"] * 1000, "z": [0.0] * 1000})
    m = sampling.margins(arrow, None, None, CONF)
    assert (m["z"], m["e_ratio"], m["e_mean"]) == (1.96, 0.01, 0.05)
    assert m["ratio"] == pytest.approx(1.96 * 0.5 * math.sqrt(1 / 1000))
    assert m["ratio_need"] == 9604
    cv = pl.Series([1.0, 2.0, 3.0, 4.0] * 250).std() / 2.5
    assert set(m["means"]) == {"x"}  # 글자와 평균이 0인 컬럼은 뺀다
    assert m["means"]["x"]["error"] == pytest.approx(1.96 * cv * math.sqrt(1 / 1000))
    assert m["means"]["x"]["need"] == sampling.mean_size(cv, 0.05, None, 1.96)
    assert sampling.margins(arrow, 1000, None, CONF)["ratio"] == 0
    part = sampling.margins(arrow, 20_000, None, CONF)
    assert part["ratio"] == pytest.approx(1.96 * 0.5 * math.sqrt((20_000 - 1000) / (1000 * (20_000 - 1))))
    strata = pa.table({"x": [1.0, 2.0] * 50, sampling.STRATUM: [0] * 50 + [1] * 50, sampling.WEIGHT: [2.0] * 50 + [4.0] * 50})
    got = sampling.margins(strata, 300, {0: 100, 1: 200}, CONF)
    assert got["ratio"] == pytest.approx(1.96 * math.sqrt((1 / 3) ** 2 * 0.5 * 0.25 / 50 + (2 / 3) ** 2 * 0.75 * 0.25 / 50))


def test_intervals():
    arrow = pa.table({"x": [1.0, None, 3.0, 4.0] * 250, "s": ["", " a", "b", None] * 250})
    got = sampling.intervals(arrow, None, None, CONF)
    est, low, high = got["x"]["null"]
    assert est == pytest.approx(0.25) and low == pytest.approx(0.25 - 1.96 * math.sqrt(pl.Series([0.0, 1.0, 0.0, 0.0] * 250).var() / 1000)) and high > est
    assert got["x"]["mean"][0] == pytest.approx(8 / 3)
    assert got["s"]["null"][0] == pytest.approx(0.25) and got["s"]["blank"][0] == pytest.approx(0.25) and got["s"]["spaces"][0] == pytest.approx(0.25)
    assert "mean" not in got["s"]
    none = sampling.intervals(pa.table({"x": [1.0] * 10}), 10, None, CONF)["x"]["null"]
    assert none == [0.0, 0.0, 0.0]  # 비율 구간은 0~1로 자른다


# ---------- 층화 ----------

def test_strata_edges():
    assert sampling.strata_edges(pl.Series(range(1, 101), dtype=pl.Float64), 5) == [20.0, 40.0, 60.0, 80.0]
    assert sampling.strata_edges(pl.Series([1.0] * 50 + [2.0] * 50), 5) == [2.0]  # 겹친 경계와 최솟값은 뺀다
    assert sampling.strata_edges(pl.Series([7.0] * 10), 5) == []


def test_neyman():
    assert sampling.neyman([1000, 1000], [1.0, 3.0], 400, 30) == [100, 300]
    assert sampling.neyman([100, 100, 10], [1.0, 1.0, 100.0], 60, 30) == [30, 30, 10]  # 셋째 층은 할당 50 > 10행 → 전수, 나머지는 다시 나눔(최소 30)
    assert sampling.neyman([100, 300], [0.0, 0.0], 100, 30) == [30, 75]  # 퍼짐이 모두 0이면 행 수 비례
    assert sampling.neyman([5, 8], [1.0, 1.0], 100, 30) == [5, 8]  # 모두 전수
    assert sampling.neyman([5000, 3000, 2000], [2.0, 5.0, 40.0], 1500, 30) == [143, 215, 1143]
    assert sampling.neyman([1000, 1000], [1.0, 3.0], 40, 2) == [10, 30]  # 최소 행 수는 설정(least)


def test_strata_size():
    n0 = (1.96 * 1 / (0.05 * 10)) ** 2
    assert sampling.strata_size([500, 500], [1.0, 1.0], 10.0, 1.96, 0.05) == math.ceil(round(n0 / (1 + n0 / 1000), 6))
    assert sampling.strata_size([500, 500], [0.0, 0.0], 10.0, 1.96, 0.05) == 0
    with pytest.raises(ValueError, match="평균이 0"):
        sampling.strata_size([500, 500], [1.0, 1.0], 0.0, 1.96, 0.05)


def test_ht_estimate():
    drawn = pl.DataFrame({"v": [1.0, 2.0, 3.0, 4.0] + [50.0] * 10, sampling.STRATUM: [0] * 4 + [1] * 10})
    est = sampling.ht_estimate(drawn, "v", {0: 100, 1: 10}, 1.96)
    half = 1.96 * math.sqrt(100 ** 2 * (1 - 4 / 100) * (5 / 3) / 4) / 110
    assert est["mean"] == pytest.approx(750 / 110) and est["total"] == pytest.approx(750)
    assert est["moe_mean"] == pytest.approx(half) and est["moe_total"] == pytest.approx(half * 110)
    assert (est["low"], est["high"]) == (pytest.approx(750 / 110 - half), pytest.approx(750 / 110 + half))
    assert (est["rows"], est["drawn"]) == (110, 14)
    assert [(s["stratum"], s["rows"], s["drawn"], s["weight"]) for s in est["strata"]] == [(0, 100, 4, 25.0), (1, 10, 10, 1.0)]
    with pytest.raises(ValueError, match="뽑지 못했"):
        sampling.ht_estimate(drawn.filter(pl.col(sampling.STRATUM) == 1), "v", {0: 100, 1: 10}, 1.96)


def test_weighted_quantiles():
    t = ibis.memtable({"x": [1.0, 2.0, 3.0, 4.0, None], "w": [3.0, 1.0, 1.0, 1.0, 9.0]})
    assert sampling.weighted_quantiles(t, "x", [0.25, 0.75], "w") == [1.0, 3.0]  # 누적 3,4,5,6 / 합계 6
    even = ibis.memtable({"x": [1.0, 2.0, 3.0, 4.0], "w": [1.0] * 4})
    assert sampling.weighted_quantiles(even, "x", [0.25, 0.5, 0.75], "w") == [1.0, 2.0, 3.0]


def test_stratified_duckdb(duck):
    con, t = duck
    pilot = t.sample(0.25, method="row").limit(5000).to_pyarrow()  # 작은 메모리 표의 블록 표본은 0행일 수 있어 행 단위로
    truth = t.v.mean().execute()
    arrow, info = sampling.stratified(con, t, "v", pilot, 5000, ROWS, CONF)
    frame = pl.from_arrow(arrow)
    assert set(sampling.HELPERS) <= set(arrow.column_names) and frame.height <= 5000 + 7 * CONF["strata_min"]
    assert frame[sampling.WEIGHT].sum() == pytest.approx(ROWS)  # 가중치 Nh/nh의 합 = 전체 행 수
    est = info["estimate"]
    assert info["column"] == "v" and abs(est["mean"] - truth) <= 3 * est["moe_mean"] + 1e-9 and est["low"] < est["mean"] < est["high"]
    assert est["rows"] == pytest.approx(ROWS - ROWS // 97 - 1, abs=1)  # 빈 값을 뺀 행(97의 배수)
    assert sum(s["rows"] for s in info["strata"]) == ROWS and any(s["empty"] for s in info["strata"])  # 빈 값 층이 따로 있다
    assert all(0 < s["drawn"] <= s["rows"] for s in info["strata"])
    assert [r["stage"] for r in info["reads"]] == ["층 크기 세기", "다시 뽑기"] and all(r["scanned"] == ROWS for r in info["reads"])


# ---------- 키 단위 ----------

def test_keyed_keeps_whole_keys(duck):
    con, t = duck
    arrow, info = sampling.keyed(con, t, "k", 2000, ROWS, CONF)
    found = pl.from_arrow(arrow)
    assert 0 < found.height <= 2000 * CONF["key_over"] and found["k"].null_count() == 0
    assert found.group_by("k").len()["len"].unique().to_list() == [ROWS // KEYS]  # 고른 키는 그 키의 행을 모두 가진다(빈 키 행은 키 999·1999에만 몰려 있다)
    assert info["null_keys"] == ROWS // 1000 and str(ROWS // 1000) in info["notes"][0]  # 키가 빈 행은 빠지고 안내한다
    assert [r["stage"] for r in info["reads"]] == ["빈 키 세기", "키 단위 뽑기"]


def test_keyed_small_table_and_errors(duck, lite):
    con, t = duck
    arrow, info = sampling.keyed(con, t, "k", 50_000, ROWS, CONF)
    assert arrow.num_rows == ROWS - ROWS // 1000  # 표가 표본보다 작으면 키가 있는 행 전부
    with pytest.raises(ValueError, match="행 수"):
        sampling.keyed(con, t, "k", 2000, None, CONF)
    with pytest.raises(ValueError, match="키 단위 표본"):
        sampling.keyed(lite[0], lite[1], "k", 2000, ROWS, CONF)  # SQLite는 해시(ops.Hash)를 컴파일하지 못한다


# ---------- draw ----------

def test_draw_strata_default(duck):
    con, t = duck
    how = sampling.settle(t, sampling.how_of({}), ROWS)
    arrow, pilot, info = sampling.draw(con, t, how, 5000, ROWS, fallback(con, t, ROWS), CONF)
    assert info["way"] == info["method"] == "strata" and info["column"] == "v"  # 변동계수가 가장 큰 숫자 컬럼(큰 값이 섞인 v)
    assert info["rows"] == arrow.num_rows and info["pilot"] == pilot.num_rows and sampling.WEIGHT not in pilot.column_names
    assert [r["stage"] for r in info["reads"]] == ["파일럿", "층 크기 세기", "다시 뽑기"]
    assert {"margins", "intervals", "estimate", "strata"} <= set(info) and info["n"] <= 5000


def test_draw_whole_and_block(duck):
    con, t = duck
    small = con.create_table("small", ibis.memtable(made(300)))
    arrow, pilot, info = sampling.draw(con, small, sampling.settle(small, sampling.how_of({}), 300), 5000, 300, fallback(con, small, 300), CONF)
    assert info["method"] == "whole" and arrow.num_rows == 300 and [r["stage"] for r in info["reads"]] == ["파일럿"]  # 파일럿이 곧 전체
    how = sampling.settle(t, {"way": "block", "column": None}, ROWS)
    arrow, pilot, info = sampling.draw(con, t, how, 3000, ROWS, fallback(con, t, ROWS), CONF)
    assert info["way"] == "block" and info["rows"] == arrow.num_rows and info["n"] <= 3000 and "estimate" not in info
    assert ("margins" in info) == (info["method"] in sampling.RANDOM_METHODS)


# ---------- prep_app 새 함수 ----------

def test_sample_key(monkeypatch):
    from prep import prep_app
    monkeypatch.setattr(prep_app, "table_key", lambda body: "K")
    assert prep_app.sample_key({"base": "t"}) == 'K|{"column": null, "way": "strata"}'
    assert prep_app.sample_key({"base": "t", "sample_how": {"way": "key", "column": "k"}}) == 'K|{"column": "k", "way": "key"}'


def test_sample_gate(monkeypatch, folder):
    from core import querying, scan_size, server
    from core.server import app
    from prep import prep_app
    sizes = {"층 크기 세기": 300, "다시 뽑기": 900}
    monkeypatch.setattr(prep_app, "run", lambda work, *a: work())
    monkeypatch.setattr(server, "expr_sql", lambda con, expr: (expr, None))
    monkeypatch.setattr(scan_size, "estimate", lambda con, sql, opened=None, dialect=None: {"bytes": sizes[sql], "exact": True, "how": "BigQuery dry run"})
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (1000, "기준 1000 B"))
    stages = [("파일럿", None), ("층 크기 세기", "층 크기 세기"), ("다시 뽑기", "다시 뽑기")]
    monkeypatch.setattr(scan_size, "dry_runs", lambda con: False)
    assert prep_app.sample_gate({}, object(), stages) is None  # 미리 잴 수 없는 연결은 묻지 않는다
    monkeypatch.setattr(scan_size, "dry_runs", lambda con: True)
    with app.test_request_context():
        with pytest.raises(querying.Confirm) as e:
            prep_app.sample_gate({}, object(), stages)
        body = e.value.response.get_json()
        assert body["bytes"] == 1200 and [s["stage"] for s in body["stages"]] == ["파일럿", "층 크기 세기", "다시 뽑기"] and body["stages"][0]["bytes"] == 0
        assert "층 크기 세기" in body["text"] and "다시 뽑기" in body["text"] and "합계" in body["text"]
        assert prep_app.sample_gate({"confirmed": True}, object(), stages) is None
        sizes.update({"층 크기 세기": 100, "다시 뽑기": 100})
        assert prep_app.sample_gate({}, object(), stages) is None  # 합계가 기준 안이면 묻지 않는다


def test_even_sample(monkeypatch):
    from prep import prep_app
    main, pilot = pa.table({"a": [1, 2], sampling.WEIGHT: [1.0, 1.0]}), pa.table({"a": [9]})
    for way, want in (("strata", pilot), ("block", main)):
        monkeypatch.setattr(prep_app, "sampled", lambda body, key, size, fresh, how, w=way: (prep_app.SAMPLES.update({key: (size, main, {"way": w}, pilot)}), (main, {"way": w}))[1])
        assert prep_app.even_sample({}, "K", 1000, {"way": way, "column": None}) is want
    prep_app.SAMPLES.clear()


# ---------- 기존 함수 수정 뒤 ----------

def test_profile_weighted():
    from prep import prep_profile
    t = ibis.memtable({"x": [1.0, None, 3.0, 4.0], "s": ["", "a", "b", "c"], sampling.STRATUM: [0, 0, 1, 1], sampling.WEIGHT: [3.0, 3.0, 1.0, 1.0]})
    found = prep_profile.profile(t, sampling.WEIGHT)
    cols = {c["name"]: c for c in found["columns"]}
    assert set(cols) == {"x", "s"} and found["rows"] == 8  # 도움 컬럼은 빼고, 행 수는 가중치 합
    assert cols["x"]["nulls"] == 3 and cols["s"]["blank"] == 3  # 빈 값·빈 글자는 가중치로 센다
    assert (cols["x"]["min"], cols["x"]["max"]) == (1.0, 4.0)
    plain = prep_profile.profile(t.drop(sampling.STRATUM, sampling.WEIGHT))
    assert plain["rows"] == 4 and {c["name"]: c for c in plain["columns"]}["x"]["nulls"] == 1  # 가중치가 없으면 지금 그대로


def test_row_sample_on_sqlite(lite):
    from core import profiler
    found, method, passed = profiler.sample(lite[0], lite[1], "", "t", 1000, ROWS, None)
    assert method == "row" and 0 < found.num_rows <= 1000 and passed == []
    assert all(profiler.METHODS[m] for m in ("row", "key", "strata", "segments"))


@pytest.fixture
def api(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute(f"create table t as select i as id, i % {KEYS} as k, case when i % 100 = 0 then i * 100.0 else (i % 50) * 1.0 end as v from range({ROWS}) r(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_profile_route_ways(api):
    auto = api.post("/api/profile", json={"base": "t", "sample": 1000}).get_json()
    s = auto["sampled"]
    assert s["way"] == "strata" and s["column"] == "v" and auto["rows"] == ROWS  # 기본은 층화, 행 수는 가중치 합(추정)
    assert {"margins", "intervals", "estimate", "reads"} <= set(s) and [r["stage"] for r in s["reads"]] == ["파일럿", "층 크기 세기", "다시 뽑기"]
    key = api.post("/api/profile", json={"base": "t", "sample": 1000, "sample_how": {"way": "key", "column": "k"}}).get_json()["sampled"]
    assert key["method"] == "key" and "margins" not in key and "intervals" not in key and sampling.NOTE_KEY_NO_CI in key["notes"]  # 구간이 없는 까닭을 안내
    block = api.post("/api/profile", json={"base": "t", "sample": 1000, "sample_how": {"way": "block"}}).get_json()["sampled"]
    assert block["way"] == "block" and "estimate" not in block
    assert api.post("/api/profile", json={"base": "t", "sample": 1000, "sample_how": {"way": "strata", "column": "없음"}}).status_code == 400
    pats = api.post("/api/patterns", json={"base": "t", "sample": 1000}).get_json()["columns"]
    assert sampling.STRATUM not in pats and sampling.WEIGHT not in pats


def test_analyze_groups_on_key_sample(api):
    how = {"kind": "groups", "by": "k", "value": "v", "agg": "count"}
    d = api.post("/api/analyze", json={"recipe": {"base": "t"}, "how": how, "sample": 1000}).get_json()
    assert d["data"]["method"] == "key" and set(d["counts"]) == {ROWS // KEYS}  # 그룹 비교는 by 컬럼을 키로 쓴 키 단위 표본: 그룹마다 행이 모두 있다
