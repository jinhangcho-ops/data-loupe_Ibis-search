"""11차 리뷰 D 묶음: 퍼널은 표본이 아니라 전체로, 실수 NaN 하루가 일 합을 비우지 않음, 끝값 HH:MM은 그 분 끝까지, 빈 구간 0, 요약의 일부 여부,
시계열은 최근 기간·빈 기간 0, 산점도 고르게 뽑기, 편차 표 엔티티 먼저 고르기, 최근 N일은 어제까지. 데이터는 시험 안에서 지어낸다."""
import datetime as dt
import json
import shutil
import subprocess

import duckdb
import polars as pl
import pytest
from lazy import ibis

from analysis import stats
from core import dash_store
from core import parse
from core import server
from core import sql_funcs
from dashboard import dash_calc
from dashboard import period
from conftest import ROOT

D = dt.date


@pytest.fixture
def real(folder, tmp_path):
    """실제 DuckDB 파일에 연결한 시험 클라이언트. 표는 connect(real, make)로 먼저 만든다."""
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    c.tmp = tmp_path
    yield c
    c.post("/api/disconnect", json={})


def connect(c, make):
    path = c.tmp / "d.duckdb"
    with duckdb.connect(str(path)) as raw:
        make(raw)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(c.tmp), "file": str(path)}).status_code == 200


# ---------- 11D1 퍼널은 행 표본이 아니라 전체로 ----------
def test_funnel_uses_whole_table_not_row_sample(real):
    def make(raw):
        raw.execute("create table ev as select 'u' || (i // 2) as who, case when i % 2 = 0 then 'a' else 'b' end as step,"
                    " timestamp '2026-01-01' + (i * interval 1 second) as ts from range(4000) t(i)")
    connect(real, make)
    how = {"kind": "funnel", "who": "who", "step": "step", "time": "ts", "steps": ["a", "b"]}
    d = real.post("/api/analyze", json={"recipe": {"base": "ev"}, "how": how, "sample": 1000}).get_json()
    assert d["data"] == {"full": True} and [s["users"] for s in d["steps"]] == [2000, 2000] and d["steps"][1]["conv"] == 1.0


# ---------- 11D2 실수 NaN 하루가 그날 합을 비우지 않음 ----------
def test_nan_does_not_blank_day_sum():
    t = ibis.memtable(pl.DataFrame({"day": [D(2026, 1, 5)] * 3, "amt": [1.0, float("nan"), 2.0]}).to_arrow())
    day = dash_calc.time_data(t, "day", [{"y": "amt", "agg": "sum"}, {"y": "amt", "agg": "mean"}], None)["by_unit"]["day"]
    s = {x["name"]: x for x in day["series"]}
    assert s["합계 (amt)"]["sum"] == [3.0] and s["합계 (amt)"]["count"] == [2] and s["합계 (amt)"]["max"] == [2.0]
    assert dash_calc.scalars(t, [{"y": "amt", "agg": "sum"}, {"y": "amt", "agg": "max"}]) == [3.0, 2.0]


# ---------- 11D4 끝값 HH:MM은 그 분 끝까지, 직전 기간은 빈틈·겹침 없이 ----------
def test_end_minute_includes_seconds():
    t = ibis.memtable(pl.DataFrame({"ts": [dt.datetime(2026, 1, 1, 10, 5, 30), dt.datetime(2026, 1, 1, 10, 6)]}).to_arrow())
    kept = lambda line: t.filter(parse.parse_period(line, t)).count().execute()
    assert kept("ts 2026-01-01 10:00 ~ 2026-01-01 10:05") == 1  # 10:05:30은 넣고 10:06은 뺀다
    assert kept("ts 2026-01-01 10:00 ~ 2026-01-01T10:05") == 1
    assert kept("ts 2026-01-01 10:00 ~ 2026-01-01 10:05:00") == 0  # 초까지 쓰면 그 순간까지
    assert kept("ts ~ 2026-01-01") == 2


def test_previous_of_mixed_clock_span_has_no_overlap():
    cur = period.parse("2026-10-09 09:00 ~ 2026-10-10", "2026-10-22")
    prev = period.previous(cur)
    assert (prev["start"], prev["end"]) == ("2026-10-07 18:00", "2026-10-09 08:59")  # 끝이 날짜만이어도 직전 끝은 시작 바로 앞 분
    prev = period.previous(period.parse("2026-10-09 ~ 2026-10-09 12:00", "2026-10-22"))
    assert (prev["start"], prev["end"]) == ("2026-10-08 11:59", "2026-10-08 23:59")  # 같은 12시간 1분


# ---------- 11G2 최근 N일은 어제까지 ----------
def test_recent_n_ends_yesterday():
    found = period.parse("최근 7일", "2026-10-22")
    assert (found["start"], found["end"], found["days"]) == ("2026-10-15", "2026-10-21", 7)
    prev = period.previous(found)
    assert (prev["start"], prev["end"]) == ("2026-10-08", "2026-10-14")
    assert period.parse("오늘", "2026-10-22")["start"] == "2026-10-22"


# ---------- 11D6 요약 카드: 직전 기간이 자료 첫 날보다 앞이면 일부 ----------
def test_summary_partial_when_prev_starts_before_data(real):
    def make(raw):
        raw.execute("create table s (day date, amt double)")
        raw.executemany("insert into s values (?, ?)", [(D(2026, 1, 8), 5.0), (D(2026, 1, 10), 10.0), (D(2026, 1, 12), 20.0)])
    connect(real, make)
    card = {"kind": "summary", "title": "t", "source": {"schema": "", "table": "s", "date": "day", "body": {"base": "s", "series": [{"y": "amt", "agg": "sum"}]}},
            "view": {"compare": {"mode": "span"}, "better": "up"}}
    ctx = {"range": {"start": "2026-01-10", "end": "2026-01-13"}, "prev": {"start": "2026-01-06", "end": "2026-01-09", "text": "직전 4일"}}
    got = dash_calc.summary_data(dash_store.check_card(card), ctx)
    full = dash_calc.summary_data(dash_store.check_card(card), {"range": ctx["range"], "prev": {"start": "2026-01-08", "end": "2026-01-09", "text": "직전 2일"}})
    assert got["values"]["prev"] == [5.0] and "일부" in got["text"] and "일부" not in full["text"]


# ---------- 11D9 시계열은 최근 기간을 남기고, 개수·합계는 빈 기간 0 ----------
def test_trend_keeps_latest_and_fills_zero(monkeypatch):
    monkeypatch.setattr(stats, "MAX_PERIODS", 3)
    t = ibis.memtable(pl.DataFrame({"d": [D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 4), D(2026, 1, 6)], "x": [1.0, 2.0, 3.0, 4.0]}).to_arrow())
    c = stats.trend(t, "d", "day", "", "count")
    assert (c["labels"], c["values"], c["more"]) == (["2026-01-04", "2026-01-05", "2026-01-06"], [1, 0, 1], True)
    m = stats.trend(t, "d", "day", "x", "mean")
    assert (m["labels"], m["values"]) == (["2026-01-02", "2026-01-04", "2026-01-06"], [2.0, 3.0, 4.0])  # 평균은 채우지 않는다
    monkeypatch.setattr(stats, "MAX_PERIODS", 10)
    w = stats.trend(t, "d", "week", "x", "sum")
    assert w["more"] is False and w["labels"] == ["2025-12-29", "2026-01-05"]
    mo = stats.trend(ibis.memtable(pl.DataFrame({"d": [D(2025, 11, 30), D(2026, 2, 1)]}).to_arrow()), "d", "month", "", "count")
    assert (mo["labels"], mo["values"]) == (["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"], [1, 0, 0, 1])


# ---------- 11D10 산점도는 앞 행이 아니라 고르게 ----------
def test_scatter_samples_evenly(monkeypatch):
    monkeypatch.setattr(stats, "MAX_POINTS", 100)
    t = ibis.memtable(pl.DataFrame({"x": list(range(1000)), "y": [float(i) for i in range(1000)]}).to_arrow())
    s = stats.scatter(t, "x", "y")
    xs = [p[0] for p in s["points"]]
    assert len(xs) <= 100 and max(xs) > 500 and s["n"] == 1000
    assert stats.scatter(t, "x", "y")["points"] == s["points"]  # 고정 씨앗이라 다시 구해도 같은 점


# ---------- 11E6 편차 표: 엔티티를 먼저 고른다 ----------
def test_deviation_picks_entities_first(real, monkeypatch):
    def make(raw):
        raw.execute("create table m (host varchar, ts timestamp, val double)")
        raw.executemany("insert into m values (?, ?, ?)", [(h, dt.datetime(2026, 1, 1, 9 + i), 1.0) for h in ("c", "a", "b", "d") for i in range(2)] + [(None, dt.datetime(2026, 1, 1, 9), 1.0)])
    connect(real, make)
    monkeypatch.setattr(dash_calc, "MAX_ENTITIES", 2)
    card = {"kind": "deviation", "title": "t", "source": {"schema": "", "table": "m", "date": "ts", "body": {"base": "m", "series": [{"y": "val", "agg": "sum"}]}},
            "view": {"entity": "host", "bucket": "hour"}}
    v = dash_calc.deviation_data(dash_store.check_card(card), None)["values"]
    assert [e["name"] for e in v["entities"]] == ["a", "b"] and v["more"] is True and len(v["buckets"]) == 2


# ---------- dash.js: 평균의 빈 합, 빈 구간 0, 카드 날마다 값 ----------
JS = """
const assert = require("assert");
globalThis.SERVER = { dash: { types: {}, time_types: [], units: [] }, merge: %(merge)s, agg_names: {} };
// 11D2 평균: 합이 빈 날만 있으면 0이 아니라 빈 값
const mean = { agg: "mean", sum: [null, null], count: [3, 0] };
assert.strictEqual(rollup(mean, ["2026-01-05", "2026-01-06"], "week").get("2026-01-05"), null);
// 11D5 읽어 온 범위 안의 빈 구간: 개수·합계는 0, 평균은 빈 값, 범위 밖은 빈 값
const days = ["2026-01-01", "2026-01-05"], loaded = { start: "2026-01-01", end: "2026-01-10" };
const cnt = { agg: "count", count: [2, 3] }, sum = { agg: "sum", sum: [1, 2], count: [1, 1] }, avg = { agg: "mean", sum: [1, 2], count: [1, 1] };
assert.strictEqual(windowValue(cnt, days, "2026-01-02", "2026-01-04", loaded), 0);
assert.strictEqual(windowValue(sum, days, "2026-01-06", "2026-01-10", loaded), 0);
assert.strictEqual(windowValue(avg, days, "2026-01-02", "2026-01-04", loaded), null);
assert.strictEqual(windowValue(cnt, days, "2025-12-20", "2025-12-31", loaded), null);  // 읽지 않은 범위
assert.strictEqual(windowValue(cnt, days, "2026-01-02", "2026-01-04"), null);  // 범위를 모르면 그대로
const card = { kind: "kpi", range: { start: "2026-01-06", end: "2026-01-10" }, view: {},
  data: { window: loaded, by_unit: { day: { labels: days, series: [cnt] } } } };
assert.strictEqual(cardValue(card), 0);
assert.strictEqual(cardValue({ ...card, data: { ...card.data, window: { start: "2026-01-08", end: "2026-01-10" } } }), null);
const cmp = compareItem(card, { start: "2026-01-06", end: "2026-01-10" }, { start: "2026-01-01", end: "2026-01-05", text: "직전 5일" });
assert.deepStrictEqual([cmp.cur, cmp.prev], [0, 5]);
// 일 값을 앞에서 잘랐으면(월 값이 서버에 있으면) 잘린 앞은 0이라고 하지 않는다
const cut = { ...card, data: { ...card.data, by_unit: { day: { labels: ["2026-01-05"], series: [cnt] }, month: { labels: ["2026-01-01"], series: [{ name: undefined, values: [5] }] } } } };
cut.data.by_unit.day.series = [{ ...cnt, name: "n", count: [3] }]; cut.data.by_unit.month.series[0].name = "n";
assert.strictEqual(windowValue(cut.data.by_unit.day.series[0], ["2026-01-05"], "2026-01-02", "2026-01-03", loadedRange(cut)), null);
// 11D9 카드의 날마다 값: 개수·합계는 빈 날을 0으로 채운다
assert.deepStrictEqual(cardDays({ ...card, range: { start: "2026-01-01", end: "2026-01-06" } }).map(d => [d.day, d.v]),
  [["2026-01-01", 2], ["2026-01-02", 0], ["2026-01-03", 0], ["2026-01-04", 0], ["2026-01-05", 3], ["2026-01-06", 0]]);
const avgCard = { ...card, range: { start: "2026-01-01", end: "2026-01-06" }, data: { ...card.data, by_unit: { day: { labels: days, series: [avg] } } } };
assert.deepStrictEqual(cardDays(avgCard).map(d => d.day), days);  // 평균은 있는 날만
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_dash_js_empty_windows(tmp_path):
    script = tmp_path / "d.js"
    script.write_text("".join((ROOT / p).read_text(encoding="utf-8") for p in ("static/core/board.js", "static/core/dash.js", "static/dashboard/dash_cards.js"))
                      + JS.replace("%(merge)s", json.dumps(list(sql_funcs.MERGE))), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), done.stderr[-3000:]
