"""감사 22-2: 받아 둔 사본의 DuckDB(held)는 연결 스레드에서만 써야 하는데, querying.runnable·prep_app.working_copy가 held.table(DESCRIBE 질의)을
요청 스레드에서 불러 연결 스레드의 다른 사본 받기(insert)와 겹치면 오류가 나거나 멈추던 것. held.table을 부른 스레드가 run이 일을 돌린 스레드인지 본다."""
import threading

import pyarrow as pa
import pytest
from lazy import ibis

from core import held, querying, server
from prep import prep_app


@pytest.fixture
def threads(monkeypatch):
    """run은 일을 새 스레드에서 돌리고 그 스레드를, held.table은 부른 스레드를 적는다(DB 없이 가짜 사본)."""
    got = {"run": [], "table": []}

    def run(work, drop=lambda value: None):
        out = {}
        t = threading.Thread(target=lambda: out.update(value=work()))
        t.start()
        t.join()
        got["run"].append(t.ident)
        return out["value"]

    def table(name):
        got["table"].append(threading.get_ident())
        return ibis.memtable(pa.table({"a": [1], held.ROW: [0]}))
    monkeypatch.setattr(server, "run", run)
    monkeypatch.setattr(querying, "run", run)
    monkeypatch.setattr(held, "ensure", lambda *a, **k: ("r0", 1))
    monkeypatch.setattr(held, "table", table)
    return got


def test_runnable_reads_copy_in_run(threads, monkeypatch):
    for name in ("build", "filtered"):
        monkeypatch.setattr(querying, name, lambda *a, **k: ibis.memtable(pa.table({"a": [1]})))
    monkeypatch.setattr(querying.source, "compiles", lambda expr: False)  # 연결이 못 하는 계산: PC 사본으로
    monkeypatch.setattr(querying, "search_key", lambda body, keep: "k")
    monkeypatch.setattr(querying, "arrange", lambda body, t, row, select: t)
    querying.runnable({})
    assert threads["table"] and threads["table"] == threads["run"]


def test_working_copy_reads_copy_in_run(threads, monkeypatch):
    monkeypatch.setattr(prep_app, "source_table", lambda recipe: None)
    monkeypatch.setattr(prep_app, "cached_key", lambda recipe: "k")
    monkeypatch.setattr(prep_app, "unused_columns", lambda recipe: [])
    assert prep_app.working_copy({}).columns == ("a",)
    assert threads["table"] and threads["table"] == threads["run"]


def test_fresh_copy_drops_copies_in_run(threads, monkeypatch):
    """산출물 직전에 낡은 사본을 지우는 DROP도 연결 스레드에서."""
    forgot = []
    monkeypatch.setattr(held, "forget", lambda key: forgot.append(threading.get_ident()))
    monkeypatch.setattr(prep_app, "local_mode", lambda recipe: True)
    monkeypatch.setattr(prep_app, "cached_key", lambda recipe: "k1")
    monkeypatch.setattr(prep_app, "copy_key", lambda recipe: "c")
    monkeypatch.setattr(prep_app, "usable_keys", lambda key: ["k2"])
    prep_app.fresh_copy({})
    assert len(forgot) == 2 and set(forgot) == set(threads["run"])
