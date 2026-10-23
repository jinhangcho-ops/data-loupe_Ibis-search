"""수집의 진행 기록은 쪽마다가 아니라 SAVE_EVERY초에 한 번 쓴다(collect_store.keep_record). 쪽마다 쓰면 기록이 커질수록 느려진다(쪽 수의 제곱).
기록을 덜 쓰는 동안 갑자기 끝나도, 이어 받으면 빠지거나 겹치는 쪽 없이 결과가 나오는지 지어낸 쪽으로 확인한다(네트워크 없음)."""
import json

import polars as pl
import pytest


@pytest.fixture
def job(tmp_path, monkeypatch):
    import collect_job
    import collect_store
    clock = [1000.0]
    monkeypatch.setattr(collect_store.time, "monotonic", lambda: clock[0])
    writes = []
    real = collect_store.write_record
    monkeypatch.setattr(collect_store, "write_record", lambda path, record: (writes.append(len(record["done"])), real(path, record))[1])
    path = tmp_path / "out.parquet"
    found = collect_job.parts(path)
    found["part"].mkdir()
    fresh = lambda: {"hash": "h", "mode": "api", "done": [], "rows": 0, "bytes": 0, "columns": [], "kinds": {}, "rename": {}}
    return collect_store, found, path, clock, writes, fresh


def page(i):
    return [{"page": i, "n": k} for k in range(3)]


def test_record_is_written_every_save_every_seconds(job):
    store, found, _, clock, writes, fresh = job
    record = fresh()
    for i in range(100):
        store.save_page(found, record, i, page(i), 10)
        clock[0] += 0.03125  # 쪽마다 1/32초(2진수로 딱 떨어지는 값) → 약 3초 동안 100쪽
    assert writes == [1, 17, 33, 49, 65, 81, 97]  # 첫 쪽에 쓰고, 그 뒤로 0.5초(16쪽)마다(쪽마다 100번이 아니다)
    with store.guard:
        store.keep_record(found, record, now=True)  # 멈춤·실패 때 work가 마저 쓴다
    assert json.loads(found["record"].read_text(encoding="utf-8"))["done"] == list(range(100))


def test_resume_after_abrupt_end_has_no_missing_or_duplicate_pages(job):
    store, found, path, clock, writes, fresh = job
    record = fresh()
    for i in range(10):
        store.save_page(found, record, i, page(i), 10)
        clock[0] += 0.125
    # 여기서 갑자기 끝남(마지막 기록을 쓰지 못함): 디스크의 기록에는 앞쪽만 있고, 쪽 파일은 10개 다 있다
    saved = json.loads(found["record"].read_text(encoding="utf-8"))
    assert saved["done"] == list(range(9)) and len(list(found["part"].iterdir())) == 10
    for i in range(10):  # 이어 받기: 기록에 없는 쪽만 다시 받는다(같은 번호의 파일을 덮어씀)
        if i not in saved["done"]:
            store.save_page(found, saved, i, page(i), 10)
    store.finish_api({"output": {"dedupe": [], "format": "parquet"}}, path, found, saved)
    got = pl.read_parquet(path)
    assert got.height == 30 and got.unique().height == 30 and sorted(set(got["page"].to_list())) == list(range(10))
    assert not found["record"].exists() and not found["part"].exists()  # 끝나면 작업 파일을 지운다


def test_record_write_never_leaves_a_half_file(tmp_path, monkeypatch):
    """적재·수집 진행 기록은 임시 파일에 쓴 뒤 바꿔치기한다: 바꾸기 전에 꺼져도 이전 기록이 그대로 읽힌다(깨진 기록이면 처음부터 다시 넣어 행이 두 번 들어간다)."""
    import os

    import collect_store
    import load_job
    out = tmp_path / "out.parquet"
    load_job.write_record(out, {"rows": 100, "sign": "s"})
    collect_store.write_record(tmp_path / "rec.json", {"done": [1]})

    def crash(*a):
        raise OSError("꺼짐")
    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        load_job.write_record(out, {"rows": 200, "sign": "s"})
    with pytest.raises(OSError):
        collect_store.write_record(tmp_path / "rec.json", {"done": [1, 2]})
    assert load_job.read_record(out)["rows"] == 100
    assert json.loads((tmp_path / "rec.json").read_text(encoding="utf-8")) == {"done": [1]}
