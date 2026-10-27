"""21차 리뷰: 날마다 적은 엑셀 목표의 1일, 작은 값 축 눈금, 버블 말풍선."""
import datetime as dt
import json
import shutil
import subprocess

import pytest
import xlsxwriter

from conftest import ROOT
from core import targets


def test_excel_daily_column_keeps_first_day(tmp_path):
    """날마다 적은 열의 1일은 그날 목표다(달 목표로 바뀌지 않는다)."""
    p = tmp_path / "targets.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    ws, day = wb.add_worksheet("목표"), wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_row(0, 0, ["이름", "값", "기간"])
    ws.write(1, 0, "방문"); ws.write(1, 1, 7); ws.write_datetime(1, 2, dt.datetime(2026, 10, 1), day)
    ws.write(2, 0, "방문"); ws.write(2, 1, 8); ws.write_datetime(2, 2, dt.datetime(2026, 10, 2), day)
    ws.write(3, 0, "방문"); ws.write(3, 1, 300); ws.write(3, 2, "2026")
    wb.close()
    targets.clear()
    rows = targets.read(p)["rows"]
    assert [r["period"] for r in rows] == ["2026-10-01", "2026-10-02", "2026"]
    assert targets.lookup(rows, "방문", "2026-10-01")["row"]["value"] == 7
    assert targets.lookup(rows, "방문", "2026-10")["row"] is None


SCRIPT = r"""
const assert = require("assert"), fs = require("fs"), R = process.argv[2];
globalThis.Chart = require(R + "chart.umd.min.js");
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#fff" });
globalThis.document = { documentElement: {}, querySelectorAll: () => [] };
const src = fs.readFileSync(R + "common.js", "utf8").replace(/\r/g, "");
eval(src.match(/const compact[\s\S]*?\n}\n/)[0].replace("const compact", "globalThis.compact").replace("function chartLook", "globalThis.chartLook = function"));
chartLook();
const canvas = () => { const c = { width: 600, height: 400, style: {} };
  const x = new Proxy({}, { get: (t, k) => k === "measureText" ? s => ({ width: String(s).length * 6 }) : k === "canvas" ? c : (k in t ? t[k] : () => {}), set: (t, k, v) => (t[k] = v, true) });
  c.getContext = () => x; return c; };
const make = cfg => new Chart(canvas(), { ...cfg, options: { animation: false, responsive: false, ...cfg.options } });
const small = make({ type: "line", data: { labels: ["a", "b", "c"], datasets: [{ data: [0.0001, 0.0003, 0.0005] }] } });
assert.ok(small.scales.y.ticks.every(t => t.label !== "0" || t.value === 0), small.scales.y.ticks.map(t => t.label).join("|"));
const big = make({ type: "bar", data: { labels: ["a"], datasets: [{ data: [12000000] }] } });
assert.ok(big.scales.y.ticks.some(t => /만/.test(t.label)));
eval(fs.readFileSync(R + "board.js", "utf8") + "\n" + fs.readFileSync(R + "dash.js", "utf8"));
const card = { data: { points: { points: [[1, 1, 5, "A"], [10, 10, 5, "A"], [9, 1, 5, "B"], [2, 9, 5, "B"]], groups: ["A", "B"], size_range: [5, 5] } }, view: { type: "bubble" } };
const bub = make(pointsConfig(card, { text: "#000", line: "#ccc", series: ["#111111", "#222222"] }, {}));
const at = bub.getDatasetMeta(0).data[0], mode = bub.options.interaction;
const hit = bub.getElementsAtEventForMode({ type: "mousemove", x: at.x, y: at.y, native: {} }, mode.mode, mode, false);
console.log(JSON.stringify(hit.map(e => [e.datasetIndex, e.index])));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_small_ticks_and_bubble_hover(tmp_path):
    js = tmp_path / "t.js"
    js.write_text(SCRIPT, encoding="utf-8")
    out = subprocess.run(["node", str(js), str(ROOT / "static" / "core") + "/"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[[0,0]]"  # 마우스 아래 버블 하나만(다른 그룹의 같은 순번 점은 빠진다)


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_metric_list_sentence_particles():
    """'내 지표' 목록의 문장(sentRender)도 서버 render처럼 조사를 받침에 맞추고 집계 id를 이름으로."""
    from query import sentence_run, sentences
    src = (ROOT / "static/query/sentences.js").read_text(encoding="utf-8")
    start = src.index("  const PAIRS = {")
    body = src[start:src.index("\n", src.index("return !text ? all", start)) + 1]
    m06, m10 = sentences.get("m06"), sentences.get("m10")
    js = ("globalThis.SERVER = { agg_names: { sum: '합계' } };\n" + body + "\nconsole.log(JSON.stringify([sentRender(%s, {'지표 A': 'm1'}, {m1: '매출'}), sentRender(%s, %s), sentRender(%s, {'지표 A': 'm2'}, {m2: 'GMV'}), sentRender(%s, {'테이블 A': 'a', '테이블 B': 'b', '연결 컬럼': 'store_num'})]));"
          % (json.dumps(m06), json.dumps(m10), json.dumps({s["name"]: ({"agg": "sum"} if s["type"] == "agg" else s["name"]) for s in m10["slots"]}), json.dumps(m06), json.dumps(sentences.get("e16"))))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got[0] == sentence_run.render("m06", {"지표 A": "매출"})
    assert "합계" in got[1] and "sum" not in got[1]
    assert got[2] == sentence_run.render("m06", {"지표 A": "GMV"})  # 영문 끝(V)은 받침 없는 소리로(가)
    assert got[3] == sentence_run.render("e16", {"테이블 A": "a", "테이블 B": "b", "연결 컬럼": "store_num"})  # 화면도 store_num으로


@pytest.mark.parametrize("line, rows", [("ts <= 2026-01-02 23:59", 1), ("ts = 2026-01-02 23:59", 1), ("ts > 2026-01-02 23:59", 0), ("ts != 2026-01-02 23:59", 0), ("ts <= 2026-01-02 23:58", 0)])
def test_condition_hhmm_end_includes_that_minute(line, rows):
    """조건의 HH:MM 값은 기간처럼 그 1분 전체(23:59:30은 '<= 23:59'에 든다)."""
    import duckdb
    import ibis
    from core import parse
    raw = duckdb.connect()
    raw.execute("create table t as select timestamp '2026-01-02 23:59:30' ts")
    t = ibis.duckdb.from_connection(raw).table("t")
    assert t.filter(parse.parse_condition(line, t)).count().execute() == rows
    assert t.filter(parse.parse_period("ts 2026-01-02 ~ 2026-01-02 23:59", t)).count().execute() == 1


def test_sentence_particles_for_latin_digit_and_unit():
    from query import sentence_run
    assert sentence_run.josa("price", "을") == "를" and sentence_run.josa("금액2", "을") == "를" and sentence_run.josa("item", "을") == "을"  # 틀 글에서 빈칸 뒤 조사를 없애 josa를 바로 본다
    assert "걸린 시간을 일 단위로" in sentence_run.render("e42", {"날짜 A": "a", "날짜 B": "b", "단위": "day"})


def test_examples_in_hints_work():
    """전처리 안내의 like 예시와 문장 기간 칸 예시는 실제로 읽힌다."""
    from dashboard import period
    assert "like 김%" not in (ROOT / "static/prep/prep.html").read_text(encoding="utf-8")
    assert "지난 7일" not in (ROOT / "static/query/sentences.js").read_text(encoding="utf-8")
    assert period.parse("최근 7일", "2026-10-27")["error"] is None


def test_latin_m_n_end_takes_euro():
    """영문 M·N 끝은 ㅁ·ㄴ 받침(으로), L은 ㄹ(로). 단위 칸에 글자가 아닌 값이 와도 render가 터지지 않는다."""
    from query import sentence_run
    assert sentence_run.josa("store_num", "으로") == "으로" and sentence_run.josa("store_url", "으로") == "로"
    assert sentence_run.render("e42", {"날짜 A": "a", "날짜 B": "b", "단위": {"x": 1}})
    assert sentence_run.render("e42", {"날짜 A": "a", "날짜 B": "b", "단위": 5})


def test_between_hhmm_end_includes_that_minute():
    import duckdb
    import ibis
    from core import parse
    raw = duckdb.connect()
    raw.execute("create table t as select timestamp '2026-01-02 23:59:30' ts")
    t = ibis.duckdb.from_connection(raw).table("t")
    assert t.filter(parse.parse_condition("ts between 2026-01-01 2026-01-02T23:59", t)).count().execute() == 1
    assert t.filter(parse.parse_condition("ts between 2026-01-01 2026-01-02", t)).count().execute() == 1
