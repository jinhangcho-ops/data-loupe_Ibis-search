"""감사 21의 대시보드 고침: 블록 카드의 칸 수, 공백 있는 날짜 컬럼의 카드 기간 줄(own_range·ownRange), 공백 있는 컬럼의 상태 규칙, 실시간 최소 안내 글."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT
from core import dash_store
from dashboard import dash_calc

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def run_js(tmp_path, body):
    """board.js·dash.js·dash_cards.js·dash_rules.js 뒤에 body를 붙여 node로 돌린 마지막 줄."""
    dash = json.dumps({"types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS, "max_palette": dash_store.MAX_PALETTE, "aggs": list(dash_calc.AGGS)}, ensure_ascii=False)
    head = "const assert = require('assert'); globalThis.SERVER = { dash: JSON.parse(%s), merge: [], agg_names: {} };\n" % json.dumps(dash, ensure_ascii=False)
    script = tmp_path / "t.js"
    script.write_text("".join((ROOT / "static" / p).read_text(encoding="utf-8") for p in ("core/board.js", "core/dash.js", "dashboard/dash_cards.js", "dashboard/dash_rules.js")) + head + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout.strip().splitlines()[-1]


@node
def test_blocks_draw_actual_cells(tmp_path):
    """그린 칸 수 = actual(+ 목표까지 남은 빈 칸), 목표를 넘은 마지막 over 칸만 진한 색."""
    body = """
const svg = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids });  // DOM 대신 모양만
const draw = (v, target, size) => { const rects = paintBlocks({ title: "t", view: { size, target: { value: target } }, data: { values: { scalar: [v] } } },{ series: ["#336699"], line: "#ccc", ink: "#000", text: "#333" }).kids.filter(k => k.tag === "rect"); return { n: rects.length, dark: rects.filter(r => r.attrs.fill === darken("#336699")).length, empty: rects.filter(r => r.attrs.fill === "none").length }; };
const cases = [[30, 20, 1], [95, 80, 10], [45, 80, 10], [30, null, 1]].map(([v, t, s]) => ({ cells: blockCells(v, t, s), drawn: draw(v, t, s) }));
console.log(JSON.stringify(cases));
"""
    got = json.loads(run_js(tmp_path, body))
    assert got[0]["cells"]["actual"] == 30 and got[0]["drawn"] == {"n": 30, "dark": 10, "empty": 0}  # 예전엔 40칸
    assert got[1]["drawn"] == {"n": 9, "dark": 1, "empty": 0}
    assert got[2]["drawn"] == {"n": 8, "dark": 0, "empty": 4}  # 4칸 채움 + 목표까지 빈 칸 4
    assert got[3]["drawn"] == {"n": 30, "dark": 0, "empty": 0}


SPACED = ["주문 일자 2026-10-01 ~ 2026-10-31"]


def test_own_range_spaced_column():
    got = dash_calc.own_range({"source": {"body": {"periods": SPACED}}})
    assert got and got["column"] == "주문 일자" and got["range"] == {"start": "2026-10-01", "end": "2026-10-31"} and got["prev"]["text"] == "지난달"  # 달에 꼭 맞는 기간은 대시보드 기간 칸처럼 지난달(24·25차 감사)
    assert dash_calc.own_range({"source": {"body": {"periods": ["day 2026-01-01 00:00 ~ 2026-01-02"]}}}) is None  # 시각이 붙은 줄은 여전히 아님


@node
def test_own_range_spaced_column_js(tmp_path):
    got = json.loads(run_js(tmp_path, "console.log(JSON.stringify(ownRange({ source: { body: { periods: %s } } })));\n" % json.dumps(SPACED, ensure_ascii=False)))
    want = dash_calc.own_range({"source": {"body": {"periods": SPACED}}})
    assert got == {"range": want["range"], "prev": want["prev"]}


@node
def test_state_rule_spaced_column(tmp_path):
    body = 'console.log(JSON.stringify([parseStateLine("cpu 사용률 >= 90 이상"), parseStateLine("상태 = DOWN 주의"), parseStateLine("cpu>=90 이상")]));\n'
    got = json.loads(run_js(tmp_path, body))
    assert got[0]["rule"] == {"column": "cpu 사용률", "op": ">=", "value": 90, "level": "bad"}
    assert got[1]["rule"] == {"column": "상태", "op": "=", "value": "DOWN", "level": "warn"} and got[2]["rule"]["column"] == "cpu"


def test_live_min_hint_says_default():
    text = (ROOT / "static" / "dashboard" / "dash_ctl.js").read_text(encoding="utf-8")
    assert "0이면 제한 없음" not in text and "원격 연결 60초" in text
