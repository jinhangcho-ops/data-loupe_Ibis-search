"""R4-A(F10): 쿼리 화면 작업 탭. index.html의 "작업 탭" 구간을 잘라 가짜 화면 위 node로 돌린다(node가 없으면 건너뛴다).
되돌리기 기록은 진짜 draft.js의 Undo(dump·load)를 쓴다. 시험 데이터는 여기서 만든다."""
import shutil

import pytest

from test_fix_audit_js import between, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
PAGE = "index.html"
BLOCK = between(PAGE, "// ---------- 작업 탭(F10)", "// ---------- 작업 탭 끝")

# 탐색 화면의 가짜: S·결과 변수와 그것을 비우고(clearExplore) 채우는(restoreSearch) 함수만 흉내 낸다.
STUB = r"""
const els = {};
globalThis.$ = id => (els[id] ??= Object.assign(mk("div"), { value: "", disabled: false, focus() {}, querySelectorAll: () => [] }));
document.querySelector = () => null;
globalThis.MutationObserver = class { observe() {} };
globalThis.Option = class { constructor(text, value) { this.text = text; this.value = value; } };
const toggleDrop = () => {}, flashed = [], toasts = [], searchCalls = [];
const flash = t => flashed.push(t), undoToast = (t, f) => toasts.push([t, f]);
const S = { base: "", conditions: [] };
let last = null, searched = null, searchedTexts = null, knownCount = null, skip = 0, drawn = null, series = [], scols = [], view = "cards", sentView = null;
const searchState = () => S.base ? { view: "cards", body: { base: S.base, conditions: [...S.conditions] } } : null;
const searchUndo = Undo(() => searchState(), async d => restoreSearch(d), { poll: 0, quiet: 0 });
function clearExplore() { S.base = ""; S.conditions = []; last = drawn = searched = null; searchUndo.reset(); }
async function restoreSearch(d) { if (d.body.base === "gone") throw new Error("없는 테이블"); S.base = d.body.base; S.conditions = [...d.body.conditions]; }
const showUnit = () => {}, showSeries = () => {}, fillSummaryCols = () => {}, mountTable = () => {}, gridSpec = () => ({}), chartData = () => ({});
async function renderJoins() {}
const renderTables = () => {}, showBig = () => {}, showTab = () => {}, inExplore = () => true, showView = () => {};
let apiHook = async () => ({});
const busy = (b, f) => f(), api = (...a) => apiHook(...a), timed = (n, f) => f(), conditionTexts = () => [], query = () => ({}), confirmBox = async () => true, searches = {};
async function runSearch(offset, fresh, page) { searchCalls.push([offset, page]); last = { rows: { rows: [1] }, summary: null, count: 5000 }; return true; }
"""


RUNSEARCH = between(PAGE, "function runSearch(", '$("search").onclick')  # 진짜 runSearch(STUB의 가짜를 덮는다)


def run(tmp_path, body, real_search=False):
    return run_node(tmp_path, body, files=["draft.js"], parts=[STUB, *([RUNSEARCH] if real_search else []), BLOCK])


@needs_node
def test_switching_tabs_keeps_each_tab_state_and_undo(tmp_path):
    body = r"""
(async () => {
  S.base = "orders"; searchUndo.tick(); S.conditions = ["amt > 1"]; searchUndo.tick();  // 탭 1: 조건 하나 → 되돌리기 기록 1
  const rows1 = { columns: ["a"], rows: [{ a: 1 }] };
  last = { rows: rows1, summary: null, count: 1 }; searched = { limit: "20" }; $("cx").value = "day";
  await addTab();                                           // 탭 2는 빈 화면
  const blank = [S.base, last, searchUndo.size()];
  S.base = "users"; last = { rows: { rows: [{ b: 2 }] }, count: 1 }; $("cx").value = "";
  await goTab(0);
  const back1 = [S.base, S.conditions, last.rows === rows1, $("cx").value, searchUndo.size()];
  await goTab(1);
  const back2 = [S.base, last.rows.rows[0].b, $("cx").value];
  showTabs();
  const names = $("wtab-list").children.map(w => [w.children[0].textContent, w.children[0].attrs["aria-selected"], w.children[1].attrs["aria-label"]]);
  console.log(JSON.stringify({ blank, back1, back2, names }));
})();
"""
    got = run(tmp_path, body)
    assert got["blank"] == ["", None, [0, 0]]
    assert got["back1"] == ["orders", ["amt > 1"], True, "day", [1, 0]]
    assert got["back2"] == ["users", 2, ""]
    assert got["names"] == [["orders", "false", "orders 탭 닫기"], ["users", "true", "users 탭 닫기"]]


@needs_node
def test_big_result_is_dropped_and_only_that_page_is_fetched_again(tmp_path):
    body = r"""
(async () => {
  S.base = "logs"; searched = { limit: "3000" }; skip = 3000; knownCount = 9000;
  last = { rows: { rows: Array(TAB_KEEP_ROWS + 1).fill(0) }, summary: { columns: [], rows: [] }, count: 9000 };
  await addTab();
  const kept = tabs[0].st.last.rows;
  await goTab(0);
  console.log(JSON.stringify({ kept, calls: searchCalls, summary: !!last.summary, known: knownCount }));
})();
"""
    got = run(tmp_path, body)
    assert got == {"kept": None, "calls": [[3000, True]], "summary": True, "known": 9000}


@needs_node
def test_add_close_and_max(tmp_path):
    body = r"""
(async () => {
  for (let i = 0; i < 10; i++) { S.base = "t" + i; await addTab(); }
  const max = [tabs.length, flashed.length];
  await goTab(2); S.base = "x";
  await closeTab(2);                                   // 지금 탭을 닫으면 그 자리로 당겨진 다음 탭(t3)으로
  const afterClose = [tabs.length, tabAt, S.base, toasts.at(-1)[0]];
  await toasts.at(-1)[1]();                            // 되돌리기: 닫은 탭이 같은 자리에 돌아온다
  const undone = [tabs.length, tabAt, S.base];
  while (tabs.length > 1) await closeTab(0);
  await closeTab(0);                                   // 마지막 탭을 닫으면 빈 탭 하나
  console.log(JSON.stringify({ max, afterClose, undone, end: [tabs.length, tabAt, S.base] }));
})();
"""
    got = run(tmp_path, body)
    assert got["max"] == [8, 3]
    assert got["afterClose"] == [7, 2, "t3", "'x' 탭을 닫았어요"]
    assert got["undone"] == [8, 2, "x"]
    assert got["end"] == [1, 0, ""]


@needs_node
def test_join_list_offers_other_tabs_and_renamed_tab(tmp_path):
    body = r"""
(async () => {
  S.base = "orders"; await addTab(); S.base = "users"; await addTab(); S.base = "orders";
  tabs[1].name = "고객"; tabs[1].auto = false;
  const sel = addTabGroup(mk("select"), [tabRef(0)]);  // 값은 탭의 등록 이름(@탭N: 탭 번호, 가공 결과)
  const group = sel.children[0];
  console.log(JSON.stringify({ label: group.label, items: group.children.map(o => [o.text, o.value]), refs: [tabRef(0), tabRef(1)], none: addTabGroup(mk("select"), [tabRef(0), tabRef(1)]).children.length }));
})();
"""
    got = run(tmp_path, body)
    assert got["refs"][0] != got["refs"][1] and all(r.startswith("@탭") for r in got["refs"])  # 같은 orders 탭이어도 번호로 다르다
    assert {k: got[k] for k in ("label", "items", "none")} == {"label": "다른 탭 (가공 결과)", "items": [["탭 고객 (가공 결과)", got["refs"][1]]], "none": 0}  # (R5 P17) 원본 테이블이 아니라 그 탭의 가공 결과


@needs_node
def test_draft_keeps_all_tabs_and_opens_them(tmp_path):
    body = r"""
(async () => {
  S.base = "orders"; S.conditions = ["a = 1"]; await addTab(); S.base = "users";
  const d = JSON.parse(JSON.stringify(tabsDraft()));
  tabs = [newTab()]; tabAt = 0; clearExplore();
  await openTabs(d);
  const opened = [tabs.length, tabAt, S.base];
  await goTab(0);
  console.log(JSON.stringify({ d, opened, first: [S.base, S.conditions] }));
})();
"""
    got = run(tmp_path, body)
    assert got["d"]["at"] == 1 and [t["search"]["body"]["base"] for t in got["d"]["tabs"]] == ["orders", "users"]
    assert got["opened"] == [2, 1, "users"]
    assert got["first"] == ["orders", ["a = 1"]]


def test_page_wires_tabs():
    page = read(PAGE)
    assert 'role="tablist"' in page and "addTabGroup(s, [S.base])" in page and 'addTabGroup($("u-add")' in page
    assert '["Ctrl+T", ' in page and '["Ctrl+W", ' in page
    assert "tabs.length > 1 ? tabsDraft()" in page


@needs_node
def test_late_search_response_is_dropped_after_tab_switch(tmp_path):
    body = r"""
(async () => {
  S.base = "orders"; $("limit").value = "20";
  let release; apiHook = () => new Promise(r => { release = r; });
  const pending = runSearch(0, true);                  // 탭 1의 느린 검색
  tabs.push(newTab()); await goTab(1);   // 검색 도중 탭 2로(탭 잠금이 없는 상태를 흉내)
  S.base = "users";
  release({ rows: { rows: [{ a: 1 }] }, count: 1 });
  const ok = await pending;
  console.log(JSON.stringify({ ok, last, searched, base: S.base, saved: tabs[0].st.last }));
})();
"""
    got = run(tmp_path, body, real_search=True)
    assert got["ok"] is False and got["last"] is None and got["searched"] is None
    assert got["base"] == "users" and got["saved"] is None


@needs_node
def test_tab_change_refused_while_searching(tmp_path):
    body = r"""
(async () => {
  S.base = "orders"; await addTab();
  const n = tabs.length, at = tabAt;
  $("search").disabled = true;
  await goTab(0); const added = await addTab(); await closeTab(0);
  const during = [tabs.length === n, tabAt === at, added, flashed.length];
  $("search").disabled = false; await goTab(0);
  console.log(JSON.stringify({ during, after: tabAt }));
})();
"""
    got = run(tmp_path, body)
    assert got["during"] == [True, True, False, 3] and got["after"] == 0
