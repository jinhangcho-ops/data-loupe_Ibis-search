"""U1(쿼리 화면 UX): 지우기 확인·되돌리기, 쪽 넘김의 count:false·known_count, 차트 x_unit·X 기본값, 테이블 고르면 자동 검색.
index.html에서 해당 구간만 잘라 node로 돌린다(node가 없으면 건너뛴다). 시험 데이터는 여기서 만든다."""
import shutil

import pytest

from test_fix_audit_js import between, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
PAGE = "index.html"


@needs_node
def test_delete_asks_first_then_offers_undo(tmp_path):
    code = between(PAGE, '$("sdel").onclick', "// 고르면 불러온다")
    stub = r"""
let tabs = [{}], tabAt = 0;  // 작업 탭(가짜 화면에는 탭 하나)
const els = {}; const $ = id => (els[id] ??= { value: id === "saved" ? "내 검색" : "", textContent: "" });
let searches = { "내 검색": { base: "t", limit: "20" } }; const calls = []; let answer = false, undo = null;
const busy = (b, f) => f();
const confirmBox = async (title, text, ok) => { calls.push(["ask", text]); return answer; };
const api = async (url, body) => { calls.push([url, body]); return { searches: {}, order: [] }; };
const showSearches = () => {}; const undoToast = (text, f) => { calls.push(["toast", text]); undo = f; };
"""
    body = r"""
(async () => {
  await $("sdel").onclick();                      // 아니오: 지우지 않는다
  const afterNo = calls.length;
  answer = true;
  await $("sdel").onclick();                      // 예: 지우고 되돌리기 알림
  await undo();                                   // 되돌리기: 저장했던 내용을 같은 이름으로 다시 저장
  console.log(JSON.stringify({ afterNo, calls }));
})();
"""
    got = run_node(tmp_path, body, parts=[stub, code])
    assert got["afterNo"] == 1 and got["calls"][0][0] == "ask" and "'내 검색'" in got["calls"][0][1]
    assert got["calls"][2] == ["/api/searches", {"name": "내 검색", "search": None}]
    assert got["calls"][4] == ["/api/searches", {"name": "내 검색", "search": {"base": "t", "limit": "20"}}]


@needs_node
def test_page_turn_sends_count_false_and_known_count(tmp_path):
    code = between(PAGE, "let searched = null", "// 결과 표의 머리글을")
    stub = r"""
let tabs = [{}], tabAt = 0;  // 작업 탭(가짜 화면에는 탭 하나)
const els = {}; const $ = id => (els[id] ??= { value: id === "limit" ? "2" : "", checked: id === "count", disabled: false, textContent: "", onclick: null });
const S = { base: "t" }; let sorts = ["a asc"], skip = 0, last = null; const sent = [];
const state = () => ({ base: "t", sorts: [...sorts] }); const query = offset => ({ ...state(), limit: $("limit").value, offset });
const busy = (b, f) => f(); const timed = (k, f) => f();
const api = async (url, body) => { sent.push(body); return { rows: { rows: [1, 2] }, count: body.count === false ? body.known_count ?? null : 57 }; };
const showBig = () => {}; const renderTables = () => {}; const conditionTexts = () => [];
"""
    body = r"""
(async () => {
  await $("search").onclick();    // 첫 검색: 센다
  await $("next").onclick();      // 쪽만 넘김: 다시 세지 않고 아는 값을 보낸다
  sorts = ["a desc"];
  await $("search").onclick();    // 정렬이 바뀐 새 검색은 다시 센다
  console.log(JSON.stringify(sent.map(b => [b.offset, b.count, b.known_count ?? null])));
})();
"""
    assert run_node(tmp_path, body, parts=[stub, code]) == [[0, True, None], [2, False, 57], [0, True, None]]


@needs_node
def test_chart_sends_x_unit_only_for_date_x_and_prefers_date_for_default_x(tmp_path):
    helpers = between(PAGE, "const xRank", "function redraw()")
    draw = between(PAGE, '$("draw").onclick', "// 차트에 그린 값의 표")
    stub = r"""
let tabs = [{}], tabAt = 0;  // 작업 탭(가짜 화면에는 탭 하나)
const els = {}; const $ = id => (els[id] ??= { value: "", classList: { toggle() {}, add() {}, remove() {} }, textContent: "" });
const S = { cols: [["id", "정수"], ["name", "문자"], ["day", "날짜"]] };
const sent = []; let series = [], drawn = null;
const busy = (b, f) => f(); const timed = (k, f) => f(); const state = () => ({ base: "t" });
const seriesNow = () => ({ y: "", agg: "count" }); const api = async (url, body) => { sent.push(body); return { labels: [], datasets: [] }; };
const toDrawn = () => ({}); const drawChart = () => {}; let chartGroups = {}; const chartKey = q => JSON.stringify(q), groupsChanged = async () => {};  // 그룹 고르기(Phase 4) const mountTable = () => {}; const gridSpec = () => ({}); const chartData = () => ({}); const rangeError = () => "";
"""
    body = r"""
(async () => {
  const first = bestX(S.cols), none = bestX([["id", "정수"], ["n", "실수"]]);
  $("cunit").value = "month";
  $("cx").value = "day"; await $("draw").onclick();     // 날짜 X: 묶는 기간을 보낸다
  $("cx").value = "id"; await $("draw").onclick();      // 날짜가 아니면 보내지 않는다
  console.log(JSON.stringify({ first, none, units: sent.map(b => b.x_unit) }));
})();
"""
    assert run_node(tmp_path, body, parts=[stub, helpers, draw]) == {"first": "day", "none": "id", "units": ["month", ""]}


@needs_node
def test_picking_a_table_runs_first_page_search_only_when_everything_else_is_empty(tmp_path):
    code = between(PAGE, "function autoSearch()", "{ const opened")
    stub = r"""
const LISTS = ["joins", "conditions"]; let view = "cards", last = null, runs = 0;
const S = { base: "t", joins: [], conditions: [], union: { tables: [] } }; const runSearch = () => { runs++; };
"""
    body = r"""
autoSearch();                          // 비어 있고 검색 전: 실행
S.conditions = ["a > 1"]; autoSearch();  // 조건이 있으면 안 함
S.conditions = []; last = {}; autoSearch();  // 이미 검색했으면 안 함
last = null; view = "sentence"; autoSearch();  // 문장 보기에서는 안 함
console.log(JSON.stringify(runs));
"""
    assert run_node(tmp_path, body, parts=[stub, code]) == 1
