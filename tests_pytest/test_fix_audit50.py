"""(감사50) 적재 키 건너뛰기를 Polars 식으로: 테이블에 있는 키와 묶음 안의 두 번째부터는 빼고, 빈 값이 든 키는 늘 넣는다. 값은 시험 안에서 지어낸다."""
import datetime as dt

import pyarrow as pa

from collect import load_table

UTC = dt.timezone.utc
DAY1, DAY2 = dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2026, 1, 2, tzinfo=UTC)


def sink(monkeypatch, have):
    put = []
    monkeypatch.setattr(load_table, "existing", lambda con, schema, name, keys, data: set(have))
    monkeypatch.setattr(load_table, "insert", lambda con, schema, name, data: put.append(data))
    s = load_table.Sink.__new__(load_table.Sink)
    s.con, s.schema, s.name, s.staging, s.keys, s.fill, s.load_id = None, "", "t", None, ["a", "t"], {}, None
    return s, put


def test_keys_skip_table_and_repeats_keep_nulls(monkeypatch):
    s, put = sink(monkeypatch, {(1, DAY1)})
    data = pa.table({"a": pa.array([1, 1, 2, 2, None, None, 3], pa.int64()), "t": pa.array([DAY1, DAY2, DAY1, DAY1, DAY1, DAY1, None], pa.timestamp("us", tz="UTC")),
                     "v": list(range(7))})
    assert s.put(data) == (5, 2, 0)
    assert put[0].column("v").to_pylist() == [1, 2, 4, 5, 6]  # (1, DAY1)은 테이블에, 두 번째 (2, DAY1)은 묶음 안에 이미


def test_keys_with_empty_table(monkeypatch):
    s, put = sink(monkeypatch, set())
    assert s.put(pa.table({"a": pa.array([5, 5], pa.int64()), "t": pa.array([DAY1, DAY1], pa.timestamp("us", tz="UTC"))})) == (1, 1, 0)
