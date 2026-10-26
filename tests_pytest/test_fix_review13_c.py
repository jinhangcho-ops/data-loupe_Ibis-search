"""13C: 끝 쪽 뒤 404를 끝으로 보기, 묶음 크기만 바꾼 [적재만 다시]는 이어 넣기, 적재가 덜 끝난 결과 파일을 다음 수집이 덮어쓰지 않기,
다른 프로세스(Windows 예약)가 수집 중이면 기능 설치를 거절하기. 네트워크·창 없이, 응답은 가짜로 바꾸고 적재는 진짜 DuckDB 파일에 한다."""
import json
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import collect_config, collect_http, collect_job, load_job, load_table
from collect.collect_http import Halt, Reply
from core import features, filelock
from integrations import schedule


# ---------- 13C1 끝 쪽 뒤 404 ----------

def serve_pages(monkeypatch, answer):
    """page 번호마다 answer(번호)가 (상태, 기록 목록)을 정한다."""
    def fetch(req, timeout, secrets=()):
        status, items = answer(int(parse_qs(urlsplit(req["url"]).query)["page"][0]))
        time.sleep(0.05)  # 동시 2개면 빈 쪽 응답 전에 다음 쪽 요청이 나가게
        return Reply(status, [["Content-Type", "application/json"]], json.dumps({"items": items}).encode(), req["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)


def run_pages(tmp_path, concurrency):
    config = collect_config.normalize({"mode": "api", "request": {"url": "https://api.example.com/items?page={page}"}, "response": {"records": "items"},
                                       "paging": {"kind": "page", "start": 1, "max_pages": 50},
                                       "balance": {"interval": 0.0, "retries": 0, "concurrency": concurrency},
                                       "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")}})
    path = collect_job.output_path(config)
    record = {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    collect_job.run_api(config, {}, path, collect_job.parts(path), record, threading.Event())
    return record


@pytest.mark.parametrize("concurrency", [1, 2])
def test_404_after_empty_page_is_end(tmp_path, monkeypatch, folder, concurrency):
    serve_pages(monkeypatch, lambda p: (200, [{"a": p}]) if p <= 3 else (200, []) if p == 4 else (404, []))
    record = run_pages(tmp_path, concurrency)
    assert record["end"] == 3 and record["rows"] == 3


def test_404_instead_of_empty_page_is_end(tmp_path, monkeypatch, folder):
    serve_pages(monkeypatch, lambda p: (200, [{"a": p}]) if p <= 3 else (404, []))
    record = run_pages(tmp_path, 1)
    assert record["end"] == 3 and record["rows"] == 3


def test_404_on_first_page_still_fails(tmp_path, monkeypatch, folder):
    serve_pages(monkeypatch, lambda p: (404, []))
    with pytest.raises(Halt, match="404"):
        run_pages(tmp_path, 2)


# ---------- 13C2 묶음 크기만 바꾸면 이어 넣기 ----------

def table_config(path, db, batch, **table):
    return collect_config.normalize({"mode": "api", "request": {"url": "https://x.example/i"}, "response": {"records": "items"},
                                     "output": {"format": "parquet", "path": str(path)},
                                     "load": {"target": "table", "table": {"connection": {"kind": "folder", "file": str(db)}, "name": "t", "how": "append", **table},
                                              "batch": batch, "retries": 0}})


def test_batch_size_change_resumes(tmp_path, monkeypatch, folder):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    pq.write_table(pa.table({"n": list(range(1000))}), path)
    con = duckdb.connect(str(db))
    con.execute("create table t (n BIGINT)")
    con.close()
    real, calls = load_table.insert, [0]

    def flaky(*a):
        calls[0] += 1
        if calls[0] == 6:
            raise RuntimeError("DB timeout")
        return real(*a)
    monkeypatch.setattr(load_table, "insert", flaky)
    with pytest.raises(RuntimeError):
        load_job.run(table_config(path, db, 100), path, "", threading.Event(), lambda **k: None)
    assert load_job.read_record(path)["rows"] == 500
    monkeypatch.setattr(load_table, "insert", real)
    load_job.run(table_config(path, db, 200), path, "", threading.Event(), lambda **k: None)
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select count(*), count(distinct n) from t").fetchone() == (1000, 1000)
    con.close()


def test_signature_keeps_target_not_batch(tmp_path):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    assert load_job.signature(table_config(path, db, 100)) == load_job.signature(table_config(path, db, 200))
    assert load_job.signature(table_config(path, db, 100)) != load_job.signature(table_config(path, db, 100, name="u"))


# ---------- 13C3 적재가 덜 끝난 결과 파일 ----------

def collect_config_with_load(tmp_path):
    return {"mode": "api", "request": {"url": "https://api.example.com/items"}, "response": {"records": "items"}, "paging": {"kind": "none"},
            "balance": {"interval": 0, "retries": 0}, "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")},
            "load": {"target": "table", "table": {"connection": {"kind": "folder", "file": str(tmp_path / "t.duckdb")}, "name": "t", "how": "append"}, "batch": 100}}


def test_collect_refuses_when_load_left(tmp_path, monkeypatch, folder):
    config = collect_config_with_load(tmp_path)
    path = tmp_path / "out.parquet"
    pq.write_table(pa.table({"n": list(range(10))}), path)
    load_job.write_record(path, {"sign": "x", "file": load_job.file_stamp(path), "rows": 5, "batches": 0})
    launched = []
    monkeypatch.setattr(collect_job, "launch", lambda *a, lock, **k: launched.append(lock))
    with pytest.raises(ValueError, match="적재만 다시"):
        collect_job.start(config)
    assert launched == [] and not collect_job.lock_of(collect_job.normalize(config)).exists()
    # 결과 파일이 그사이 바뀐 옛 기록이면 막지 않는다
    load_job.write_record(path, {"sign": "x", "file": [0, 0], "rows": 5, "batches": 0})
    collect_job.start(config)
    assert len(launched) == 1
    filelock.let_go(launched[0])


# ---------- 13C5 다른 프로세스의 수집 중 설치 거절 ----------

def test_install_refuses_while_other_process_collects(tmp_path, monkeypatch, folder):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DataLoupe.exe"))
    (tmp_path / "features.ini").write_text("[app]\nversion=1.2.3\nfeatures=query,collect\n", encoding="utf-8")
    fetched = []
    monkeypatch.setattr(features, "fetch", lambda url, out: fetched.append(url) or (_ for _ in ()).throw(RuntimeError("받기 멈춤")))
    run_id = schedule.add_run("매일", "windows")  # 살아 있는 프로세스가 도는 중
    with pytest.raises(ValueError, match="수집"):
        features.install(["query", "collect"])
    assert fetched == []
    schedule.change_runs(lambda runs: [r.update(owner="999999 1.0") for r in runs if r["id"] == run_id])  # 끝난 프로세스의 남은 기록
    with pytest.raises(RuntimeError, match="받기 멈춤"):
        features.install(["query", "collect"])
    assert len(fetched) == 1
