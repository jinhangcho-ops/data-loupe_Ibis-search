"""묶음 3(수집·적재·예약) 리뷰 지적 재현. 바깥 인터넷은 쓰지 않고 가짜 응답·가짜 적재 대상으로 확인한다. 데이터는 여기서 지어낸다."""
import datetime as dt
import json
import threading
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import collect_config
import collect_http
import collect_job
import collect_store
import filelock
import load_job
import load_table
import schedule
import settings
import uploads
from collect_http import Halt, Reply


def reply(body, url):
    return Reply(200, [["Content-Type", "application/json"]], json.dumps(body).encode(), url)


def api_config(tmp_path, **paging):
    return collect_config.normalize({
        "mode": "api", "request": {"url": "https://api.example.com/items"}, "response": {"records": "items"},
        "paging": {"kind": "next_url", "next_path": "next", "max_pages": 5, **paging},
        "balance": {"interval": 0, "retries": 0}, "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")}})


def prepared(config):
    path = collect_job.output_path(config)
    return path, collect_job.parts(path), {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}


def serve(monkeypatch, pages):
    """주소 → 응답 몸통. 보낸 주소를 모아 돌려준다."""
    sent = []

    def fetch(request, timeout, secrets=()):
        sent.append(request["url"])
        return reply(pages[request["url"]], request["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)
    return sent


# ---------- M3: next_url이 다른 호스트를 가리키면 멈춘다 ----------

def test_next_url_other_host_halts(tmp_path, monkeypatch, folder):
    config = api_config(tmp_path)
    path, found, record = prepared(config)
    sent = serve(monkeypatch, {"https://api.example.com/items": {"items": [{"a": 1}], "next": "https://evil.example.net/steal"}})
    with pytest.raises(Halt) as error:
        collect_job.run_api(config, {}, path, found, record, threading.Event())
    assert sent == ["https://api.example.com/items"]
    assert "evil.example.net" in str(error.value)


def test_next_url_same_host_still_followed(tmp_path, monkeypatch, folder):
    config = api_config(tmp_path)
    path, found, record = prepared(config)
    sent = serve(monkeypatch, {"https://api.example.com/items": {"items": [{"a": 1}], "next": "/items?p=2"},
                               "https://api.example.com/items?p=2": {"items": [{"a": 2}], "next": None}})
    assert collect_job.run_api(config, {}, path, found, record, threading.Event()) is True
    assert len(sent) == 2 and pq.read_table(path).num_rows == 2


# ---------- M8: 0건이면 파일을 새로 만들지 않았다고 알리고, 옛 결과 파일을 적재·기록하지 않는다 ----------

class FakeKafka:
    """적재 대상 흉내: 받은 행을 모은다. batch 번호별로 put 호출을 세고, fail_at번째 put에서 오류를 낸다."""
    fail_at = None
    password_in_error = ""
    made = 0
    rows = []

    @staticmethod
    def ready(config):
        pass

    @staticmethod
    def sign(config):
        return {"topic": "t"}

    @staticmethod
    def where(config):
        return "fake"

    class Sink:
        fresh = False
        placed = {}
        last = ""

        def __init__(self, config, password, schema, resumed, load_id=None):
            FakeKafka.made += 1
            self.calls = 0

        def put(self, unit):
            self.calls += 1
            if FakeKafka.fail_at == self.calls:
                raise ValueError(f"연결 실패 {FakeKafka.password_in_error}")
            FakeKafka.rows += unit.column("n").to_pylist()
            return unit.num_rows, 0, 0

        def done(self):
            pass

        def close(self):
            pass


@pytest.fixture
def fake_kafka(monkeypatch):
    FakeKafka.fail_at, FakeKafka.password_in_error, FakeKafka.made, FakeKafka.rows = None, "", 0, []
    monkeypatch.setitem(load_job.TARGETS, "kafka", FakeKafka)
    return FakeKafka


def load_config(path, **load):
    return collect_config.normalize({"mode": "api", "request": {"url": "https://api.example.com/items"}, "response": {"records": "items"},
                                     "output": {"format": "parquet", "path": str(path)},
                                     "load": {"target": "kafka", "kafka": {"destination": "d"}, "batch": 100, "retries": 0, **load}})


def write_result(path, count, start=0):
    pq.write_table(pa.table({"n": list(range(start, start + count))}), path)


def test_zero_rows_does_not_load_or_log_old_file(tmp_path, monkeypatch, folder, fake_kafka):
    config = load_config(tmp_path / "out.parquet")
    path = collect_job.output_path(config)
    write_result(path, 3)  # 지난번에 받은 결과 파일
    _, found, record = prepared(config)
    serve(monkeypatch, {"https://api.example.com/items": {"items": []}})
    collect_store.update(started=time.time())
    collect_job.work({**config, "paging": {**config["paging"], "kind": "none"}}, {}, path, found, record, threading.Event(), "")
    assert fake_kafka.made == 0 and fake_kafka.rows == []
    assert uploads.entries() == []
    assert collect_job.snapshot()["phase"] == "끝"


def test_run_api_reports_whether_it_made_a_file(tmp_path, monkeypatch, folder):
    config = api_config(tmp_path, kind="none")
    path, found, record = prepared(config)
    serve(monkeypatch, {"https://api.example.com/items": {"items": []}})
    assert collect_job.run_api(config, {}, path, found, record, threading.Event()) is False
    assert not path.exists()


# ---------- M7: 적재 기록은 결과 파일이 그대로일 때만 이어 넣는다 ----------

def test_load_record_ignored_when_result_file_changed(tmp_path, folder, fake_kafka):
    path = tmp_path / "out.parquet"
    config = load_config(path)
    write_result(path, 250)
    fake_kafka.fail_at = 2  # 둘째 묶음에서 실패: 첫 묶음 100행까지 기록
    with pytest.raises(ValueError):
        load_job.run(config, path, "", threading.Event(), lambda **k: None)
    assert load_job.read_record(path)["rows"] == 100
    fake_kafka.fail_at, fake_kafka.rows = None, []
    write_result(path, 300, start=1000)  # 결과 파일이 바뀜
    load_job.run(config, path, "", threading.Event(), lambda **k: None)
    assert fake_kafka.rows == list(range(1000, 1300))  # 처음부터


def test_load_record_resumes_when_result_file_same(tmp_path, folder, fake_kafka):
    path = tmp_path / "out.parquet"
    config = load_config(path)
    write_result(path, 250)
    fake_kafka.fail_at = 2
    with pytest.raises(ValueError):
        load_job.run(config, path, "", threading.Event(), lambda **k: None)
    fake_kafka.fail_at, fake_kafka.rows = None, []
    load_job.run(config, path, "", threading.Event(), lambda **k: None)
    assert fake_kafka.rows == list(range(100, 250))


# ---------- 적재 실패 문구의 비밀번호 가리기 ----------

def test_ledger_error_masks_password(tmp_path, folder, fake_kafka):
    path = tmp_path / "out.parquet"
    config = load_config(path)
    write_result(path, 50)
    fake_kafka.fail_at, fake_kafka.password_in_error = 1, "pw-Secret-9"
    with pytest.raises(ValueError):
        load_job.run(config, path, "pw-Secret-9", threading.Event(), lambda **k: None)
    error = uploads.entries()[0]["error"]
    assert "pw-Secret-9" not in error and "연결 실패" in error


# ---------- M14: 같은 결과 파일에 두 곳이 수집하지 못한다 ----------

def test_start_refuses_when_result_lock_held(tmp_path, monkeypatch, folder):
    config = api_config(tmp_path, kind="none")
    held = filelock.lock_path("x")
    other = collect_job.lock_of(config)
    assert other != held
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text(f"{__import__('os').getpid()} {__import__('psutil').Process().create_time()}", encoding="ascii")  # 다른 곳이 잡은 잠금(살아 있는 프로세스)
    with pytest.raises(ValueError) as error:
        collect_job.start(config)
    assert str(error.value) == collect_job.BUSY
    assert not collect_job.parts(collect_job.output_path(config))["record"].exists()  # 진행 기록도 건드리지 않음
    other.unlink()


def test_start_holds_lock_until_job_ends(tmp_path, monkeypatch, folder):
    config = api_config(tmp_path, kind="none")
    gate = threading.Event()
    monkeypatch.setitem(collect_job.RUNNERS, "api", lambda *a: gate.wait(5) and True)
    collect_job.start(config)
    lock = collect_job.lock_of(config)
    assert lock.exists()
    gate.set()
    collect_job.current["thread"].join(5)
    assert not lock.exists()


# ---------- S: 예약 inapp+windows ----------

def test_tick_skips_windows_schedules(folder, monkeypatch):
    base = {**schedule.DEFAULT, "every": "interval", "minutes": 5}
    settings.write(schedule.FILE, {"둘 다": {**base, "inapp": True, "windows": True}, "프로그램만": {**base, "inapp": True, "windows": False}})
    ran, now = [], [dt.datetime(2026, 10, 15, 9, 0, 1)]
    monkeypatch.setattr(schedule, "clock", lambda: now[0])
    monkeypatch.setattr(schedule, "execute", lambda name, how: ran.append(name))
    due = {}
    schedule.tick(due)
    now[0] = dt.datetime(2026, 10, 15, 9, 6, 0)
    schedule.tick(due)
    assert ran == ["프로그램만"]


def test_screen_turns_inapp_off_when_windows_on():
    html = open("static/collect.html", encoding="utf-8").read()
    assert '$("sc-windows").onchange' in html and '$("sc-inapp").checked = false' in html


# ---------- S: {today}·{now}는 이어 받기 해시에 날짜를 넣는다 ----------

def test_digest_changes_with_day_when_url_has_today(tmp_path, monkeypatch):
    config = collect_config.normalize({"mode": "api", "request": {"url": "https://api.example.com/{today}/items"}})
    plain = collect_config.normalize({"mode": "api", "request": {"url": "https://api.example.com/items"}})
    web = collect_config.normalize({"mode": "web", "web": {"url": "https://shop.example.com/?d={today}", "items": "li", "fields": [{"name": "a", "selector": "", "attr": "text"}]}})
    days = {}
    for day in ("2026-10-15", "2026-10-16"):
        monkeypatch.setattr(collect_job, "today", lambda day=day: day)
        days[day] = (collect_job.digest(config), collect_job.digest(plain), collect_job.digest(web))
    first, second = days.values()
    assert first[0] != second[0] and first[2] != second[2]
    assert first[1] == second[1]


# ---------- S: 약한 ETag는 If-Range에 쓰지 않는다 ----------

def test_if_range_uses_only_strong_etag():
    modified = "Wed, 21 Oct 2026 07:28:00 GMT"
    assert collect_job.if_range('"abc"', modified) == '"abc"'
    assert collect_job.if_range('W/"abc"', modified) == modified
    assert collect_job.if_range('W/"abc"', None) is None
    assert collect_job.if_range(None, modified) == modified


# ---------- S: settings.write 원자적 ----------

def test_settings_write_keeps_old_file_when_writing_breaks(folder, monkeypatch):
    settings.write("x.json", {"a": 1})
    real = type(folder).write_text

    def broken(self, text, *a, **k):
        real(self, text[:3], *a, **k)
        raise OSError("디스크가 꽉 찼습니다")
    monkeypatch.setattr(type(folder), "write_text", broken)
    with pytest.raises(OSError):
        settings.write("x.json", {"a": 2})
    monkeypatch.undo()
    assert json.loads((folder / "x.json").read_text(encoding="utf-8")) == {"a": 1}
    assert sorted(p.name for p in folder.glob("x.json*")) == ["x.json", "x.json.bak"]


@pytest.mark.parametrize("which", ["collect_store", "load_job"])
def test_progress_records_are_written_atomically(tmp_path, monkeypatch, which):
    target = tmp_path / "r.part.json" if which == "collect_store" else load_job.record_path(tmp_path / "r.parquet")
    write = (lambda: collect_store.write_record(target, {"done": [1]})) if which == "collect_store" else (lambda: load_job.write_record(tmp_path / "r.parquet", {"rows": 1}))
    write()
    before = target.read_text(encoding="utf-8")
    real = type(target).write_text

    def broken(self, text, *a, **k):
        real(self, text[:3], *a, **k)
        raise OSError("꺼짐")
    monkeypatch.setattr(type(target), "write_text", broken)
    with pytest.raises(OSError):
        write()
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == before


# ---------- S: 여러 프로세스가 읽고-쓰는 기록은 잠금 파일로 감싼다 ----------

def blocked_while_locked(name, action, monkeypatch):
    """다른 곳이 name 잠금을 잡고 있는 동안 action이 끝나지 않고, 풀면 끝나는지."""
    monkeypatch.setattr(filelock, "WAIT", 3)
    lock = filelock.locks() / name
    assert filelock.hold(lock)
    thread = threading.Thread(target=action, daemon=True)
    thread.start()
    thread.join(0.3)
    waited = thread.is_alive()
    filelock.let_go(lock)
    thread.join(3)
    return waited and not thread.is_alive()


def test_uploads_put_waits_for_lock(folder, monkeypatch):
    assert blocked_while_locked("uploads.lock", lambda: uploads.log_file(folder / "a.csv", 1), monkeypatch)
    assert len(uploads.entries()) == 1


def test_load_tables_remember_waits_for_lock(folder, monkeypatch):
    link = {"kind": "folder", "file": str(folder / "w.duckdb"), "url": "", "user": ""}
    assert blocked_while_locked("load_tables.lock", lambda: load_table.remember(link, "", "items", [["a", "정수"]]), monkeypatch)
    assert len(load_table.made()) == 1
