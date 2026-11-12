"""감사 45(수집): offset 짧은 첫 쪽의 빠진 행, Parquet 바꿔치기 실패 때 받은 파일, [시험]의 한도 기다림, '.part' 결과 이름, CSV 수식 막기의 앞 빈칸. 응답·값은 여기서 지어낸다."""
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lazy import ibis
from collect import collect_config, collect_http, collect_job, collect_limits, collect_store
from collect.collect_http import Halt, Reply


def offset_run(tmp_path, monkeypatch, total, cap):
    """서버: 전체 total행, 한 번에 cap행까지. step은 100, 전체 수는 알리지 않는다."""
    def fetch(request, timeout, secrets=()):
        q = parse_qs(urlsplit(request["url"]).query)
        off, lim = int(q["offset"][0]), int(q["limit"][0])
        body = {"items": [{"id": i} for i in range(total)[off:off + min(lim, cap)]]}
        return Reply(200, [["Content-Type", "application/json"]], json.dumps(body).encode(), request["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)
    config = collect_config.normalize({
        "mode": "api", "request": {"url": "https://api.example.com/items", "query": [["offset", "{offset}"], ["limit", "100"]]},
        "response": {"records": "items"}, "paging": {"kind": "offset", "start": 0, "step": 100, "max_pages": 10},
        "balance": {"interval": 0, "retries": 0, "concurrency": 1}, "output": {"format": "parquet", "path": str(tmp_path / "o.parquet")}})
    path = collect_job.output_path(config)
    record = {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    collect_job.run_api(config, {}, path, collect_job.parts(path), record, threading.Event())
    return pq.read_table(path).column("id").to_pylist()


def test_offset_server_cap_halts(tmp_path, monkeypatch, folder):
    with pytest.raises(Halt, match="50"):  # 50~69행을 말없이 빠뜨리지 않는다
        offset_run(tmp_path, monkeypatch, 70, 50)


def test_offset_short_whole_table_ok(tmp_path, monkeypatch, folder):
    assert offset_run(tmp_path, monkeypatch, 70, 100) == list(range(70))


def test_parquet_replace_blocked_keeps_download(tmp_path, folder):
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"a": [0]}), path)
    found = collect_job.parts(path)
    pq.write_table(pa.table({"a": list(range(10))}), found["part"])
    config = {"file": {"kind": "parquet", "encoding": "auto"}, "output": {"format": "parquet"}}
    with open(path, "rb"), pytest.raises(Halt):  # 다른 프로그램이 결과 파일을 읽는 중
        collect_job.finish_file(config, path, found, {"url": "https://x.invalid/d.parquet"})
    assert found["part"].exists()
    collect_job.finish_file(config, path, found, {"url": "https://x.invalid/d.parquet"})  # 닫은 뒤에는 받은 파일로 바꾼다
    assert pq.read_table(path).num_rows == 10


def test_try_does_not_wait_for_limit(monkeypatch, folder):
    collect_limits.save("g", {"per_hour": 1})
    collect_limits.take("g", threading.Event())
    monkeypatch.setattr(collect_http, "fetch", lambda *a, **k: pytest.fail("보내면 안 된다"))
    config = {"mode": "api", "limit_group": "g", "request": {"method": "GET", "url": "http://127.0.0.1:9/x", "query": [], "headers": [], "body_kind": "none", "body": ""},
              "paging": {"start": 1, "step": 1}, "balance": {"timeout": 5}}
    with pytest.raises(Halt, match=collect_job.TRY_LIMITED):
        collect_job.first_page(config, {})


def test_part_output_name_refused(tmp_path, folder):
    config = {"mode": "api", "request": {"url": "https://api.example.com/x"}, "response": {"records": "items"},
              "output": {"format": "parquet", "path": str(tmp_path / "data.part.parquet")}}
    with pytest.raises(ValueError, match="'.part'"):
        collect_job.start(config)


def test_csv_guard_skips_leading_space(tmp_path):
    out = tmp_path / "r.csv"
    collect_store.write_output(ibis.duckdb.connect(), ibis.memtable({"v": [" =1+1", "\t=2", "ok"]}), "csv", out, str(tmp_path))
    assert out.read_text(encoding="utf-8-sig").splitlines()[1:] == ["' =1+1", "'\t=2", "ok"]
