"""감사 21의 대시보드 고침: 공백 있는 날짜 컬럼의 카드 기간 줄(own_range·web/lib/calc.ts ownRange), 공백 있는 컬럼의 상태 규칙, 실시간 최소 안내 글.
(블록 카드의 칸 수는 test_dash_js의 blockCells로 본다. 옛 화면의 그리기 시험은 옛 화면과 함께 지웠다.)"""
import json
import shutil

import pytest

from conftest import ROOT
from dashboard import dash_calc
from test_dash_js import run_ts

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


SPACED = ["주문 일자 2026-10-01 ~ 2026-10-31"]


def test_own_range_spaced_column():
    got = dash_calc.own_range({"source": {"body": {"periods": SPACED}}})
    assert got and got["column"] == "주문 일자" and got["range"] == {"start": "2026-10-01", "end": "2026-10-31"} and got["prev"]["text"] == "지난달"  # 달에 꼭 맞는 기간은 대시보드 기간 칸처럼 지난달(24·25차 감사)
    assert dash_calc.own_range({"source": {"body": {"periods": ["day 2026-01-01 00:00 ~ 2026-01-02"]}}}) is None  # 시각이 붙은 줄은 여전히 아님


@node
def test_own_range_spaced_column_js(tmp_path):
    got = json.loads(run_ts(tmp_path, "console.log(JSON.stringify(ownRange({ source: { body: { periods: %s } } })));\n" % json.dumps(SPACED, ensure_ascii=False)))
    want = dash_calc.own_range({"source": {"body": {"periods": SPACED}}})
    assert got == {"range": want["range"], "prev": want["prev"]}


@node
def test_state_rule_spaced_column(tmp_path):
    body = 'console.log(JSON.stringify([parseStateLine("cpu 사용률 >= 90 이상"), parseStateLine("상태 = DOWN 주의"), parseStateLine("cpu>=90 이상")]));\n'
    got = json.loads(run_ts(tmp_path, body))
    assert got[0]["rule"] == {"column": "cpu 사용률", "op": ">=", "value": 90, "level": "bad"}
    assert got[1]["rule"] == {"column": "상태", "op": "=", "value": "DOWN", "level": "warn"} and got[2]["rule"]["column"] == "cpu"


def test_live_min_hint_says_default():
    text = (ROOT / "web" / "components" / "shell" / "PeriodBar.tsx").read_text(encoding="utf-8")  # 대시보드 화면(Next)의 기간 줄
    assert "0이면 제한 없음" not in text and "원격 연결 60초" in text
