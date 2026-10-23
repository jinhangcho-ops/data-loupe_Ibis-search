"""리뷰 반영 묶음 4(대시보드): 계열 이름의 옵션, 같은 읽기 묶음 열쇠의 계산 줄, 대시보드 열기 경로, NaN, 최신화 중 편집·두 번 시작·예외,
실시간 since, 시간대 시각, 기간 숫자 넘침, 요약 기여 분해 상한, 편차 표, 저장소 읽기 캐시. 값은 손으로 계산하고 데이터는 테스트 안에서 지어낸다."""
import contextlib
import datetime as dt
import json
import shutil
import subprocess
import threading
import time

import duckdb
import pytest

from core import querying
from core import credentials
from dashboard import dash_calc
from dashboard import dash_refresh as refresh
from core import dash_store as store
from core import metric_file
from dashboard import period
from core import server
from core import watch
from conftest import ROOT, spy_queries

NOW = dt.datetime(2026, 10, 22, 12, 0, 0)
FOLDER = {"kind": "folder", "path": "C:/data/sales"}
KEY = store.source_key(FOLDER)


def strict(response):
    """NaN·Infinity가 든 JSON은 브라우저가 못 읽으므로 거부하며 읽는다."""
    def refuse(name):
        raise ValueError(f"JSON에 {name}가 들어 있어요")
    return json.loads(response.data, parse_constant=refuse)


def card(title, key=KEY, kind="chart", body=None, data=None, **more):
    return {"kind": kind, "title": title, "source": {"key": key, "label": "x", "table": "t", "body": body or {"base": "t", "x": "a", "series": [{"y": None, "agg": "count"}]}, **more},
            "data": {"values": {"labels": ["old"], "series": []}, **(data or {})}}


# ---------- M10 계열 이름에 옵션 ----------

def test_series_name_has_opts_so_two_quantiles_differ():
    q = lambda p: {"y": "amt", "agg": "quantile", "opts": {"p": p}}
    assert dash_calc.series_name(q(0.9)) == "분위수 (amt) p=0.9" and dash_calc.series_name(q(0.5)) == "분위수 (amt) p=0.5"
    assert dash_calc.series_name({"y": "amt", "agg": "sum"}) == "합계 (amt)"  # 옵션이 없으면 그대로
    assert dash_calc.series_name({"y": "amt", "agg": "quantile", "opts": {"p": [0.1, 0.9], "order": "x"}}) == "분위수 (amt) p=0.1,0.9"  # order는 이름에 넣지 않는다


def test_two_quantile_series_stay_two_series_in_time_data():
    import polars as pl
    from lazy import ibis
    t = ibis.memtable(pl.DataFrame({"day": [dt.date(2026, 1, 5)] * 3, "amt": [1.0, 2.0, 3.0]}).to_arrow())
    series = [{"y": "amt", "agg": "quantile", "opts": {"p": 0.0}}, {"y": "amt", "agg": "quantile", "opts": {"p": 1.0}}]
    day = dash_calc.time_data(t, "day", series, None)["by_unit"]["day"]
    assert {s["name"]: s["values"] for s in day["series"]} == {"분위수 (amt) p=0.0": [1.0], "분위수 (amt) p=1.0": [3.0]}


# ---------- M9 같은 읽기 묶음 열쇠에 계산 줄 ----------

def test_group_key_separates_calcs():
    plain = {"base": "t", "series": [{"y": "amt", "agg": "sum"}]}
    assert dash_calc.group_key(plain) != dash_calc.group_key({**plain, "calcs": ["두배 = amt * 2"]})
    assert dash_calc.group_key({**plain, "calcs": ["a = amt * 2"]}) != dash_calc.group_key({**plain, "calcs": ["a = amt * 3"]})


def test_plan_shared_does_not_mix_cards_with_different_calcs():
    def kpi(title, calcs):
        body = {"base": "t", "series": [{"y": "d", "agg": "sum"}], **({"calcs": calcs} if calcs else {})}
        return store.check_card({"kind": "kpi", "title": title, "source": {"body": body, "date": "day"}})
    plan = dash_calc.plan_shared([kpi("가", ["d = amt * 2"]), kpi("나", ["d = amt * 3"]), kpi("다", None)])
    assert len(plan["series"]) == 3


# ---------- M15 대시보드 열기는 차례를 기다리지 않는 경로 ----------

def test_open_dashboard_is_free_route(client):
    saved = client.post("/api/dash", json={"name": "열기", "cards": []}).get_json()["dashboard"]
    assert "/api/dash/get" in server.FREE
    got = client.get("/api/dash/get", query_string={"id": saved["id"]}).get_json()
    assert got["dashboard"]["id"] == saved["id"]
    assert "찾을 수 없" in client.get("/api/dash/get", query_string={"id": "nope"}).get_json()["error"]
    assert client.get(f"/api/dash/{saved['id']}").status_code == 404  # 차례를 기다리는 예전 경로는 없앴다


def test_open_dashboard_does_not_wait_for_the_turn(client):
    saved = client.post("/api/dash", json={"name": "안기다림", "cards": []}).get_json()["dashboard"]
    done = []
    with server.turn:  # 다른 요청이 연결을 쓰는 중
        worker = threading.Thread(target=lambda: done.append(client.get("/api/dash/get", query_string={"id": saved["id"]}).get_json()))
        worker.start()
        worker.join(3)
        assert done and done[0]["dashboard"]["id"] == saved["id"]


# ---------- M6 NaN·무한 → null ----------

@pytest.fixture
def nandb(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "n.duckdb"))
    raw.execute("create table nn (day date, v double)")
    raw.executemany("insert into nn values (?, ?)", [(dt.date(2026, 1, 5), float("nan")), (dt.date(2026, 1, 5), 1.0), (dt.date(2026, 1, 6), 2.0)])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "n.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_compute_turns_nan_into_null(nandb):
    sums = [{"y": "v", "agg": "sum"}]
    scalar = nandb.post("/api/dash/card/compute", json={"card": {"kind": "kpi", "title": "t", "source": {"body": {"base": "nn", "series": sums}}}})
    assert strict(scalar)["data"]["values"]["scalar"] == [None]  # NaN이 든 합계는 값이 없는 것으로
    timed = nandb.post("/api/dash/card/compute", json={"card": {"kind": "kpi", "title": "t", "source": {"body": {"base": "nn", "series": sums}, "date": "day"}}})
    assert strict(timed)["data"]["by_unit"]["day"]["series"][0]["sum"] == [None, 2.0]


def test_stored_nan_is_cleaned_when_read(folder):
    saved = store.save_dashboard({"name": "엔", "cards": [card("가")]})
    raw = store.editable()
    raw[saved["id"]]["cards"][0]["data"]["values"] = {"labels": ["a"], "series": [{"name": "s", "values": [float("nan"), float("inf"), 3.0]}]}
    store.write(raw)  # 예전에 NaN이 그대로 저장된 파일
    got = store.get_dashboard(saved["id"])["cards"][0]["data"]["values"]["series"][0]["values"]
    assert got == [None, None, 3.0]


# ---------- S 최신화 중 편집 / 두 번 시작 / 예외 ----------

@pytest.fixture
def fake(folder, monkeypatch):
    monkeypatch.setitem(server.state, "opened", FOLDER)
    monkeypatch.setattr(querying, "using", lambda: contextlib.nullcontext())
    refresh.JOB.update(running=False, total=0, done=0, current="", results=[])
    yield
    refresh.wait(10)


def save(name, cards, every="1일"):
    return store.save_dashboard({"name": name, "cards": cards, "refresh": {"every": every}})


def test_edit_during_refresh_keeps_the_edit_and_skips_the_stale_value(fake, monkeypatch):
    d = save("편집", [card("가")])
    edited = {**store.get_dashboard(d["id"])}
    edited["cards"] = [{**edited["cards"][0], "source": {**edited["cards"][0]["source"], "body": {"base": "t", "x": "b", "series": [{"y": None, "agg": "count"}]}}}]

    def slow(c, now, ctx=None):
        store.save_dashboard(edited)  # 구하는 동안 사용자가 조회를 바꿔 저장했다
        return {"values": {"labels": ["stale"], "series": []}, "computed_at": now}
    monkeypatch.setattr(dash_calc, "compute", slow)
    refresh.start(NOW)
    refresh.wait()
    result = refresh.status()["results"][0]
    now_card = store.get_dashboard(d["id"])["cards"][0]
    assert result["state"] == "skipped" and "고쳤" in result["message"]
    assert now_card["data"]["values"]["labels"] == ["old"] and now_card["source"]["body"]["x"] == "b"  # 옛 정의로 구한 값을 새 정의에 붙이지 않는다


def test_set_card_data_same_check(folder):
    d = store.save_dashboard({"name": "확인", "cards": [card("가")]})
    cid = d["cards"][0]["id"]
    assert store.set_card_data(d["id"], cid, {"note": "a"}, same=lambda c: False) is None  # 달라졌으면 쓰지 않는다
    assert store.get_dashboard(d["id"])["cards"][0]["data"].get("note") is None
    assert store.set_card_data(d["id"], cid, {"note": "b"}, same=lambda c: True) is True
    assert store.set_card_data(d["id"], "없음", {"note": "c"}) is False


def test_start_twice_launches_one_job(fake, monkeypatch):
    save("둘", [card("가")])
    original, started = store.editable, []
    monkeypatch.setattr(store, "editable", lambda: (time.sleep(0.3), original())[1])  # 확인과 JOB 갱신 사이를 벌린다
    monkeypatch.setattr(refresh, "work", lambda plan, now: started.append(1))
    outs = []
    threads = [threading.Thread(target=lambda: outs.append(refresh.start(NOW))) for _ in range(2)]
    threads[0].start()
    time.sleep(0.05)
    threads[1].start()
    for th in threads:
        th.join(5)
    refresh.wait(5)
    assert len(started) == 1 and len(outs) == 2


def test_one_bad_dashboard_does_not_kill_the_job(fake, monkeypatch):
    save("가", [card("가카드")])
    save("나", [card("나카드")])
    real = refresh.context

    def context(d, today=None):
        if d["name"] == "가":
            raise OverflowError("날짜가 너무 커요")
        return real(d, today)
    monkeypatch.setattr(refresh, "context", context)
    monkeypatch.setattr(dash_calc, "compute", lambda c, now, ctx=None: {"values": {"labels": ["new"], "series": []}, "computed_at": now})
    refresh.start(NOW)
    refresh.wait()
    done = refresh.status()
    assert not done["running"] and done["done"] == 2
    assert {r["title"]: r["state"] for r in done["results"]} == {"가카드": "failed", "나카드": "ok"}


def test_refresh_reads_cards_together_via_plan(fake, monkeypatch):
    """같은 테이블·조건의 값 카드 둘은 일 값 집계를 한 번만 구한다(최신화)."""
    body = lambda y: {"base": "t", "series": [{"y": y, "agg": "sum"}]}
    save("묶음", [card("가", kind="kpi", body=body("a"), date="day"), card("나", kind="kpi", body=body("b"), date="day")])
    seen = []
    monkeypatch.setattr(dash_calc, "compute", lambda c, now, ctx=None: (seen.append(sorted((ctx or {}).get("plan", {}).get("series", {}).values())), {"values": {}, "computed_at": now})[1])
    refresh.start(NOW)
    refresh.wait()
    assert seen and all(len(s) == 1 and len(s[0]) == 2 for s in seen)  # 계열 합집합이 둘


def test_cards_compute_route_shares_reads(client, monkeypatch):
    monkeypatch.setitem(server.state, "opened", FOLDER)
    body = lambda y: {"base": "t", "series": [{"y": y, "agg": "sum"}]}
    seen = []
    monkeypatch.setattr(dash_calc, "compute", lambda c, now, ctx=None: (seen.append(ctx), {"values": {}})[1])
    cards = [{**card("가", kind="kpi", body=body("a"), date="day"), "id": "c1"}, {**card("나", kind="kpi", body=body("b"), date="day"), "id": "c2"}]
    got = client.post("/api/dash/cards/compute", json={"cards": cards, "ctx": {"range": {"start": "2026-01-01", "end": "2026-01-31"}}}).get_json()
    assert [r["id"] for r in got["results"]] == ["c1", "c2"] and all("plan" in c and "shared" in c for c in seen) and seen[0]["shared"] is seen[1]["shared"]


# ---------- S 실시간 since ----------

pytest_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def run_node(tmp_path, files, body):
    parts = "".join(next((ROOT / "static").rglob(f)).read_text(encoding="utf-8") for f in files)
    script = tmp_path / "t.js"
    script.write_text("const assert = require('assert'); globalThis.SERVER = { dash: { types: {}, time_types: [], units: {}, max_palette: 12, aggs: [] }, merge: [], agg_names: JSON.parse(%s) };\n" % json.dumps(json.dumps(metric_file.AGG_NAMES, ensure_ascii=False))
                      + parts + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout.strip().splitlines()[-1]


@pytest_node
def test_live_requests_keep_since(tmp_path):
    body = """
const watched = { id: "a", kind: "status", title: "t", view: {}, source: {}, data: { since: { a: "2026-01-01T00:00:00" }, values: { entities: [] } } };
const plain = { id: "b", kind: "kpi", title: "t", view: {}, source: {}, data: { values: { scalar: [1] } } };
console.log(JSON.stringify([cardRequest(watched).data, cardRequest(plain).data]));
"""
    got = json.loads(run_node(tmp_path, ["board.js", "dash.js", "dash_cards.js", "dash_rules.js", "dash_ctl.js"], body))
    assert got == [{"since": {"a": "2026-01-01T00:00:00"}}, {}]  # 나빠진 시각은 이어 가고 값은 보내지 않는다


@pytest_node
def test_series_title_matches_server(tmp_path):
    cases = [{"y": "amt", "agg": "quantile", "opts": {"p": 0.9}}, {"y": "amt", "agg": "sum"}, {"y": None, "agg": "count"}, {"y": "amt", "agg": "quantile", "opts": {"p": [0.1, 0.9], "order": "x"}}]
    want = [dash_calc.series_name(s) for s in cases]
    body = "console.log(JSON.stringify(%s.map(seriesTitle)));\n" % json.dumps(cases)
    assert json.loads(run_node(tmp_path, ["board.js", "dash.js", "dash_cards.js", "dash_rules.js", "dash_menu.js"], body)) == want


# ---------- S 시간대 시각 ----------

def test_watch_moment_converts_time_zone():
    nine = dt.timezone(dt.timedelta(hours=9))
    assert watch.moment(dt.datetime(2026, 1, 1, 12, 0, tzinfo=nine)) == watch.moment(dt.datetime(2026, 1, 1, 3, 0, tzinfo=dt.timezone.utc))  # 같은 순간
    assert watch.moment("2026-01-01T12:00:00+09:00") == watch.moment("2026-01-01T03:00:00+00:00")
    assert watch.moment("2026-01-01T12:00:00") == dt.datetime(2026, 1, 1, 12, 0)  # 시간대가 없으면 그대로


def test_watch_latest_row_ignores_rows_without_time():
    rows = [{"n": "a", "t": "2026-01-01T09:00:00", "cpu": 99}, {"n": "a", "t": None, "cpu": 1}]
    got = watch.evaluate(rows, [{"column": "cpu", "op": ">=", "value": 90, "level": "bad"}], "n", dt.datetime(2026, 1, 1, 9, 5), "t")
    assert got["entities"][0]["level"] == "bad" and got["entities"][0]["last"] == "2026-01-01T09:00:00"  # 시각이 빈 행이 최신 행을 가리지 않는다


# ---------- N 기간 숫자 넘침 ----------

@pytest.mark.parametrize("text", ["최근 99999999999일", "최근 99999999999 달", "최근 99999999999 년", "최근 99999999999주"])
def test_huge_period_numbers_are_a_message(text):
    found = period.parse(text, "2026-10-22")
    assert found["error"] and "너무" in found["error"]


# ---------- N 기간 읽기 응답에 서버가 구한 보는 기간·직전 기간 ----------

def test_period_parse_returns_context(client):
    post = lambda **b: client.post("/api/period/parse", json=b).get_json()
    got = post(text="최근 7일", as_of="2026-10-22")
    assert got["context"] == {"range": {"start": "2026-10-16", "end": "2026-10-22"}, "prev": {"start": "2026-10-09", "end": "2026-10-15", "text": "직전 7일"}}
    month = post(text="2026-10", as_of="2026-10-20")["context"]  # 기준일로 자른다
    assert month["range"] == {"start": "2026-10-01", "end": "2026-10-20"} and month["prev"] == {"start": "2026-09-01", "end": "2026-09-30", "text": "직전 30일"}
    assert post(text="", as_of="")["context"] is None and post(text="엉터리", as_of="")["context"] is None


def test_ctl_uses_server_context_not_its_own_arithmetic():
    ctl = (ROOT / "static" / "dashboard" / "dash_ctl.js").read_text(encoding="utf-8")
    assert "parsed.context" in ctl and ".slice(0, 10)" not in ctl.split("async apply")[1].split("async check")[0]


# ---------- S 요약 기여 분해 상한 ----------

def test_group_values_over_limit_gives_none(monkeypatch):
    import polars as pl
    from lazy import ibis
    t = ibis.memtable(pl.DataFrame({"g": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]}).to_arrow())
    s = {"y": "v", "agg": "sum"}
    assert dash_calc.group_values(t, "g", s) == {"a": 1.0, "b": 2.0, "c": 3.0}
    monkeypatch.setattr(dash_calc, "MAX_GROUPS", 2)
    assert dash_calc.group_values(t, "g", s) is None  # 잘린 그룹으로 기여를 말하지 않는다


@pytest.fixture
def sumdb(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table s (day date, g varchar, v double)")
    rows = [(dt.date(2026, 1, 12), "a", 10.0), (dt.date(2026, 1, 12), "b", 30.0), (dt.date(2026, 1, 12), "c", 5.0), (dt.date(2025, 12, 25), "a", 10.0), (dt.date(2025, 12, 25), "b", 10.0), (dt.date(2025, 12, 25), "c", 5.0)]
    raw.executemany("insert into s values (?, ?, ?)", rows)
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_summary_drops_share_sentence_when_groups_exceed_limit(sumdb, monkeypatch):
    ctx = {"range": {"start": "2026-01-12", "end": "2026-01-18"}, "prev": {"start": "2025-12-22", "end": "2025-12-28", "text": "직전 7일"}}
    c = {"kind": "summary", "title": "t", "view": {"group": "g"}, "source": {"body": {"base": "s", "series": [{"y": "v", "agg": "sum"}]}, "date": "day"}}
    first = sumdb.post("/api/dash/card/compute", json={"card": c, "ctx": ctx}).get_json()["data"]
    assert first["values"]["scalar"] == [45] and first["values"]["prev"] == [25] and any("몫" in s for s in first["sentences"])  # b가 +20 중 20 → 100%
    monkeypatch.setattr(dash_calc, "MAX_GROUPS", 2)
    cut = sumdb.post("/api/dash/card/compute", json={"card": c, "ctx": ctx}).get_json()["data"]
    assert not any("몫" in s for s in cut["sentences"]) and "2개를 넘" in cut["note"]


# ---------- N 편차 표 ----------

@pytest.fixture
def hours(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "h.duckdb"))
    raw.execute("create table h (host varchar, ts timestamp, val double)")
    raw.executemany("insert into h values (?, ?, ?)", [("x", dt.datetime(2026, 1, 1, 8, 5), 1.0), ("x", dt.datetime(2026, 1, 1, 9, 5), 10.0), ("x", dt.datetime(2026, 1, 1, 10, 5), 30.0),
                                                       ("y", dt.datetime(2026, 1, 1, 10, 30), 7.0), ("z", None, 99.0)])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "h.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def deviation(c, **view):
    body = {"card": {"kind": "deviation", "title": "t", "view": {"entity": "host", "bucket": "hour", **view}, "source": {"body": {"base": "h", "series": [{"y": "val", "agg": "mean"}]}, "date": "ts"}}}
    return c.post("/api/dash/card/compute", json=body).get_json()


def test_deviation_ignores_empty_times_and_reads_only_recent_buckets(hours, monkeypatch):
    all_ = deviation(hours)["data"]["values"]
    assert all_["buckets"] == ["2026-01-01 08:00:00", "2026-01-01 09:00:00", "2026-01-01 10:00:00"]  # 시각이 빈 행(z)이 'None' 시간대를 만들지 않는다
    assert [e["name"] for e in all_["entities"]] == ["x", "y"]
    monkeypatch.setattr(dash_calc, "MAX_BUCKETS", 2)
    queries = spy_queries(monkeypatch)
    recent = deviation(hours)["data"]["values"]
    assert recent["buckets"] == ["2026-01-01 09:00:00", "2026-01-01 10:00:00"] and {e["name"]: e["values"] for e in recent["entities"]} == {"x": [10, 30], "y": [None, 7]}
    assert any("AVG" in q and ">= MAKE_TIMESTAMP(2026, 1, 1, 9, 0" in q for q in queries)  # 값을 묶는 조회는 최근 2개 시간대(9시~)만 읽는다


# ---------- N 저장소 읽기 캐시 ----------

def test_store_load_is_cached_until_file_changes(folder, monkeypatch):
    saved = store.save_dashboard({"name": "캐시", "cards": [card("가")]})
    calls = []
    real = credentials.reveal
    monkeypatch.setattr(credentials, "reveal", lambda blob: (calls.append(1), real(blob))[1])
    store.get_dashboard(saved["id"])
    store.get_dashboard(saved["id"])
    store.listing()
    assert len(calls) == 1  # 파일이 그대로면 다시 풀지 않는다
    store.set_card_data(saved["id"], saved["cards"][0]["id"], {"note": "새 값"})
    assert store.get_dashboard(saved["id"])["cards"][0]["data"]["note"] == "새 값" and len(calls) >= 2  # 쓰면 바뀐 파일을 읽는다


def test_editing_a_loaded_copy_does_not_leak_into_the_cache(folder):
    saved = store.save_dashboard({"name": "복사", "cards": [card("가")]})
    mine = store.editable()
    mine[saved["id"]]["name"] = "바뀜"  # 저장하지 않고 고쳐 본 것
    assert store.get_dashboard(saved["id"])["name"] == "복사"


# ---------- N 쓰지 않는 상수 ----------

def test_unused_constants_removed():
    from core import compare
    assert not hasattr(compare, "BETTER") and not hasattr(watch, "LEVEL_NAMES")


@pytest_node
def test_shelf_keeps_each_series_own_opts(tmp_path):
    """같은 컬럼·집계에 옵션만 다른 계열 둘: 선반이 각자의 옵션을 그대로 들고 다니고, 집계를 바꾸면 옛 옵션은 버린다."""
    body = """
const series = [{ y: "amt", agg: "quantile", opts: { p: 0.5 } }, { y: "amt", agg: "quantile", opts: { p: 0.9 } }, { y: "amt", agg: "sum" }];
console.log(JSON.stringify([shelfSeries(series), setAgg(series, 1, "sum")[1]]));
"""
    got = json.loads(run_node(tmp_path, ["board.js", "dash.js", "dash_cards.js", "dash_rules.js", "dash_shelf.js", "dash_panel.js"], body))
    assert got[0] == [{"y": "amt", "agg": "quantile", "opts": {"p": 0.5}}, {"y": "amt", "agg": "quantile", "opts": {"p": 0.9}}, {"y": "amt", "agg": "sum"}]
    assert got[1] == {"y": "amt", "agg": "sum"}


@pytest_node
def test_panel_close_does_not_loop_through_board_edit(tmp_path):
    """[×] 닫기: close → board.edit(null) → onEdit(null) → open(null) → close 가 한 번에서 멈춘다(예전에는 끝없이 돌아 스택 넘침)."""
    body = """
const root = { classList: { add() {} } }, host = { popRoot: { append() {} }, menuRoot: {} };
let edits = 0;
host.board = { editing: { id: "a" }, edit(card) { edits++; this.editing = card; panel.open(card); } };  // EditBoard.edit과 같은 차례
const panel = new CardPanel(root, host);
panel.close();
console.log(JSON.stringify({ edits, editing: host.board.editing }));
"""
    got = json.loads(run_node(tmp_path, ["board.js", "dash.js", "dash_cards.js", "dash_rules.js", "dash_shelf.js", "dash_panel.js"], body))
    assert got == {"edits": 1, "editing": None}
