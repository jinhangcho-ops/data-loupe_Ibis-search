"""3차 감사 3D(대시보드·수집·연동·사본): 고치기 전에는 실패하던 것들을 하나씩 재현한다. 연결·알림·Kafka는 모두 가짜다."""
import contextlib
import datetime as dt
import threading
import time
import types

import pyarrow as pa
import pytest

from core import dash_store as store
from core import querying
from core import server
from dashboard import dash_calc
from dashboard import dash_refresh as refresh

A = {"kind": "folder", "path": "C:/dbA"}
B = {"kind": "folder", "path": "C:/dbB"}


def chart(title, key):
    return {"kind": "chart", "title": title, "source": {"key": key, "label": "x", "table": "t", "body": {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}},
            "data": {"values": {"labels": ["old"], "series": []}}}


@pytest.fixture
def quiet(folder, monkeypatch):
    monkeypatch.setitem(server.state, "opened", A)
    monkeypatch.setitem(server.state, "passwords", [])
    refresh.JOB.update(running=False, total=0, done=0, current="", results=[])
    monkeypatch.setattr(refresh, "scan_decision", lambda c, x: "")
    monkeypatch.setattr(dash_calc, "shared_context", lambda m, c: None)
    monkeypatch.setattr(refresh, "context", lambda d: None)
    yield
    refresh.wait(10)


# ---------- 3D1 카드 사이에 다른 DB로 연결하면 남은 카드는 건너뛴다 ----------

def test_connection_change_between_cards_skips_the_rest(quiet, monkeypatch):
    key = store.current_source()["key"]
    d = store.save_dashboard({"name": "D", "cards": [chart("c1", key), chart("c2", key), chart("c3", key)], "refresh": {"every": "1시간"}})
    turns = []

    @contextlib.contextmanager
    def using(fresh=False, renewed=None):
        turns.append(1)
        if len(turns) == 2:
            server.state["opened"] = B  # 카드 사이에 사용자가 다른 DB에 연결한다
        yield
    monkeypatch.setattr(querying, "using", using)
    monkeypatch.setattr(dash_calc, "compute", lambda c, now, ctx=None: {"values": {"labels": [server.state["opened"]["path"]], "series": []}, "computed_at": now})
    refresh.start()
    refresh.wait()
    by = {r["title"]: r for r in refresh.status()["results"]}
    assert by["c1"]["state"] == "ok" and by["c2"]["state"] == "skipped" and "연결이 바뀌어" in by["c2"]["message"] and by["c3"]["state"] == "skipped"
    saved = {c["title"]: c for c in store.get_dashboard(d["id"])["cards"]}
    assert saved["c1"]["data"]["values"]["labels"] == ["C:/dbA"]
    assert saved["c2"]["data"]["values"]["labels"] == ["old"] and saved["c3"]["data"]["values"]["labels"] == ["old"]  # B로 구한 값이 A 열쇠로 저장되지 않는다


# ---------- 3D2 카드를 구한 시각을 찍고, 저장된 값이 더 늦으면 쓰지 않는다 ----------

def test_cards_are_stamped_when_computed(quiet, monkeypatch):
    key = store.current_source()["key"]
    store.save_dashboard({"name": "D", "cards": [chart("c1", key), chart("c2", key)], "refresh": {"every": "1시간"}})
    stamps = {}

    def compute(c, now, ctx=None):
        stamps[c["title"]] = dt.datetime.fromisoformat(now)
        if c["title"] == "c1":
            time.sleep(1.2)
        return {"values": {"labels": ["new"], "series": []}, "computed_at": now}
    monkeypatch.setattr(dash_calc, "compute", compute)
    monkeypatch.setattr(querying, "using", lambda **k: contextlib.nullcontext())
    refresh.start()
    refresh.wait()
    assert (stamps["c2"] - stamps["c1"]).total_seconds() >= 1  # 작업 시작 시각이 아니라 카드마다 구한 시각


def test_set_cards_data_keeps_newer_saved_value(folder):
    d = store.save_dashboard({"name": "D", "cards": [chart("c", "folder:c:/dba")]})
    cid = d["cards"][0]["id"]
    h = store.def_hash(dash_calc.definition(store.check_card(d["cards"][0])))
    mine = lambda at, label: {"x": "a", "values": {"labels": [label], "series": []}, "computed_at": at, "def_hash": h}
    assert store.set_cards_data(d["id"], [(cid, mine("2026-10-24T10:30:00", "UI"), None)]) == [True]
    assert store.set_cards_data(d["id"], [(cid, mine("2026-10-24T10:00:00", "late job"), None)]) == [True]  # 늦게 도착한 옛 값
    assert store.get_dashboard(d["id"])["cards"][0]["data"]["values"]["labels"] == ["UI"]
    store.set_cards_data(d["id"], [(cid, mine("2026-10-24T11:00:00", "newer"), None)])
    assert store.get_dashboard(d["id"])["cards"][0]["data"]["values"]["labels"] == ["newer"]


# ---------- 3D3 예약 저장은 읽고-고치고-쓰기를 묶는다 ----------

def test_concurrent_schedule_saves_do_not_lose_one(folder, monkeypatch):
    from integrations import schedule as s
    monkeypatch.setattr(s, "collection", lambda n: {})
    monkeypatch.setattr(s, "register", lambda name, item, taken: (time.sleep(0.8), "T_" + name)[1])
    first = threading.Thread(target=lambda: s.save("A", {"windows": True, "inapp": False}))
    first.start()
    time.sleep(0.2)
    s.save("B", {"windows": False, "inapp": True})
    first.join()
    assert sorted(s.schedules()) == ["A", "B"]


# ---------- 3D4 이어 받은 성공 알림에 오류 줄이 붙지 않는다 ----------

def test_success_notice_has_no_error_line(monkeypatch):
    from integrations import notify, schedule as s
    sent = []
    monkeypatch.setattr(notify, "send", lambda n, p: sent.append(p))
    monkeypatch.setattr(s, "schedules", lambda: {"c": {"notify": "x", "notify_on": "always"}})
    assert s.tell("c", "끝", 10, 3.0, "이어 받습니다.") == ""
    assert "오류" not in sent[-1]["text"]
    s.tell("c", "실패", None, 3.0, "연결이 거부되었습니다")
    assert "오류:" in sent[-1]["text"]


# ---------- 3D7 알림이 어떤 예외를 내도 실행은 끝난 것으로 기록한다 ----------

def test_notify_failure_of_any_kind_still_ends_run(folder, monkeypatch):
    import http.client
    from integrations import notify, schedule as s
    monkeypatch.setattr(s, "schedules", lambda: {"c": {"notify": "x", "notify_on": "fail"}})

    def boom(n, p):
        raise http.client.IncompleteRead(b"")
    monkeypatch.setattr(notify, "send", boom)
    rid = s.add_run("c", "windows")
    s.finish(rid, "c", "실패", None, 1.0, "x")
    run = next(r for r in s.read_runs() if r["id"] == rid)
    assert run["result"] == "실패" and "알림을 보내지 못했습니다" in run["message"]


def test_end_run_happens_even_if_tell_raises_unexpectedly(folder, monkeypatch):
    from integrations import schedule as s
    monkeypatch.setattr(s, "tell", lambda *a: (_ for _ in ()).throw(KeyError("x")))
    rid = s.add_run("c", "windows")
    with pytest.raises(KeyError):
        s.finish(rid, "c", "끝", 1, 1.0, "")
    assert next(r for r in s.read_runs() if r["id"] == rid)["result"] == "끝"


# ---------- 3D5 키가 있는 토픽의 영구 오류 행은 재시도를 쓰지 않는다 ----------

def keyed_sink(monkeypatch, producer):
    from test_fix_audit2_dash import _sink
    sink = _sink(monkeypatch, producer, key="id")
    producer.poll = lambda t: producer.flush(0) if t == 0 else None  # 전달 보고가 보내는 중에 온다고 본다
    return sink


def test_keyed_topic_continues_past_permanent_rows(monkeypatch):
    from test_fix_audit2_dash import _Producer
    p = _Producer(always={1, 3})
    sink = keyed_sink(monkeypatch, p)
    assert sink.put(pa.table({"id": [0, 1, 2, 3, 4, 5]})) == (4, 0, 2)  # 예외 없이: 4행 전달, 영구 실패 2행


def test_keyed_topic_still_stops_on_transient_error(monkeypatch):
    from test_fix_audit2_dash import _Producer
    p = _Producer(once={1}, always={3})
    sink = keyed_sink(monkeypatch, p)
    data = pa.table({"id": [0, 1, 2, 3]})
    with pytest.raises(ValueError):
        sink.put(data)
    assert 2 not in p.log  # 일시 오류 뒤 행은 순서를 지키려고 보내지 않는다


# ---------- 3D6 멈출 때 영구 실패 수도 기록에 남긴다 ----------

def test_partial_includes_failed_and_record_keeps_failures(tmp_path, folder, monkeypatch):
    from collect import load_job
    from test_fix_collect import load_config, write_result

    class FakeSink:
        placed = {}
        last = ""
        fresh = False

        def __init__(self, config, password, schema, resumed, load_id=None):
            pass

        def put(self, unit):
            raise ValueError("끝내 못 넣음")

        def partial(self):
            return 2, 1, 1  # 앞에서 2행 처리, 1행 전달, 영구 실패 1행

        def done(self):
            pass

        def close(self):
            pass
    Target = types.SimpleNamespace(Sink=FakeSink, sign=lambda config: {}, where=lambda config: "x", ready=lambda config: None)
    monkeypatch.setitem(load_job.TARGETS, "kafka", Target)
    path = tmp_path / "out.parquet"
    write_result(path, 5)
    with pytest.raises(ValueError):
        load_job.run(load_config(path), path, "", threading.Event(), lambda **k: None)
    record = load_job.read_record(path)
    assert record["rows"] == 2 and record["inserted"] == 1 and record["failures"] == 1


def test_kafka_partial_returns_failed(monkeypatch):
    from test_fix_audit2_dash import _Producer, _sink
    p = _Producer(always={0}, once={2})
    sink = _sink(monkeypatch, p)
    with pytest.raises(ValueError):
        sink.put(pa.table({"id": [0, 1, 2]}))
    assert sink.partial() == (2, 1, 1)


# ---------- 3D8 새로고침은 쓸 사본만 새로 받는다 ----------

def test_using_fresh_keeps_other_copies_and_renews_only_used_keys(monkeypatch):
    from core import held
    monkeypatch.setitem(server.state, "con", object())
    monkeypatch.setattr(querying, "run", lambda f: f())
    assert not hasattr(held, "forget_all")
    held.store["items"].update({"other": ("r9", 1)})
    try:
        with querying.using(fresh=True):
            assert "other" in held.store["items"]  # 검색·전처리 사본은 그대로
            assert held.renews("k") is True
            assert held.renews("k") is False  # 같은 작업에서 두 번째는 방금 받은 사본을 쓴다
        assert held.renews("k") is False  # 작업이 끝나면 새로 받지 않는다
    finally:
        held.store["items"].pop("other", None)


def test_ensure_renews_used_copy_once_and_leaves_others(tmp_path):
    from core import held
    from lazy import ibis
    path = tmp_path / "a.csv"
    path.write_text("n\n1\n2\n", encoding="utf-8")
    expr = ibis.duckdb.connect().read_csv(str(path), table_name="src")
    stop = threading.Event()
    try:
        assert held.ensure("used", expr, stop)[1] == 2 and held.ensure("other", expr, stop)[1] == 2
        path.write_text("n\n1\n2\n3\n", encoding="utf-8")
        job = set()  # 새로고침 하나: 카드마다 차례를 다시 잡아도 같은 집합
        with held.renewing(job):
            assert not held.has("used") and held.has("other") is False  # 이 작업이 아직 새로 받지 않은 사본은 용량 확인 대상
            assert held.ensure("used", expr, stop)[1] == 3  # 이 작업이 쓰는 사본은 새로
            assert held.has("used")
        path.write_text("n\n1\n", encoding="utf-8")
        assert held.ensure("used", expr, stop)[1] == 3  # 차례를 놓은 사이의 화면 요청은 새로 받지 않는다
        with held.renewing(job):
            assert held.ensure("used", expr, stop)[1] == 3  # 같은 작업의 다음 카드는 방금 받은 것
        assert held.ensure("other", expr, stop)[1] == 2  # 쓰지 않은 사본은 지워지지 않았다
    finally:
        held.clear()


# ---------- 3D9 큰 저장소는 중간 저장 간격을 늘린다 ----------

def test_flush_gap_grows_with_store_size(folder, monkeypatch):
    small = refresh.flush_gap()
    assert small == refresh.FLUSH_SECONDS
    monkeypatch.setattr(store, "path_of", lambda: type("P", (), {"stat": lambda self: type("S", (), {"st_size": 32 * 1024 * 1024})()})())
    assert refresh.flush_gap() >= refresh.FLUSH_SECONDS * 4


# ---------- 3D10 기준일이 오늘 뒤면 거부 ----------

def test_future_as_of_is_rejected(folder):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="오늘"):
        store.save_dashboard({"name": "D", "cards": [], "as_of": tomorrow})
    assert store.save_dashboard({"name": "D", "cards": [], "as_of": dt.date.today().isoformat()})["as_of"] == dt.date.today().isoformat()


# ---------- 3D11 inapp과 windows는 같이 고르지 못한다 ----------

def test_inapp_and_windows_together_rejected(monkeypatch):
    from integrations import schedule as s
    with pytest.raises(ValueError, match="같이"):
        s.checked({"inapp": True, "windows": True})
    assert s.checked({"inapp": False, "windows": True})["windows"] is True


# ---------- 3D12 Sink.put의 rejected는 지웠다 ----------

def test_sink_put_has_no_rejected_set():
    import inspect
    from collect import load_kafka
    assert "rejected" not in inspect.getsource(load_kafka.Sink.put)


# ---------- 3D13 권한 때문에 확인 못 한 잠금은 빼앗지 않는다 ----------

def test_left_behind_unverifiable_is_not_stale(tmp_path, monkeypatch):
    import os
    import psutil
    from core import filelock, scratch
    path = tmp_path / "x.lock"
    path.write_text("123 456.0", encoding="ascii")
    old = time.time() - 3600
    os.utime(path, (old, old))
    monkeypatch.setattr(scratch, "ended", lambda pid, started: (_ for _ in ()).throw(psutil.AccessDenied(123)))
    assert filelock.left_behind(path) is False  # 살아 있는지 모르므로 빼앗지 않는다
    monkeypatch.undo()
    path.write_text("", encoding="ascii")
    os.utime(path, (old, old))
    assert filelock.left_behind(path) is True  # 빈 파일은 mtime 규칙
    path.write_text("abc def", encoding="ascii")
    os.utime(path, (old, old))
    assert filelock.left_behind(path) is True  # 깨진 파일
