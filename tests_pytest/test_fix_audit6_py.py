"""6차 감사 묶음 6C(core·쿼리·전처리·지표) 시험. 값은 시험 안에서 지어냈다."""
import json
import os
import runpy
import sys
import time
from pathlib import Path

import polars as pl
import pytest
from lazy import ibis

from core import scratch, server, settings, source
from metrics import metric_catalog as cat
from prep import prep_app

ROOT = Path(__file__).resolve().parent.parent


def cp949(path, *rows):
    path.write_bytes(("이름,값\n" + "".join(f"{r}\n" for r in rows)).encode("cp949"))


@pytest.fixture
def opened():
    cons = []

    def make(*paths):
        con = source.open_flat_files.__wrapped__(list(paths))
        cons.append(con)
        server.state["tables"].clear()
        server.state["rows"].clear()
        return con
    yield make
    for c in cons:
        source.close(c)
    server.state["tables"].clear()
    server.state["rows"].clear()


# ---------- 6C1 예약 옮기기 표식 ----------

def test_migrate_ignores_the_task_move_mark(tmp_path):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    old.mkdir()
    (old / "settings.json").write_text("{}")
    new.mkdir()
    (new / settings.FAILED).write_text("x", encoding="utf-8")
    (new / "old_tasks_moved.json").write_text('{"done": true}')
    assert settings.migrate(new / "settings.json") is None
    assert not old.exists() and (new / "settings.json").exists()
    assert not (new / "old_tasks_moved.json").exists()


def test_task_move_mark_name_matches_schedule():
    from integrations import schedule
    assert settings.TASKS_MOVED == schedule.MOVED


# ---------- 6C2 살아 있는 잠금 ----------

def test_migrate_keeps_a_live_lock(tmp_path):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    old.mkdir()
    (old / "settings.json").write_text("{}")
    (new / "locks").mkdir(parents=True)
    lock = new / "locks" / "app.lock"
    lock.write_text(scratch.stamp(), encoding="ascii")  # 이 프로세스가 잡은 잠금
    (new / settings.FAILED).write_text("x", encoding="utf-8")
    assert settings.migrate(new / "settings.json") is None
    assert lock.exists() and old.exists()  # 지우지도 옮기지도 않았다


def test_migrate_clears_a_dead_lock(tmp_path):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    old.mkdir()
    (old / "settings.json").write_text("{}")
    (new / "locks").mkdir(parents=True)
    lock = new / "locks" / "app.lock"
    lock.write_text("", encoding="ascii")
    os.utime(lock, (time.time() - 60, time.time() - 60))  # 쓰다 멈춘 빈 잠금
    assert settings.migrate(new / "settings.json") is None
    assert not old.exists() and (new / "settings.json").exists()


# ---------- 6C3 row_count ----------

def test_row_count_of_a_virtual_table_does_not_rebuild_the_expression(monkeypatch):
    con = ibis.duckdb.connect()
    con.create_table("t", ibis.memtable(pl.DataFrame({"a": [1, 2, 3]}).to_arrow()))
    calls = []
    orig = type(con)._get_schema_using_query
    monkeypatch.setattr(type(con), "_get_schema_using_query", lambda self, q: (calls.append(q), orig(self, q))[1])
    monkeypatch.setattr(server, "get_con", lambda: con)
    monkeypatch.setattr(server, "run", lambda f: f())
    vts = {"vk": {"sql": "select * from t where a > 1"}}
    monkeypatch.setattr(server, "virtual_tables", lambda: vts)
    server.state["tables"].clear()
    server.state["rows"].clear()
    assert server.row_count(None, "vk") == 2
    calls.clear()
    assert server.row_count(None, "vk") == 2 and server.row_count(None, "vk") == 2
    assert calls == []  # 세어 둔 수를 그대로 썼다
    server.state["rows"].clear()


def test_row_count_of_a_virtual_table_follows_the_csv(tmp_path, opened, monkeypatch):
    p = tmp_path / "t0.csv"
    cp949(p, "가,1")
    con = opened(p)
    monkeypatch.setattr(server, "get_con", lambda: con)
    monkeypatch.setattr(server, "run", lambda f: f())
    monkeypatch.setattr(server, "virtual_tables", lambda: {"vk": {"sql": "select * from t0"}})
    assert server.row_count(None, "vk") == 1
    cp949(p, "가,1", "나,2", "다,3")
    assert server.row_count(None, "t0") == 3
    assert server.row_count(None, "vk") == 3


# ---------- 6C4 refresh_csv 간격 ----------

def test_refresh_csv_skips_within_a_second(tmp_path, opened, monkeypatch):
    p = tmp_path / "k.csv"
    cp949(p, "가,1")
    con = opened(p)
    monkeypatch.setattr(source, "CHECK_EVERY", 1.0)
    stats = []
    orig = source.stamp_of
    monkeypatch.setattr(source, "stamp_of", lambda path: (stats.append(path), orig(path))[1])
    source.refresh_csv(con)
    n = len(stats)
    assert n == 1
    cp949(p, "가,1", "나,2")
    assert source.refresh_csv(con) == [] and len(stats) == n  # 1초 안: stat도 하지 않는다
    monkeypatch.setattr(source, "CHECK_EVERY", 0)
    assert source.refresh_csv(con) == ["k"]


# ---------- 6C5 쓰는 중인 UTF-8 ----------

def test_utf8_csv_cut_mid_character_stays_utf8(tmp_path, opened):
    p = tmp_path / "log.csv"
    p.write_text("이름,값\n가,1\n", encoding="utf-8")
    con = opened(p)
    with open(p, "ab") as f:
        f.write("나,2\n다".encode("utf-8")[:-1])
    assert source.refresh_csv(con) == ["log"]
    assert con.csv_sources["log"][2] == p  # 변환하지 않고 원본 그대로
    with open(p, "ab") as f:
        f.write("다".encode("utf-8")[-1:] + b",3\n")
    assert source.refresh_csv(con) == ["log"]
    t = server.any_table(con, "log", None, vts={})
    assert con.execute(t).values.tolist() == [["가", 1], ["나", 2], ["다", 3]]


def test_cp949_csv_ending_in_hangul_is_still_converted(tmp_path, opened):
    p = tmp_path / "c.csv"
    p.write_bytes("이름,값\n1,가\n".encode("cp949"))
    con = opened(p)
    assert con.csv_sources["c"][2] != p
    assert con.execute(server.any_table(con, "c", None, vts={})).values.tolist() == [[1, "가"]]


# ---------- 6C6 앞 공백 있는 식 ----------

@pytest.mark.parametrize("expr", [" mean(col('a'))", "\tmean(col('a'))", "\nmean(col('a')) "])
def test_formula_reads_all_ignores_surrounding_whitespace(expr):
    assert prep_app.formula_reads_all(expr) is True


# ---------- 6C7 ----------

def two_groups_table(c, e):
    return ibis.polars.connect().create_table("t", pl.DataFrame({"grp": ["c"] * len(c) + ["e"] * len(e), "m": [float(v) for v in c + e]}))


@pytest.mark.parametrize("eid", ["mde", "power"])
def test_mde_and_power_explain_zero_spread(eid):
    slots = {"실험군": "grp", "대조군 값": "c", "실험군 값": "e", "지표 값": "m", "유의수준": 0.05, "검정력": 0.8}
    if eid == "power":
        slots.pop("검정력")
    r = cat.compute(eid, slots, {}, cat.Ctx(table=two_groups_table([1, 1], [2, 2])), None, None)
    assert r["value"] is None
    assert any("각 군 안의 값이 모두 같아 표준편차가 0이라" in n for n in r["notes"])


def run_desktop(monkeypatch, argv, migrate_result=None):
    """desktop.py를 인자만 바꿔 처음부터 돌린다(창·서버가 뜨기 전에 끝나는 분기만)."""
    from core import drivers
    from integrations import schedule
    calls = []
    monkeypatch.setattr(settings, "migrate", lambda path=None: migrate_result)
    monkeypatch.setattr(drivers, "use_installed", lambda: calls.append("drivers"))
    monkeypatch.setattr(schedule, "move_old_tasks", lambda: calls.append("move"))
    monkeypatch.setattr(schedule, "run_alone", lambda name="": calls.append(("run", name)) or 7)
    monkeypatch.setattr(sys, "argv", ["desktop.py", *argv])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(ROOT / "desktop.py"), run_name="__main__")
    return calls, e.value.code


def test_desktop_move_tasks_branch(monkeypatch):
    calls, code = run_desktop(monkeypatch, ["--move-tasks"])
    assert calls == ["drivers", "move"] and code is None


def test_desktop_move_tasks_swallows_errors_and_skips_the_message_box(monkeypatch):
    import ctypes
    from integrations import schedule
    boxes = []
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *a: boxes.append(a))

    def boom():
        raise RuntimeError("x")
    calls, code = run_desktop(monkeypatch, ["--move-tasks"], migrate_result="이유")
    monkeypatch.setattr(schedule, "move_old_tasks", boom)
    monkeypatch.setattr(sys, "argv", ["desktop.py", "--move-tasks"])
    with pytest.raises(SystemExit):
        runpy.run_path(str(ROOT / "desktop.py"), run_name="__main__")
    assert boxes == []


def test_desktop_run_branch(monkeypatch):
    calls, code = run_desktop(monkeypatch, ["--run", "구성"])
    assert calls == ["drivers", ("run", "구성")] and code == 7


def test_desktop_docstring_mentions_move_tasks():
    assert "--move-tasks" in (ROOT / "desktop.py").read_text(encoding="utf-8").split('"""')[1]


def test_analysis_doc_mentions_infinite_values_in_outliers():
    assert any("무한대" in (ROOT / "docs" / "functions" / f).read_text(encoding="utf-8") for f in ("prep.md", "analysis_tab.md"))
