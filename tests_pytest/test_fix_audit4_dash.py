"""4차 4D(서버·이름·설치·수집): 고치기 전에는 실패하던 것들을 하나씩 재현한다. Kafka·작업 스케줄러·창은 모두 가짜다."""
import datetime as dt
import os
from pathlib import Path

import pyarrow as pa
import pytest
from flask import Response

from collect import load_kafka
from core import dash_store as store
from core import server
from core import settings
from integrations import schedule

ROOT = Path(__file__).resolve().parent.parent


# ---------- 4D1 옮기기 실패: 비었거나 표식만 있는 새 폴더는 다시 옮긴다 ----------

def old_folder(tmp_path):
    old = tmp_path / "ibis_search"
    old.mkdir()
    (old / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    return old, tmp_path / "DataLoupe" / "settings.json"


def test_failed_move_is_retried_next_start_and_marker_goes_away(tmp_path, monkeypatch):
    old, path = old_folder(tmp_path)
    real = os.replace

    def locked(src, dst):
        raise PermissionError("사용 중")
    monkeypatch.setattr(os, "replace", locked)
    assert "사용 중" in settings.migrate(path)
    assert (path.parent / settings.FAILED).is_file()
    monkeypatch.setattr(os, "replace", real)  # 예전 앱을 껐다
    assert settings.migrate(path) is None
    assert (path.parent / "settings.json").read_text(encoding="utf-8") == '{"theme": "dark"}'
    assert not (path.parent / settings.FAILED).exists() and not old.exists()


def test_new_folder_with_content_is_not_touched(tmp_path, monkeypatch):
    old, path = old_folder(tmp_path)
    path.parent.mkdir()
    (path.parent / "settings.json").write_text("{}", encoding="utf-8")
    (path.parent / settings.FAILED).write_text("x", encoding="utf-8")
    assert settings.migrate(path) is None
    assert old.is_dir() and (path.parent / "settings.json").read_text(encoding="utf-8") == "{}"


def test_desktop_shows_the_reason():
    source = (ROOT / "desktop.py").read_text(encoding="utf-8")
    assert "moved_failed = settings.migrate()" in source and "MessageBoxW(None, moved_failed" in source


# ---------- 4D2 Kafka: 영구 오류가 남아도 행마다 flush하지 않는다 ----------

class Msg:
    def __init__(self, n):
        self.n = n

    def offset(self):
        return self.n

    def partition(self):
        return 0

    def value(self):
        return b"v"

    def key(self):
        return b"k"


class FakeProducer:
    def __init__(self):
        self.queue, self.flushes = [], 0

    def produce(self, topic, body, key, on_delivery):
        self.queue.append((body, on_delivery))

    def poll(self, timeout):
        queue, self.queue = self.queue, []
        for body, callback in queue:
            n = int(body.decode().split(":")[1].strip(" }"))
            callback("MSG_SIZE_TOO_LARGE" if n == 3 else None, None if n == 3 else Msg(n))

    def flush(self, timeout):
        self.flushes += 1
        self.poll(0)
        return 0


def test_permanent_error_does_not_flush_per_row(monkeypatch):
    fake = FakeProducer()
    monkeypatch.setattr(load_kafka, "destination", lambda name: {"topic": "t"})
    monkeypatch.setattr(load_kafka, "producer", lambda item, password, on_error=None: fake)
    sink = load_kafka.Sink({"load": {"kafka": {"destination": "x", "key": "id"}}}, "", pa.schema([("id", pa.int64())]), None)
    ok, _, failed = sink.put(pa.table({"id": list(range(3000))}))
    assert (ok, failed) == (2999, 1)
    assert fake.flushes <= 3


# ---------- 4D3 CI 권한 ----------

def test_workflow_permissions():
    import yaml
    flow = yaml.safe_load((ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8"))
    build, release = flow["jobs"]["build"], flow["jobs"]["release"]
    assert build["permissions"] == {"contents": "read"} and flow["permissions"] == {"contents": "read"}
    assert release["needs"] == "build" and release["permissions"] == {"contents": "write"} and "tag" in release["if"]
    assert next(step for step in build["steps"] if "checkout" in step.get("uses", ""))["with"]["persist-credentials"] is False
    text = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    assert "--clobber" in text and "gh release create" in text


# ---------- 4D4 제거할 때 예약 작업 지우기 ----------

def test_uninstall_removes_only_dataloupe_folder_tasks():
    text = (ROOT / "installer" / "DataLoupe.iss").read_text(encoding="utf-8")
    run = text[text.index("[UninstallRun]"):]
    assert "Unregister-ScheduledTask" in run and "-TaskPath '\\DataLoupe\\'" in run and "runhidden" in run
    assert "설치할 때 설치 프로그램이 새 이름으로 옮깁니다" in (ROOT / "README.md").read_text(encoding="utf-8")


# ---------- 4D5 이미지·압축 형식은 gzip하지 않는다 ----------

@pytest.mark.parametrize("mimetype", ["image/png", "application/zip", "application/gzip", "application/vnd.apache.parquet"])
def test_squeeze_skips_images_and_packed(mimetype):
    big = Response(b"x" * (server.GZIP_FROM + 10), mimetype=mimetype)
    with server.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        got = server.squeeze(big)
    assert got.headers.get("Content-Encoding") is None


def test_squeeze_still_gzips_json():
    big = Response(b"[" + b"1," * server.GZIP_FROM + b"1]", mimetype="application/json")
    with server.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        got = server.squeeze(big)
    assert got.headers["Content-Encoding"] == "gzip"


# ---------- 4D6 예전 작업 옮기기는 한 번 끝나면 다시 안 돈다 ----------

class FakeTasks:
    OLD_FOLDER = "\\old\\"

    def __init__(self, fail=False):
        self.queries, self.deleted, self.fail = [], [], fail

    def query(self, task, folder, strict=False):
        self.queries.append(task)
        if self.fail:
            raise OSError("작업 스케줄러가 답하지 않음")
        return {"registered": True}

    def delete(self, task, folder, known=False):
        self.deleted.append(task)


def test_move_old_tasks_runs_once(folder, monkeypatch):
    settings.write(schedule.FILE, {"a": {"windows": True, "task": "t1"}, "b": {"inapp": True}})
    fake = FakeTasks()
    monkeypatch.setattr(schedule, "wintask", fake)
    monkeypatch.setattr(schedule, "register", lambda name, item, taken: item["task"])
    assert schedule.move_old_tasks() == ["a"]
    assert fake.queries == ["t1"] and fake.deleted == ["t1"]
    assert schedule.move_old_tasks() == [] and fake.queries == ["t1"]  # 표식이 있어 다시 조회하지 않는다


def test_move_old_tasks_retries_after_failure(folder, monkeypatch):
    settings.write(schedule.FILE, {"a": {"windows": True, "task": "t1"}})
    monkeypatch.setattr(schedule, "wintask", FakeTasks(fail=True))
    assert schedule.move_old_tasks() == []
    assert settings.read(schedule.MOVED) is None  # 못 옮겼으니 다음 시작 때 다시
    fake = FakeTasks()
    monkeypatch.setattr(schedule, "wintask", fake)
    monkeypatch.setattr(schedule, "register", lambda name, item, taken: item["task"])
    assert schedule.move_old_tasks() == ["a"]


# ---------- 4D7 conftest가 notify를 미리 올린다 ----------

def test_conftest_preloads_notify():
    assert "from integrations import notify" in (ROOT / "tests_pytest" / "conftest.py").read_text(encoding="utf-8")


# ---------- 4D8 기준일 미래 검사는 바뀔 때만 ----------

def test_saved_future_as_of_does_not_block_other_edits(folder):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    saved = store.save_dashboard({"name": "D", "cards": []})
    boards = store.editable()
    boards[saved["id"]] = {**boards[saved["id"]], "as_of": tomorrow}  # 예전에 미래 날짜로 저장된 대시보드
    store.write(boards)
    again = store.save_dashboard({**store.get_dashboard(saved["id"]), "name": "D2"})
    assert again["as_of"] == tomorrow and again["name"] == "D2"
    with pytest.raises(ValueError, match="오늘"):
        store.save_dashboard({**again, "as_of": (dt.date.today() + dt.timedelta(days=2)).isoformat()})  # 바꾸는 값은 여전히 검사
    with pytest.raises(ValueError, match="오늘"):
        store.save_dashboard({"name": "N", "cards": [], "as_of": tomorrow})  # 새 대시보드


# ---------- 4D9 수집 화면: 양쪽 다 ----------

def test_inapp_on_turns_windows_off():
    html = (ROOT / "static" / "collect" / "collect.html").read_text(encoding="utf-8")
    assert '$("sc-inapp").onchange' in html and '$("sc-windows").checked = false' in html
