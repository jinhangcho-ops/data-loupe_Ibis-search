"""결과 전체를 한 번에 받아 버리는 연결(ibis 12의 SQLite·MySQL·SingleStore)은 커서에서 묶음씩 받는다(result.whole_fetch·cursor_batches).
지어낸 SQLite 파일로만 확인한다(다른 DB에는 붙지 않는다). 받은 값·타입이 Ibis의 받기와 같은지, 묶음 크기를 넘게 읽지 않는지 본다."""
import sqlite3

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq
import pytest

from lazy import ibis


@pytest.fixture
def lite(tmp_path):
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table t (id integer, amt real, name text, day date, at timestamp, ok boolean)")
    raw.executemany("insert into t values (?, ?, ?, ?, ?, ?)",
                    [(i, i / 4 if i % 7 else None, f"이름{i % 13}" if i % 5 else None, f"2026-01-{1 + i % 28:02d}",
                      f"2026-01-01 0{i % 10}:30:00", i % 2) for i in range(1, 251)])
    raw.commit()
    raw.close()
    con = ibis.sqlite.connect(tmp_path / "s.db")
    yield con
    con.disconnect()


def test_whole_fetch_is_chosen_by_how_ibis_fetches(lite, tmp_path):
    from core import result
    assert result.whole_fetch(lite)
    assert not result.whole_fetch(ibis.duckdb.connect())  # DuckDB는 Ibis가 이미 묶음씩 받는다
    assert not result.whole_fetch(ibis.polars.connect())


def test_same_values_and_types_in_small_batches(lite, monkeypatch):
    from core import result
    seen = []
    real = type(lite)._fetch_from_cursor
    monkeypatch.setattr(type(lite), "_fetch_from_cursor", lambda self, cursor, schema: (seen.append(len(cursor)), real(self, cursor, schema))[1])
    t = lite.table("t")
    expr = t.filter(t.id > 3).order_by("id")
    got = list(result.batches(expr, size=40))
    assert [b.num_rows for b in got] == [40] * 6 + [7] and max(seen) == 40  # 커서에서 40행씩만 읽는다
    whole = pa.Table.from_batches(got)
    monkeypatch.setattr(type(lite), "_fetch_from_cursor", real)
    assert whole.equals(expr.to_pyarrow())  # Ibis가 한 번에 받은 것과 값·타입이 같다(빈 값, 날짜·시각, 참거짓 포함)


def test_falls_back_when_ibis_internals_change(lite, monkeypatch):
    from core import result

    def broken(self, cursor, schema):
        raise AttributeError("바뀐 Ibis")
    t = lite.table("t")
    want = t.to_pyarrow()
    monkeypatch.setattr(type(lite), "_fetch_from_cursor", broken)
    monkeypatch.setattr(type(lite), "to_pyarrow_batches", lambda self, expr, chunk_size=0, **k: pa.RecordBatchReader.from_batches(want.schema, want.to_batches()))
    assert pa.Table.from_batches(list(result.batches(t, size=40))).equals(want)


def test_db_errors_are_not_hidden(lite):
    from core import result
    t = lite.table("t")
    lite.raw_sql("drop table t")
    with pytest.raises(Exception) as found:
        list(result.batches(t, size=40))
    assert not isinstance(found.value, (AttributeError, TypeError))


def test_files_match_ibis_writers(lite, tmp_path):
    from core import result
    t = lite.table("t").order_by("id")
    result.write_parquet(t, tmp_path / "new.parquet")
    t.to_parquet(tmp_path / "old.parquet")
    assert pq.read_table(tmp_path / "new.parquet").equals(pq.read_table(tmp_path / "old.parquet"))
    result.write_csv(t, tmp_path / "new.csv")
    t.to_csv(tmp_path / "old.csv")
    assert (tmp_path / "new.csv").read_bytes() == (tmp_path / "old.csv").read_bytes()
    empty = t.filter(t.id < 0)
    result.write_csv(empty, tmp_path / "empty.csv")
    assert pcsv.read_csv(tmp_path / "empty.csv").column_names == list(t.columns)  # 행이 없어도 머리글은 쓴다
