"""4차 감사 묶음 4B(Python) 시험. 값은 시험 안에서 지어내고 손으로 계산했다."""
import datetime as dt
import time
import types
from pathlib import Path

import polars as pl
import pytest
from lazy import ibis

from core import held, prep_ops, server, source
from geo import geojson
from metrics import catalog_app, metric_catalog as cat, metric_kernels as mk
from prep import prep_app, prep_expr
from query import sentence_run

D = dt.datetime


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def cp949(path, *rows):
    path.write_bytes(("이름,값\n" + "".join(f"{r}\n" for r in rows)).encode("cp949"))


def count(con, name):
    t = server.any_table(con, name, None, vts={})  # 화면이 표를 꺼내는 길
    return int(con.execute(t.aggregate(n=t.count())).n[0])


@pytest.fixture
def opened():
    cons = []

    def make(*paths):
        con = source.open_flat_files.__wrapped__(list(paths))
        cons.append(con)
        server.state["tables"].clear()
        return con
    yield make
    for c in cons:
        source.close(c)
    server.state["tables"].clear()


# ---------- 4B1 화면 경로에서도 바뀐 CSV를 읽는다 ----------

def test_screen_path_sees_edited_cp949_csv(tmp_path, opened):
    p = tmp_path / "k.csv"
    cp949(p, "가,1", "나,2")
    con = opened(p)
    assert count(con, "k") == 2
    cp949(p, "가,1", "나,2", "다,3")
    assert count(con, "k") == 3


# ---------- 4B2 UTF-8 CSV는 다시 등록하지 않는다 ----------

def test_utf8_csv_is_not_tracked_or_rechecked(tmp_path, opened, monkeypatch):
    p = tmp_path / "u.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    con = opened(p)
    assert not getattr(con, "csv_sources", {})
    calls = []
    monkeypatch.setattr(source, "utf8_csv", lambda *a: calls.append(a))
    p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    source.refresh_csv(con)
    assert calls == []
    assert count(con, "u") == 2  # Polars가 필요할 때 원본을 읽으니 오류 없이 센다


# ---------- 4B4 한 파일이 실패해도 다른 표는 된다 ----------

def test_one_failed_csv_does_not_break_the_others(tmp_path, opened):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    cp949(a, "가,1")
    cp949(b, "나,2")
    con = opened(a, b)
    b.write_bytes("이름,값\n나,2\n".encode("utf-16"))  # b를 UTF-16으로 다시 저장
    cp949(a, "가,1", "다,3")
    assert count(con, "a") == 2  # a는 새 값
    with pytest.raises(Exception, match="UTF-16"):
        count(con, "b")
    assert count(con, "a") == 2  # 이어서 해도 a는 계속 된다
    cp949(b, "나,2", "라,4", "마,5")  # 고쳐 저장하면 b도 다시 된다
    assert count(con, "b") == 3


# ---------- 4B5 예전 임시 사본을 지운다 ----------

def test_old_scratch_copies_are_removed(tmp_path, opened):
    p = tmp_path / "k.csv"
    cp949(p, "가,1")
    con = opened(p)
    for i in range(4):
        cp949(p, *["가,1"] * (i + 2))
        source.refresh_csv(con)
    assert len([x for x in Path(con.scratch).iterdir() if x.suffix == ".csv"]) == 1
    assert count(con, "k") == 5


def test_drop_copy_only_inside_scratch(tmp_path, opened):
    p = tmp_path / "k.csv"
    cp949(p, "가,1")
    con = opened(p)
    outside = tmp_path / "keep.csv"
    outside.write_text("x")
    source.drop_copy(con, outside)
    assert outside.exists()


# ---------- 4B6 지표 도우미 ----------

def test_first_keys_and_revenue_by_offset_pick_first_key_in_key_order():
    # 사용자 1: 1일에 b(5)·a(7), 3일에 b(10) -> 첫 키는 같은 날이면 키 순으로 a. 사용자 2: 2일에 b(1)
    x = mem(u=[1, 1, 1, 2], day=[1, 1, 3, 2], k0=["b", "a", "b", "b"], amt=[5.0, 7.0, 10.0, 1.0])
    first = {r["u"]: r["k0"] for r in mk.first_keys(x, ["k0"]).to_pyarrow().to_pylist()}
    assert first == {1: "a", 2: "b"}
    align = {"kind": "key", "keys": [["k", "k"]]}
    got = mk.revenue_by_offset(x, align, 5)
    assert got == {(("a",), 0): 12.0, (("a",), 2): 10.0, (("b",), 0): 1.0} or got == {("a", 0): 12.0, ("a", 2): 10.0, ("b", 0): 1.0}


def test_top_rows_notes_when_long():
    ctx = cat.Ctx()
    rows = list(range(mk.MAX_ROWS + 5))
    assert mk.top_rows(rows, ctx, "코호트") == rows[:mk.MAX_ROWS] and len(ctx.notes) == 1
    ctx = cat.Ctx()
    assert mk.top_rows([1, 2], ctx, "코호트") == [1, 2] and ctx.notes == []


def test_usable_keys_accepts_copies_with_fewer_skipped_columns(monkeypatch):
    monkeypatch.setitem(held.store, "items", {
        ("prep", "T", "[]", ()): ("n1", 1), ("prep", "T", "[]", ("x",)): ("n2", 1), ("prep", "T", "[]", ("x", "y")): ("n3", 1),
        ("prep", "U", "[]", ()): ("n4", 1), ("prep", "T", "[]", ("z",)): (None, 1)})
    assert prep_app.usable_keys(("prep", "T", "[]", ("x",))) == [("prep", "T", "[]", ()), ("prep", "T", "[]", ("x",))]


def test_fills_stat_and_outlier_order():
    assert prep_ops.fills_stat("fill", {"how": "mean"}) and prep_ops.fills_stat("fill", {"how": "mode"})
    assert not prep_ops.fills_stat("fill", {"how": "drop"}) and not prep_ops.fills_stat("fill", {"how": "value", "value": 1}) and not prep_ops.fills_stat("outlier", {"how": "mean"})
    assert prep_ops.OUTLIER_AT == prep_ops.OPS_NAMES.index("outlier")


def test_outlier_and_fill_stats_do_not_depend_on_column_order():
    t = ibis.memtable(pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 1000.0, None], "b": [10.0, None, 30.0, 40.0, 50.0, 60.0]}).to_arrow())
    cols = {"a": {"ops": {"outlier": {"by": "iqr", "how": "drop"}}}, "b": {"ops": {"fill": {"how": "mean"}}}}

    def run(c):
        rows = prep_expr.shape(None, {"base": "x", "columns": c}, t)[0].to_pyarrow().to_pylist()
        return sorted((-1 if r["a"] is None else r["a"], -1 if r["b"] is None else r["b"]) for r in rows)
    assert run(cols) == run(dict(reversed(list(cols.items()))))


# ---------- 4B7 기간 맞춤은 사본이 필요 없다 ----------

def test_period_align_does_not_need_copy_on_remote(monkeypatch):
    monkeypatch.setattr(catalog_app.settings, "load", lambda: {"prep_where": "new"})
    remote = types.SimpleNamespace(name="postgres")
    cac = cat.get("cac")
    assert catalog_app.needs_copy(remote, cac, {"kind": "key", "keys": [["a", "a"]]}) and catalog_app.needs_copy(remote, cac)
    assert not catalog_app.needs_copy(remote, cac, {"kind": "period", "unit": "month"})
    assert catalog_app.needs_copy(remote, cat.get("session_count"), {"kind": "period"})  # 두 테이블 지표가 아니면 그대로
    assert catalog_app.needs_copy(types.SimpleNamespace(name="polars"), cac, {"kind": "period"})


# ---------- 4B8 오류 문구에 자리표시가 나오지 않는다 ----------

def test_spaced_column_error_shows_original_text():
    with pytest.raises(sentence_run.Bad) as e:
        sentence_run.formula_one("unit price 이상한말", ["unit price"])
    assert "unit price 이상한말" in str(e.value) and "\x00" not in str(e.value)


# ---------- 4B9 payback 솎기 안내, 날짜 변경선 ----------

def test_payback_thinning_is_noted():
    users = mem(uid=[1, 2], ts=[D(2026, 3, 1), D(2026, 3, 1)], ch=["A", "A"], amt=[10.0, 10.0])
    ads = {"ads": mem(cost=[5.0], ch=["A"])}
    s = {"사용자": "uid", "시각": "ts", "금액": "amt", "최대 일수": 1000, "광고 테이블": "ads", "광고비": "cost"}
    ctx = cat.Ctx(table=users, other=lambda n: ads[n])
    got = cat.compute("payback", s, {}, ctx, {"kind": "key", "keys": [["ch", "ch"]]}, None)
    assert len(got["rows"]) < 1001 and any("하나씩만" in n for n in got["notes"])


def test_crosses_dateline_matches_old_rule_and_is_fast():
    old = lambda xs: (lambda v: len(v) >= 2 and v[-1] - v[0] < 360 and max(b - a for a, b in zip(v, v[1:])) > 360 - geojson.DATELINE_SPAN)(sorted(set(xs)))
    for xs in ([178, -179], [-180, 0, 180], [-100, 100], [5], [], [10, 20, 30], [179.5, -179.5, 178]):
        assert geojson.crosses_dateline(xs) == old(xs), xs
    big = [i * 0.00035 - 175 for i in range(1_000_000)]  # 서로 다른 경도 100만 개
    t0 = time.perf_counter()
    geojson.crosses_dateline(iter(big))
    assert time.perf_counter() - t0 < 0.5


# ---------- 4B3 pca도 전체 집계라 통계 단계 ----------

def test_pca_computes_stats():
    assert prep_app.computes_stats({"base": "t", "pca": {"columns": ["a", "b"], "n": 1}}) is True
    assert prep_app.computes_stats({"base": "t"}) is False
