"""2차 감사 반영 2D: 대시보드·수집·연동. 진짜 함수로 시험하고(가짜가 결함을 가리지 않게), 값은 시험 안에서 지어낸다."""
import pytest

from test_dash_calc import db  # noqa: F401  진짜 DuckDB 파일에 연결한 시험 클라이언트


def test_geo_scan_gets_sql_text_not_tuple(db, monkeypatch):
    """2D1: 진짜 server.expr_sql을 거쳐 limit_check에 SQL 글이 간다(예전엔 (SQL, 방언) 튜플이 가서 판정 불능)."""
    from core import held, scan_size, server, source
    from dashboard import dash_refresh
    from geo import prep_geo
    con = server.state["con"]
    monkeypatch.setattr(held, "remote", lambda c: True)
    monkeypatch.setattr(prep_geo, "check", lambda geo: geo)
    monkeypatch.setattr(prep_geo, "reference_exprs", lambda geo, c: [("k1", source.get_table(c, "orders"))])
    seen = []
    real = scan_size.limit_check
    monkeypatch.setattr(scan_size, "limit_check", lambda c, sql, opened=None, dialect=None, **k: (seen.append(sql), real(c, sql, opened, dialect, **k))[1])
    monkeypatch.setattr(scan_size, "limit", lambda config: (1, "1바이트"))
    card = {"kind": "analysis", "title": "t", "source": {"recipe": {"base": "orders", "geo": {"x": 1}}}, "view": {}}
    why = dash_refresh.scan_decision(card, {})
    assert seen and all(isinstance(s, str) for s in seen)
    assert "알 수 없" not in why  # 튜플이면 '처리 용량을 알 수 없어요'로 빠졌다
    assert con is not None


def test_full_analysis_remote_copy_is_scan_checked(db, monkeypatch):
    """2D2: 원격·local_mode·full 분석 카드는 작업 사본을 받기 전에 용량 확인을 받는다."""
    from core import held, scan_size, dash_store
    from dashboard import dash_calc, dash_refresh
    monkeypatch.setattr(held, "remote", lambda con: True)
    asked = []
    monkeypatch.setattr(scan_size, "limit_check", lambda con, sql, opened=None, dialect=None, **k: (asked.append(sql), {"over": True, "text": "너무 커요"})[1])
    c = {"kind": "analysis", "title": "a", "source": {"schema": "", "table": "orders", "recipe": {"base": "orders"}, "full": True,
                                                       "analysis": {"kind": "groups", "by": "store", "agg": "sum", "value": "amt"}}, "view": {}}
    checked = dash_store.check_card(c)
    assert dash_refresh.scan_decision(checked, None) == "너무 커요" and len(asked) == 1 and isinstance(asked[0], str) and "orders" in asked[0]
    assert dash_calc.read_sql(checked, None) is None
    # 표본(full 아님)이면 묻지 않는다
    asked.clear()
    c["source"]["full"] = False
    assert dash_refresh.scan_decision(dash_store.check_card(c), None) == "" and not asked


# ---------- 2D3: 받아 둔 조각 파일이 잘렸으면 이어 받지 않는다 ----------
def _resume(tmp_path, monkeypatch, on_disk):
    import threading
    from collect import collect_config, collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 1}, "balance": {"interval": 0, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    found["part"].write_bytes(on_disk)
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [[0, 5]], "rows": 0, "bytes": 5, "size": 10, "etag": '"E1"', "modified": None}
    starts = []

    class Body:
        def __init__(self, start, end):
            self.status, self.data = 206, b"y" * (end - start + 1)
            self.headers = {"Content-Range": f"bytes {start}-{end}/10", "ETag": '"E1"'}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            out, self.data = (self.data[:n], self.data[n:]) if n else (self.data, b"")
            return out
    monkeypatch.setattr(collect_http, "open_url", lambda method, url, headers, body, timeout, hide=(): (starts.append(int(dict(headers)["Range"].removeprefix("bytes=").split("-")[0])), (Body(*(int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))), url))[1])
    try:
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    except Exception:
        pass
    return starts


def test_resume_skips_truncated_part_file(folder, tmp_path, monkeypatch):
    starts = _resume(tmp_path, monkeypatch, b"AB")  # 기록은 [0,5)를 받았다는데 파일은 2바이트뿐
    assert 5 not in starts and starts.count(0) >= 2  # 처음부터 다시 받는다


def test_resume_continues_when_part_file_intact(folder, tmp_path, monkeypatch):
    starts = _resume(tmp_path, monkeypatch, b"A" * 10)
    assert 5 in starts and starts.count(0) == 1  # 탐색 요청 한 번뒤 5바이트부터 이어 받는다


# ---------- 2D4~2D6, 2D12: 대시보드 저장소 ----------
def _card(title, data=None):
    return {"kind": "chart", "title": title, "source": {"key": "k", "label": "x", "table": "t", "body": {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}},
            "data": data if data is not None else {"values": {"labels": ["old"], "series": []}}}


def test_autosave_keeps_newer_refreshed_data(folder):
    from core import dash_store as store
    old = {"computed_at": "2026-10-24T09:00:00", "def_hash": "h1", "values": {"labels": ["old"], "series": []}}
    d = store.save_dashboard({"name": "자동", "cards": [_card("가", old), _card("나", old)]})
    ids = [c["id"] for c in d["cards"]]
    fresh = {**old, "computed_at": "2026-10-24T10:00:00", "values": {"labels": ["new"], "series": []}}
    assert store.set_cards_data(d["id"], [(ids[0], fresh, None), (ids[1], fresh, None)]) == [True, True]
    # 화면이 옛 값으로 자동 저장: 같은 정의(def_hash)면 저장소의 최신 값을 둔다. 정의가 달라졌으면(def_hash 다름) 들어온 값이 이긴다
    page = [{**c, "data": old} for c in d["cards"]]
    page[1]["data"] = {**old, "def_hash": "h2", "values": {"labels": ["edited"], "series": []}}
    saved = store.save_dashboard({**d, "cards": page})
    assert saved["cards"][0]["data"]["values"]["labels"] == ["new"] and saved["cards"][1]["data"]["values"]["labels"] == ["edited"]
    assert store.get_dashboard(d["id"])["cards"][0]["data"]["computed_at"] == "2026-10-24T10:00:00"
    # 들어온 값이 더 최신이면 들어온 값
    page = [{**c, "data": {**old, "computed_at": "2026-10-24T11:00:00"}} for c in d["cards"]]
    assert store.save_dashboard({**d, "cards": page})["cards"][0]["data"]["computed_at"] == "2026-10-24T11:00:00"


def test_set_cards_data_isolates_failed_card(folder):
    from core import dash_store as store
    d = store.save_dashboard({"name": "격리", "cards": [_card("가"), _card("나"), _card("다")]})
    ids = [c["id"] for c in d["cards"]]
    huge = {"note": "x" * (store.MAX_CARD_BYTES + 10)}
    got = store.set_cards_data(d["id"], [(ids[0], {"note": "a"}, None), (ids[1], huge, None), (ids[2], {"note": "c"}, None)])
    assert got[0] is True and isinstance(got[1], ValueError) and got[2] is True
    assert [c["data"].get("note") for c in store.get_dashboard(d["id"])["cards"]] == ["a", None, "c"]


def test_refresh_flushes_midway_and_reports_failed_card(folder, monkeypatch):
    import contextlib
    import datetime as dt
    from core import dash_store as store, querying, server
    from dashboard import dash_calc, dash_refresh as refresh
    monkeypatch.setitem(server.state, "opened", {"kind": "folder", "path": "C:/x"})
    here = lambda c: {**c, "source": {**c["source"], "key": store.current_source()["key"]}}  # 지금 연결에서 구한 카드(카드를 구하기 전에 연결이 같은지 다시 본다)
    d = store.save_dashboard({"name": "중간", "refresh": {"every": "1일"}, "cards": [here(_card("가")), here(_card("나")), here(_card("다"))]})
    monkeypatch.setattr(querying, "using", lambda **k: contextlib.nullcontext())
    monkeypatch.setattr(refresh, "scan_decision", lambda card, ctx: "")
    monkeypatch.setattr(refresh, "FLUSH_SECONDS", 0)  # 카드 하나 구할 때마다 저장
    monkeypatch.setattr(dash_calc, "compute", lambda card, now, ctx=None: {"note": card["title"], "computed_at": now})
    writes = []
    real = store.set_cards_data
    monkeypatch.setattr(store, "set_cards_data", lambda *a: (writes.append(len(a[1])), real(*a))[1])
    for c in d["cards"]:
        store.set_cards_data(d["id"], [(c["id"], {"computed_at": "2000-01-01T00:00:00"}, None)])
    writes.clear()
    plan = [(store.editable()[d["id"]], c) for c in store.editable()[d["id"]]["cards"]]
    refresh.JOB.update(running=True, total=3, done=0, current="", results=[refresh.result(dd, c, "waiting") for dd, c in plan])
    refresh.work(plan, dt.datetime(2026, 10, 24, 12, 0, 0))
    assert writes == [1, 1, 1]  # 끝에 한 번이 아니라 카드마다
    assert [c["data"]["note"] for c in store.get_dashboard(d["id"])["cards"]] == ["가", "나", "다"]


def test_check_data_allows_user_key_inside_group_values(folder):
    from core import dash_store as store
    card = {"kind": "analysis", "title": "그룹", "source": {"key": "k", "label": "x", "table": "t", "recipe": {"base": "t"}, "analysis": {"kind": "groups", "by": "user", "agg": "count"}},
            "data": {"values": {"groups": [{"user": "a", "password": "b", "n": 1}]}}}
    assert store.check_card(card)["data"]["values"]["groups"][0]["user"] == "a"


def test_load_turns_stored_nan_into_none_without_walk(folder):
    import json
    from core import dash_store as store
    d = store.save_dashboard({"name": "엔", "cards": [_card("가", {"values": {"labels": ["a"], "series": [{"values": [1, 2.5, None]}]}})]})
    assert store.unpack('{"a": [NaN, Infinity, -Infinity, 1, 2.5, "NaN"]}') == {"a": [None, None, None, 1, 2.5, "NaN"]}
    text = json.dumps({"dashboards": {d["id"]: {**store.get_dashboard(d["id"]), "cards": [{**_card("가"), "id": "c1", "data": {"values": {"x": float("nan"), "y": [float("inf"), 3]}}}]}}})
    got = store.unpack(text)["dashboards"][d["id"]]["cards"][0]["data"]["values"]
    assert got == {"x": None, "y": [None, 3]}
    assert store.tidy(store.unpack(text)["dashboards"])[d["id"]]["cards"][0]["data"]["values"] == got  # tidy는 값을 더 건드리지 않는다


# ---------- 2D7: Kafka ----------
class _Msg:
    def __init__(self, n):
        self.n = n

    def offset(self): return self.n
    def partition(self): return 0
    def value(self): return b"v"
    def key(self): return b"k"


class _Producer:
    """always: 브로커가 늘 거절하는 행(영구 오류), flaky: 계속 시간 초과인 행(일시 오류), once: 처음 한 번만 시간 초과."""

    def __init__(self, always=(), flaky=(), once=()):
        self.always, self.flaky, self.once, self.queue, self.log, self.offset = set(always), set(flaky), set(once), [], [], 0

    def produce(self, topic, body, key, on_delivery):
        import json
        self.queue.append((body, on_delivery))
        self.log.append(json.loads(body)["id"])

    def poll(self, t): pass

    def flush(self, t):
        import json
        for body, cb in self.queue:
            row = json.loads(body)["id"]
            if row in self.always:
                cb("KafkaError{code=MSG_SIZE_TOO_LARGE}", None)
            elif row in self.flaky:
                cb("KafkaError{code=_MSG_TIMED_OUT}", None)
            elif row in self.once:
                self.once.discard(row)
                cb("KafkaError{code=_MSG_TIMED_OUT}", None)
            else:
                self.offset += 1
                cb(None, _Msg(self.offset))
        self.queue = []
        return 0


def _sink(monkeypatch, producer, key=""):
    import pyarrow as pa
    from collect import load_kafka
    monkeypatch.setattr(load_kafka, "destination", lambda name: {"topic": "t", "servers": "h:1"})
    monkeypatch.setattr(load_kafka, "producer", lambda item, password, on_error=None: producer)
    return load_kafka.Sink({"load": {"kafka": {"destination": "x", "key": key}}}, "", pa.schema([("id", pa.int64())]), None)


def test_kafka_permanent_row_is_counted_failed_and_load_continues(monkeypatch):
    import pyarrow as pa
    p = _Producer(always={2})
    sink = _sink(monkeypatch, p)
    assert sink.put(pa.table({"id": [0, 1, 2, 3, 4]})) == (4, 0, 1)  # 한 행이 영구 오류여도 나머지는 들어가고 failed로 센다
    assert p.log == [0, 1, 2, 3, 4]  # 영구 오류 행은 다시 보내지 않는다


def test_kafka_transient_error_still_raises_and_retries_only_those(monkeypatch):
    import pyarrow as pa
    p = _Producer(once={1})
    sink = _sink(monkeypatch, p)
    data = pa.table({"id": [0, 1, 2]})
    with pytest.raises(ValueError, match="1행"):
        sink.put(data)
    assert sink.put(data) == (3, 0, 0) and p.log == [0, 1, 2, 1]


def test_kafka_keyed_topic_stops_sending_after_failure_and_resends_in_order(monkeypatch):
    import pyarrow as pa
    p = _Producer(once={0})
    sink = _sink(monkeypatch, p, key="id")
    sink.producer.poll = lambda t: sink.producer.flush(0) if t == 0 else None  # 전달 보고가 보내는 중에 온다고 본다
    data = pa.table({"id": [0, 1, 2]})
    with pytest.raises(ValueError):
        sink.put(data)
    assert p.log == [0]  # 첫 행이 실패한 걸 알면 뒤 행은 보내지 않는다(같은 키 순서를 지킨다)
    assert sink.put(data) == (3, 0, 0) and p.log == [0, 0, 1, 2]  # 다시 시도는 빠진 행부터 차례대로


def test_kafka_partial_delivery_is_recorded_so_resume_does_not_duplicate(tmp_path, folder, monkeypatch):
    import threading
    from collect import load_job, load_kafka, uploads
    from test_fix_collect import load_config, write_result
    monkeypatch.setitem(load_job.TARGETS, "kafka", load_kafka)
    monkeypatch.setattr(load_job, "BACKOFF", 0)
    path = tmp_path / "out.parquet"
    config = load_config(path, retries=1)
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"id": list(range(5))}), path)
    first = _Producer(flaky={2})
    monkeypatch.setattr(load_kafka, "destination", lambda name: {"topic": "t", "servers": "h:1", "security": "PLAINTEXT", "mechanism": "PLAIN", "user": "", "ca": ""})
    monkeypatch.setattr(load_kafka, "producer", lambda item, password, on_error=None: first)
    with pytest.raises(ValueError):
        load_job.run(config, path, "", threading.Event(), lambda **k: None)
    assert load_job.read_record(path)["rows"] == 2  # 0, 1은 들어갔다
    assert uploads.entries()[0]["rows"] == 4  # 기록(원장)에는 실제로 들어간 4행(0, 1, 3, 4)
    second = _Producer()
    monkeypatch.setattr(load_kafka, "producer", lambda item, password, on_error=None: second)
    load_job.run(config, path, "", threading.Event(), lambda **k: None)
    assert second.log == [2, 3, 4]  # 0, 1을 다시 보내지 않는다(예전엔 0부터 다시 보내 겹쳤다)


# ---------- 2D8: 알림 문구는 허용 목록만 ----------
def test_notice_text_sends_only_known_phrases():
    from integrations import schedule
    for leaky in ["(1062, \"Duplicate entry 'kim@corp.com' for key\")", 'malformed array literal: "{홍길동,김"', "Numeric value 'Kim Minsu' is not recognized",
                  'Could not cast literal "010-1234-5678" to type INT64', "Cannot parse string 'Hong Gildong' as UInt8", 'invalid byte sequence for encoding "UTF8": 0xed 0x95']:
        got = schedule.notice_text(leaky)
        assert got == "실패했습니다 (자세한 내용은 프로그램의 실행 기록)" or "데이터 값" in got
        assert not any(v in got for v in ("kim", "홍길동", "Minsu", "010", "Hong", "0xed"))
    assert schedule.notice_text("Connection refused").startswith("연결이 거부되었습니다")
    assert schedule.notice_text("[Errno 110] Connection timed out").startswith("시간이 초과")
    assert schedule.notice_text("[Errno 28] No space left on device").startswith("디스크 공간")
    assert schedule.notice_text("Permission denied: kim_salary.csv").startswith("권한이 없습니다") and "kim_salary" not in schedule.notice_text("Permission denied: kim_salary.csv")
    assert schedule.notice_text("[WinError 5] 액세스가 거부되었습니다").startswith("권한이 없습니다")
    assert schedule.notice_text("[WinError 53] network path").startswith("실패했습니다")  # 5로 시작하는 다른 번호는 권한이 아니다
    assert schedule.notice_text(schedule.SKIP_RUNNING) == schedule.SKIP_RUNNING


# ---------- 2D9: 비었거나 깨진 잠금 파일이 영영 막지 않는다 ----------
def test_empty_or_broken_lock_file_is_stale_after_ten_seconds(folder):
    import os
    import time
    from core import filelock
    path = filelock.lock_path("x")
    path.parent.mkdir(parents=True, exist_ok=True)
    for body in ("", "abc", "1234"):
        path.write_text(body, encoding="ascii")
        assert filelock.hold(path) is False  # 방금 만든 것은 쓰는 중일 수 있어 건드리지 않는다
        old = time.time() - 60
        os.utime(path, (old, old))
        assert filelock.hold(path) is True and path.read_text(encoding="ascii")  # 오래된 빈·깨진 파일은 치우고 잡는다
        filelock.let_go(path)
    path.write_text(__import__("core.scratch", fromlist=["x"]).stamp(), encoding="ascii")  # 살아 있는 프로세스(이 프로세스)의 잠금은 오래돼도 그대로
    os.utime(path, (old, old))
    assert filelock.hold(path) is False


# ---------- 2D10: 분기·연 기준일 직전 기간, 기준일이 기간 앞 ----------
def test_context_prev_for_quarter_and_year_cut_same_position():
    from dashboard import dash_refresh as refresh
    q = refresh.context({"period": "2026-Q3", "as_of": "2026-08-15"})
    assert q["range"] == {"start": "2026-07-01", "end": "2026-08-15"} and (q["prev"]["start"], q["prev"]["end"]) == ("2026-04-01", "2026-05-15")
    y = refresh.context({"period": "2026", "as_of": "2026-10-23"})
    assert (y["prev"]["start"], y["prev"]["end"]) == ("2025-01-01", "2025-10-23")
    m = refresh.context({"period": "2026-03", "as_of": "2026-03-30"})  # 달은 그대로(2월은 28일까지)
    assert (m["prev"]["start"], m["prev"]["end"]) == ("2026-02-01", "2026-02-28")


def test_context_as_of_before_period_is_empty_without_prev():
    from dashboard import dash_refresh as refresh
    ctx = refresh.context({"period": "2026-10", "as_of": "2026-09-15"})
    assert ctx["prev"] is None and ctx["range"]["start"] > ctx["range"]["end"]  # 시작이 끝보다 늦은 빈 기간


# ---------- 2D11·2D13: 평균류에 기여 분해 문장 없음, 고유 개수 직전 기간은 span 비교일 때만 ----------
def _value_card(kind, series, view=None, date="day"):
    return {"kind": kind, "title": "t", "source": {"schema": "", "table": "orders", "body": {"base": "orders", "series": series}, "date": date}, "view": view or {}}


def test_summary_share_sentence_only_for_sum_and_count(db, monkeypatch):
    from dashboard import dash_calc, summary_text
    ctx = {"range": {"start": "2026-01-12", "end": "2026-02-02"}, "prev": {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"}}
    reads, passed = [], {}
    real, summarize = dash_calc.group_values, summary_text.summarize
    monkeypatch.setattr(dash_calc, "group_values", lambda *a, **k: (reads.append(a[2]["agg"]), real(*a, **k))[1])
    monkeypatch.setattr(summary_text, "summarize", lambda *a, **k: (passed.update(groups=a[8]), summarize(*a, **k))[1])
    for agg in ("sum", "count", "mean", "max"):
        passed.clear()
        series = [{"y": None if agg == "count" else "amt", "agg": agg}]
        assert db.post("/api/dash/card/compute", json={"card": _value_card("summary", series, {"group": "store"}), "ctx": ctx}).get_json().get("error") is None
        assert (passed["groups"] is not None) == (agg in ("sum", "count")), agg  # 평균·최대는 기여 분해에 쓸 그룹 값이 없다
    assert reads == ["sum", "sum", "count", "count"]  # 평균·최대는 그룹 값을 읽지도 않는다
    assert dash_calc.group_pairs({"A": 1}, {"A": 2}, "mean") is None and dash_calc.group_pairs({"A": 1}, {}, "count") == {"A": (1, 0)}


def test_nunique_previous_period_only_for_span_compare(db):
    ctx = {"range": {"start": "2026-01-06", "end": "2026-01-12"}, "prev": {"start": "2026-01-05", "end": "2026-01-05", "text": "직전 1일"}}
    uniq = [{"y": "uid", "agg": "nunique"}]
    span = db.post("/api/dash/card/compute", json={"card": _value_card("kpi", uniq, {"compare": {"mode": "span"}}), "ctx": ctx}).get_json()["data"]["values"]["window_nunique"]
    point = db.post("/api/dash/card/compute", json={"card": _value_card("kpi", uniq, {"compare": {"mode": "point"}}), "ctx": ctx}).get_json()["data"]["values"]["window_nunique"]
    assert "prev" in span and "prev" not in point  # 지점 비교는 직전 기간 값을 쓰지 않으니 DB에서 읽지 않는다


def test_remote_confirm_text_says_unique_counts_read_per_period(db, monkeypatch):
    from core import dash_store, held, scan_size
    from dashboard import dash_calc, dash_refresh
    ctx = {"range": {"start": "2026-01-06", "end": "2026-01-12"}, "prev": {"start": "2026-01-05", "end": "2026-01-05", "text": "직전 1일"}}
    uniq = dash_store.check_card(_value_card("kpi", [{"y": "uid", "agg": "nunique"}]))
    plain = dash_store.check_card(_value_card("kpi", [{"y": "amt", "agg": "sum"}]))
    assert dash_calc.unique_per_period(uniq, ctx) and not dash_calc.unique_per_period(plain, ctx) and not dash_calc.unique_per_period(uniq, None)
    monkeypatch.setattr(held, "remote", lambda con: True)
    monkeypatch.setattr(dash_calc, "copies_to_pc", lambda card, ctx=None: True)
    monkeypatch.setattr(scan_size, "limit_check", lambda con, sql, opened=None, dialect=None, **k: {"over": True, "text": "PC로 약 9GB를 받아요"})
    assert dash_refresh.scan_decision(uniq, ctx).endswith("고유 개수는 기간별로 따로 읽어요.")
    assert "고유 개수" not in dash_refresh.scan_decision(plain, ctx)


# ---------- 2D14: GA4 교체 직전 재검사, 구성 삭제 순서, 죽은 코드·불필요한 쓰기·조회 ----------
def test_ga4_fetch_does_not_overwrite_file_created_while_fetching(client, tmp_path, monkeypatch):
    import types
    import polars as pl
    from integrations import integrations_app
    out = tmp_path / "ga.parquet"

    def fetch(key_path, spec):
        out.write_bytes(b"someone else's file")  # 보고서를 받는 사이 같은 이름의 파일이 생겼다
        return pl.DataFrame({"a": [1]}), 1
    monkeypatch.setattr(integrations_app, "ga4", types.SimpleNamespace(fetch=fetch))
    monkeypatch.setattr(integrations_app, "ga4_settings", lambda: {"key_path": "k.json", "output": str(out)})
    got = client.post("/api/ga4/fetch", json={}).get_json()
    assert "이미 있습니다" in got["error"] and out.read_bytes() == b"someone else's file" and not list(tmp_path.glob("*.part"))


def test_collect_limits_lock_clear_does_not_remove_fresh_lock(folder):
    import os
    import time
    from collect import collect_limits
    path = folder / collect_limits.LOCK
    path.write_text("")
    collect_limits.clear_stale(path)  # 오래됐다고 잘못 본 사이 다른 프로세스가 새로 잡은 잠금
    assert path.exists() and not list(folder.glob("*.old"))
    old = time.time() - collect_limits.STALE_LOCK - 5
    os.utime(path, (old, old))
    collect_limits.clear_stale(path)
    assert not path.exists() and not list(folder.glob("*.old"))
    with collect_limits.Locked():  # 실제 잡기·풀기도 그대로
        assert path.exists()
    assert not path.exists()


def test_deleting_collection_keeps_it_when_schedule_cannot_be_deleted(client, monkeypatch):
    from collect import collect_config
    from core import settings
    from integrations import schedule

    def broken(task):
        raise ValueError("Windows 예약 작업을 지우지 못했습니다.")
    monkeypatch.setattr(schedule.wintask, "delete", broken)
    settings.write("collections.json", {"daily": collect_config.normalize({})})
    settings.write(schedule.FILE, {"daily": {"every": "daily", "task": "ibis_daily"}})
    assert "지우지 못했습니다" in client.post("/api/collections", json={"name": "daily", "collection": None}).get_json()["error"]
    assert "daily" in settings.named("collections.json") and "daily" in schedule.schedules()  # 구성이 남아 다시 지울 수 있다


def test_runs_view_writes_only_when_changed_and_overview_queries_folder_once(folder, monkeypatch):
    from core import settings
    from integrations import schedule, wintask
    schedule.add_run("a", "수동")
    writes = []
    real = settings.write
    monkeypatch.setattr(settings, "write", lambda name, value: (writes.append(name), real(name, value))[1])
    schedule.runs_view()
    schedule.mark_seen()  # 실패 기록이 없으니 바뀐 게 없다
    assert writes == []
    schedule.add_run("b", "수동")
    assert writes == [schedule.RUNS]
    calls = []
    head = ",".join(f'"h{i}"' for i in range(28))
    row = lambda task: ",".join(f'"{v}"' for v in ["PC", wintask.FOLDER + task, "2026-10-16 오전 9:00:00", "준비", "대화형만", "2026-10-15 오후 1:05:00", "0", "me", "x --run"] + ["N/A"] * 19)
    monkeypatch.setattr(wintask, "execute", lambda args: (calls.append(args), (0, head + "\n" + row("t1") + "\n" + row("t2") + "\n", ""))[1])  # 진짜 schtasks는 부르지 않는다
    found = {"a": {"windows": True, "task": "t1"}, "b": {"windows": True, "task": "T2"}, "c": {"windows": True, "task": "t3"}, "d": {"windows": False}}
    view = schedule.tasks(found)
    assert len(calls) == 1 and calls[0][3] == wintask.FOLDER
    assert view["a"]["registered"] and view["b"]["registered"] and not view["c"]["registered"] and "d" not in view


# ---------- 2D10 직전 기간: 1일에 시작해 기준일이 그 달 안이면 지난달 같은 위치까지 ----------

@pytest.mark.parametrize("period, as_of, start, end", [
    ("2026-10-01 ~ 2026-10-20", "2026-10-15", "2026-09-01", "2026-09-15"),  # 달 전체가 아니어도 예전처럼 지난달 같은 위치
    ("2026-10-01 ~ 2026-12-31", "2026-11-15", "2026-07-01", "2026-08-15"),  # 분기
    ("2026-10-05 ~ 2026-10-20", "2026-10-15", "2026-09-24", "2026-10-04"),  # 1일 시작이 아니면 바로 앞 같은 일수
])
def test_context_previous_period(period, as_of, start, end):
    from dashboard import dash_refresh
    prev = dash_refresh.context({"period": period, "as_of": as_of})["prev"]
    assert (prev["start"], prev["end"]) == (start, end)
