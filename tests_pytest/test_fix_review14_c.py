"""14C: 행 수 세기를 끈 원격 검색이 큰 결과를 받다 버리지 않기(D4), BigQuery 요금 확인(집계·저장·쪽 넘기기에서 다시 돌릴 때, D5),
요약의 고유값(D8), 값 목록 정렬의 빈 값 처리(D7), 원격 저장 경고(D10), 엑셀의 큰 정수(A2).
원격 DB는 지어낸 SQLite 파일(sqlite://)·DuckDB 파일에 실제로 연결하고, BigQuery는 그 연결에 dry run만 흉내 내는 가짜 클라이언트를 붙인다(접속 없음)."""
import re
import sqlite3
import sys
import types
import zipfile

import duckdb
import pytest
from lazy import ibis

ROWS = 23


@pytest.fixture
def remote(folder, tmp_path):
    """sqlite:// 주소로 연결한 시험 클라이언트(held.remote가 참인 연결). (요청 함수, 응답 그대로 받는 함수)."""
    from core import server
    from query.search_app import app
    raw = sqlite3.connect(tmp_path / "shop.db")
    raw.execute("create table orders (id integer, grp integer, name text)")
    raw.executemany("insert into orders values (?, ?, ?)", [(i, i % 5, f"n{i}") for i in range(ROWS)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 'shop.db'}"}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def bigquery(remote, monkeypatch):
    """remote 연결을 BigQuery처럼 보이게 한다: 이름과 dry run(처리 바이트 10 TB를 답함)만 가짜. 서버에 결과를 두는 쪽 읽기(query·list_rows)도 흉내 낸다."""
    from core import server
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = types.SimpleNamespace(QueryJobConfig=lambda **k: types.SimpleNamespace(**k))
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", cloud.bigquery)

    class Client:
        dry, runs = [], []

        def query(self, sql, job_config=None, project=None):
            if job_config is not None and getattr(job_config, "dry_run", False):
                self.dry.append(sql)
                return types.SimpleNamespace(total_bytes_processed=10 * 1024 **4)
            self.runs.append(sql)
            return types.SimpleNamespace(destination="temp.r", result=lambda: types.SimpleNamespace(total_rows=ROWS))

        def list_rows(self, table, start_index=0, max_results=None):
            import pyarrow as pa
            return types.SimpleNamespace(to_arrow=lambda: pa.Table.from_pylist([{"id": i} for i in range(ROWS)][start_index:start_index + max_results]))
    con = server.state["con"]
    monkeypatch.setattr(con, "name", "bigquery", raising=False)
    con.client = Client()
    yield con.client
    del con.client


# ---------- D4 행 수 세기를 끈 원격 검색 ----------

def test_count_off_does_not_stream_big_results(remote, monkeypatch):
    from core import held
    from core import result
    streamed = []
    real = result.batches
    monkeypatch.setattr(result, "batches", lambda expr, size=None: (b for b in real(expr, size) if not streamed.append(b.num_rows)))
    monkeypatch.setattr(held, "MAX_ROWS", 3)  # 이 PC에 받아 두기에는 큰 결과
    d = remote.post("/api/query", json={"base": "orders", "limit": 5, "count": False, "fresh": True}).get_json()
    assert not d["held"] and d["count"] is None and len(d["rows"]["rows"]) == 5 and "받아 두지 않고" in d["note"]
    assert sum(streamed) == 0  # 상한을 넘는지는 세어서 알고, 받았다 버리지 않는다
    monkeypatch.setattr(held, "MAX_ROWS", 1000)
    small = remote.post("/api/query", json={"base": "orders", "limit": 5, "count": False, "fresh": True}).get_json()
    assert small["held"] and small["count"] == ROWS and sum(streamed) == ROWS  # 작은 결과는 그대로 받아 두고 쪽은 이 PC에서


def test_count_label_matches_behavior():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "static" / "query" / "index.html").read_text(encoding="utf-8")
    label = re.search(r'<label title="([^"]*)"><input type="checkbox" id="count"', html)[1]
    assert "한 번으로" not in label and "100만" in label


# ---------- D5 BigQuery 요금 확인 ----------

def test_bigquery_aggregate_checks_dry_run(bigquery, monkeypatch):
    from core import scan_size
    from core import server
    found = scan_size.limit_check(server.state["con"], "SELECT COUNT(DISTINCT name) FROM orders", None, "sqlite", {"memory": 4096})
    assert found["over"] and found["bytes"] == 10 * 1024 ** 4 and bigquery.dry and "읽어요" in found["text"]


def test_bigquery_export_and_summary_ask_first(bigquery, remote, monkeypatch):
    from core import scan_size
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (10 ** 9, "작업 메모리 상한 테스트"))
    body = {"base": "orders", "fresh": True}
    for api, extra in [("/api/export", {"format": "csv"}), ("/api/summary", {}), ("/api/sql/export", {"sql": "select * from orders", "format": "csv"})]:
        r = remote.post(api, json={**body, **extra})
        assert r.headers.get("X-Needs-Confirm") == "1", api  # 저장·요약도 읽는 양(요금)을 먼저 묻는다
        if api == "/api/summary":
            assert "읽어요" in r.get_json()["text"]  # 요약은 집계라 받는 양이 아니라 읽는 양(dry run)
    assert remote.post("/api/summary", json={**body, "confirmed": True}).status_code == 200
    assert bigquery.runs == []  # 묻는 동안 실제 조회는 보내지 않았다(요약은 이 SQLite 연결로 돌았다)


def test_bigquery_rerun_while_paging_asks(bigquery, remote, monkeypatch):
    from core import held
    from core import scan_size
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (10 ** 9, "작업 메모리 상한 테스트"))
    held.store["jobs"].clear()
    first = remote.post("/api/query", json={"base": "orders", "limit": 5, "fresh": True, "confirmed": True}).get_json()
    assert first["server"] and len(bigquery.runs) == 1
    assert remote.post("/api/query", json={"base": "orders", "limit": 5, "offset": 5}).headers.get("X-Needs-Confirm") is None  # 결과 테이블을 읽기만
    held.store["jobs"].clear()  # 결과 테이블을 잊었다(KEEP을 넘음): 쪽을 넘기면 쿼리를 다시 돌려야 한다
    again = remote.post("/api/query", json={"base": "orders", "limit": 5, "offset": 10})
    assert again.headers.get("X-Needs-Confirm") == "1" and len(bigquery.runs) == 1  # 묻지 않고 다시 돌리지 않는다
    sql = remote.post("/api/sql/run", json={"sql": "select id from orders", "rows": 5, "offset": 5})
    assert sql.headers.get("X-Needs-Confirm") == "1" and len(bigquery.runs) == 1
    held.store["jobs"].clear()


# ---------- D8 요약 ----------

def test_remote_summary_skips_unique_without_chosen_columns(remote, monkeypatch):
    from core import result
    sent = []
    real = ibis.expr.types.Table.to_polars
    monkeypatch.setattr(ibis.expr.types.Table, "to_polars", lambda self, *a, **k: (sent.append(str(ibis.to_sql(self))), real(self, *a, **k))[1])
    rows = remote.post("/api/summary", json={"base": "orders"}).get_json()["summary"]["rows"]
    assert [r["unique"] for r in rows] == [None, None, None] and rows[0]["count"] == ROWS
    assert not any("DISTINCT" in s.upper() for s in sent)  # 원격 DB에 컬럼마다 COUNT(DISTINCT)를 보내지 않는다
    chosen = remote.post("/api/summary", json={"base": "orders", "summary_columns": ["grp"]}).get_json()["summary"]["rows"]
    assert chosen[0]["unique"] == 5  # 고른 컬럼은 센다
    assert result.get_summary(ibis.memtable({"a": [1, 1, 2]}))[0]["unique"] == 2  # 이 PC 연결은 그대로


# ---------- D7 값 목록 정렬 ----------

@pytest.mark.parametrize("dialect", ["mysql", "mssql"])
def test_values_sort_keeps_native_null_order(dialect, monkeypatch):
    import ibis.backends.sql.compilers as sc
    import ibis.expr.operations as ops
    from ibis.backends.duckdb import Backend
    from core import result
    fake = type("Fake", (Backend,), {"name": dialect, "compiler": getattr(sc, dialect).compiler})()  # 그 DB의 SQL만 만드는 가짜 연결(접속 없음)
    t = ops.DatabaseTable("orders", ibis.schema({"id": "int64", "g": "string"}), source=fake).to_expr()
    sent = []

    class Stop(Exception):
        pass
    monkeypatch.setattr(ibis.expr.types.Table, "to_polars", lambda self, *a, **k: (sent.append(str(ibis.to_sql(self))), (_ for _ in ()).throw(Stop()))[1])
    with pytest.raises(Stop):
        result.get_values(t, "g")
    assert "ORDER BY" in sent[0].upper() and "CASE" not in sent[0].upper()


def test_values_order_unchanged_locally():
    from core import result
    assert result.get_values(ibis.memtable({"g": ["b", None, "a", "b"]}), "g") == (["a", "b"], False)


# ---------- D10 원격 저장 ----------

@pytest.fixture
def pretend_remote(tmp_path, monkeypatch, client):
    """DuckDB 파일 연결을 원격으로 보게 한다(held.remote가 참). 1000행."""
    from core import held
    from core import server
    path = tmp_path / "r.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table orders as select i as id, 'abcd' as name from range(1000) t(i)")
    con = ibis.duckdb.connect(str(path), read_only=True)
    server.state.update(con=con, opened={"kind": "folder", "path": str(tmp_path), "file": str(path)}, rows={})
    server.forget_tables()
    monkeypatch.setattr(held, "remote", lambda con: True)
    return con


def test_remote_big_export_warns(client, pretend_remote, monkeypatch):
    from core import held
    query = {"base": "orders", "format": "csv"}
    assert client.post("/api/export", json=query).headers.get("X-Needs-Confirm") is None  # 작은 저장은 그대로
    monkeypatch.setattr(held, "MAX_ROWS", 100)  # 받아 두기 상한을 넘는 저장
    for api, body in [("/api/export", query), ("/api/sql/export", {"sql": "select * from orders", "format": "csv"})]:
        r = client.post(api, json=body)
        assert r.headers.get("X-Needs-Confirm") == "1" and "복제본" in r.get_json()["text"], api
        assert client.post(api, json={**body, "confirmed": True}).status_code == 200


# ---------- A2 엑셀의 큰 정수 ----------

def test_excel_keeps_big_integers(tmp_path):
    import pyarrow as pa
    from core import result
    ids = [1234567890123456789, 9007199254740993, 999_999_999_999_999, -10 ** 15, None]
    result.export(ibis.memtable(pa.table({"id": pa.array(ids, pa.int64())})), tmp_path / "o.xlsx")
    xml = zipfile.ZipFile(tmp_path / "o.xlsx").read("xl/worksheets/sheet1.xml").decode()
    cells = re.findall(r'<c r="A(\d+)"( t="inlineStr")?[^>]*>(?:<is><t>|<v>)([^<]*)<', xml)  # 메모리 절약 모드는 글자를 칸 안에 바로 쓴다
    assert [(int(r), bool(kind), v) for r, kind, v in cells[1:]] == [
        (2, True, "1234567890123456789"), (3, True, "9007199254740993"), (4, False, "999999999999999"), (5, True, "-1000000000000000")]  # 10^15 이상만 글자, 그보다 작으면 숫자 그대로

