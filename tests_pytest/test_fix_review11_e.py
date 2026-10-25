"""11차 리뷰 묶음 E: 적재(결과 CSV 읽기, DB 테이블에 넣기·키 건너뛰기, Kafka 보내기). 진짜 DuckDB·SQLite·pyarrow로 시험하고 데이터는 시험 안에서 만든다."""
import datetime as dt
import threading
import tracemalloc

import pyarrow as pa
import pytest

from collect import collect_config, load_job, load_table
from core import source


def rows_of(path, size=1000):
    with load_job.Batches(path, size) as batches:
        return batches.schema, pa.concat_tables(list(batches))


# ---------- 11E1: 결과 CSV를 파일 전체를 보고 읽는다 ----------

def test_csv_keeps_leading_zero_text(tmp_path):
    path = tmp_path / "out.csv"
    path.write_text("code,n\n00001,1\n00020,2\n", encoding="utf-8-sig")
    _, table = rows_of(path)
    assert table.column("code").to_pylist() == ["00001", "00020"]


def test_csv_column_empty_at_first_then_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(load_job, "CSV_BLOCK", 1 << 16, raising=False)  # 고치기 전 코드가 첫 덩어리로 타입을 정하던 것을 작은 파일로 재현
    path = tmp_path / "out.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write("id,late\n")
        f.writelines(f"{i},\n" for i in range(50_000))
        f.write("50000,뒤에 온 값\n")
    schema, table = rows_of(path, 7_000)
    assert not pa.types.is_null(schema.field("late").type)
    assert table.num_rows == 50_001 and table.column("late").to_pylist()[-1] == "뒤에 온 값"


def test_csv_semicolon_delimiter(tmp_path):
    path = tmp_path / "out.csv"
    path.write_text("a;b\n1;x\n2;y\n", encoding="utf-8")
    schema, table = rows_of(path)
    assert schema.names == ["a", "b"] and table.column("b").to_pylist() == ["x", "y"]


# ---------- 11E2·11E3·11E4: DB 테이블 ----------

def sqlite(tmp_path):
    return source.open_url(f"sqlite:///{(tmp_path / 'w.sqlite').as_posix()}")


@source.on_worker
def tables(con):
    return con.list_tables()


def test_sqlite_insert_null_timestamp_big_int_and_no_left_memtable(tmp_path):
    con = sqlite(tmp_path)
    try:
        data = pa.table({"t": pa.array([dt.datetime(2024, 1, 2, 3, 4, 5), None], pa.timestamp("us")), "n": pa.array([2 ** 53 + 1, None], pa.int64())})
        load_table.create(con, "", "items", data.schema)
        load_table.insert(con, "", "items", data)
        load_table.insert(con, "", "items", data)
        assert tables(con) == ["items"]  # 묶음마다 올린 임시 테이블(memtable)이 남지 않는다
    finally:
        source.close(con)
    import sqlite3
    with sqlite3.connect(tmp_path / "w.sqlite") as raw:  # Ibis로 읽으면 읽을 때 실수를 거쳐 큰 정수가 틀어지므로 저장된 값을 바로 본다
        got = raw.execute("select n, t from items").fetchall()
    raw.close()
    assert [n for n, _ in got] == [2 ** 53 + 1, None, 2 ** 53 + 1, None] and got[1][1] is None


def test_existing_splits_in_list_for_oracle(tmp_path):
    import ibis
    data = pa.table({"k": list(range(2_500)), "g": [i % 3 for i in range(2_500)]})
    table = ibis.table({"k": "int64", "g": "int64"}, name="items")
    queries = load_table.lookups(table, ["k", "g"], data)
    sqls = [ibis.to_sql(q, dialect="oracle") for q in queries]
    assert len(sqls) == 3 and all(s.count(",") < 2 * 1000 + 10 for s in sqls)  # IN 목록마다 1000개 이하
    con = sqlite(tmp_path)
    try:
        load_table.create(con, "", "items", data.schema)
        load_table.insert(con, "", "items", data.slice(0, 1_200))
        have = load_table.existing(con, "", "items", ["k", "g"], data)
        assert have == {(i, i % 3) for i in range(1_200)}
    finally:
        source.close(con)


def table_config(tmp_path, batch):
    return collect_config.normalize({"mode": "api", "request": {"url": "https://api.example.com/items"}, "response": {"records": "items"},
                                     "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")},
                                     "load": {"target": "table", "batch": batch, "retries": 0,
                                              "table": {"connection": {"kind": "url", "url": f"sqlite:///{(tmp_path / 'w.sqlite').as_posix()}"},
                                                        "name": "items", "how": "append", "key": ["k"]}}})


@pytest.mark.parametrize("batch", [100, 1000])
def test_key_skip_same_regardless_of_batch_size(tmp_path, folder, batch):
    import pyarrow.parquet as pq
    keys = [1, 1, *range(100, 197), None, None, 1, 2, 2]  # 묶음 100이면 0~99행이 첫 묶음, 100~103행이 둘째 묶음
    data = pa.table({"k": keys, "v": list(range(len(keys)))})
    path = tmp_path / "out.parquet"
    pq.write_table(data, path)
    con = sqlite(tmp_path)
    try:
        load_table.create(con, "", "items", data.schema)
    finally:
        source.close(con)
    load_job.run(table_config(tmp_path, batch), path, "", threading.Event(), lambda **k: None)
    con = sqlite(tmp_path)
    try:
        got = source.within(None, lambda: con.table("items").to_pyarrow().to_pylist(), "")
    finally:
        source.close(con)
    assert sorted(r["v"] for r in got) == [v for v in range(len(keys)) if v not in (1, 101, 103)]  # 같은 키는 첫 행만, 빈 키는 모두(다른 묶음의 빈 키도 늘 넣는 것과 같게)


# ---------- 11E5: Kafka는 묶음을 나눠 Python 값으로 바꾼다 ----------

def test_kafka_does_not_turn_whole_batch_into_python(monkeypatch):
    from test_fix_audit2_dash import _Producer, _sink
    p = _Producer()
    sink = _sink(monkeypatch, p)
    n = 60_000
    data = pa.table({"id": list(range(n)), **{f"c{j}": [f"값{j}-{i:08d}" for i in range(n)] for j in range(6)}})
    p.flush = lambda t: (p.queue.clear(), 0)[1]  # 메시지 몸을 쌓아 두지 않는다(재는 것은 행을 바꾼 크기)
    p.produce = lambda topic, body, key, on_delivery: on_delivery(None, None)
    tracemalloc.start()
    try:
        assert sink.put(data) == (n, 0, 0)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 30 << 20, peak  # 묶음 전체를 dict로 바꾸면 수십 MB
