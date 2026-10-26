"""보안 감사 12A: 파일 모드 조각 요청의 비밀 헤더(다른 호스트), next_url의 https→http, 수집 CSV 수식 막기, 지울 때 쓸 명령의 방언별 리터럴."""
import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import sqlglot
from sqlglot import exp

from collect import collect_config
from collect import collect_http
from collect import collect_job
from collect import uploads
from collect.collect_http import Halt, Reply

TOKEN = "dummy-12a-token"


def serve(data, redirect=False):
    """127.0.0.1 서버 B(Range를 받는 파일)와, redirect면 B로 302를 주는 서버 A. 받은 요청의 헤더를 모은다."""
    seen = []

    class B(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(("B", dict(self.headers)))
            rng = self.headers.get("Range")
            if rng:
                s, e = (int(x) for x in rng.split("=")[1].split("-"))
                body = data[s:e + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{s + len(body) - 1}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("ETag", '"x1"')
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    b = ThreadingHTTPServer(("127.0.0.1", 0), B)

    class A(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(("A", dict(self.headers)))
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{b.server_port}/data.csv")  # 다른 호스트·포트
            self.send_header("Content-Length", "0")
            self.end_headers()

    servers = [b] + ([ThreadingHTTPServer(("127.0.0.1", 0), A)] if redirect else [])
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{servers[-1].server_port}/data.csv", seen, servers


def collect_file(tmp_path, url, headers=()):
    env = tmp_path / "x.env"
    env.write_text(f"TOKEN={TOKEN}\n", encoding="utf-8")
    out = tmp_path / "out" / "result.csv"
    out.parent.mkdir()
    collect_job.start({"mode": "file", "env_path": str(env), "file": {"url": url, "headers": [list(h) for h in headers], "chunks": 2, "kind": "csv"},
                       "output": {"format": "csv", "path": str(out)}, "balance": {"concurrency": 1, "interval": 0, "retries": 0, "timeout": 5}})
    collect_job.current["thread"].join(20)
    return out


def test_file_chunks_to_other_host_drop_secret_headers(tmp_path, folder):
    url, seen, servers = serve(b"a,b\n1,2\n3,4\n", redirect=True)
    try:
        collect_file(tmp_path, url, [["Authorization", "Bearer {env:TOKEN}"], ["X-Api-Key", "{env:TOKEN}"]])
    finally:
        for s in servers:
            s.shutdown()
    assert collect_job.snapshot()["error"] == ""
    at_b = [h for who, h in seen if who == "B"]
    assert any("Range" in h and h["Range"] != "bytes=0-0" for h in at_b)  # 조각 요청이 B로 갔다
    assert all(not any(k.lower() == "authorization" or TOKEN in v for k, v in h.items()) for h in at_b)
    assert any(h.get("Authorization") == f"Bearer {TOKEN}" for who, h in seen if who == "A")  # 원래 호스트에는 보낸다


def test_next_url_https_to_http_same_port_halts(tmp_path, monkeypatch, folder):
    config = collect_config.normalize({
        "mode": "api", "request": {"url": "https://api.example.com/items"}, "response": {"records": "items"},
        "paging": {"kind": "next_url", "next_path": "next", "max_pages": 5},
        "balance": {"interval": 0, "retries": 0}, "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")}})
    path = collect_job.output_path(config)
    found, record = collect_job.parts(path), {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    sent = []

    def fetch(request, timeout, secrets=()):
        sent.append(request["url"])
        body = {"items": [{"a": 1}], "next": "http://api.example.com:443/items?p=2"}
        return Reply(200, [["Content-Type", "application/json"]], json.dumps(body).encode(), request["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)
    with pytest.raises(Halt):
        collect_job.run_api(config, {}, path, found, record, threading.Event())
    assert sent == ["https://api.example.com/items"]


def rows_of(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_collected_csv_formulas_guarded(tmp_path, folder):
    url, seen, servers = serve("=h,memo,n,code\nx,=1+1,-5,007\ny,@x,1.10,010\n".encode())
    try:
        out = collect_file(tmp_path, url)
    finally:
        for s in servers:
            s.shutdown()
    assert collect_job.snapshot()["error"] == ""
    assert rows_of(out) == [["'=h", "memo", "n", "code"], ["x", "'=1+1", "-5", "007"], ["y", "'@x", "1.10", "010"]]  # 숫자 컬럼(-5)과 글자 모양(007, 1.10)은 그대로


def test_write_output_csv_guarded(tmp_path):
    from collect.collect_store import duck, write_output
    from core import scratch
    con, work = duck()
    try:
        t = con.sql("select '=1+1' as \"=h\", '@x' as memo, -5 as n")
        assert write_output(con, t, "csv", tmp_path / "o.csv", work) == 1
        assert rows_of(tmp_path / "o.csv") == [["'=h", "memo", "n"], ["'=1+1", "'@x", "-5"]]
        write_output(con, t, "parquet", tmp_path / "o.parquet", work)
        import pyarrow.parquet as pq
        assert pq.read_table(tmp_path / "o.parquet").to_pylist() == [{"=h": "=1+1", "memo": "@x", "n": -5}]  # Parquet은 그대로
    finally:
        con.disconnect()
        scratch.remove(work)


BAD = "~\\' OR 1=1 --"
LINKS = {"snowflake": "snowflake://acct/db/sch (u)", "clickhouse": "clickhouse://h:8123/db (u)", "mysql": "mysql://h/db (u)",
         "postgres": "postgres://h/db (u)", "duckdb": "C:\\data\\wh.duckdb"}


@pytest.mark.parametrize("dialect", LINKS)
def test_removal_literal_stays_inside_in_each_dialect(dialect):
    entry = {"id": "x", "kind": "table", "resumed": True, "target": {"connection": LINKS[dialect], "schema": "s", "name": "items"},
             "range": {"keys": {"k": [BAD, "zz"]}}}
    text = uploads.removal(entry)["text"]
    where = sqlglot.parse_one(text, read=dialect).args["where"].this
    assert isinstance(where, exp.Between) and not list(where.find_all(exp.Or))
    assert where.args["low"].this == BAD


def test_removal_unknown_dialect_refuses_backslash():
    entry = {"id": "x", "kind": "table", "resumed": True, "target": {"connection": "weird://h/db (u)", "name": "items"}, "range": {"keys": {"k": [BAD, "zz"]}}}
    assert uploads.removal(entry)["text"] == ""
    entry["range"]["keys"]["k"] = ["a", "b"]
    assert uploads.removal(entry)["text"] == "DELETE FROM \"items\" WHERE \"k\" BETWEEN 'a' AND 'b';"
