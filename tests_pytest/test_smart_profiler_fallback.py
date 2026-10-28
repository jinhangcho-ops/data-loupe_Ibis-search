"""비용을 아끼는 추론(profiler): 표본 방법의 차례(미리보기 → 작은 테이블 전부 → 블록 샘플링 → 최신 파티션 → 앞 행)와 넘어가기,
Parquet 파일 정보, 데이터 명세서, 추론, 캐시. 블록 샘플링은 무작위라 차례는 가짜 단계로 확인하고, 각 방법은 실제 DuckDB로 확인한다."""
import datetime as dt
import json

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lazy import ibis

from core import profiler


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "shop.duckdb"
    raw = duckdb.connect(str(path))
    today = dt.date.today()
    raw.execute("create table small as select i as id, case when i % 2 = 0 then 'Y' else 'N' end as flag from range(10) t(i)")
    raw.execute(f"create table parted as select i as id, date '{today}' - cast(i % 3 as integer) as day from range(30) t(i)")
    raw.close()
    con = ibis.duckdb.connect(str(path))
    yield con
    con.disconnect()


def test_fallback_order_matches_spec():
    assert [name for name, _ in profiler.FALLBACK] == ["preview", "whole", "block", "partition", "head"]


def test_falls_through_errors_and_none(monkeypatch):
    calls = []

    def step(name, outcome):
        def run(*args):
            calls.append(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return name, run

    table = pa.table({"x": [1]})
    monkeypatch.setattr(profiler, "FALLBACK", [step("preview", None), step("whole", None), step("block", RuntimeError("과금 거부")), step("partition", table), step("head", table)])
    found, method, passed = profiler.sample(None, None, "", "t", 5000, 10**9, None)
    assert (calls, method, found, passed) == (["preview", "whole", "block", "partition"], "partition", table, [["block", "과금 거부"]])
    monkeypatch.setattr(profiler, "FALLBACK", [step("preview", ValueError("a")), step("head", ValueError("b"))])
    with pytest.raises(ValueError, match="표본을 읽지 못했어요"):
        profiler.sample(None, None, "", "t", 5000, None, None)


def test_preview_api_only_for_bigquery():
    class Rows:
        def to_arrow(self):
            return pa.table({"x": [1, 2]})

    class Client:
        asked = None

        def list_rows(self, ref, max_results):
            Client.asked = (ref, max_results)
            return Rows()

    class BigQuery:
        name, current_catalog, current_database, client = "bigquery", "proj", "ds", Client()

    found, method, _ = profiler.sample(BigQuery(), None, "", "events", 5000, None, None)
    assert (method, found.num_rows, Client.asked) == ("preview", 2, ("proj.ds.events", 5000))
    assert profiler.preview(object(), None, "", "t", 5000, None, None) is None


def test_small_table_read_whole(db):
    found, method, passed = profiler.sample(db, db.table("small"), "", "small", 5000, 10, None)
    assert (method, found.num_rows, passed) == ("whole", 10, [])


def test_block_too_few_moves_on(db):
    view = db.sql("select * from small")  # 뷰의 블록 샘플링은 0행이 올 수 있다 → 다음 방법으로
    assert profiler.block(db, view, "", "small", 1000, 5000, None) is None
    assert profiler.block(db, view, "", "small", 1000, None, None) is None  # 행 수를 모르면 하지 않는다


def test_partition_uses_spec(db, monkeypatch):
    monkeypatch.setattr(profiler, "block", lambda *a: None)
    monkeypatch.setattr(profiler, "FALLBACK", [(n, profiler.block if n == "block" else f) for n, f in profiler.FALLBACK])
    t, today = db.table("parted"), dt.date.today()
    found, method, _ = profiler.sample(db, t, "", "parted", 5, 30, {"column": "day", "latest": today})
    assert method == "partition" and set(found.column("day").to_pylist()) == {today} and found.num_rows == 5
    found, method, _ = profiler.sample(db, t, "", "parted", 100, 300, {"column": "day", "recent_days": 2})
    assert method == "partition" and min(found.column("day").to_pylist()) >= today - dt.timedelta(days=1)
    found, method, passed = profiler.sample(db, t, "", "parted", 5, 30, {"column": "없는컬럼", "latest": today})
    assert method == "head" and passed[0][0] == "partition"  # 명세서가 틀리면 앞 행으로
    assert profiler.sample(db, t, "", "parted", 5, 30, None)[1] == "head"  # 파티션이 없으면 앞 행


def test_parquet_facts_read_no_data(tmp_path):
    path = tmp_path / "f.parquet"
    pq.write_table(pa.table({"same": ["A"] * 6, "gap": [1, None, 3, None, 5, 6], "none": pa.array([None] * 6, pa.int64())}), path, row_group_size=2)
    facts = profiler.parquet_facts(path)
    assert facts["rows"] == 6
    assert facts["columns"]["same"] == {"nulls": 0, "min": "A", "max": "A", "constant": True}
    assert facts["columns"]["gap"] == {"nulls": 2, "min": 1, "max": 6, "constant": False}
    assert facts["columns"]["none"]["constant"] is True


def test_spec_json_and_yaml(tmp_path, folder):
    spec = {"tables": {"sales.orders": {"description": "주문", "partition": {"column": "day", "recent_days": 3}, "columns": {"status": {"description": "상태"}}}}}
    (tmp_path / "s.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "s.yaml").write_text("tables:\n  orders:\n    description: 주문\n    partition: {column: day, latest: '2026-10-01'}\n", encoding="utf-8")
    assert profiler.read_spec(tmp_path / "s.json") == spec
    assert profiler.read_spec(tmp_path / "s.yaml")["tables"]["orders"]["partition"] == {"column": "day", "latest": "2026-10-01"}
    assert profiler.save_spec_path(str(tmp_path / "s.json")) == ["sales.orders"]
    assert profiler.spec_table("sales", "orders")["description"] == "주문" and profiler.spec_table("", "orders") == {}
    for bad in ({"tables": []}, {"tables": {"t": {"partition": {"column": "d"}}}}, {"tables": {"t": {"partition": {"column": "d", "recent_days": 0}}}}):
        (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError):
            profiler.read_spec(tmp_path / "bad.json")
    (tmp_path / "s.json").write_text("{깨진", encoding="utf-8")
    assert profiler.spec_table("sales", "orders") == {}  # 명세서가 깨져도 통계는 명세서 없이 구한다
    with pytest.raises(ValueError):
        profiler.read_spec(tmp_path / "s.txt")


def test_infer_columns():
    found = profiler.infer(pa.table({"id": list(range(8)), "flag": ["Y", "N"] * 4, "one": ["x"] * 8, "ymd": ["20260101", "20251231"] * 4,
                                     "iso": ["2026-01-01"] * 7 + [None]}))
    assert "flags" not in found["id"] and found["flag"]["flags"] == ["N", "Y"]
    assert found["one"]["constant"] is True and found["ymd"]["date_format"] == "YYYYMMDD"
    assert (found["iso"]["date_format"], found["iso"]["null_ratio"]) == ("YYYY-MM-DD", 0.125)


def test_cache_by_size(folder):
    profiler.remember("k", 5000, profile={"rows": 1}, sampled={"rows": 1, "method": "whole", "passed": []})
    profiler.remember("k", 5000, patterns={"c": []})
    assert profiler.cached("k", 5000)["profile"] == {"rows": 1} and profiler.cached("k", 5000)["patterns"] == {"c": []}
    assert profiler.cached("k", 1000) is None  # 표본 크기가 다르면 다시 구한다
    profiler.remember("k", 1000, profile={"rows": 2})
    assert "patterns" not in profiler.cached("k", 1000)
