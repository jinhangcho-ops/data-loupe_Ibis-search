"""전처리 ④ 새 컬럼 화면 정리(Phase 20): 계산 컬럼의 정렬 기준·묶을 컬럼을 찾아서 토글로 고르기, 접어 둔 식 설명,
쓸 때만 펼치는 기능 목록(PCA·K-Means·좌표)과 계산 기준 글, 분석 종류 카드의 이름·한 줄 설명.
prep.html의 해당 구간만 잘라 가짜 화면 위에서 node로 돌린다(test_fix_audit_js의 방식). 고치기 전 코드에는 이 구간·마크업이 없어 모두 실패한다."""
import re
import shutil
from pathlib import Path

import pytest

from test_fix_audit_js import between, read, run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

PAGE = "prep.html"
ANALYSIS = "analysis.js"
FEATURES = ("pca", "kmeans", "geo")
OLD_QUESTIONS = ("값이 어떻게 퍼져 있나요?", "두 숫자가 같이 움직이나요?", "묶음마다 값이 얼마나 다른가요?", "기간마다 어떻게 바뀌었나요?", "단계마다 얼마나 남나요?",
                 "다시 오는 고객은 얼마나?", "좋은 고객은 누구?", "적은 항목이 대부분을 차지하나요?", "두 항목이 서로 관련 있나요?", "A와 B의 차이가 우연인가요?",
                 "같이 사는 상품은?", "평균이 왜 바뀌었나요?", "반복되는 흐름과 튀는 때는?")
KIND_NAMES = {"distribution": "분포", "correlation": "상관관계", "groups": "그룹 비교", "trend": "기간별 추이", "funnel": "퍼널(단계별 통과)",
              "retention": "리텐션(다시 나타남)", "rfm": "RFM(최근·빈도·크기 등급)", "pareto": "파레토(ABC 등급)", "chisq": "관계 검정(카이제곱)",
              "abtest": "A/B 검정(그룹 차이)", "basket": "연관 항목(장바구니)", "decompose": "변화 요인", "seasonal": "시계열 분해"}
KIND_NEED = "필요한 컬럼: "
FAKE_SERVER = {"pca_max": 7, "kmeans": {"max_cols": 8, "max_k": 9, "k": 4, "iters": 13, "tol": 0.5, "init_rows": 1234}}  # 서버 기본값과 다른 숫자: 화면이 숫자를 따로 적지 않았는지 본다

# 계산 컬럼 칸의 대역: id마다 가짜 요소 하나, 컬럼 다섯 개(대소문자·한글), [추가]가 부르는 것들
PICKER_STUBS = r"""
const els = {}; const $ = id => els[id] ??= mk("input", { value: "", id });
const chip = (text, remove) => mk("span", { className: "chip" }, text, mk("button", { textContent: "×", onclick: remove }));
let prof = { columns: ["Date", "order_date", "amount", "date", "지역"].map(name => ({ name })) }, deriv = [], formulas = [], preview = null, failed = "";
const cols = () => prof.columns, fail = m => { failed = m; }, showAll = () => fxPickers.forEach(p => p.show());
const key = (id, e) => { const ev = { prevented: 0, stopped: 0, preventDefault() { this.prevented++; }, stopPropagation() { this.stopped++; }, ...e }; $(id).onkeydown(ev); return ev; };
const texts = id => $(id).children.map(c => c.textContent);
const pressed = id => $(id).children.filter(c => c.attrs["aria-pressed"] === "true").map(c => c.textContent);
const press = (id, name) => $(id).children.find(c => c.textContent === name).onclick();
"""

# 기능 목록의 대역: 단추 셋과 설정 칸 셋(hidden 클래스를 실제로 기억한다)
FEATURE_STUBS = r"""
const box = () => { const set = new Set(["hidden"]); return { classList: { toggle(c, on) { on ? set.add(c) : set.delete(c); }, contains: c => set.has(c) } }; };
const els = Object.fromEntries(FEATURES.map(k => ["dv-" + k, box()])); const $ = id => els[id];
const buttons = FEATURES.map(k => { const used = { textContent: "" }, b = mk("button", { dataset: { feat: k } }); b.querySelector = () => used; b.used = used; return b; });
document.querySelectorAll = () => buttons;
let pca = { columns: [], n: 2 }, kmeans = { columns: [] }, geoSteps = [];
const state = () => Object.fromEntries(buttons.map(b => [b.dataset.feat, [!$("dv-" + b.dataset.feat).classList.contains("hidden"), b.attrs["aria-expanded"], b.used.textContent]]));
"""


def picker_parts() -> list[str]:
    """정렬 기준·묶을 컬럼 고르기와 [추가] 단추의 코드."""
    return [PICKER_STUBS, between(PAGE, "const fxCols = ", "// ④ 쓸 수 있는 기능"), between(PAGE, '$("fx-add").onclick', '$("fx-expr").onkeydown')]


def feature_parts() -> list[str]:
    """기능 목록을 펴고 접는 코드."""
    return [f"const FEATURES = {list(FEATURES)};", FEATURE_STUBS, between(PAGE, "const DV_USED = ", "// PCA·K-Means가 어떤 기준")]


def derive_step() -> str:
    """④ 새 컬럼 단계의 마크업."""
    return between(PAGE, 'data-step="derive"', 'data-step="dedupe"')


def test_find_matches_same_and_containing_names_ignoring_case(tmp_path: Path) -> None:
    got = run_node(tmp_path, """
const before = texts("fx-order-list");
$("fx-order-find").value = "date"; const ev = key("fx-order-find", { key: "Enter" });
const some = texts("fx-order-list");
$("fx-order-find").value = " DATE "; key("fx-order-find", { key: "Enter" }); const upper = texts("fx-order-list");
$("fx-order-find").value = "없는이름"; key("fx-order-find", { key: "Enter" }); const none = texts("fx-order-list");
$("fx-order-find").value = ""; key("fx-order-find", { key: "Enter" });
console.log(JSON.stringify({ before, some, upper, none, all: texts("fx-order-list"), prevented: ev.prevented, stopped: ev.stopped, open: $("fx-order-open").attrs["aria-expanded"] }));
""", parts=picker_parts())
    assert got["before"] == []  # Enter 전에는 목록이 없다
    assert got["some"] == ["Date", "date", "order_date"] == got["upper"]  # 같은 이름이 먼저, 그다음 글자가 든 이름
    assert got["none"] == [] and got["all"] == ["Date", "order_date", "amount", "date", "지역"]  # 빈 칸이면 모든 컬럼
    assert got["prevented"] == 1 and got["stopped"] == 1 and got["open"] == "true"  # Enter가 다른 단추로 번지지 않는다


def test_composing_enter_is_left_alone(tmp_path: Path) -> None:
    got = run_node(tmp_path, """
$("fx-group-find").value = "지";
const ev = key("fx-group-find", { key: "Enter", isComposing: true });
console.log(JSON.stringify({ list: texts("fx-group-list"), prevented: ev.prevented, stopped: ev.stopped }));
""", parts=picker_parts())
    assert got == {"list": [], "prevented": 0, "stopped": 0}


def test_toggle_picks_and_unpicks_and_keeps_chips_when_closed(tmp_path: Path) -> None:
    got = run_node(tmp_path, """
key("fx-order-find", { key: "Enter" });
press("fx-order-list", "date"); press("fx-order-list", "지역");
const two = { value: $("fx-order").value, pressed: pressed("fx-order-list"), all: $("fx-order-list").children.every(c => c.tag === "button" && "aria-pressed" in c.attrs) };
$("fx-order-chips").children[0].children[1].onclick();  // 칩의 오름·내림 단추
const desc = $("fx-order").value;
$("fx-order-open").onclick();  // 목록 접기
const closed = { list: texts("fx-order-list"), chips: $("fx-order-chips").children.map(c => c.children[0]), open: $("fx-order-open").attrs["aria-expanded"] };
key("fx-order-find", { key: "Enter" }); press("fx-order-list", "date");
const one = { value: $("fx-order").value, pressed: pressed("fx-order-list") };
key("fx-group-find", { key: "Enter" }); press("fx-group-list", "amount"); press("fx-group-list", "지역"); press("fx-group-list", "지역"); press("fx-group-list", "Date");
console.log(JSON.stringify({ two, desc, closed, one, group: $("fx-group").value }));
""", parts=picker_parts())
    assert got["two"] == {"value": "date; 지역", "pressed": ["date", "지역"], "all": True}
    assert got["desc"] == "date desc; 지역"
    assert got["closed"] == {"list": [], "chips": ["date", "지역"], "open": "false"}  # 목록을 닫아도 고른 것은 칩으로 남는다
    assert got["one"] == {"value": "지역", "pressed": ["지역"]}  # 다시 누르면 빠진다
    assert got["group"] == "amount, Date"


def test_added_formula_keeps_the_old_shape(tmp_path: Path) -> None:
    got = run_node(tmp_path, """
$("fx-order").value = "date desc; 지역"; $("fx-group").value = "amount, Date";  // 예전에 손으로 적던 글자 그대로
fxPickers.forEach(p => p.show());
const chips = [$("fx-order-chips").children.length, $("fx-group-chips").children.length];
$("fx-name").value = "순위"; $("fx-expr").value = "rank()";
$("fx-add").onclick();
$("fx-name").value = "배"; $("fx-expr").value = "amount * 2";
$("fx-add").onclick();
console.log(JSON.stringify({ chips, formulas, left: [$("fx-order").value, $("fx-group").value, $("fx-order-chips").children.length, $("fx-group-chips").children.length], failed }));
""", parts=picker_parts())
    assert got["chips"] == [2, 2]
    assert got["formulas"] == [{"name": "순위", "expr": "rank()", "order": ["date desc", "지역"], "group": ["amount", "Date"]}, {"name": "배", "expr": "amount * 2"}]
    assert got["left"] == ["", "", 0, 0] and got["failed"] == ""  # 더한 뒤에는 고른 것이 비워진다


def test_formula_fields_markup() -> None:
    step = derive_step()
    assert '<input id="fx-order" type="hidden"><input id="fx-group" type="hidden">' in step  # 보내는 값을 두는 칸은 그대로(화면에서만 숨김)
    for key in ("order", "group"):
        assert re.search(rf'<input id="fx-{key}-find" [^>]*aria-label="[^"]+"', step), key
        assert f'<div class="kinds cols" id="fx-{key}-list"></div>' in step and f'id="fx-{key}-chips"' in step  # .cols: 정해진 높이 안에서 스크롤
    assert 'id="fx-find"' in step
    assert '.kinds button[aria-pressed="true"]' in read("common.css")
    assert "onclick=" not in step and "onkeydown=" not in step  # CSP: 인라인 핸들러 없음


def test_formula_guide_is_folded() -> None:
    step = derive_step()
    assert '<details class="hint" id="fx-help"><summary>사칙연산과 함수를 쓸 수 있어요. 예: 금액 * 수량</summary><div id="fx-guide"></div></details>' in step
    assert '$("fx-guide").textContent = SERVER.formula_guide;' in read(PAGE)  # 문단의 내용은 그대로 서버 글


def test_features_start_folded_and_open_on_click(tmp_path: Path) -> None:
    step = derive_step()
    for key in FEATURES:
        assert re.search(rf'<button type="button" class="fpanel-item" data-feat="{key}" aria-expanded="false" aria-controls="dv-{key}">', step), key
        assert f'<div id="dv-{key}" class="hidden">' in step
    for inner, key in (("pca-cols", "pca"), ("km-cols", "kmeans"), ("geo-form", "geo")):
        assert step.index(f'id="dv-{key}"') < step.index(f'id="{inner}"')
    got = run_node(tmp_path, """
showFeatures(); const first = state();
buttons[0].onclick(); const opened = state();
buttons[0].onclick(); const folded = state();
console.log(JSON.stringify({ first, opened, folded }));
""", parts=feature_parts())
    assert got["first"] == {k: [False, "false", ""] for k in FEATURES} == got["folded"]
    assert got["opened"] == {"pca": [True, "true", ""], "kmeans": [False, "false", ""], "geo": [False, "false", ""]}


def test_features_in_use_stay_open(tmp_path: Path) -> None:
    got = run_node(tmp_path, """
kmeans = { columns: ["a", "b"] }; geoSteps = [{ op: "tm" }];  // 저장한 구성·초안을 되살린 뒤
showFeatures(); const first = state();
buttons[1].onclick(); const clicked = state();  // 쓰는 중이면 눌러도 접지 않는다
console.log(JSON.stringify({ first, clicked }));
""", parts=feature_parts())
    assert got["first"] == {"pca": [False, "false", ""], "kmeans": [True, "true", "쓰는 중"], "geo": [True, "true", "쓰는 중"]} == got["clicked"]
    assert "showFeatures();" in between(PAGE, "function showPrepSummaries() {", "\n}")  # 요약을 다시 그릴 때(되살리기 포함) 함께
    assert "dvOpen.clear();" in between(PAGE, "function resetTable() {", "\n}")


def test_pca_and_kmeans_show_their_basis(tmp_path: Path) -> None:
    step, code = derive_step(), between(PAGE, '$("pca-basis").replaceChildren', '$("fx-add").onclick')
    assert step.index('id="dv-pca"') < step.index('id="pca-basis"') < step.index('id="pca-cols"')  # 컬럼을 고르기 전에 기준부터
    assert step.index('id="dv-kmeans"') < step.index('id="km-basis"') < step.index('id="km-cols"')
    got = run_node(tmp_path, """
console.log(JSON.stringify({ pca: $("pca-basis").children.map(c => c.textContent), km: $("km-basis").children.map(c => c.textContent) }));
""", parts=[f"globalThis.SERVER = {FAKE_SERVER};".replace("'", '"'), 'const els = {}; const $ = id => els[id] ??= mk("div");', code])
    pca, km = " ".join(got["pca"]), " ".join(got["km"])
    for word in ("표준화", "빈 값", "상관 행렬", "고유값 분해", "2~7개"):
        assert word in pca, word
    for word in ("표준화", "유클리드", "k-means++", "씨앗", "반복", "빈 값", "2~8개", "2~9(기본 4)", "앞 1,234행", "0.5보다 덜", "13번"):
        assert word in km, word


def test_unnest_is_the_last_screen_op() -> None:
    ops = between(PAGE, "const OPS = {", "\n};")
    assert ops.index("\n  expand: {") < ops.index("\n  unnest: {") and ops.rstrip().endswith("value: () => true },")
    assert 'unnest: { label: "목록을 행으로 펼치기", group: "새 컬럼", need: c => /^(array|json)/.test(c.type) || isStr(c), init: {}, load: () => ({}),' in ops
    assert 'fields: () => [{ hint: "값 하나마다 행 하나로 늘어나요. 다른 컬럼 값은 복사돼요." }]' in ops


def test_analysis_cards_have_name_line_and_needed_columns() -> None:
    page = read(PAGE)
    cards = re.findall(r'<label data-hint title="([^"]+)"><input type="radio" name="akind" value="(\w+)"(?: checked)?> <span>([^<]+)<small>([^<]+)</small></span></label>', page)
    assert {value: name for _, value, name, _ in cards} == KIND_NAMES and [value for _, value, _, _ in cards] == list(KIND_NAMES)
    for title, value, _, line in cards:
        assert title.startswith(KIND_NEED) and "예: " in title, value
        assert line.strip() and "?" not in line, value
    for text in (page, read(ANALYSIS)):
        assert not [q for q in OLD_QUESTIONS if q in text]
