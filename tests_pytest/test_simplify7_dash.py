"""7차 /simplify G2(대시보드) 시험: 단순화·효율 고침이 옛 동작과 같은 결과를 내는지. 데이터는 시험 안에서 지어냈고 엔진은 진짜(DuckDB·Polars)를 쓴다."""
import datetime as dt
import types

import duckdb
import pytest
from lazy import ibis

from core import metric_file, scan_size, server
from dashboard import dash_app, dash_calc, dash_refresh


@pytest.fixture
def con_duck(tmp_path):
    raw = duckdb.connect(str(tmp_path / "g2.duckdb"))
    raw.execute("create table t (day date, event varchar, g varchar, amt double)")
    rows = [(dt.date(2026, 1, 1 + i % 9), "click" if i % 3 else "view", "ab"[i % 2] if i % 7 else None, float(i)) for i in range(30)]
    raw.executemany("insert into t values (?, ?, ?, ?)", rows)
    raw.close()
    return ibis.duckdb.connect(str(tmp_path / "g2.duckdb"))


def card(kind="chart", **source):
    return {"kind": kind, "title": "t", "source": {"table": "t", "body": {"base": "t"}, **source}, "view": {}}


# ---------- S-A2b: 방언은 scan_size의 규칙 하나 ----------

def test_read_sql_dialect_follows_files_dialect(monkeypatch):
    c = card("number", date=None)
    c["source"]["body"] = {"base": "t", "series": [{"agg": "count"}]}
    expr = ibis.memtable({"a": [1, 2]})
    monkeypatch.setattr(dash_calc.querying, "build", lambda body, select=False: expr)
    monkeypatch.setattr(dash_calc.querying, "slim", lambda e, keep: e)
    monkeypatch.setattr(dash_calc, "card_columns", lambda card, ctx=None: None)
    for name, dialect in (("polars", "duckdb"), ("duckdb", None), (None, None)):
        monkeypatch.setattr(server, "state", {**server.state, "con": types.SimpleNamespace(name=name) if name else None})
        assert dash_calc.read_sql(c, None) == scan_size.sql_of(server.state["con"], expr)[0]
        assert scan_size.files_dialect(server.state["con"]) == dialect


# ---------- S-S8: fetched ----------

def test_fetched_none_for_unreadable_kinds_and_shaped_otherwise():
    assert dash_calc.fetched({"kind": "recent_rows", "source": {"body": {"base": "t"}}}) is None
    assert dash_calc.fetched({"kind": "analysis", "source": {"body": {"base": "t"}}}) is None
    assert dash_calc.fetched({"kind": "chart", "source": {"body": "x"}}) is None
    assert dash_calc.fetched({"kind": "chart", "source": None}) is None
    ctx = {"range": {"start": "2026-01-05", "end": "2026-01-09"}}
    c = {"kind": "chart", "source": {"body": {"base": "t", "x": "day"}}}
    assert dash_calc.fetched(c, ctx) == dash_calc.with_period({"base": "t", "x": "day"}, "day", dash_calc.fetch_window(ctx))  # 차트는 X가 날짜 컬럼
    n = {"kind": "number", "source": {"body": {"base": "t", "x": "day"}}}
    assert dash_calc.fetched(n, ctx) == dash_calc.with_period({"base": "t", "x": "day"}, None, dash_calc.fetch_window(ctx))
    assert dash_calc.copies_to_pc({"kind": "recent_rows", "source": {"body": {}}}) is False
    assert dash_calc.copies_to_pc({"kind": "chart", "source": None}) is False


def test_unique_per_period_is_one_clean_line():
    text = open(dash_calc.__file__, encoding="utf-8").read()
    assert " " * 9 + "and any(" not in text


# ---------- S-A3b: result.split_parts를 쓴다 ----------

def test_dash_calc_has_no_own_split_parts(con_duck):
    assert not hasattr(dash_calc, "split_parts")
    parts, more = dash_calc.result.split_parts(con_duck.table("t"), "g")
    assert parts == ["a", "b"] or parts == ["b", "a"]
    assert more is False


# ---------- S-A7b: scan_unknown은 block만 ----------

def test_scan_block_removed_and_setting_read_through_scan_size(monkeypatch):
    assert not hasattr(dash_refresh, "SCAN_BLOCK")
    assert scan_size.blocks_unknown({"scan_unknown": "block"}) and not scan_size.blocks_unknown({"scan_unknown": "차단"})


# ---------- S-E4: 비율 지표는 한 번만 조회 ----------

METRICS = {
    "ratio": {"description": "", "format": "percent", "numerator": {"agg": "count", "column": None, "where": ["event = click"]}, "denominator": {"agg": "count", "column": None, "where": []}},
    "zero_den": {"description": "", "format": "number", "numerator": {"agg": "count", "column": None, "where": []}, "denominator": {"agg": "count", "column": None, "where": ["event = never"]}},
    "plain": {"description": "", "format": "number", "numerator": {"agg": "sum", "column": "amt", "where": []}, "denominator": None},
}


def old_scalar(expr, name, m):
    """고치기 전의 계산(집계를 두 번)."""
    from analysis import stats
    row = stats.one(expr.aggregate(v=metric_file.metric_expr(expr, name, m)))
    pair = None
    if m["denominator"] is not None:
        both = stats.one(expr.aggregate(num=metric_file.aggregate(expr, m["numerator"]), den=metric_file.aggregate(expr, m["denominator"])))
        pair = {"num_label": dash_calc.part_text(m["numerator"]), "num": both["num"], "den_label": dash_calc.part_text(m["denominator"]), "den": both["den"]}
    return row["v"], pair, m["format"]


@pytest.mark.parametrize("name", ["ratio", "zero_den", "plain"])
def test_metric_scalar_same_values_and_one_query(name, con_duck, monkeypatch):
    from ibis.expr.types.relations import Table
    expr = con_duck.table("t")
    monkeypatch.setattr(metric_file, "registry", lambda: METRICS)
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=False: expr)
    want = old_scalar(expr, name, METRICS[name])
    ran = []
    original = Table.to_pyarrow
    monkeypatch.setattr(Table, "to_pyarrow", lambda self, *a, **k: ran.append(1) or original(self, *a, **k))
    got = dash_calc.metric_scalar({"base": "t"}, name)
    assert got == want and len(ran) == 1
    if name == "zero_den":
        assert got[0] is None and got[1]["den"] == 0
    if name == "plain":
        assert got[1] is None


def test_metric_scalar_empty_table_and_errors(con_duck, monkeypatch):
    empty = con_duck.table("t").filter(con_duck.table("t").amt > 1e9)
    monkeypatch.setattr(metric_file, "registry", lambda: METRICS)
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=False: empty)
    assert dash_calc.metric_scalar({}, "ratio") == old_scalar(empty, "ratio", METRICS["ratio"])
    assert dash_calc.metric_scalar({}, "plain") == old_scalar(empty, "plain", METRICS["plain"])
    with pytest.raises(ValueError, match="지표"):
        dash_calc.metric_scalar({}, "없는지표")
    bad = {"x": {**METRICS["ratio"], "numerator": {"agg": "sum", "column": "event", "where": []}}}
    monkeypatch.setattr(metric_file, "registry", lambda: bad)
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, select=False: con_duck.table("t"))
    with pytest.raises(ValueError, match="지표 'x'"):
        dash_calc.metric_scalar({}, "x")


# ---------- S-S6: 다른 연결의 카드 ----------

def test_other_source():
    now = {"key": "A"}
    assert dash_app.other_source("B", now) is True
    assert dash_app.other_source("A", now) is False
    assert dash_app.other_source("", now) is False and dash_app.other_source(None, now) is False
    assert dash_app.other_source("B", None) is False


# ---------- S-R8b: pdf 파일 이름은 server의 규칙 ----------

@pytest.mark.parametrize("name,want", [("a/b:c", "a_b_c"), ("CON", "대시보드"), ("lpt3", "대시보드"), ("  ..  ", "대시보드"), (None, "대시보드"), ("x" * 100, "x" * 80), ("보고서?", "보고서_")])
def test_pdf_stem(name, want):
    assert dash_app.pdf_stem(name) == want
    assert not hasattr(dash_app, "RESERVED")
