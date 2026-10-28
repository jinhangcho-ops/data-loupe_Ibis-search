"""14D: 적재 전에 실패해도 결과 파일을 지키기, 최대 쪽 수에서 멈춤 알리기, 429 간격 상한·되돌리기, 바꾸기의 이름 바꾸기(원래 것을 지우지 않음),
덧붙이기의 빈 컬럼·없는 컬럼, DuckDB는 Arrow 그대로 넣기, 오래된 이어 받기, 간격 예약의 건너뜀 알림, 한 건씩·키 인덱스 안내.
네트워크·창 없이, 응답은 가짜로 바꾸고 적재는 진짜 DuckDB 파일에 한다. PG·MySQL·Snowflake는 접속 없이 만들어지는 SQL만 본다."""
import datetime as dt
import json
import os
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import collect_config, collect_http, collect_job, load_job, load_table
from collect.collect_http import Balancer, Halt, Reply
from integrations import notify, schedule


def serve(monkeypatch, answer):
    """page 번호마다 answer(번호)가 기록 목록을 정한다."""
    def fetch(req, timeout, secrets=()):
        items = answer(int(parse_qs(urlsplit(req["url"]).query)["page"][0]))
        return Reply(200, [["Content-Type", "application/json"]], json.dumps({"items": items}).encode(), req["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)


def config(path, db=None, how="append", max_pages=100, kind="page", **load):
    found = {"mode": "api", "request": {"url": "https://x.example/i?page={page}"}, "response": {"records": "items"},
             "paging": {"kind": kind, "start": 1, "max_pages": max_pages, "cursor_path": "next"},
             "balance": {"interval": 0.0, "retries": 0, "concurrency": 2}, "output": {"format": "parquet", "path": str(path)}}
    if db:
        found["load"] = {"target": "table", "table": {"connection": {"kind": "folder", "file": str(db)}, "name": "t", "how": how}, "retries": 0, **load}
    return collect_config.normalize(found)


def collect(cfg):
    collect_job.start(cfg)
    collect_job.current["thread"].join()
    return collect_job.snapshot()


def load(cfg, path):
    return load_job.run(cfg, path, "", threading.Event(), lambda **k: None)


def make_table(db, sql):
    con = duckdb.connect(str(db))
    con.execute(sql)
    con.close()


# ---------- E1 넣을 곳을 열기 전에 실패해도 기록을 남긴다 ----------

def test_failure_before_sink_keeps_result_file(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    make_table(db, "create table t (id BIGINT, price BIGINT)")
    pq.write_table(pa.table({"id": [1], "price": ["비쌈"]}), path)  # 타입이 달라 덧붙일 수 없다
    cfg = config(path, db)
    with pytest.raises(ValueError, match="컬럼이 달라"):
        load(cfg, path)
    assert load_job.read_record(path) is not None
    with pytest.raises(ValueError, match="지난 적재가 끝나지 않았어요.*out.load.json"):  # 막힌 이유와 빠져나갈 길(기록 파일 이름)
        collect_job.start(cfg)


def test_failure_before_sink_keeps_other_target_progress(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    pq.write_table(pa.table({"id": [1, 2]}), path)
    stamp = load_job.file_stamp(path)
    load_job.write_record(path, {"sign": "다른 대상", "file": stamp, "rows": 1, "batches": 1, "inserted": 1, "skipped": 0, "failures": 0})
    with pytest.raises(ValueError, match="없어요"):  # 덧붙일 테이블이 없다
        load(config(path, db), path)
    assert load_job.read_record(path)["sign"] == "다른 대상"  # 덜 넣은 다른 대상의 기록을 지우지 않는다


# ---------- E2 최대 쪽 수에서 멈추면 알린다 ----------

def test_max_pages_is_reported(tmp_path, monkeypatch, folder):
    serve(monkeypatch, lambda p: [{"id": p}] if p <= 8 else [])
    s = collect(config(tmp_path / "a.parquet", max_pages=5))
    assert (s["phase"], s["rows"]) == ("끝", 5) and s["message"].startswith("최대 쪽 수 5에서 멈췄어요")
    s = collect(config(tmp_path / "b.parquet", max_pages=20))  # 빈 쪽으로 끝났다
    assert (s["phase"], s["rows"], s["message"]) == ("끝", 8, "")


def test_max_pages_is_reported_for_cursor(tmp_path, monkeypatch, folder):
    def fetch(req, timeout, secrets=()):
        p = int(parse_qs(urlsplit(req["url"]).query).get("c", ["0"])[0] or 0)
        return Reply(200, [], json.dumps({"items": [{"id": p}], "next": p + 1}).encode(), req["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)
    cfg = config(tmp_path / "c.parquet", max_pages=3, kind="cursor")
    cfg["request"]["url"] = "https://x.example/i?c={cursor}"
    s = collect(cfg)
    assert s["rows"] == 3 and s["message"].startswith("최대 쪽 수 3에서")


def test_capped_message_goes_out_and_counts_as_failure():
    text = collect_job.CAPPED.format(100)
    assert schedule.notice_text(text) == text  # 값이 없는 정해진 문구라 그대로 알린다
    assert text.startswith(collect_job.CAPPED.split("{")[0])


# ---------- E3 429 간격 상한, 잇따라 받으면 되돌림, 다시 시도로 받은 것은 실패로 세지 않음 ----------

def test_slow_is_capped_and_recovers():
    b = Balancer(2, 1.0, 5, threading.Event())
    for _ in range(10):
        b.slow()
    assert (b.limit, b.interval) == (1, 8.0)
    for _ in range(collect_http.STEADY):
        b.fine()
    assert (b.limit, b.interval) == (2, 1.0)


def test_retried_429_does_not_halt():
    b = Balancer(1, 0.0, 5, threading.Event())
    b.slow = b.pause = lambda *a: None
    n = [0]

    def attempt():
        n[0] += 1
        return Reply(429 if n[0] % 5 == 0 else 200, [["Retry-After", "0"]], b"", "u")
    for _ in range(200):  # 고치기 전에는 84쪽 뒤 "실패가 20번을 넘어" 멈췄다
        b.call(attempt)
    assert b.failures > collect_http.MAX_FAILURES and b.lost == 0


def test_request_that_keeps_failing_still_halts():
    b = Balancer(1, 0.0, 1, threading.Event())
    b.slow = b.pause = lambda *a: None
    with pytest.raises(Halt, match="다시 시도해도 실패"):
        b.call(lambda: Reply(429, [["Retry-After", "0"]], b"", "u"))


# ---------- E4 바꾸기: 이름 바꾸기가 안 되면 원래 테이블을 지우지 않는다, 방언별 문장 ----------

def test_replace_with_index_keeps_original(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    pq.write_table(pa.table({"id": [1, 2, 3]}), path)
    load(config(path, db, how="new"), path)
    make_table(db, "create index ix on t(id)")  # DuckDB는 인덱스가 걸린 테이블의 이름을 바꾸지 못한다
    pq.write_table(pa.table({"id": [9]}), path)
    with pytest.raises(ValueError, match="원래 테이블은 그대로"):
        load(config(path, db, how="replace"), path)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select id from t order by id").fetchall() == [(1,), (2,), (3,)]
    assert con.execute("select id from t_ibis_load").fetchall() == [(9,)]
    assert con.execute("select index_name from duckdb_indexes()").fetchall() == [("ix",)]
    con.close()


def test_replace_swaps_on_duckdb(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    pq.write_table(pa.table({"id": [1, 2, 3]}), path)
    load(config(path, db, how="new"), path)
    pq.write_table(pa.table({"id": [9]}), path)
    load(config(path, db, how="replace"), path)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select id from t").fetchall() == [(9,)]
    assert sorted(r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()) == ["t"]
    con.close()


def fake(name, dialect):
    """SQL만 만들어 볼 연결 모양(접속하지 않음)."""
    return SimpleNamespace(name=name, dialect=dialect, compiler=SimpleNamespace(quoted=True))


def test_swap_sql_by_dialect():
    pg = load_table.swap_sql(fake("postgres", "postgres"), "s", "t", "t_ibis_load")
    assert pg == ("BEGIN; SET LOCAL lock_timeout = '5s'; ALTER TABLE \"s\".\"t\" RENAME TO \"t_ibis_old\"; "
                  "ALTER TABLE \"s\".\"t_ibis_load\" RENAME TO \"t\"; COMMIT")
    assert load_table.swap_sql(fake("mysql", "mysql"), "s", "t", "t_ibis_load") == "RENAME TABLE `s`.`t` TO `s`.`t_ibis_old`, `s`.`t_ibis_load` TO `s`.`t`"
    assert load_table.swap_sql(fake("clickhouse", "clickhouse"), "s", "t", "t_ibis_load") == 'EXCHANGE TABLES "s"."t" AND "s"."t_ibis_load"'  # 여러 테이블 RENAME은 원자적이지 않다
    assert load_table.swap_sql(fake("snowflake", "snowflake"), "s", "t", "t_ibis_load") is None
    assert load_table.rename_sql(fake("snowflake", "snowflake"), "s", "t", "t_ibis_old") == 'ALTER TABLE "s"."t" RENAME TO "s"."t_ibis_old"'
    assert load_table.rename_sql(fake("duckdb", "duckdb"), "s", "t", "t_ibis_old") == 'ALTER TABLE "s"."t" RENAME TO "t_ibis_old"'


# ---------- E5 덧붙이기: 값이 없는 컬럼은 테이블 타입으로, 없는 컬럼은 빈 값으로 ----------

def test_append_fills_empty_and_missing_columns(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    make_table(db, "create table t (id BIGINT, price BIGINT, note VARCHAR)")
    pq.write_table(pa.table({"id": [1, 2], "price": pa.array([None, None], pa.string())}), path)  # price는 이번에 모두 빈 값, note는 없음
    load(config(path, db), path)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select * from t order by id").fetchall() == [(1, None, None), (2, None, None)]
    con.close()


def test_check_route_agrees_with_load_on_empty_column(tmp_path, folder):
    """[확인]도 적재처럼 값이 하나도 없는 글자 컬럼을 테이블 타입으로 본다(확인은 막고 적재는 넣는 엇갈림이 없게)."""
    from collect import collect_app  # noqa: F401  적재 라우트가 앱에 달린다
    from core import server
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    make_table(db, "create table t (id BIGINT, memo BIGINT)")
    pq.write_table(pa.table({"id": [1], "memo": pa.array([None], pa.string())}), path)
    c = server.app.test_client()
    c.set_cookie("key", server.KEY)
    got = c.post("/api/load/table/check", json={"config": config(path, db)}).get_json()
    assert got["ok"] and got["message"] == "컬럼이 같아 덧붙일 수 있어요."
    pq.write_table(pa.table({"id": [1], "memo": pa.array([None], pa.string()), "extra": ["a"]}), path)
    got = c.post("/api/load/table/check", json={"config": config(path, db)}).get_json()
    assert not got["ok"] and got["message"].endswith("다른 점: 결과 파일에만 있는 컬럼 extra")  # 빈 memo는 다른 점이 아니다


def test_append_still_rejects_other_type_and_new_column(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    make_table(db, "create table t (id BIGINT)")
    pq.write_table(pa.table({"id": [1.5]}), path)
    with pytest.raises(ValueError, match="타입이 다른 컬럼 id"):
        load(config(path, db), path)
    pq.write_table(pa.table({"id": [1], "extra": [None]}), path)
    with pytest.raises(ValueError, match="결과 파일에만 있는 컬럼 extra"):
        load(config(path, db), path)


def test_empty_columns_csv(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a,b\n1,\n2,\n", encoding="utf-8")
    assert load_job.empty_columns(path, load_job.file_schema(path)) == {"b"}


# ---------- E6 DuckDB는 Arrow 그대로: 큰 정수·빈 날짜시각도 그대로 ----------

def test_duckdb_insert_keeps_values(tmp_path, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    pq.write_table(pa.table({"n": pa.array([2**62 + 1, None], pa.int64()), "ts": pa.array([dt.datetime(2026, 1, 1), None], pa.timestamp("us"))}), path)
    load(config(path, db, how="new"), path)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select * from t").fetchall() == [(2**62 + 1, dt.datetime(2026, 1, 1)), (None, None)]
    con.close()


# ---------- E7 하루보다 오래된 진행 기록은 이어 받지 않는다 ----------

def test_old_record_is_not_resumed(tmp_path, monkeypatch, folder):
    serve(monkeypatch, lambda p: [{"id": p}] if p <= 2 else [])
    cfg = config(tmp_path / "o.parquet")
    found = collect_job.parts(collect_job.output_path(cfg))
    began = (dt.datetime.now() - dt.timedelta(days=2)).isoformat(timespec="seconds")
    found["record"].write_text(json.dumps({"hash": collect_job.digest(cfg), "mode": "api", "done": [0], "rows": 1, "bytes": 1, "began": began}), encoding="utf-8")
    assert collect_job.resumable(cfg) is not None  # 시작은 이틀 전이어도 방금 받았으면(하루 한도로 매일 이어 받기) 이어 받는다
    old = time.time() - 2 * 86400
    os.utime(found["record"], (old, old))  # 마지막으로 받은 지 이틀
    assert collect_job.resumable(cfg) is None
    assert collect_job.start(cfg) is False
    collect_job.current["thread"].join()
    assert collect_job.snapshot()["rows"] == 2


def test_today_config_from_yesterday_is_not_resumed(tmp_path, monkeypatch, folder):
    """{today}가 있는 구성은 해시에 오늘 날짜가 들어가, 어제 받다 만 기록은 방금 썼어도 이어 받지 않는다(어제 날짜로 받지 않게)."""
    cfg = collect_config.normalize({**config(tmp_path / "o.parquet"), "request": {"url": "https://x.example/i?d={today:%Y%m%d}&page={page}"}})
    found = collect_job.parts(collect_job.output_path(cfg))
    with monkeypatch.context() as m:
        m.setattr(collect_job, "today", lambda: "2000-01-01")  # 어제 쓴 기록의 해시
        found["record"].write_text(json.dumps({"hash": collect_job.digest(cfg), "mode": "api", "done": [0], "rows": 1, "bytes": 1}), encoding="utf-8")
    assert collect_job.resumable(cfg) is None


# ---------- E8 간격 예약의 건너뜀은 '실패할 때만' 알림에서 뺀다 ----------

def test_interval_skip_is_not_notified(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda n, p: sent.append(p["title"]))
    items = {"i": {"every": "interval", "notify": "x", "notify_on": "fail"}, "d": {"every": "daily", "notify": "x", "notify_on": "fail"},
             "a": {"every": "interval", "notify": "x", "notify_on": "always"}}
    monkeypatch.setattr(schedule, "schedules", lambda: items)
    for name in ("i", "d", "a"):
        schedule.tell(name, "건너뜀", None, 0.0, schedule.SKIP_RUNNING)
    assert sent == ["수집을 건너뛰었어요"] * 2  # 매일 예약과 '늘' 알림만


# ---------- D9 한 건씩·키 인덱스 안내 ----------

def test_single_unit_on_remote_db_warns(tmp_path):
    cfg = config(tmp_path / "x.parquet", tmp_path / "t.duckdb", unit="single")
    assert "원격 DB" not in load_job.note_for(cfg, None)
    cfg["load"]["table"]["connection"].update(kind="url", url="postgresql://db.example/x")
    assert "원격 DB" in load_job.note_for(cfg, None)


def test_key_hint_mentions_index(tmp_path):
    cfg = config(tmp_path / "x.parquet", tmp_path / "t.duckdb")
    assert load_job.note_for(cfg, None) is None
    cfg["load"]["table"]["key"] = ["id"]
    assert "키(id)로 거를 때 키 컬럼에 인덱스가 없으면" in load_job.note_for(cfg, None)


def test_clickhouse_swap_drops_staging_with_old_content(monkeypatch):
    """ClickHouse는 맞바꾸므로 옛 내용이 staging에 있다: 지우는 것은 staging(원래 이름의 새 내용이 아니다)."""
    done = []
    monkeypatch.setattr(load_table, "swap_all", lambda con, statement: done.append(statement))
    monkeypatch.setattr(load_table, "drop", lambda con, schema, name: done.append(("drop", name)))
    con = SimpleNamespace(name="clickhouse", dialect="clickhouse", compiler=SimpleNamespace(quoted=True), list_tables=lambda database=None: ["t", "t_ibis_load"])
    load_table.swap(con, "s", "t", "t_ibis_load")
    assert done == ['EXCHANGE TABLES "s"."t" AND "s"."t_ibis_load"', ("drop", "t_ibis_load")]
    done.clear()
    load_table.swap(SimpleNamespace(**{**vars(con), "name": "mysql", "dialect": "mysql"}), "s", "t", "t_ibis_load")
    assert done[-1] == ("drop", "t_ibis_old")  # 이름을 옮긴 연결은 옮겨 둔 옛 테이블을 지운다
