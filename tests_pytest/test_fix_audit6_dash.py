"""6차 6D(예약·설치): 고치기 전에는 실패하던 것들을 재현한다. 작업 스케줄러는 모두 가짜다."""
import threading
import time
from pathlib import Path

import pytest

from core import dash_store, settings
from integrations import schedule, wintask

ROOT = Path(__file__).resolve().parent.parent
ITEM = {"windows": True, "inapp": False, "every": "daily", "time": "09:00"}


@pytest.fixture
def fake(folder, monkeypatch):
    """가짜 작업 스케줄러: 등록된 작업 {작업 이름: 구성 이름}과 지운 목록."""
    state = {"tasks": {}, "deleted": []}

    def reg(task, name, item, start):
        time.sleep(0.05)
        state["tasks"][task] = name

    def delete(task, folder=wintask.FOLDER, known=False):
        if folder == wintask.FOLDER:
            state["tasks"].pop(task, None)
        state["deleted"].append((task, folder))
    monkeypatch.setattr(schedule, "collection", lambda n: None)
    monkeypatch.setattr(schedule, "checked", lambda i: {**schedule.DEFAULT, **i})
    monkeypatch.setattr(wintask, "register", reg)
    monkeypatch.setattr(wintask, "delete", delete)
    return state


# ---------- 6D1 ----------

def test_move_old_tasks_does_nothing_when_move_failed(folder, monkeypatch):
    (folder / settings.FAILED).write_text("x", encoding="utf-8")
    settings.write(schedule.FILE, {})
    monkeypatch.setattr(wintask, "execute", lambda a: (0, "", ""))
    assert schedule.move_old_tasks() == []
    assert not (folder / schedule.MOVED).exists()


# ---------- 6D2 ----------

def test_similar_names_get_different_tasks(fake):
    ts = [threading.Thread(target=schedule.save, args=(n, dict(ITEM))) for n in ("a?", "a*")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    found = {k: v["task"] for k, v in schedule.schedules().items()}
    assert len(found) == 2 and len(set(found.values())) == 2
    assert set(fake["tasks"]) == set(found.values())


# ---------- 6D3 ----------

def test_conflict_undoes_new_task(fake, monkeypatch):
    real = wintask.register

    def reg(task, name, item, start):
        real(task, name, item, start)
        settings.write(schedule.FILE, {"x": {**schedule.DEFAULT, "time": "10:00"}})  # 그사이 다른 곳이 바꿈
    monkeypatch.setattr(wintask, "register", reg)
    with pytest.raises(ValueError):
        schedule.save("x", dict(ITEM))
    assert fake["tasks"] == {}


def test_conflict_restores_existing_task(fake, monkeypatch):
    settings.write(schedule.FILE, {"x": {**schedule.DEFAULT, "windows": True, "task": "t1"}})
    calls = []
    real = wintask.register

    def reg(task, name, item, start):
        calls.append(item.get("time"))
        real(task, name, item, start)
        if len(calls) == 1:
            settings.write(schedule.FILE, {"x": {**schedule.DEFAULT, "windows": True, "task": "t1", "time": "10:00"}})
    monkeypatch.setattr(wintask, "register", reg)
    with pytest.raises(ValueError):
        schedule.save("x", dict(ITEM))
    assert calls == ["09:00", "10:00"]  # 파일의 지금 값으로 다시 등록


# ---------- 6D4 ----------

def test_move_old_tasks_removes_new_task_when_gone(fake, monkeypatch):
    settings.write(schedule.FILE, {"a": {**schedule.DEFAULT, "windows": True, "task": "t1"}})
    monkeypatch.setattr(wintask, "execute", lambda a: (0, "", ""))
    monkeypatch.setattr(wintask, "query", lambda task, folder=wintask.FOLDER, strict=False: {"registered": True})
    real = wintask.register

    def reg(task, name, item, start):
        real(task, name, item, start)
        settings.write(schedule.FILE, {"a": {**schedule.DEFAULT, "windows": False}})  # 그사이 windows를 껐다
    monkeypatch.setattr(wintask, "register", reg)
    schedule.move_old_tasks()
    assert fake["tasks"] == {}


# ---------- 6D5 ----------

def test_installer_move_tasks_line():
    line = next(l for l in (ROOT / "installer" / "DataLoupe.iss").read_text(encoding="utf-8").splitlines() if "--move-tasks" in l)
    assert "runasoriginaluser" in line and "Check:" in line


# ---------- 6D6 ----------

def test_move_old_tasks_stops_at_first_error(folder, monkeypatch):
    settings.write(schedule.FILE, {n: {**schedule.DEFAULT, "windows": True, "task": "t" + n} for n in "abc"})
    seen = []

    def query(task, folder=wintask.FOLDER, strict=False):
        seen.append(task)
        raise RuntimeError("scheduler down")
    monkeypatch.setattr(wintask, "query", query)
    assert schedule.move_old_tasks() == []
    assert len(seen) == 1 and not (folder / schedule.MOVED).exists()


def test_strict_query_access_denied_is_error(monkeypatch):
    full = wintask.full("t")

    def execute(args):
        if "/TN" in args:
            return 1, "", "ERROR: Access is denied."
        return 0, f'"{full}","N/A","Ready"\n', ""  # 목록에는 있다
    monkeypatch.setattr(wintask, "execute", execute)
    with pytest.raises(Exception):
        wintask.query("t", strict=True)
    monkeypatch.setattr(wintask, "execute", lambda a: (1, "", "not found") if "/TN" in a else (0, '"\\other","N/A","Ready"\n', ""))
    assert wintask.query("t", strict=True)["registered"] is False


def test_kept_not_when_both_data_none(folder):
    text = (ROOT / "core" / "dash_store.py").read_text(encoding="utf-8")
    assert "is not None and" in text.split("kept = ", 1)[1].split("\n", 1)[0]
