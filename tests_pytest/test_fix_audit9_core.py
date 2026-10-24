"""9차 감사 수정 9C(core): 고치기 전에는 실패하던 것들. 데이터는 시험 안에서 지어낸다."""
import json

import ibis
import polars as pl
import pytest

from core import parse, result, server, source


# ---------- 9C1 정렬 동률에서 쪽 넘김 ----------

def test_paging_sorted_repeated_values_returns_every_row_once(tmp_path):
    n = 1_200_000
    pq = tmp_path / "big.parquet"
    pl.DataFrame({"i": pl.int_range(n, eager=True), "k": pl.int_range(n, eager=True) % 3}).write_parquet(pq, row_group_size=100_000)
    con = source.open_flat_files([pq])
    t = con.table("big").order_by("k")
    seen = []
    for off in range(0, 400_000, 100_000):
        seen += result.get_rows(t, 100_000, off)["i"].to_list()
    assert len(seen) == len(set(seen)) == 400_000


def test_paging_sorted_in_memory_engine_is_stable(tmp_path):
    n = 300_000
    pq = tmp_path / "m.parquet"
    pl.DataFrame({"i": pl.int_range(n, eager=True), "k": pl.int_range(n, eager=True) % 3}).write_parquet(pq)
    con = source.open_flat_files([pq])
    t = con.table("m").order_by("k")
    seen = []
    for off in range(0, n, 50_000):
        seen += t.limit(50_000, offset=off).to_polars()["i"].to_list()
    assert seen == sorted(range(n), key=lambda i: (i % 3, i))


# ---------- 9C2 목록·구조 안의 이진 값 ----------

def test_text_wide_handles_nested_binary_and_write_panic_becomes_error():
    df = pl.DataFrame({"a": [[b"x"]], "s": [{"r": b"y"}], "b": [b"z"]})
    out = server.text_wide(df, True)
    assert not any("Binary" in str(t) for t in out.dtypes)
    assert json.loads(out.write_json())
    assert out["b"].to_list() == ["7a"]  # 맨 위 이진은 그대로 16진수
    with server.app.test_request_context():
        resp = server.json_reply(df)
    assert json.loads(resp.get_data())["rows"]["rows"]
    with pytest.raises(RuntimeError, match="데이터 엔진 내부 오류"):  # 패닉은 BaseException이라 그대로 두면 연결이 끊긴다
        server.dump(df)


# ---------- 9C3 Parquet 새 스탬프 ----------

def test_refresh_csv_parquet_stores_new_stamp(tmp_path):
    pq = tmp_path / "b.parquet"
    pl.DataFrame({"x": [1, 2, 3]}).write_parquet(pq)
    old = source.CHECK_EVERY
    source.CHECK_EVERY = 0
    try:
        con = source.open_flat_files([pq])
        pl.DataFrame({"x": [1, 2]}).write_parquet(pq)
        assert source.refresh_csv(con) == ["b"]
        gen = dict(con.csv_gen)
        assert source.refresh_csv(con) == []
        assert con.csv_gen == gen
    finally:
        source.CHECK_EVERY = old


# ---------- 9C4 겹치는 이름이 있는 조인 두 개 ----------

def test_two_joins_with_shared_column_name():
    o = ibis.memtable({"oid": [1], "cid": [1], "pid": [1], "name": ["o"]})
    c = ibis.memtable({"cid": [1], "name": ["kim"]})
    p = ibis.memtable({"pid": [1], "name": ["pen"]})
    tables = {"customers": c, "products": p}
    out = parse.apply_joins(o, [("customers", "left", [("cid", "cid")], None), ("products", "left", [("pid", "pid")], None)], tables.__getitem__)
    assert out.count().execute() == 1
    assert len(set(out.columns)) == len(out.columns) == 8 and "name_right" in out.columns and "name_products" in out.columns  # 첫 겹침은 _right 그대로


def test_self_join_twice():
    t = ibis.memtable({"id": [1, 2], "v": [3, 4]})
    out = parse.apply_joins(t, [("t", "inner", [("id", "id")], None), ("t", "inner", [("id", "id")], None)], lambda _: t)
    assert out.count().execute() == 2 and len(set(out.columns)) == len(out.columns)


# ---------- 9C5 진짜 패닉 ----------

def test_within_real_polars_panic():
    with pytest.raises(RuntimeError, match="데이터 엔진 내부 오류"):
        source.within(5, lambda: pl.DataFrame({"a": [[b"x"]]}).write_json(), "late")
    assert source.within(5, lambda: 7, "late") == 7
