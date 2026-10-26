"""15차 감사 묶음 A: 받아 둔 결과·적재·전처리가 DB에 올린 memtable 뷰와 지울 표를 실제로 지우는지(진짜 DuckDB·SQLite로). 값은 시험 안에서 지어낸다."""
import threading

import pyarrow as pa
import pytest

from lazy import ibis


@pytest.fixture
def fresh_held(folder):
    from core import held
    held.clear()
    yield held
    held.clear()


def test_held_keeps_at_most_keep_tables_and_no_memtable_views(fresh_held):
    held, stop = fresh_held, threading.Event()
    t = ibis.memtable({"a": list(range(100))})
    for i in range(held.KEEP + 2):
        held.ensure(f"k{i}", t, stop)
    names = held.local().list_tables()
    assert len([n for n in names if n.startswith("r")]) <= held.KEEP
    assert [n for n in names if n.startswith("ibis_pyarrow_")] == []


def test_insert_all_failure_keeps_original_error_and_drops_table(folder, tmp_path, monkeypatch):
    from core import result
    from prep import prep_app
    db = ibis.sqlite.connect(tmp_path / "t.sqlite")

    def broken(expr):
        yield pa.record_batch({"a": [1]})
        raise RuntimeError("원래 오류")
    monkeypatch.setattr(result, "batches", broken)
    with pytest.raises(RuntimeError, match="원래 오류"):
        prep_app.insert_all(db, "new_t", None, ibis.memtable({"a": [1, 2]}))
    assert db.list_tables() == []  # 만든 표도, 넣은 묶음의 memtable도 남지 않는다


def test_duckdb_load_insert_leaves_no_memtable_view(tmp_path):
    from collect import load_table
    from core import source
    con = source.open_duckdb(tmp_path / "w.duckdb", read_only=False)
    try:
        data = pa.table({"a": [1, 2, 3]})
        load_table.create(con, None, "t", data.schema)
        load_table.insert(con, None, "t", data)
        names = source.within(None, lambda: con.list_tables(), "")
        assert [n for n in names if n.startswith("ibis_")] == []
        assert source.within(None, lambda: con.table("t").count().execute(), "") == 3
    finally:
        source.close(con)


def test_prep_sample_run_drops_its_memtable(folder):
    from prep import prep_app
    default = ibis.get_backend()
    before = set(default.list_tables())  # 다른 시험이 남긴 것은 보지 않는다
    assert prep_app.sample_run(pa.table({"a": [1, 2, 3]}), lambda t: t.a.sum().execute()) == 6
    assert set(default.list_tables()) - before == set()
    flat = ibis.polars.connect()  # 폴더(파일) 연결: 좌표 단계처럼 표본을 이 연결의 표와 잇는 경우
    flat.create_table("area", pa.table({"a": [1, 2]}))
    assert prep_app.sample_run(pa.table({"a": [1, 2, 3]}), lambda t: t.join(flat.table("area"), "a").count().execute(), flat) == 2
    assert flat.list_tables() == ["area"]
