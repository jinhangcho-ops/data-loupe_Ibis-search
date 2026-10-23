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


# ---------- 실행 전 확인(limit_check)과 서버의 같은 판정 ----------

def test_limit_check(db, monkeypatch):
    sql = "select name from orders"  # 1000행 × 16B = 16,000B
    small = scan_size.limit_check(db, sql, config={"memory": 256})
    assert small["bytes"] == 16_000 and not small["over"] and not small["unknown"] and small["limit"] == 256_000_000
    assert small["tables"] == ["orders"] and small["columns"] == ["name"] and "작업 메모리 상한 0.256 GB" in small["text"]
    import monitor
    monkeypatch.setattr(monitor, "available", lambda: 10_000)  # 제한 없음 → 지금 PC의 사용 가능 메모리
    big = scan_size.limit_check(db, "select * from orders", config={})
    assert big["limit"] == 10_000 and big["over"] and "약 " in big["text"] and "모두" in big["text"] and "사용 가능 메모리" in big["text"]
    assert not scan_size.limit_check(db, sql, config={"memory": 256, "scan_unknown": "block"})["blocked"]  # 알 수 있으면 차단하지 않는다


def test_limit_check_unknown(db):
    class Other:  # DuckDB·Postgres·BigQuery가 아닌 연결
        name, dialect = "mysql", "mysql"
    run = scan_size.limit_check(Other(), "select a from t", config={})
    assert run["unknown"] and not run["over"] and not run["blocked"]
    stop = scan_size.limit_check(Other(), "select a from t", config={"scan_unknown": "block"})
    assert stop["unknown"] and stop["blocked"] and "실행하지 않아요" in stop["text"]


def test_too_few_setting(folder):
    import settings
    assert settings.get("sample_too_few") == 0.2
    settings.save({"sample_too_few": 0.5})
    assert settings.get("sample_too_few") == 0.5
    for bad in (0.01, 0.95, "0.3", True):
        with pytest.raises(ValueError):
            settings.save({"sample_too_few": bad})
    settings.save({"sample_too_few": 0})  # 비우면 기본값으로
    assert settings.get("sample_too_few") == 0.2
    settings.save({"scan_unknown": "block"})
    assert settings.load()["scan_unknown"] == "block"
    with pytest.raises(ValueError):
        settings.save({"scan_unknown": "maybe"})


def test_profiler_uses_setting(folder, monkeypatch):
    import profiler
    import settings
    monkeypatch.setattr(profiler, "fetch", lambda con, t: types.SimpleNamespace(num_rows=30))
    table = types.SimpleNamespace(sample=lambda *a, **k: types.SimpleNamespace(limit=lambda n: None))
    assert profiler.block(None, table, None, "t", 100, 1000, None) is not None  # 30행 ≥ 기대 100행의 20%
    settings.save({"sample_too_few": 0.5})
    assert profiler.block(None, table, None, "t", 100, 1000, None) is None  # 30행 < 50행 → 다음 방법으로


def post_json(client, url, **body):
    return client.post(url, json=body)


def test_server_gate(client, tmp_path, folder):
    """서버도 같은 판정: 기준을 넘으면 실행하지 않고 needs_confirm, confirmed를 붙이면 실행. 차단 설정이면 알 수 없을 때 확인해도 실행하지 않는다."""
    import server
    path = tmp_path / "s.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table orders as select i as id, 'x' || i as name from range(1000) t(i)")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    import settings
    settings.save({"memory": 256})
    ok = post_json(client, "/api/sql/run", sql="select * from orders", rows=5)
    assert ok.status_code == 200 and "rows" in ok.get_json()  # 기준(256MB) 아래면 그대로
    import monitor
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(scan_size, "limit", lambda config=None: (1000, "작업 메모리 상한 테스트"))
        ask = post_json(client, "/api/sql/run", sql="select * from orders", rows=5)
        d = ask.get_json()
        assert ask.headers["X-Needs-Confirm"] == "1" and d["needs_confirm"] and d["over"] and not d["blocked"] and "읽어요" in d["text"] and "rows" not in d
        again = post_json(client, "/api/sql/run", sql="select * from orders", rows=5, confirmed=True)
        assert "rows" in again.get_json()
        page = post_json(client, "/api/sql/run", sql="select * from orders", rows=5, offset=5)  # 쪽 넘기기는 묻지 않는다
        assert "rows" in page.get_json()
        export = post_json(client, "/api/sql/export", sql="select * from orders", format="csv")
        assert export.headers.get("X-Needs-Confirm") == "1"
        assert post_json(client, "/api/sql/export", sql="select * from orders", format="csv", confirmed=True).status_code == 200
    finally:
        mp.undo()
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(scan_size, "estimate", lambda *a, **k: {"bytes": None, "exact": False, "how": "테스트"})
        assert "rows" in post_json(client, "/api/sql/run", sql="select * from orders", rows=5).get_json()  # 알 수 없음 + 실행(기본)
        settings.save({"scan_unknown": "block"})
        stop = post_json(client, "/api/sql/run", sql="select * from orders", rows=5, confirmed=True).get_json()
        assert stop["needs_confirm"] and stop["blocked"] and "rows" not in stop
    finally:
        mp.undo()


def test_gate_on_explore_and_prep(client, tmp_path, folder, monkeypatch):
    import server
    path = tmp_path / "s.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table orders as select i as id, 'x' || i as name from range(1000) t(i)")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (1000, "작업 메모리 상한 테스트"))
    query = dict(base="orders", joins=[], conditions=[], periods=[], sorts=[], columns=[], limit=5)
    asked = client.post("/api/query", json=query)
    assert asked.headers.get("X-Needs-Confirm") == "1" and "orders" in asked.get_json()["tables"]
    assert client.post("/api/query", json={**query, "confirmed": True}).get_json()["count"] == 1000
    assert "rows" in client.post("/api/query", json={**query, "offset": 5}).get_json()  # 쪽 넘기기는 묻지 않는다
    assert client.post("/api/export", json={**query, "format": "csv"}).headers.get("X-Needs-Confirm") == "1"
    recipe = {"base": "orders", "columns": {}}
    ran = client.post("/api/run", json={"recipe": recipe, "target": {"kind": "file", "format": "csv"}})
    assert ran.headers.get("X-Needs-Confirm") == "1"
    assert client.post("/api/run", json={"recipe": recipe, "target": {"kind": "file", "format": "csv"}, "confirmed": True}).status_code == 200


# ---------- 원격 DB: DB가 읽는 양이 아니라 이 PC로 받는 양, 메타데이터만으로(COUNT·표본·집계 다시 돌리기 없음) ----------

@pytest.fixture
def sent(monkeypatch):
    """원격 연결로 나가는 조회 글을 모은다(식 실행 + DuckDB raw_sql)."""
    from ibis.backends.duckdb import Backend
    from ibis.expr.types.relations import Table
    found = []
    for name in ("to_pyarrow", "to_polars", "execute", "to_pyarrow_batches"):
        original = getattr(Table, name)
        monkeypatch.setattr(Table, name, lambda self, *a, _o=original, **k: (found.append(str(ibis.to_sql(self))), _o(self, *a, **k))[1])
    raw = Backend.raw_sql
    monkeypatch.setattr(Backend, "raw_sql", lambda self, query, **k: (found.append(str(query)), raw(self, query, **k))[1])
    return found


def scanning(sqls):
    """보낸 조회 중 행을 세거나 읽는 것(EXPLAIN·DESCRIBE·현재 DB 이름 같은 메타데이터 조회는 뺀다)."""
    return [q for q in sqls if not q.lstrip().upper().startswith(("EXPLAIN", "DESCRIBE", "SELECT CURRENT_"))]


@pytest.fixture
def pretend_remote(tmp_path, monkeypatch, client):
    """DuckDB 파일 연결을 원격으로 보게 한다(held.remote가 참). 1000행: id 정수, name·note 글자, day 날짜."""
    import held
    import server
    path = tmp_path / "r.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create table orders as select i as id, 'abcd' as name, repeat('n', 40) as note, date '2026-01-01' + (i % 30)::int as day from range(1000) t(i)")
    con = ibis.duckdb.connect(str(path), read_only=True)
    server.state.update(con=con, opened={"kind": "folder", "path": str(tmp_path), "file": str(path)}, rows={})
    server.forget_tables()
    monkeypatch.setattr(held, "remote", lambda con: True)
    return con


def test_remote_rows_from_plan_times_schema_width(pretend_remote, sent):
    found = scan_size.limit_check(pretend_remote, "select id, name, note from orders", config={"memory": 256})
    assert found["remote"] and found["bytes"] == 1000 * (8 + scan_size.WIDE_STRING * 2) and not found["over"]  # 실행 계획의 ~1,000 rows × 타입 어림 한 행 72바이트
    assert "실행 계획 추정" in found["how"] and "스키마" in found["how"] and "약 1,000행 × 행당 약 72바이트" in found["how"]
    assert "PC로 약 " in found["text"] and "받아요(추정" in found["text"] and "작업 메모리 상한 0.256 GB" in found["text"]
    assert scanning(sent) == []  # COUNT도, 표본(LIMIT)도, 어떤 조회도 보내지 않았다


def test_remote_aggregate_is_not_counted(pretend_remote, sent):
    for sql in ("select count(*) as n from orders", "select name, sum(id) as s from orders group by name", "select max(id) as m, min(id) as l from orders"):
        found = scan_size.limit_check(pretend_remote, sql, config={"memory": 256})
        assert found["bytes"] == 0 and not found["over"] and not found["unknown"] and found["how"] == "집계 결과라 작음"
    assert sent == []  # 아무 조회도 보내지 않았다(EXPLAIN도)
    assert scan_size.is_aggregate("select * from orders", None) is False
    assert scan_size.is_aggregate("select id, count(*) over () as n from orders", None) is False  # 창 함수는 집계가 아니다


def test_remote_known_rows_and_unknown(pretend_remote, sent):
    again = scan_size.limit_check(pretend_remote, "select * from orders", config={"memory": 256}, rows=10)
    assert again["bytes"] == 10 * (8 + 32 * 2 + 4) and "아는 행 수" in again["how"]  # id 8 + name·note 32씩 + day 날짜 4

    class Mute:  # 실행 계획도 통계도 없는 연결
        name, dialect = "sqlite", "sqlite"

        def sql(self, sql):
            return ibis.table({"a": "int64"}, name="t")

        def raw_sql(self, q):
            raise RuntimeError("계획 없음")
    stop = scan_size.limit_check(Mute(), "select a from t", config={"scan_unknown": "block"})
    assert stop["unknown"] and stop["blocked"] and "받을 행 수를 메타데이터로 알 수 없음" in stop["how"]
    assert not scan_size.limit_check(Mute(), "select a from t", config={})["blocked"]


def test_plan_row_patterns(monkeypatch):
    import plan
    texts = {"Seq Scan on t  (cost=0.00..1.00 rows=1234 width=8)": 1234, "Estimates: {rows: 41,000,000 (1GB), cpu: ?}": 41_000_000,
             "physical_plan\t│ ~2,500 rows │": 2500, "EC: 77": 77, "id\tselect_type\ttable\trows\n1\tSIMPLE\tt\t900": 900, "아무것도 없음": None}
    for text, want in texts.items():
        monkeypatch.setattr(plan, "explain", lambda con, sql, dialect, _t=text: {"text": _t})
        assert scan_size.plan_rows(None, "select 1", "postgres") == want
    monkeypatch.setattr(plan, "explain", lambda *a: (_ for _ in ()).throw(ValueError("보지 않는 방언")))
    assert scan_size.plan_rows(None, "select 1", "bigquery") is None


def test_row_width_sources(folder, tmp_path):
    import profiler
    expr = ibis.table({"id": "int64", "name": "string", "note": "string", "tags": "array<string>"}, name="orders")
    plain = types.SimpleNamespace(name="mysql")
    assert scan_size.row_width(plain, expr, ["orders"]) == (8 + 32 + 32 + 64, "스키마")
    spec = tmp_path / "spec.json"
    spec.write_text('{"tables": {"orders": {"columns": {"name": {"bytes": 4}, "note": {"bytes": 40}}}}}', encoding="utf-8")
    profiler.save_spec_path(str(spec))
    assert scan_size.row_width(plain, expr, ["orders"]) == (8 + 4 + 40 + 64, "데이터 명세서")  # 명세서의 컬럼별 크기가 타입 어림보다 먼저
    spec.write_text('{"tables": {"orders": {"columns": {"name": {"bytes": 0}}}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="bytes"):
        profiler.read_spec(spec)
    profiler.save_spec_path("")

    class Cursor:  # Postgres 통계(pg_stats)
        def execute(self, sql, params):
            self.params = params

        def fetchall(self):
            return [("id", 4), ("name", 6), ("note", 0)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    pg = types.SimpleNamespace(name="postgres", con=types.SimpleNamespace(cursor=lambda: Cursor()))
    assert scan_size.row_width(pg, expr, ["orders"]) == (4 + 6 + 32 + 64, "DB 통계")  # avg_width가 0인 컬럼은 타입 어림

    info = types.SimpleNamespace(num_bytes=10_000, num_rows=100)
    bq = types.SimpleNamespace(name="bigquery", client=types.SimpleNamespace(get_table=lambda ref: info), current_catalog="p", current_database="d")
    assert scan_size.row_width(bq, expr, ["orders"]) == (100, "테이블 메타")  # 테이블의 평균 행 크기(num_bytes / num_rows)


def test_bigquery_partitions_and_dry_run(folder, tmp_path, monkeypatch):
    import sys

    import profiler
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = types.SimpleNamespace(QueryJobConfig=lambda **k: k)  # 이 PC에 BigQuery 라이브러리가 없어도 시험하도록 가짜
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", cloud.bigquery)
    spec = tmp_path / "spec.json"
    spec.write_text('{"tables": {"events": {"partition": {"column": "day", "recent_days": 7}}}}', encoding="utf-8")
    profiler.save_spec_path(str(spec))
    asked = []

    class Job:
        total_bytes_processed = 123_456

        def __init__(self, rows):
            self.rows = rows

        def result(self):
            return self.rows
    parts = [{"partition_id": f"202601{d:02d}", "total_rows": 100 * d} for d in range(1, 11)] + [{"partition_id": "__NULL__", "total_rows": 5}]

    def query(sql, job_config=None, **k):
        asked.append(sql)
        return Job(parts) if "INFORMATION_SCHEMA.PARTITIONS" in sql else Job([])
    info = types.SimpleNamespace(num_bytes=0, num_rows=0)
    bq = types.SimpleNamespace(name="bigquery", dialect="bigquery", current_catalog="p", current_database="d",
                               client=types.SimpleNamespace(query=query, get_table=lambda ref: info),
                               sql=lambda sql: ibis.table({"day": "date", "v": "int64"}, name="events"))
    sql = "SELECT day, v FROM events WHERE day >= '2026-01-03' AND day <= '2026-01-05'"
    low, high = scan_size.date_bounds(sql, "bigquery", "day")
    assert (str(low), str(high)) == ("2026-01-03", "2026-01-05")
    found = scan_size.fetched(bq, sql, dialect="bigquery")
    assert found["rows"] == 300 + 400 + 500 and "파티션 메타" in found["how"] and found["bytes"] == 1200 * 12  # 날짜 4 + 정수 8
    assert len(asked) == 1 and all("COUNT" not in q.upper() for q in asked)  # 파티션 메타데이터 조회 하나뿐
    whole = scan_size.fetched(bq, "SELECT day, v FROM events", dialect="bigquery")
    assert whole["rows"] == sum(100 * d for d in range(1, 11))  # 범위가 없으면 모든 날짜 파티션(__NULL__ 같은 것은 뺌)
    profiler.save_spec_path("")  # 파티션 정보가 없으면 dry run 바이트(받는 양의 최대)
    dry = scan_size.fetched(bq, sql, dialect="bigquery")
    assert dry["bytes"] == 123_456 and dry["rows"] is None and "dry run" in dry["how"]


def test_server_gate_remote(client, pretend_remote, monkeypatch, sent):
    """원격 DB: 검색·SQL 실행은 PC로 받는 양을 메타데이터로만 어림해 묻고, 저장·집계는 묻지 않으며, COUNT·표본을 보내지 않는다."""
    small = lambda config=None: (10_000, "작업 메모리 상한 테스트")
    big = lambda config=None: (10**9, "작업 메모리 상한 테스트")
    monkeypatch.setattr(scan_size, "limit", small)
    query = dict(base="orders", joins=[], conditions=[], periods=[], sorts=[], columns=[], limit=5, fresh=True)
    asked = client.post("/api/query", json=query)
    d = asked.get_json()
    assert asked.headers.get("X-Needs-Confirm") == "1" and d["remote"] and d["bytes"] == 1000 * (8 + 32 * 2 + 4) and "PC로 약" in d["text"]
    assert scanning(sent) == []  # 묻는 동안 아무 조회도 가지 않았다
    monkeypatch.setattr(scan_size, "limit", big)
    assert client.post("/api/query", json={**query, "confirmed": True}).get_json()["count"] == 1000
    monkeypatch.setattr(scan_size, "limit", small)
    assert "rows" in client.post("/api/query", json={**query, "fresh": False}).get_json()  # 이미 받아 둔 결과는 다시 받지 않으니 묻지 않는다
    # 파일로 흘려 쓰는 저장은 PC 메모리에 올리지 않으므로 묻지 않는다
    assert client.post("/api/export", json={**query, "format": "csv"}).headers.get("X-Needs-Confirm") is None
    assert client.post("/api/sql/export", json={"sql": "select * from orders", "format": "csv"}).headers.get("X-Needs-Confirm") is None
    sent.clear()
    run = client.post("/api/sql/run", json={"sql": "select * from orders", "rows": 5})
    assert run.headers.get("X-Needs-Confirm") == "1" and run.get_json()["remote"]
    assert scanning(sent) == []
    sent.clear()
    agg = client.post("/api/sql/run", json={"sql": "select count(*) as n from orders", "rows": 5})
    assert agg.get_json()["rows"]["rows"] == [{"n": 1000}]  # 집계 결과는 작아 묻지 않는다
    assert all("count(*) as n" in q.lower() for q in scanning(sent) if "orders" in q.lower())  # 원본 테이블에 간 것은 사용자가 쓴 그 집계뿐(판정용 조회 없음)


def test_prep_run_remote_copy_checks_without_counting(client, pretend_remote, monkeypatch, sent):
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (10_000, "작업 메모리 상한 테스트"))
    recipe = {"base": "orders", "columns": {}}
    ask = client.post("/api/run", json={"recipe": recipe, "target": {"kind": "file", "format": "csv"}})
    d = ask.get_json()
    assert ask.headers.get("X-Needs-Confirm") == "1" and d["remote"] and d["bytes"] > 10_000
    assert scanning(sent) == []


def test_fetch_stops_at_byte_limit(pretend_remote, monkeypatch):
    """받는 중 받은 크기가 작업 메모리 상한을 넘으면 곧 멈추고, 받던 것을 지운다."""
    import threading

    import held
    import monitor
    table = pretend_remote.table("orders")
    monkeypatch.setattr(monitor, "bound_bytes", lambda config=None: 1000)
    before = set(held.local().list_tables())
    assert held.fetch(("k", 1), table, threading.Event()) == (None, None)  # 행 상한이 있는 쪽: 받지 않았다고 알린다
    with pytest.raises(ValueError, match="작업 메모리 상한"):
        held.fetch(("k", 2), table, threading.Event(), cap=False)  # 작업 사본: 이유와 함께 멈춘다
    assert set(held.local().list_tables()) == before  # 받던 테이블은 지웠다
    monkeypatch.setattr(monitor, "bound_bytes", lambda config=None: 10**9)
    name, rows = held.fetch(("k", 3), table, threading.Event())
    assert rows == 1000 and name in held.local().list_tables()


def test_dashboard_refresh_remote_aggregate_sends_nothing(pretend_remote, sent, monkeypatch):
    """대시보드 최신화: 원격 DB에서 DB 안의 집계로 구하는 카드는 크기를 알아보려고 아무 조회도 보내지 않는다."""
    import dash_calc
    import dash_refresh
    import dash_store
    card = dash_store.check_card({"kind": "kpi", "title": "t", "source": {"table": "orders", "body": {"base": "orders", "series": [{"y": "id", "agg": "sum"}]}, "date": "day"}, "view": {}})
    assert dash_calc.copies_to_pc(card, {}) is False
    assert dash_refresh.scan_decision(card, {}) == ""
    assert scanning(sent) == []
    monkeypatch.setattr(dash_calc, "copies_to_pc", lambda c, ctx=None: True)  # PC로 옮기는 카드는 판정하되 COUNT·표본은 없다
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (10, "작업 메모리 상한 테스트"))
    assert "PC로 약" in dash_refresh.scan_decision(card, {})
    assert scanning(sent) == []
