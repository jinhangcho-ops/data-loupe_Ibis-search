"""14차 리뷰 A: SQL 검사가 직렬화한 SQL 실행·원격 DB의 파일/네트워크 함수·잠금을 막는다(정상 SQL은 그대로).
보내기(transport)는 넘겨주기(3xx)를 따라가지 않고, 밖으로 나간 요청을 설정 폴더의 egress_log.jsonl에 시각·종류·호스트·경로만 남긴다.
Kafka SASL_PLAINTEXT+PLAIN이면 화면에 경고가 보인다. 데이터는 시험 안에서 만든다."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import duckdb
import pytest
from lazy import ibis

from core import settings
from core import sql_exec
from integrations.adapters import transport

ROOT = Path(__file__).resolve().parent.parent


def test_serialized_sql_cannot_run_hidden_file_read(tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("k,v\nTOKEN,abc123\n", encoding="utf-8")
    inner = f"SELECT * FROM read_text('{secret.as_posix()}')"
    ser = duckdb.connect().execute("SELECT json_serialize_sql(?)", [inner]).fetchone()[0].replace("'", "''")
    sql = f"SELECT * FROM json_execute_serialized_sql('{ser}')"
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql, "duckdb")
    con = ibis.duckdb.connect()
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.to_expr(con, sql, {})


BLOCKED = [
    ("clickhouse", "SELECT * FROM url('http://169.254.169.254/latest/meta-data/', 'TSV')"),
    ("clickhouse", "SELECT * FROM file('users.csv')"),
    ("clickhouse", "SELECT * FROM remote('10.0.0.5', db.t)"),
    ("clickhouse", "SELECT * FROM s3('http://b/x.csv')"),
    ("postgres", "SELECT query_to_xml('select 1', true, true, '')"),
    ("postgres", "SELECT lo_unlink(16400)"),
    ("snowflake", "SELECT SYSTEM$CANCEL_ALL_QUERIES(123)"),
    ("snowflake", "SELECT SYSTEM$ABORT_SESSION(1)"),
    ("tsql", "SELECT * FROM t WITH (TABLOCKX)"),
    ("tsql", "SELECT * FROM t WITH (UPDLOCK, HOLDLOCK)"),
    ("postgres", "SELECT * FROM t FOR UPDATE"),
    ("postgres", "SELECT * FROM t FOR SHARE"),
    ("mysql", "SELECT * FROM t LOCK IN SHARE MODE"),
    ("postgres", "SELECT pg_advisory_lock(1)"),
    ("postgres", "SELECT pg_try_advisory_lock(1)"),
    ("mysql", "SELECT GET_LOCK('a', 10)"),
    ("postgres", "SELECT pg_reload_conf()"),
    ("postgres", "SELECT pg_stat_reset()"),
    ("postgres", "SELECT pg_switch_wal()"),
    ("postgres", "SELECT pg_drop_replication_slot('s')"),
    ("mysql", "SELECT BENCHMARK(1000, MD5('a'))"),
]


@pytest.mark.parametrize("dialect,sql", BLOCKED)
def test_remote_file_lock_and_server_functions_are_blocked(dialect, sql):
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql, dialect)


ALLOWED = [
    ("duckdb", "SELECT city, COUNT(*) AS n, SUM(amt) FROM sales GROUP BY city HAVING COUNT(*) > 1"),
    ("duckdb", "SELECT a.id, b.name FROM a JOIN b ON a.id = b.id LEFT JOIN c USING (id)"),
    ("duckdb", "SELECT id, ROW_NUMBER() OVER (PARTITION BY city ORDER BY ts) AS rn, LAG(amt) OVER (ORDER BY ts) FROM sales"),
    ("duckdb", "WITH x AS (SELECT * FROM t) SELECT json_extract(j, '$.a'), lower(name), log(2, amt) FROM x"),
    ("tsql", "SELECT TOP 10 * FROM t WITH (NOLOCK)"),
    ("postgres", "SELECT date_trunc('month', ts), pg_typeof(amt) FROM t"),
    ("clickhouse", "SELECT toStartOfMonth(ts), uniq(u) FROM t GROUP BY 1"),
]


@pytest.mark.parametrize("dialect,sql", ALLOWED)
def test_normal_sql_still_passes(dialect, sql):
    sql_exec.check(sql, dialect)


def serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    return tmp_path


def test_redirect_to_other_host_is_not_followed(log_dir):
    got = []

    class Other(BaseHTTPRequestHandler):
        def do_GET(self):
            got.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    other = serve(Other)

    class Bounce(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{other.server_port}/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    bounce = serve(Bounce)
    try:
        with pytest.raises(ValueError, match="302"):
            transport.post(f"http://127.0.0.1:{bounce.server_port}/v1/chat/completions", b"{}", {"Authorization": "Bearer sk-FAKE-123"})
        assert got == []
    finally:
        bounce.shutdown()
        other.shutdown()


def test_egress_log_keeps_only_time_kind_host_path(log_dir, monkeypatch):
    class Ok(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    ok = serve(Ok)
    try:
        url = f"http://127.0.0.1:{ok.server_port}/services/T0SECRET1/B0SECRET2/xyz9SECRETabcdefghijklmnopqrstuvwxyz?token=qsecret"
        assert transport.post(url, b"value-secret", {"Authorization": "Bearer hsecret"}) == b"ok"
        lines = (log_dir / transport.LOG).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert set(row) == {"at", "kind", "host", "path"}
        assert row["kind"] == "POST" and row["host"] == f"127.0.0.1:{ok.server_port}"
        assert row["path"].startswith("/services/")
        assert "SECRET" not in lines[0] and "secret" not in lines[0]
        monkeypatch.setattr(transport, "MAX_LOG", 2_000)  # 상한을 넘으면 오래된 줄을 지운다
        for _ in range(40):
            transport.post(f"http://127.0.0.1:{ok.server_port}/api/chat.postMessage", b"x", {})
        size = (log_dir / transport.LOG).stat().st_size
        assert size <= 2_000 + 200
        assert json.loads((log_dir / transport.LOG).read_text(encoding="utf-8").splitlines()[-1])["path"] == "/api/chat.postMessage"
    finally:
        ok.shutdown()


@pytest.mark.parametrize("secret", ["abcdefghijklmnopqrstuvwX", "AbCdEfGhIjKlMnOpQrStUvWx", "abcdefghijklmnopqrstuvwx", "aBCDefghijklmnopqrstuvw7"])
def test_slack_webhook_secret_is_hidden_even_without_digits(log_dir, secret):
    transport.log("POST", f"https://hooks.slack.com/services/T0AAAAAAA/B0BBBBBBB/{secret}")
    assert json.loads((log_dir / transport.LOG).read_text(encoding="utf-8"))["path"] == "/services/*/*/*"


def test_known_api_paths_stay_readable(log_dir):
    for url in ("https://slack.com/api/chat.postMessage", "https://api.openai.com/v1/chat/completions", "https://analyticsdata.googleapis.com/v1beta/properties/123:runReport"):
        transport.log("POST", url)
    paths = [json.loads(x)["path"] for x in (log_dir / transport.LOG).read_text(encoding="utf-8").splitlines()]
    assert paths == ["/api/chat.postMessage", "/v1/chat/completions", "/v1beta/properties/*"]


def test_failed_send_is_logged_too(log_dir):
    with pytest.raises(ValueError):
        transport.post("http://127.0.0.1:9/hook", b"x", {}, timeout=2)
    assert json.loads((log_dir / transport.LOG).read_text(encoding="utf-8"))["host"] == "127.0.0.1:9"


def test_kafka_sasl_plaintext_plain_warns_on_screen():
    page = (ROOT / "static" / "collect" / "collect.html").read_text(encoding="utf-8")
    assert 'id="kf-plain-warn"' in page
    assert 'show("kf-plain-warn", $("kf-security").value === "SASL_PLAINTEXT" && $("kf-mech").value === "PLAIN")' in page
    assert '"kf-mech"].forEach' in page  # SASL 방식을 바꿀 때도 다시 그린다
