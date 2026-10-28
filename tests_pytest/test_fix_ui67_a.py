"""문서 그림을 찍다 찾은 대시보드 문제(목표 추이·문장·퍼널 색·KPI 숫자·빈 색)를 node로 돌려 본다. node가 없으면 건너뛴다.
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다."""
from test_fix_audit_js import ROOT, pytestmark, run_node  # noqa: F401 (pytestmark: node가 없으면 건너뜀)

COLORS = 'const colors = { series: ["#111", "#222"], line: "#ccc", ink: "#000", text: "#555", card: "#fff" };\n'
TEXTS = 'const texts = n => { const out = []; (function walk(x) { if (typeof x === "string") return; if (x.tag === "text") out.push(x.textContent); (x.children ?? []).forEach(walk); })(n); return out; };\n'


def test_target_trend_compares_day_average_for_sums(tmp_path):
    # 30일 합계(3,000)를 하루 목표(500)와 견주면 "2,500 높아요"가 된다. 선과 점선은 일 값이므로 일 평균(100)과 견준다
    body = COLORS + TEXTS + """
const labels = Array.from({ length: 30 }, (_, i) => `2026-01-${String(i + 1).padStart(2, "0")}`);
const day = { labels, series: [{ name: "합계 (금액)", agg: "sum", sum: labels.map(() => 100), count: labels.map(() => 1) }] };
const card = { title: "t", range: { start: labels[0], end: labels[29] }, view: {}, data: { by_unit: { day }, target: { value: 500 } } };
console.log(JSON.stringify(texts(paintTargetTrend(card, colors))));
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js", "dash_cards.js"])
    assert any(t.startswith("일 평균이 목표보다 400 낮아요") for t in got), got


def test_sentence_delta_after_label_says_comparison_once(tmp_path):
    body = """
document.createTextNode = t => t;
const card = { view: { template: "직전 기간보다 {비교}", better: "up" }, data: { values: { blanks: { 비교: { kind: "delta", cur: 1, prev: 2, format: "number", text: "직전 30일보다 11.4% 줄었어요 (−1)" } } } } };
const all = { view: { template: "{비교}", better: "up" }, data: card.data };
console.log(JSON.stringify([paintSentence(card, {}).textContent, paintSentence(all, {}).textContent]));
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js", "dash_rules.js", "dash_cards.js"])
    assert got == ["직전 기간보다 11.4% 줄었어요 (−1)", "직전 30일보다 11.4% 줄었어요 (−1)"]


def test_funnel_color_names(tmp_path):
    got = run_node(tmp_path, 'console.log(JSON.stringify(colorNames({ kind: "funnel", view: {}, data: { values: { steps: [] } } })));', files=["board.js", "dash.js"])
    assert got == {"group": "series", "names": ["단계 막대", "다음 단계", "이탈"]}  # paintFunnel이 읽는 이름


def test_kpi_value_shrinks_to_fit():
    html = (ROOT / "static/dashboard/dashboard.html").read_text(encoding="utf-8")
    cards = (ROOT / "static/dashboard/dash_cards.js").read_text(encoding="utf-8")
    assert "container-type:inline-size" in html and "--n" in html and "--n:" in cards  # 카드 폭과 글자 수로 글자 크기를 줄인다(…로 잘리지 않게)


def test_empty_colors_do_not_crash(tmp_path):
    body = COLORS + """
const card = { kind: "kpi", colors: {}, view: {}, title: "t", data: { values: { scalar: [5], names: ["합계 (금액)"] } } };
const a = pickColor(card, "series", "합계 (금액)", colors, 0), b = pickColor(card, "parts", "A", colors, 1);
console.log(JSON.stringify([a, b]));
"""
    assert run_node(tmp_path, body, files=["board.js", "dash.js"]) == ["#111", "#222"]
