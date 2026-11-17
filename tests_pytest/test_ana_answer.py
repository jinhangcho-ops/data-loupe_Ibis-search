"""분석 화면의 한 문장 답(analysis.js anaAnswer)과 고른 분석 종류의 강조.
화면 스크립트는 해당 함수와 그것이 쓰는 한 줄 함수만 잘라 node로 돌린다. 값은 시험 안에서 지어낸다."""
import re
import shutil

import pytest

from test_fix_audit_js import func, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def const(page: str, name: str) -> str:
    """page에서 `const name = …` 한 줄."""
    return re.search(rf"^const {name} = .*$", read(page), re.M)[0].split("  //")[0].rstrip() + "\n"


def answers(tmp_path, cases: str) -> list:
    from analysis import stats
    parts = [f"const SERVER = {{ analysis: {{ additive: {list(stats.ADDITIVE)!r} }} }};\n", const("common.js", "subject"), const("common.js", "UNIT_NAMES"), const("analysis.js", "PCT"), const("analysis.js", "pctText"), const("prep.html", "fmt"), func("analysis.js", "anaAnswer")]
    return run_node(tmp_path, f"console.log(JSON.stringify([{cases}].map(([d, view]) => anaAnswer(d, view))));", parts=parts)


@needs_node
def test_groups_answer(tmp_path):
    got = answers(tmp_path, """
[{ agg: "sum", labels: ["가전", "가구", "식품"], values: [410, 390, 200], more: false }, "groups"],
[{ agg: "count", labels: ["서울", "부산"], values: [3, 1], more: false }, "groups"],
[{ agg: "mean", labels: ["가구", "가전"], values: [1234.5, 20], more: false }, "groups"],
[{ agg: "sum", labels: ["가전", "가구"], values: [410, 390], more: true }, "groups"],
[{ agg: "sum", labels: ["가전", "환불"], values: [100, -50], more: false }, "groups"],
[{ agg: "sum", labels: ["a", "<b>x</b>"], values: [null, 5], more: false }, "groups"],
[{ agg: "sum", labels: ["a"], values: [null], more: false }, "groups"]""")
    assert got[0] == ["가전이 전체의 ", "41.0%", "로 가장 커요."]
    assert got[1] == ["서울이 전체의 ", "75.0%", "로 가장 커요."]
    assert got[2] == ["가구가 가장 커요(", "1,234.5", ")."]  # 평균은 비중이 뜻이 없다
    assert got[3] == ["보이는 묶음 중 가전이 가장 커요(", "410", ")."]  # 묶음이 잘리면 전체 합을 모른다
    assert got[4] == ["가전이 가장 커요(", "100", ")."]  # 음수가 섞이면 비중을 내지 않는다
    assert got[5][0].startswith("<b>x</b>") and got[5][1] == "100.0%"  # 빈 값은 건너뛴다. 이름은 글자 그대로(화면이 textContent로 넣는다)
    assert got[6] == []


@needs_node
def test_trend_answer(tmp_path):
    got = answers(tmp_path, """
[{ unit: "month", values: [100, 80, 150], more: false }, "trend"],
[{ unit: "week", values: [200, 150], more: false }, "trend"],
[{ unit: "day", values: [10, 30], more: true }, "trend"],
[{ unit: "day", values: [0, 30], more: false }, "trend"],
[{ unit: "day", values: [null, 30], more: false }, "trend"],
[{ unit: "day", values: [10, null], more: false }, "trend"],
[{ unit: "day", values: [10], more: false }, "trend"],
[{ unit: "day", values: [10, 5, 10], more: false }, "trend"],
[{ values: { labels: ["a"], counts: [1] } }, "values"]""")
    assert got[0] == ["첫 월보다 마지막 월이 ", "50.0%", " 늘었어요."]
    assert got[1] == ["첫 주보다 마지막 주가 ", "25.0%", " 줄었어요."]
    assert got[2] == ["보이는 기간 중 첫 일보다 마지막 일이 ", "200.0%", " 늘었어요."]
    assert got[3:] == [[], [], [], [], [], []]  # 첫 값 0·빈 값, 마지막 빈 값, 기간 하나, 변화 없음, 다른 분석


def test_answer_is_drawn_as_text_and_kind_is_marked():
    html, draw = read("prep.html"), func("analysis.js", "drawAna")
    assert html.index('id="a-title"') < html.index('<div class="big a-answer" id="a-answer"></div>') < html.index('id="a-box"')  # 제목 아래, 그래프 위
    assert 'el("b", { textContent: part })' in draw and "innerHTML" not in draw[draw.index('$("a-answer")'):draw.index("\n", draw.index('$("a-answer")'))]
    assert ".big.a-answer b { font-size:inherit; color:var(--accent); }" in html and ".big.a-answer:empty { display:none; }" in html
    assert ".akinds label:has(input:checked) { border-color:var(--accent); background:var(--accent-bg); }" in html
