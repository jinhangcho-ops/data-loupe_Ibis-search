"""32차 리뷰·감사: 계산 줄의 '그룹'·'기준'·'이름' 컬럼, 공백 이름 창 함수, 목표 2026.1, 비율 직전 0, 0.01% 미만, 복사 따옴표 닫기, 웹훅 확인 https, 문장 → 카드 정렬·셋째 컬럼."""
import json
import shutil

import pytest
from lazy import ibis

from core import calc, compare, targets
from integrations.adapters import webhook
from metrics import my_metrics
from query import sentence_run as sr

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_column_named_like_option_word():
    t = ibis.memtable({"일자": [1, 2, 3], "금액": [1.0, 2.0, 3.0], "그룹": ["x", "x", "y"]})
    got = calc.apply(["cum_sum 금액 기준 일자 그룹 그룹"], t).order_by("일자").to_pyarrow().column(-1).to_pylist()
    assert got == [1.0, 3.0, 3.0]  # 그룹별 누적(전에는 전체 누적 1,3,6)


def test_spaced_names_in_old_window_and_next_line():
    t = ibis.memtable({"주문 일": [1, 2, 3], "판매 금액": [1.0, 2.0, 3.0]})
    assert calc.apply(["누적합 판매 금액 기준 주문 일"], t).count().to_pyarrow().as_py() == 3
    u = ibis.memtable({"d": [1, 2, 3], "amt": [10.0, 20.0, 30.0]})
    out = calc.apply(["dense_rank 기준 amt 이름 내 순위", "cum_sum 내 순위 기준 d"], u)
    assert out.count().to_pyarrow().as_py() == 3


def test_ambiguous_target_month_refused():
    with pytest.raises(ValueError, match="10월"):
        targets.make_row("매출", 100, "2026.1", "", "", 1)
    assert targets.make_row("매출", 100, "2026.3", "", "", 1)["period"] == "2026-03"


def test_percent_from_zero_and_tiny_rate():
    assert compare.compare(0.00001, 0, "percent")["direction"] == "flat"
    assert compare.percent_text(1e-9) == "0.01% 미만"


def test_rename_keeps_quoted_value_with_apostrophe():
    assert my_metrics.rename_in("memo = 'it's date' and date > 1", {"date": "dt"}) == "memo = 'it's date' and dt > 1"


def test_webhook_check_refuses_http():
    with pytest.raises(ValueError, match="https"):
        webhook.check({"url": "http://example.test/h", "content": "text", "template": "{표}"})


@node
def test_sentence_card_keeps_desc_order_and_refuses_three_columns(tmp_path):
    from test_fix_audit_js import between, run_node
    cols = {"store": "text", "city": "text", "kind": "text", "amt": "num"}
    base = {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}
    compiled = {"three": sr.compile("e02", {"컬럼들": ["store", "city", "kind"], "중복": "빼고"}, base, cols),
                "desc": sr.compile("e10", {"조건": "amt > 1", "무엇": "store"}, base, cols)}
    code = between("sentences.js", "function sentCard", "// ---------- 화면")
    body = f"const c = {json.dumps(compiled, ensure_ascii=False)};\nconsole.log(JSON.stringify(Object.fromEntries(Object.entries(c).map(([k, v]) => [k, sentCard(v, 't')]))));"
    got = run_node(tmp_path, body, parts=[code])
    assert "reason" in got["three"], got["three"]
    assert got["desc"].get("body", {}).get("order") == "value", got["desc"]


@pytest.mark.parametrize("line, name, want", [
    ("식 이름 이름 복사", "복사", ["b", "a", "c"]),
    ("lag 이름 기준 일자", "이름_lag", [None, "b", "a"]),
    ("누적합 기준 기준 일자", "기준_누적합", [5.0, 11.0, 18.0]),
    ("순위 기준 금액 desc 그룹 그룹", "금액_순위", [2, 1, 1]),  # 순위는 본문을 받지 않아 첫 '기준'은 칸 낱말
])
def test_first_word_that_names_a_column(line, name, want):
    t = ibis.memtable({"일자": [1, 2, 3], "이름": ["b", "a", "c"], "그룹": ["x", "x", "y"], "기준": [5.0, 6.0, 7.0], "금액": [1.0, 2.0, 3.0]})
    out = calc.apply([line], t).order_by("일자").to_pyarrow()
    assert out.column(name).to_pylist() == want
