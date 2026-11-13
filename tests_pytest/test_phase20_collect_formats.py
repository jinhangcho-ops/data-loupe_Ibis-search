"""수집 결과 형식 더하기(Excel·DuckDB 파일·SQLite 파일)와 적재할 DB 파일 새로 만들기. 데이터는 시험 안에서 지어내고 바깥으로 보내지 않는다."""
import json
import sqlite3
import threading
from pathlib import Path

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import collect_config
from collect import collect_http
from collect import collect_job
from collect import collect_store
from collect import load_job
from collect import load_table
from collect.collect_http import Halt, Reply
from core import result
from core import server

URL = "https://api.example.com/items"
ITEMS = [{"id": 1, "name": "가"}, {"id": 2, "name": "=1+1"}, {"id": 3, "name": "-1500"}]
NEW_FORMATS = ("xlsx", "duckdb", "db")
STATIC = Path(__file__).resolve().parent.parent / "static" / "collect" / "collect.html"


def config_for(tmp_path, fmt, **more):
    return collect_config.normalize({"mode": "api", "request": {"url": URL}, "response": {"records": "items"}, "balance": {"interval": 0, "retries": 0},
                                     "output": {"format": fmt, "path": str(tmp_path / f"items.{fmt}")}, **more})


def collect(monkeypatch, config, items=ITEMS):
    """가짜 응답 한 쪽으로 수집을 끝까지 돌리고 결과 경로를 돌려준다."""
    monkeypatch.setattr(collect_http, "fetch", lambda request, timeout, secrets=(): Reply(200, [["Content-Type", "application/json"]], json.dumps({"items": items}).encode(), request["url"]))
    path = collect_job.output_path(config)
    record = {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    assert collect_job.run_api(config, {}, path, collect_job.parts(path), record, threading.Event()) is True
    return path


def rows_of(path, table="items"):
    """결과 파일의 행들(id 순)."""
    if path.suffix == ".xlsx":
        return pl.read_excel(path).sort("id").rows()
    if path.suffix == ".duckdb":
        with duckdb.connect(str(path), read_only=True) as con:
            return con.table(table).order("id").fetchall()
    con = sqlite3.connect(path)
    try:
        return con.execute(f'select * from "{table}" order by id').fetchall()
    finally:
        con.close()


# ---------- 구성 검사 ----------

@pytest.mark.parametrize("fmt", NEW_FORMATS)
def test_config_accepts_new_formats(tmp_path, fmt):
    config = config_for(tmp_path, fmt)
    assert config["output"]["format"] == fmt
    assert collect_job.output_path({**config, "output": {**config["output"], "path": str(tmp_path / "a")}}).name == f"a.{fmt}"


def test_config_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="output.format"):
        config_for(tmp_path, "json")


# ---------- 수집 한 번 ----------

@pytest.mark.parametrize("fmt", NEW_FORMATS)
def test_collect_writes_each_format(tmp_path, monkeypatch, folder, fmt):
    path = collect(monkeypatch, config_for(tmp_path, fmt))
    assert rows_of(path) == [(1, "가"), (2, "=1+1"), (3, "-1500")]  # 수식 막기 '는 CSV에만 붙인다(Excel은 글자를 수식으로 쓰지 않는다)
    assert collect_job.snapshot()["rows"] == 3
    assert not collect_job.parts(path)["out"].exists() and not collect_job.parts(path)["part"].exists()


@pytest.mark.parametrize("fmt", NEW_FORMATS)
def test_collect_dedupes_in_each_format(tmp_path, monkeypatch, folder, fmt):
    path = collect(monkeypatch, config_for(tmp_path, fmt, output={"format": fmt, "path": str(tmp_path / f"items.{fmt}"), "dedupe": ["id"]}), [*ITEMS, {"id": 1, "name": "다시"}])
    assert [r[0] for r in rows_of(path)] == [1, 2, 3]


@pytest.mark.parametrize("fmt", NEW_FORMATS)
def test_file_mode_converts_to_each_format(tmp_path, folder, fmt):
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/items.csv"}, "output": {"format": fmt, "path": str(tmp_path / f"items.{fmt}")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].write_text("id,name\n1,가\n2,나\n", encoding="utf-8")  # 받아 둔 파일
    assert collect_job.finish_file(config, path, found, {"url": config["file"]["url"]}) is True
    assert rows_of(path) == [(1, "가"), (2, "나")] and not found["part"].exists() and not found["out"].exists()


def test_table_name_comes_from_file_name(tmp_path):
    assert collect_store.table_name(tmp_path / "my items-2026.duckdb") == "my_items_2026"
    assert collect_store.table_name(tmp_path / "주문.db") == "주문"
    assert collect_store.table_name(tmp_path / "!!!.db") == collect_store.DEFAULT_TABLE


# ---------- 이미 있는 DB 파일 ----------

def test_duckdb_keeps_other_tables_and_replaces_own(tmp_path, monkeypatch, folder):
    path = tmp_path / "items.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("create table other as select 7 as n")
        con.execute("create table items as select 99 as id")
    collect(monkeypatch, config_for(tmp_path, "duckdb"))
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.table("other").fetchall() == [(7,)]
        assert sorted(r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()) == ["items", "other"]
    assert [r[0] for r in rows_of(path)] == [1, 2, 3]


def test_sqlite_keeps_other_tables_and_replaces_own(tmp_path, monkeypatch, folder):
    path = tmp_path / "items.db"
    con = sqlite3.connect(path)
    con.execute("create table other (n integer)")
    con.execute("insert into other values (7)")
    con.execute("create table items (id integer)")
    con.execute("insert into items values (99)")
    con.commit()
    con.close()
    collect(monkeypatch, config_for(tmp_path, "db"))
    con = sqlite3.connect(path)
    try:
        assert con.execute("select n from other").fetchall() == [(7,)]
        assert sorted(r[0] for r in con.execute("select name from sqlite_master where type in ('table', 'view')").fetchall()) == ["items", "other"]
    finally:
        con.close()
    assert [r[0] for r in rows_of(path)] == [1, 2, 3]


@pytest.mark.parametrize("fmt, tail", [("duckdb", ".wal"), ("db", "-wal"), ("db", "-journal")])
def test_db_file_in_use_by_another_program_is_refused(tmp_path, monkeypatch, folder, fmt, tail):
    path = tmp_path / f"items.{fmt}"
    path.write_bytes(b"")
    Path(str(path) + tail).write_bytes(b"x")  # 다른 프로그램이 쓰는 중(또는 제대로 닫히지 않음)
    monkeypatch.setattr(collect_http, "fetch", lambda request, timeout, secrets=(): Reply(200, [], json.dumps({"items": ITEMS}).encode(), request["url"]))
    config = config_for(tmp_path, fmt)
    record = {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    with pytest.raises(Halt, match="다른 프로그램"):
        collect_job.run_api(config, {}, path, collect_job.parts(path), record, threading.Event())
    assert path.read_bytes() == b""  # 원래 파일은 그대로


# ---------- Excel 행 한도 ----------

def test_excel_row_limit_is_an_error_not_a_cut(tmp_path, monkeypatch, folder):
    monkeypatch.setattr(result, "EXCEL_MAX_ROWS", 2)
    config = config_for(tmp_path, "xlsx")
    monkeypatch.setattr(collect_http, "fetch", lambda request, timeout, secrets=(): Reply(200, [], json.dumps({"items": ITEMS}).encode(), request["url"]))
    path = collect_job.output_path(config)
    record = {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    with pytest.raises(ValueError, match="Excel은 2행까지"):
        collect_job.run_api(config, {}, path, collect_job.parts(path), record, threading.Event())
    assert not path.exists()
    assert collect_job.parts(path)["part"].exists()  # 받은 것은 남는다(형식을 바꿔 다시 시작하면 이어서 바꾼다)


# ---------- 화면 ----------

def test_screen_draws_formats_from_server():
    formats = server.constants("collect")["collect_formats"]
    assert formats == [list(pair) for pair in collect_config.OUTPUT_FORMATS.items()] or formats == list(collect_config.OUTPUT_FORMATS.items())
    assert [f for f, _ in formats] == list(collect_config.CHOICES[("output", "format")]) == ["parquet", "csv", "xlsx", "duckdb", "db"]
    html = STATIC.read_text(encoding="utf-8")
    assert '<select id="o-format" style="margin-bottom:6px"></select>' in html  # 목록을 화면에 박지 않는다
    assert "SERVER.collect_formats" in html and "(parquet|csv)" not in html


def test_ask_path_uses_format_extension(client, monkeypatch):
    asked = []
    monkeypatch.setattr(server, "ask_save", lambda name, kinds: asked.append((name, kinds)) or "C:\\x\\picked")
    assert client.post("/api/collect/ask_path", json={"format": "duckdb", "name": "items"}).get_json() == {"path": "C:\\x\\picked.duckdb"}
    assert asked == [("items.duckdb", ("DuckDB 파일 (*.duckdb)",))]
    assert client.post("/api/collect/ask_path", json={"format": "json", "name": "items"}).status_code == 400


# ---------- 결과 열기·적재 ----------

@pytest.mark.parametrize("fmt", NEW_FORMATS)
def test_open_result_connects_to_new_formats(tmp_path, monkeypatch, folder, fmt):
    path = collect(monkeypatch, config_for(tmp_path, fmt))
    c = server.app.test_client()
    c.get("/?key=" + server.KEY)
    try:
        reply = c.post("/api/collect/open", json={"path": str(path)})
        assert reply.get_json() == {"table": "items"}
        assert c.post("/api/query", json={"base": "items"}).get_json()["count"] == 3
    finally:
        server.drop_connection()


@pytest.mark.parametrize("fmt", NEW_FORMATS)
def test_load_reads_new_formats_in_batches(tmp_path, monkeypatch, folder, fmt):
    path = collect(monkeypatch, config_for(tmp_path, fmt))
    with load_job.Batches(path, 2) as batches:
        parts = list(batches)
        assert batches.schema.names == ["id", "name"]
    assert [p.num_rows for p in parts] == [2, 1]
    assert sorted(pa.concat_tables(parts).to_pylist(), key=lambda r: r["id"]) == [{"id": 1, "name": "가"}, {"id": 2, "name": "=1+1"}, {"id": 3, "name": "-1500"}]  # Excel의 수식 막기 '는 뗀다
    assert load_job.empty_columns(path, load_job.file_schema(path)) == set()


# ---------- 적재할 DB 파일이 없으면 만든다 ----------

def table_config(tmp_path, connection, **table):
    result_file = tmp_path / "out.parquet"
    pq.write_table(pa.table({"id": [1, 2, 3], "name": ["가", "나", "다"]}), result_file)
    return collect_config.normalize({"mode": "api", "request": {"url": URL}, "output": {"format": "parquet", "path": str(result_file)},
                                     "load": {"target": "table", "table": {"connection": connection, "name": "loaded", **table}, "batch": 100, "retries": 0}})


def test_load_creates_missing_duckdb_file(tmp_path, folder):
    file = tmp_path / "new.duckdb"
    config = table_config(tmp_path, {"kind": "folder", "file": str(file)})
    load_table.ready(config)
    columns = load_table.arrow_columns(load_job.file_schema(tmp_path / "out.parquet"))
    found = load_table.check(config, "", columns)
    assert (found["will_create"], found["ok"], found["exists"]) == (True, True, False) and "new.duckdb" in found["message"] and "새로 만들어요" in found["message"]
    assert not file.exists()  # [확인]은 파일을 만들지 않는다
    load_job.run(config, tmp_path / "out.parquet", "", threading.Event(), lambda **k: None)
    with duckdb.connect(str(file), read_only=True) as con:
        assert con.table("loaded").order("id").fetchall() == [(1, "가"), (2, "나"), (3, "다")]
    assert load_table.check(config, "", columns)["will_create"] is False


def test_load_creates_missing_sqlite_file(tmp_path, folder):
    file = tmp_path / "new.db"
    config = table_config(tmp_path, {"kind": "url", "url": "sqlite:///" + file.as_posix()})
    load_table.ready(config)
    load_job.run(config, tmp_path / "out.parquet", "", threading.Event(), lambda **k: None)
    assert rows_of(file, "loaded") == [(1, "가"), (2, "나"), (3, "다")]


def test_load_refuses_missing_folder_and_unknown_extension(tmp_path, folder):
    with pytest.raises(ValueError, match="폴더가 없어요"):
        load_table.ready(table_config(tmp_path, {"kind": "folder", "file": str(tmp_path / "nowhere" / "new.duckdb")}))
    assert not (tmp_path / "nowhere").exists()
    with pytest.raises(ValueError, match=r"적재할 DB 파일\(\.duckdb\)을 고르세요"):
        load_table.ready(table_config(tmp_path, {"kind": "folder", "file": str(tmp_path / "new.txt")}))


def test_load_into_existing_file_keeps_other_tables(tmp_path, folder):
    file = tmp_path / "have.duckdb"
    with duckdb.connect(str(file)) as con:
        con.execute("create table other as select 7 as n")
    config = table_config(tmp_path, {"kind": "folder", "file": str(file)})
    load_job.run(config, tmp_path / "out.parquet", "", threading.Event(), lambda **k: None)
    with duckdb.connect(str(file), read_only=True) as con:
        assert con.table("other").fetchall() == [(7,)] and con.table("loaded").count("*").fetchall() == [(3,)]


def test_screen_tells_missing_db_file_is_created():
    html = STATIC.read_text(encoding="utf-8")
    assert "없는 파일 이름을 적으면 새로 만들어요" in html
