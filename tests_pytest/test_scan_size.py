"""실행 전 처리 용량(scan_size): 읽는 테이블·컬럼만 센다. DuckDB는 행 수 × 컬럼 크기, Parquet은 파일 정보, CSV는 파일 크기, BigQuery는 dry run.
데이터는 테스트 안에서 지어낸다(BigQuery는 가짜 클라이언트)."""
import types

import duckdb
import polars as pl
import pytest
from lazy import ibis

import scan_size
import source


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "s.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table orders as select i as id, 'x' || i as name, i * 1.5 as amt from range(1000) t(i)")
    return ibis.duckdb.connect(str(path), read_only=True)


def test_duckdb_columns(db):
    one = scan_size.estimate(db, "select name from orders where id > 5")  # name(글자 16) + id(8)
    assert one == {"bytes": 1000 * (scan_size.STRING_BYTES + 8), "exact": False, "how": "DuckDB 행 수 × 컬럼 크기"}
    assert scan_size.estimate(db, "select * from orders")["bytes"] == 1000 * (scan_size.STRING_BYTES + 8 + 16)  # amt는 decimal(16)
    assert scan_size.message(one) == "실행하면 약 23.4 KB를 처리합니다(추정 · DuckDB 행 수 × 컬럼 크기)"


def test_files(tmp_path):
    pq, csv = tmp_path / "ev.parquet", tmp_path / "ref.csv"
    pl.DataFrame({"a": list(range(5000)), "b": [f"value {i}" for i in range(5000)]}).write_parquet(pq)
    pl.DataFrame({"k": [1, 2]}).write_csv(csv)
    con = source.open_flat_files([pq, csv])
    opened = {"kind": "folder", "path": str(tmp_path), "files": [str(pq), str(csv)]}
    only_a, whole = scan_size.estimate(con, "select a from ev", opened), scan_size.estimate(con, "select * from ev", opened)
    assert only_a["exact"] and 0 < only_a["bytes"] < whole["bytes"]  # 읽는 컬럼만
    both = scan_size.estimate(con, "select a, k from ev join ref on a = k", opened)
    assert both["bytes"] == only_a["bytes"] + csv.stat().st_size and not both["exact"]  # CSV는 파일 크기(최대)


def test_unknown_and_bigquery(db, monkeypatch):
    other = types.SimpleNamespace(name="snowflake", dialect="snowflake")
    assert scan_size.estimate(other, "select 1 from t")["bytes"] is None
    seen = []
    import sys
    fake = types.ModuleType("google.cloud.bigquery")
    fake.QueryJobConfig = lambda dry_run, use_query_cache: types.SimpleNamespace(dry_run=dry_run, use_query_cache=use_query_cache)
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = fake
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", fake)

    class Client:
        def query(self, sql, job_config):
            seen.append((sql, job_config.dry_run))
            return types.SimpleNamespace(total_bytes_processed=5 * 1024 ** 3)
    bq = types.SimpleNamespace(name="bigquery", dialect="bigquery", client=Client())
    found = scan_size.estimate(bq, "select a from ds.t")
    assert found == {"bytes": 5 * 1024 ** 3, "exact": True, "how": "BigQuery dry run"} and seen == [("select a from ds.t", True)]
    assert scan_size.message(found) == "실행하면 5.0 GB를 처리합니다(정확 · BigQuery dry run)"


def test_safe_never_raises():
    found = scan_size.safe(lambda: 1 / 0)
    assert found["bytes"] is None and scan_size.message(found).startswith("처리 용량: 알 수 없음")


def test_scan_route(client, tmp_path, folder):
    import server
    path = tmp_path / "s.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table orders as select i as id, 'x' || i as name from range(100) t(i)")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    d = client.post("/api/scan", json={"base": "orders", "columns": ["id"]}).get_json()
    assert d["bytes"] == 100 * 8 and d["message"].startswith("실행하면 약 800 B")
