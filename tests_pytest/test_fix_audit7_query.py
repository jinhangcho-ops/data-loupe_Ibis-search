"""7차 감사 묶음 7B(쿼리·지표) 시험. 데이터는 시험 안에서 지어내고 진짜 엔진(Ibis·DuckDB·Polars)으로 돌린다."""
import datetime as dt

import duckdb
import polars as pl
import pytest
from lazy import ibis

from core import parse, result, server, source
from metrics import metric_calc as mc
from metrics import metric_catalog as cat
from query import flow, nl2sql

D = dt.datetime
SCHEMA = {"u": pl.Int64, "ts": pl.Datetime, "n1": pl.Float64}


def table(kind):
    if kind == "empty":
        frame = pl.DataFrame({k: [] for k in SCHEMA}, schema=SCHEMA)
    else:  # 시각이 전부 비었다
        frame = pl.DataFrame({"u": [1, 2], "ts": [None, None], "n1": [1.0, 2.0]}, schema=SCHEMA)
    return ibis.memtable(frame.to_arrow())


def run(eid, t, **extra):
    entry = [e for e in cat.ENTRIES if e["id"] == eid][0]
    pick = {"date": "ts", "num": "n1"}
    slots = {s["name"]: ("u" if s["role"] == "user" else pick.get(s["kinds"], "n1")) for s in entry["slots"] if s["type"] == "column"}
    return cat.compute(eid, {**slots, **extra}, {}, cat.Ctx(table=t), None, None)


# ---------- 7B1 빈 결과·시각 전부 NULL ----------

@pytest.mark.parametrize("kind", ["empty", "nulls"])
def test_cohort_metrics_on_no_rows_or_all_null_times_have_no_value(kind):
    t = table(kind)
    assert mc.cohort_matrix(t, "u", "ts")["cohorts"] == []
    assert mc.cohort_matrix(t, "u", "ts", mode="range", span=(1, 3))["cohorts"] == []
    assert mc.ltv(t, "u", "ts", "n1", 30) == []
    assert mc.nrr(t, "u", "ts", "n1") == []
    for eid in ("retention_classic", "retention_rolling", "retention_range", "flattening", "ltv", "nrr"):
        assert run(eid, t)["value"] is None, eid


# ---------- 7B2 tiers ----------

def test_tiers_quantile_mode_rejects_zero_and_reversed_percentages():
    t = ibis.memtable(pl.DataFrame({"u": [1, 2, 3, 4], "amt": [10.0, 20.0, 30.0, 40.0]}).to_arrow())
    base = {"사용자": "u", "금액": "amt"}
    go = lambda **extra: cat.compute("tiers", {**base, **extra}, {}, cat.Ctx(table=t), None, None)
    with pytest.raises(ValueError, match="0 < Whale"):
        go(**{"Whale 기준": 0})
    with pytest.raises(ValueError, match="상위 %"):  # 상위 50%가 상위 10%보다 넓어 Whale이 Dolphin보다 많다
        go(**{"Whale 기준": 50, "Dolphin 기준": 10})
    assert go(**{"Whale 기준": 25, "Dolphin 기준": 50})["rows"][0]["users"] == 1
    with pytest.raises(ValueError, match="Whale 기준은 Dolphin 기준 이상"):  # 금액 방식 문구는 그대로
        go(**{"기준 방식": "금액", "Whale 기준": 5, "Dolphin 기준": 50})


# ---------- 7B3 이탈 페이지 동률 ----------

@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_exit_rates_same_last_time_picks_the_smaller_page(order):
    t = ibis.memtable(pl.DataFrame({"sid": ["s", "s", "s"], "ts": [D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 2)], "pg": ["home", *order]}).to_arrow())
    got = {r["page"]: r["exits"] for r in mc.exit_rates(t, "sid", "ts", "pg")}
    assert got == {"home": 0, "a": 1, "b": 0}


# ---------- 7B4 가상 테이블 저장·삭제 ----------

def test_saving_a_virtual_table_clears_every_counted_row_total(client, tmp_path):
    path = tmp_path / "v.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.execute("create table t as select i as a from range(10) r(i)")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    post = lambda url, **body: client.post(url, json=body)
    assert post("/api/virtual", name="v1", sql="select * from t where a >= 5").status_code == 200
    assert post("/api/virtual", name="v2", sql="select * from v1").status_code == 200
    assert post("/api/rowcount", base="v2").get_json()["rows"] == 5
    assert post("/api/virtual", name="v1", sql="select * from t where a >= 8").status_code == 200  # v2가 쓰는 v1이 바뀌었다
    assert post("/api/rowcount", base="v2").get_json()["rows"] == 2
    assert post("/api/virtual", name="v1", sql=None, parquet=None).status_code == 200
    assert server.state["rows"] == {}


# ---------- 7B5 공백 컬럼 이름 읽기는 한 곳 ----------

COLS = {"class name": "text", "total": "number", "total in": "number", "a b c": "text"}


@pytest.mark.parametrize("text,condition,want", [
    ("class name = 7", False, "class name"), ("class name", True, None), ("total in 1, 2", True, None), ("total in = 3", True, "total in"),
    ("total in 1, 2", False, "total in"), ("classname = 1", True, None), ("a b c like x", True, "a b c"), ("a b", True, None), ("class name!=1", True, "class name"),
])
def test_head_name_rule_is_shared_by_parse_and_flow(text, condition, want):
    cols = list(COLS)
    assert parse.head_name(text, cols, condition) == want == flow.head_name("  " + text, COLS, condition)
    expr = ibis.table({c: "string" for c in cols}, name="x")
    name, rest = parse.spaced(text, expr, condition)
    assert name == want and (rest == parse.SPACED + text[len(want):] if want else rest == text)


# ---------- 7B6 리텐션 표 컬럼 상한 ----------

def test_retention_table_columns_are_capped_but_the_value_is_not():
    days = list(range(0, 400, 2)) * 3
    t = ibis.memtable(pl.DataFrame({"u": [i % 7 for i in range(len(days))], "ts": [D(2025, 1, 1) + dt.timedelta(days=d) for d in days]}).to_arrow())
    r = run("retention_classic", t, **{"N": 300})
    assert len(r["columns"]) <= 102 and all(len(row) <= 102 for row in r["rows"])
    assert any("앞의 100칸" in n for n in r["notes"])
    small = run("retention_classic", t, **{"N": 7})
    assert len(small["columns"]) == 2 + 8 and not any("칸만" in n for n in small["notes"])


# ---------- 7B7 NL2SQL 테이블 수 ----------

def test_nl2sql_prompt_says_when_tables_were_left_out():
    con = ibis.duckdb.connect()
    for i in range(nl2sql.MAX_TABLES + 2):
        con.create_table(f"t{i:02d}", ibis.memtable(pl.DataFrame({"a": [i]}).to_arrow()))
    ctx = nl2sql.context(con, "k")
    assert len(ctx["tables"]) == nl2sql.MAX_TABLES and ctx["more_tables"] is True
    assert "더 있으나 생략" in nl2sql.system_prompt(ctx)
    few = nl2sql.context(ibis.duckdb.connect(), "k")
    assert few["more_tables"] is False and "생략" not in nl2sql.system_prompt(few)


# ---------- 7B8 규칙 번호가 리스트 ----------

def test_optimize_apply_ignores_non_integer_rule_numbers(client):
    for rules in ([[1]], ["1"], [{"a": 1}], 5, "x"):
        got = client.post("/api/flow/optimize/apply", json={"graph": flow.from_body({"base": "t"}), "rules": rules}).get_json()
        assert got["error"] == "적용할 규칙을 고르세요.", rules


# ---------- 7P9·리뷰: 검색의 전체 행 수는 늘 다시 센다(세어 둔 값은 DB·Parquet이 바뀌어도 그대로라) ----------

def test_plain_search_counts_again_after_rows_are_added(client, tmp_path):
    con = ibis.duckdb.connect(str(tmp_path / "grow.duckdb"))
    con.raw_sql("create table t as select i as a, i % 3 as g from range(50) r(i)")
    server.state.update(con=con, opened={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "grow.duckdb")})
    server.state["rows"].clear()
    body = {"base": "t", "conditions": [], "periods": [], "sorts": [], "columns": [], "limit": 5}
    q = lambda **extra: client.post("/api/query", json={**body, **extra}).get_json()
    assert q()["count"] == 50 and q(conditions=["g = 1"])["count"] == 17 and q(count=False)["count"] is None
    con.raw_sql("insert into t select i, 0 from range(20) r(i)")  # 적재 등으로 행이 늘었다
    assert q()["count"] == 70  # 세어 둔 행 수(테이블 행 수)를 쓰면 다시 [검색 실행]해도 50에 머문다
    con.disconnect()
    server.state["rows"].clear()


def test_plain_search_on_a_virtual_table_counts_the_same(client, tmp_path):
    path = tmp_path / "v.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.execute("create table t as select i as a from range(10) r(i)")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    server.state["rows"].clear()
    assert client.post("/api/virtual", json={"name": "big", "sql": "select * from t where a >= 4"}).status_code == 200
    body = {"base": "big", "conditions": [], "periods": [], "sorts": [], "columns": [], "limit": 3}
    assert client.post("/api/query", json=body).get_json()["count"] == 6 == client.post("/api/query", json=body).get_json()["count"]
    assert client.post("/api/query", json={**body, "conditions": ["a > 6"]}).get_json()["count"] == 3
    assert client.post("/api/query", json={**body, "calcs": ["누적합 a 기준 a"]}).get_json()["count"] == 6
    server.state["rows"].clear()
