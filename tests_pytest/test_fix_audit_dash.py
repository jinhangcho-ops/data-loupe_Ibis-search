"""감사 반영 묶음 D: 기준일 직전 기간, 적재 주소 비밀, 지도 카드 원격 확인, 구성 삭제 시 예약, 알림 오류 문구, offset 건너뜀 등.
값은 시험 안에서 지어낸다. APPDATA는 conftest의 임시 폴더."""
import pytest

from collect import collect_config
from dashboard import dash_refresh as refresh


def test_context_prev_follows_as_of_cut():
    ctx = refresh.context({"period": "2026-10", "as_of": "2026-10-15"})
    assert ctx["range"] == {"start": "2026-10-01", "end": "2026-10-15"}
    assert (ctx["prev"]["start"], ctx["prev"]["end"]) == ("2026-09-01", "2026-09-15")


def test_context_prev_unchanged_when_not_cut():
    ctx = refresh.context({"period": "2026-09", "as_of": "2026-10-15"})
    assert (ctx["prev"]["start"], ctx["prev"]["end"]) == ("2026-08-01", "2026-08-31")


@pytest.mark.parametrize("url", ["postgresql://h:5432/db?password=hunter2", "postgresql://h/db?sslmode=x&api_key=abc", "postgresql://h/db?TOKEN=abc"])
def test_load_url_query_secret_rejected(url):
    from collect import load_job
    cfg = collect_config.normalize({})
    cfg["load"]["table"]["connection"]["url"] = url
    with pytest.raises(ValueError, match="비밀번호"):
        load_job.check(cfg["load"])
    cfg["load"]["table"]["connection"]["url"] = "postgresql://h:5432/db?sslmode=require"
    load_job.check(cfg["load"])


def test_geomap_with_points_counts_as_copy_to_pc(monkeypatch):
    from dashboard import dash_calc
    monkeypatch.setattr(dash_calc.connection, "compiles", lambda expr: True)  # 연결이 SQL로 풀 수 있는 식이어도
    body = {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}
    geo = {"lat": "la", "lon": "lo"}
    card = {"kind": "geomap", "source": {"body": body, "table": "t"}, "view": {"geo": geo}}
    monkeypatch.setattr(dash_calc.querying, "search_key", lambda *a: ("k",))
    assert dash_calc.copies_to_pc(card, {}) is True  # 점은 limit로 잘라 받아 오는 행이다
    monkeypatch.setattr(dash_calc.querying, "build", lambda *a, **k: None)
    card["view"] = {"geo": {"regions": {}}}
    assert dash_calc.copies_to_pc(card, {}) is False


def test_deleting_collection_deletes_schedule_and_windows_task(client, monkeypatch):
    from core import settings
    from integrations import schedule
    gone = []
    monkeypatch.setattr(schedule.wintask, "delete", gone.append)
    settings.write("collections.json", {"daily": collect_config.normalize({})})
    settings.write(schedule.FILE, {"daily": {"every": "daily", "task": "ibis_daily"}})
    assert client.post("/api/collections", json={"name": "daily", "collection": None}).status_code == 200
    assert "daily" not in schedule.schedules() and gone == ["ibis_daily"]


def test_notice_text_cuts_values_out_of_error():
    from integrations import schedule
    short = schedule.notice_text('Constraint Error: Duplicate key "id: 5" violates primary key constraint')
    assert "5" not in short and "실행 기록" in short
    long = schedule.notice_text("연결이 끊겼습니다 " * 30)
    assert len(long) < 140 and long.endswith("(자세한 내용은 프로그램의 실행 기록)")
    assert schedule.notice_text("") == ""


# ---------- 수집: offset 건너뜀, {today} 고정 ----------

def api(tmp_path, url, **paging):
    import threading
    from collect import collect_job
    config = collect_config.normalize({
        "mode": "api", "request": {"url": url}, "response": {"records": "items"}, "paging": {"max_pages": 5, **paging},
        "balance": {"interval": 0, "retries": 0}, "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")}})
    path = collect_job.output_path(config)
    return config, path, collect_job.parts(path), {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}, threading.Event()


def serve(monkeypatch, rows_of, urls):
    import json as js
    from collect import collect_http
    from collect.collect_http import Reply

    def fetch(request, timeout, secrets=()):
        urls.append(request["url"])
        return Reply(200, [["Content-Type", "application/json"]], js.dumps(rows_of(request["url"])).encode(), request["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)


@pytest.mark.parametrize("total", [None, 250])
def test_offset_short_page_halts(tmp_path, monkeypatch, folder, total):
    from collect import collect_job
    from collect.collect_http import Halt
    config, path, found, record, halt = api(tmp_path, "https://api.example.com/items?offset={offset}", kind="offset", step=100, start=0, **({"total_path": "total"} if total else {}))
    # 서버는 step이 100이어도 50행만 준다(0~49, 100~149 …)
    serve(monkeypatch, lambda url: {"items": [{"a": n} for n in range(50)], **({"total": total} if total else {})}, [])
    with pytest.raises(Halt, match="50행만 줍니다"):
        collect_job.run_api(config, {}, path, found, record, halt)


def test_offset_full_pages_still_work(tmp_path, monkeypatch, folder):
    import pyarrow.parquet as pq
    from collect import collect_job
    config, path, found, record, halt = api(tmp_path, "https://api.example.com/items?offset={offset}", kind="offset", step=3, start=0, total_path="total")
    data = list(range(7))
    serve(monkeypatch, lambda url: {"items": [{"a": n} for n in data[int(url.rsplit("=", 1)[1]):][:3]], "total": 7}, [])
    assert collect_job.run_api(config, {}, path, found, record, halt) is True
    assert pq.read_table(path).num_rows == 7


def test_today_fixed_at_start(tmp_path, monkeypatch, folder):
    import datetime as dt
    from collect import collect_job
    config, path, found, record, halt = api(tmp_path, "https://api.example.com/{today}/items?offset={offset}", kind="offset", step=1, max_pages=3)
    urls, clock = [], iter(dt.datetime(2026, 10, d) for d in range(10, 30))

    class Fake(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return next(clock)
    monkeypatch.setattr(collect_job.dt, "datetime", Fake)
    serve(monkeypatch, lambda url: {"items": [{"a": 1}]}, urls)
    collect_job.run_api(config, {}, path, found, record, halt)
    assert len({u.split("/")[3] for u in urls}) == 1 and len(urls) == 3


def test_file_resume_needs_etag_or_modified(tmp_path, monkeypatch, folder):
    import threading
    from collect import collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 1}, "balance": {"interval": 0, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    found["part"].write_bytes(b"x" * 10)  # 앞 5바이트는 이미 받은 것으로 친다
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [[0, 5]], "rows": 0, "bytes": 5, "size": 10, "etag": None, "modified": None}
    starts = []

    class Body:
        def __init__(self, start, end):
            self.status, self.headers, self.data = 206, {"Content-Range": f"bytes {start}-{end}/10"}, b"y" * (end - start + 1)
            self.headers = type("H", (dict,), {"get": dict.get, "items": dict.items})(self.headers)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            out, self.data = self.data[:n], self.data[n:] if n else b""
            return out

    def open_url(method, url, headers, body, timeout, hide=()):
        start, end = (int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))
        starts.append(start)
        return Body(start, end), url
    monkeypatch.setattr(collect_http, "open_url", open_url)
    try:
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    except Exception:
        pass  # 결과 파일 만들기까지는 보지 않는다
    assert 0 in starts[1:]  # 검증값이 없으면 처음부터 다시 받는다(5부터 이어 받지 않는다)


# ---------- Kafka 부분 실패 ----------

def test_kafka_partial_delivery_failure_raises():
    import pyarrow as pa
    from collect import load_kafka

    class Producer:
        def produce(self, topic, body, key, on_delivery):
            on_delivery("전달 실패" if len(body) and b'"n": 1' in body else None, None)

        def poll(self, t):
            pass

        def flush(self, t):
            return 0
    sink = load_kafka.Sink.__new__(load_kafka.Sink)
    sink.item, sink.key, sink.password, sink.last, sink.producer = {"topic": "t"}, "", "", "", Producer()
    with pytest.raises(ValueError, match="1행을 전달하지 못했습니다"):
        sink.put(pa.table({"n": [0, 1, 2]}))


def test_load_with_failed_rows_is_reported(tmp_path, monkeypatch, folder):
    import threading
    import pyarrow as pa
    import pyarrow.parquet as pq
    import time
    from collect import collect_job, collect_store, load_job

    class Target:
        ready = staticmethod(lambda config: None)
        sign = staticmethod(lambda config: {"topic": "t"})
        where = staticmethod(lambda config: "fake")

        class Sink:
            fresh, placed, last = False, {}, ""

            def __init__(self, *a):
                pass

            def put(self, unit):
                return unit.num_rows - 1, 0, 1  # 한 행은 넣지 못했다

            def done(self):
                pass

            def close(self):
                pass
    monkeypatch.setitem(load_job.TARGETS, "kafka", Target)
    config = collect_config.normalize({"mode": "api", "output": {"format": "parquet", "path": str(tmp_path / "o.parquet")},
                                       "load": {"target": "kafka", "kafka": {"destination": "d"}, "batch": 100, "retries": 0}})
    path = collect_job.output_path(config)
    pq.write_table(pa.table({"n": [1, 2, 3]}), path)
    collect_store.update(started=time.time())
    collect_job.work(config, {}, path, collect_job.parts(path), None, threading.Event(), "")
    state = collect_job.snapshot()
    assert state["phase"] == "끝" and "실패 1행" in state["message"] and state["load"]["failures"] == 1


def test_wintask_refuses_percent_in_name():
    from integrations import wintask
    with pytest.raises(ValueError, match="%"):
        wintask.command("매출%TEMP%")
    assert wintask.command("매출")[1].endswith('"매출"') or "매출" in wintask.command("매출")[1]


def test_summary_new_group_counts_as_zero_for_sum():
    from dashboard import dash_calc, summary_text
    pairs = dash_calc.group_pairs({"새 지점": 20, "A": 110}, {"A": 100}, "sum")
    assert pairs == {"새 지점": (20, 0), "A": (110, 100)}
    assert summary_text.share_sentence(130, 100, pairs, "number") == "늘어난 몫의 67%가 새 지점이에요."
    assert dash_calc.group_pairs({"새 지점": 20}, {}, "avg") is None  # 평균 등은 기여 분해를 하지 않는다


def test_filelock_does_not_steal_live_lock_judged_stale_too_early(folder, monkeypatch):
    from core import filelock
    path = filelock.lock_path("race")
    assert filelock.hold(path)  # 살아 있는 프로세스(이 프로세스)가 잡은 잠금
    real, calls = filelock.left_behind, []

    def first_wrong(p):  # 판단한 뒤 그사이 다른 프로세스가 새로 잡은 것을 흉내: 처음 한 번만 "끝났다"고 답한다
        calls.append(p)
        return True if len(calls) == 1 else real(p)
    monkeypatch.setattr(filelock, "left_behind", first_wrong)
    assert filelock.hold(path) is False  # 살아 있는 잠금을 빼앗지 못한다
    assert path.is_file()


def test_set_cards_data_writes_store_once(folder, monkeypatch):
    from core import dash_store as store
    card = lambda t: {"kind": "chart", "title": t, "source": {"key": "k", "label": "x", "table": "t", "body": {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}},
                      "data": {"values": {"labels": ["old"], "series": []}}}
    d = store.save_dashboard({"name": "묶음", "cards": [card("가"), card("나"), card("다")]})
    ids = [c["id"] for c in d["cards"]]
    writes = []
    real = store.write
    monkeypatch.setattr(store, "write", lambda x: (writes.append(1), real(x))[1])
    got = store.set_cards_data(d["id"], [(ids[0], {"note": "a"}, None), (ids[1], {"note": "b"}, lambda c: False), ("없음", {"note": "c"}, None), (ids[2], {"note": "d"}, None)])
    assert got == [True, None, False, True] and len(writes) == 1
    notes = [c["data"].get("note") for c in store.get_dashboard(d["id"])["cards"]]
    assert notes == ["a", None, "d"]


def test_analysis_card_geo_reference_tables_are_scan_checked(monkeypatch):
    import types
    from lazy import ibis
    from core import held, scan_size, server
    from geo import prep_geo
    monkeypatch.setattr(refresh, "run", lambda work: work())
    monkeypatch.setitem(server.state, "con", types.SimpleNamespace(name="postgres"))
    monkeypatch.setitem(server.state, "opened", {})
    monkeypatch.setattr(held, "remote", lambda con: True)
    monkeypatch.setattr(prep_geo, "check", lambda geo: geo)
    monkeypatch.setattr(prep_geo, "reference_exprs", lambda geo, con: [("k1", ibis.memtable({"a": [1]}))])  # expr_sql은 진짜(가짜는 (SQL, 방언) 튜플 결함을 가렸다)
    asked = []
    monkeypatch.setattr(scan_size, "limit_check", lambda con, sql, opened=None, dialect=None: asked.append(sql) or {"over": True, "text": "지점 표가 너무 커요"}, raising=False)
    card = {"kind": "analysis", "title": "t", "source": {"recipe": {"base": "t", "geo": {"x": 1}}}, "view": {}}
    assert refresh.scan_decision(card, {}) == "지점 표가 너무 커요" and len(asked) == 1 and isinstance(asked[0], str)
    card["source"]["recipe"] = {"base": "t"}  # 좌표 단계가 없으면 묻지 않는다
    assert refresh.scan_decision(card, {}) == ""
