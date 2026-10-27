"""감사 38차: Parquet 가상 테이블에 SQL 식·처리 용량 확인, 가상 테이블 이름의 대소문자(따옴표)."""
import duckdb
import pytest

from lazy import ibis
from core import calc, scan_size, sql_exec, sql_funcs


@pytest.fixture
def pv(tmp_path):
    """Parquet 가상 테이블 pv와 SQL 가상 테이블 sv가 있는 DuckDB 파일 연결."""
    pq = (tmp_path / "p.parquet").as_posix()
    duckdb.sql(f"COPY (SELECT range AS id, range * 10 AS amount FROM range(5)) TO '{pq}' (FORMAT parquet)")
    db = str(tmp_path / "vt.duckdb")
    w = duckdb.connect(db)
    w.sql("CREATE TABLE orders AS SELECT 1 AS id")
    w.close()
    con = ibis.duckdb.connect(db, read_only=True)
    yield con, {"pv": {"parquet": pq}, "sv": {"sql": "SELECT id, id*2 AS d FROM orders"}}
    con.disconnect()


def test_sql_expr_on_parquet_virtual_table(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    out = calc.apply(["SQL식 amount + 1 이름 x"], expr).execute()
    assert out["x"].tolist() == [1, 11, 21, 31, 41]


def test_raw_sql_still_checks_user_expression(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    with pytest.raises(sql_exec.SecurityViolationError):  # 사용자 식의 파일 읽기는 여전히 막는다
        sql_funcs.raw_sql(expr, "x", "(SELECT count(*) FROM read_csv('a.csv'))")
    with pytest.raises(sql_exec.SecurityViolationError):  # 주석으로 FROM을 바꾸는 것도 막는다(A7)
        sql_funcs.raw_sql(expr, "x", "1) AS x FROM orders --")


def test_limit_check_counts_parquet_virtual_table(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    sql, dialect = scan_size.sql_of(con, expr)
    r = scan_size.limit_check(con, sql, None, dialect, {"scan_unknown": "block"})
    assert not r["unknown"] and not r["blocked"] and r["bytes"] > 0 and "Parquet" in r["how"]


class Con:
    def __init__(self, name, dialect):
        self.name, self.dialect = name, dialect


@pytest.mark.parametrize("dialect", ["postgres", "snowflake", "duckdb"])
def test_virtual_table_reference_keeps_exact_case(dialect):
    vts = {"Sales": {"sql": "SELECT 1 AS a"}, "orders_vt": {"sql": "SELECT a FROM sales"}}
    con = Con(dialect, dialect)
    tree = sql_exec.expand(con, sql_exec.check("SELECT Sales.a FROM Sales JOIN orders_vt o ON TRUE", dialect), vts)
    exp = sql_exec.glot()[1]
    refs = {t.this.this: t.this.quoted for t in tree.find_all(exp.Table)}
    assert refs == {"Sales": True, "orders_vt": True}  # 정의 안의 sales도 저장한 이름 "Sales"로
    assert {c.alias_or_name for c in tree.find_all(exp.CTE)} == {"Sales", "orders_vt"}


def test_virtual_table_case_runs_on_duckdb(pv):
    con, _ = pv
    vts = {"Sales": {"sql": "SELECT 1 AS a"}}
    assert con.raw_sql(sql_exec.expand(con, sql_exec.check("SELECT Sales.a FROM sales", "duckdb"), vts).sql(dialect="duckdb")).fetchall() == [(1,)]


def test_failed_piece_lets_running_pieces_finish(tmp_path, monkeypatch, folder):
    """파일 받기: 한 조각이 실패해도 받던 조각은 끝까지 받아 기록한다(다음에 그 범위는 다시 받지 않는다)."""
    import threading
    import time
    from collect import collect_config, collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 2}, "balance": {"interval": 0, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [], "rows": 0, "bytes": 0}
    failed = threading.Event()
    H = type("H", (dict,), {"get": dict.get, "items": dict.items})

    class Body:
        def __init__(self, status, headers, data, slow=False):
            self.status, self.headers, self.data, self.slow = status, H(headers), data, slow

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            if self.slow:  # 다른 조각이 실패한 뒤에야 몸통이 온다
                failed.wait(5)
                time.sleep(0.1)
                self.slow = False
            out, self.data = (self.data[:n], self.data[n:]) if n else (self.data, b"")
            return out

    def open_url(method, url, headers, body, timeout, hide=()):
        start, end = (int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))
        if start >= 10:
            failed.set()
            return Body(500, {}, b"down"), url
        return Body(206, {"Content-Range": f"bytes {start}-{end}/20", "ETag": '"e1"'}, b"y" * (end - start + 1), slow=end > 0), url
    monkeypatch.setattr(collect_http, "open_url", open_url)
    with pytest.raises(Exception):
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    assert record["done"] == [[0, 10]]


def test_failed_piece_stops_waiting_pieces(tmp_path, monkeypatch, folder):
    """파일 받기: 한 조각이 실패하면 차례를 기다리던 조각은 요청을 보내지 않고, 실패 이유가 그대로 보인다."""
    import threading
    from collect import collect_config, collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 8}, "balance": {"interval": 0.2, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [], "rows": 0, "bytes": 0}
    H = type("H", (dict,), {"get": dict.get, "items": dict.items})
    sent = []

    class Body:
        def __init__(self, status, headers, data):
            self.status, self.headers, self.data = status, H(headers), data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            out, self.data = (self.data[:n], self.data[n:]) if n else (self.data, b"")
            return out

    def open_url(method, url, headers, body, timeout, hide=()):
        start, end = (int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))
        sent.append(start)
        if start == 0 and end > 0:
            return Body(404, {}, b"gone"), url
        return Body(206, {"Content-Range": f"bytes {start}-{end}/80", "ETag": '"e1"'}, b"y" * (end - start + 1)), url
    monkeypatch.setattr(collect_http, "open_url", open_url)
    with pytest.raises(Exception) as caught:
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    assert sent.count(0) == 2 and sent[-1] == 0  # 실패한 조각(0-9) 뒤로는 요청이 없다(조각이 시작하는 차례는 그때그때 다르다)
    assert getattr(caught.value, "failed", True)  # 멈춤이 아니라 실패로 끝난다
