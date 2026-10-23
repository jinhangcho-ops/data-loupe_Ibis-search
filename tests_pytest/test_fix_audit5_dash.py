"""5차 5D(대시보드·수집·설치): 고치기 전에는 실패하던 것들을 재현한다. 작업 스케줄러·Kafka는 모두 가짜다."""
import threading
import time
from pathlib import Path

import pyarrow as pa
import pytest

from collect import load_kafka as lk
from core import settings
from integrations import schedule, wintask

ROOT = Path(__file__).resolve().parent.parent


# ---------- 5D1 schtasks 오류는 '없음'이 아니다 ----------

def test_move_old_tasks_scheduler_error_is_retried(folder, monkeypatch):
    settings.write(schedule.FILE, {"a": {"windows": True, "task": "t1"}})
    monkeypatch.setattr(wintask, "execute", lambda args: (1, "", "ERROR: The Task Scheduler service is not available."))
    monkeypatch.setattr(schedule, "register", lambda name, item, taken: item["task"])
    assert schedule.move_old_tasks() == []
    assert settings.read(schedule.MOVED) is None  # 오류를 '옮길 것 없음'으로 보지 않아 표식이 없다


def test_move_old_tasks_really_missing_is_done(folder, monkeypatch):
    settings.write(schedule.FILE, {"a": {"windows": True, "task": "t1"}})

    def execute(args):  # 그 작업만 없고 작업 스케줄러는 답한다
        return (1, "", "ERROR: The system cannot find the file specified.") if "/TN" in args else (0, "", "")
    monkeypatch.setattr(wintask, "execute", execute)
    assert schedule.move_old_tasks() == []
    assert settings.read(schedule.MOVED) == {"done": True}


def test_delete_with_known_skips_query(monkeypatch):
    calls = []
    monkeypatch.setattr(wintask, "execute", lambda args: calls.append(args[1]) or (0, "", ""))
    wintask.delete("t", wintask.OLD_FOLDER, known=True)
    assert calls == ["/Delete"]


# ---------- 5D2 schtasks가 도는 동안 설정 잠금을 쥐지 않는다 ----------

def test_slow_register_does_not_block_settings_save(folder, monkeypatch):
    started = threading.Event()

    def slow(name, item, taken):
        started.set()
        time.sleep(1.5)
        return "t1"
    monkeypatch.setattr(schedule, "register", slow)
    monkeypatch.setattr(schedule, "collection", lambda name: None)
    item = {"windows": True, "inapp": False, "every": "daily", "time": "09:00"}
    monkeypatch.setattr(schedule, "checked", lambda i: dict(i))
    worker = threading.Thread(target=lambda: schedule.save("n1", item))
    worker.start()
    assert started.wait(5)
    t0 = time.time()
    settings.save({"theme": "dark"})
    waited = time.time() - t0
    worker.join()
    assert waited < 0.8
    assert schedule.schedules()["n1"]["task"] == "t1"


def test_save_refuses_when_same_schedule_changed_meanwhile(folder, monkeypatch):
    settings.write(schedule.FILE, {"n1": {"windows": False, "inapp": True}})
    monkeypatch.setattr(schedule, "collection", lambda name: None)
    monkeypatch.setattr(schedule, "checked", lambda i: dict(i))

    def register(name, item, taken):
        settings.write(schedule.FILE, {"n1": {"windows": False, "inapp": False}})  # 등록하는 사이 다른 곳이 바꿨다
        return "t1"
    monkeypatch.setattr(schedule, "register", register)
    with pytest.raises(ValueError, match="바뀌었"):
        schedule.save("n1", {"windows": True, "inapp": False})
    assert schedule.schedules()["n1"] == {"windows": False, "inapp": False}


# ---------- 5D3 설치가 끝날 때 옮기기 ----------

def test_installer_runs_move_tasks_and_readme_says_so():
    iss = (ROOT / "installer" / "DataLoupe.iss").read_text(encoding="utf-8-sig")
    line = next(l for l in iss.splitlines() if "--move-tasks" in l)
    assert "DataLoupe.exe" in line and "runhidden" in line and "waituntilterminated" in line and "postinstall" not in line and "skipifsilent" not in line
    assert "--move-tasks" in (ROOT / "desktop.py").read_text(encoding="utf-8")
    assert "설치할 때" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_desktop_move_tasks_branch_comes_before_server_imports():
    text = (ROOT / "desktop.py").read_text(encoding="utf-8")
    assert text.index('["--move-tasks"]') < text.index("import webview") and text.index('["--move-tasks"]') < text.index("from werkzeug")
    assert '["--move-tasks"]):' in text  # 이전 폴더 옮기기 실패 안내 창은 이때도 띄우지 않는다


# ---------- 5D4 ----------

class Msg:
    def __init__(self, o):
        self.o = o

    def offset(self):
        return self.o

    def partition(self):
        return 0

    def value(self):
        return b"v"

    def key(self):
        return b"k"


class Prod:
    def __init__(self, plan):
        self.plan, self.cbs, self.n, self.attempt = plan, [], 0, {}

    def produce(self, topic, body, key, on_delivery):
        import json
        self.cbs.append((json.loads(body)["id"], on_delivery))

    def poll(self, t=0):
        self.flush()

    def flush(self, t=0):
        cbs, self.cbs = self.cbs, []
        for idx, cb in cbs:
            a = self.attempt.get(idx, 0)
            self.attempt[idx] = a + 1
            errs = self.plan.get(idx, [])
            e = errs[a] if a < len(errs) else None
            cb(e, Msg(self.n) if e is None else None)
            self.n += 1


def sink(plan, key="id"):
    k = lk.Sink.__new__(lk.Sink)
    k.item, k.key, k.password, k.last, k.placed = {"topic": "t"}, key, "", "", {}
    k.producer, k.fresh = Prod(plan), False
    return k


DATA = pa.table({"id": list(range(6))})


def test_partial_counts_only_failures_before_resume_point():
    s = sink({5: ["MSG_SIZE_TOO_LARGE"], 2: ["_TRANSPORT timeout"] * 9}, key=None)
    with pytest.raises(ValueError):
        s.put(DATA)
    done, ok, failed = s.partial()
    assert done == 2 and failed == 0  # 5번 행의 영구 실패는 이어 받을 때 다시 보내져 그때 센다
    s2 = sink({5: ["MSG_SIZE_TOO_LARGE"]}, key=None)
    assert s2.put(DATA.slice(done)) == (3, 0, 1)


def test_partial_counts_failures_before_hole():
    s = sink({1: ["MSG_SIZE_TOO_LARGE"], 3: ["_TRANSPORT timeout"] * 9}, key=None)
    with pytest.raises(ValueError):
        s.put(DATA)
    assert s.partial()[0] == 3 and s.partial()[2] == 1


def test_keyed_permanent_error_in_the_middle_continues():
    s = sink({1: ["MSG_SIZE_TOO_LARGE"], 3: ["INVALID_MSG"]})
    assert s.put(DATA) == (4, 0, 2)
    assert sorted(s.ok) == [0, 2, 4, 5]


# set_cards_data가 저장값이 이겼을 때 KEPT를 돌리는 것은 test_fix_audit3_dash.test_set_cards_data_keeps_newer_saved_value가 본다.


def test_refresh_marks_kept_card_not_ok():
    text = (ROOT / "dashboard" / "dash_refresh.py").read_text(encoding="utf-8")
    assert "dash_store.KEPT" in text and "here = (" in text and "here =(" not in text


def test_ci_runs_pytest_and_docs_match_behavior():
    flow = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    assert "python -m pytest tests_pytest -q" in flow and "requirements-dev.txt" in flow
    doc = (ROOT / "docs" / "dashboard.md").read_text(encoding="utf-8")
    assert "남은 카드는 \"멈췄어요\"" in doc
