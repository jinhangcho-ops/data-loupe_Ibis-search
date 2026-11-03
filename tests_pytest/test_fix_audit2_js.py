"""2차 감사 화면 JS 수정(2E 묶음)을 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit_js.py와 같은 방식: static 파일을 이어 붙이거나 html 안의 인라인 코드 구간을 잘라 가짜 화면 위에서 돌린다.
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다."""
import shutil

import pytest

from test_fix_audit_js import COMMON, GRID_HOST, STATIC, STUBS, between, index_slice, run_node


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_apply_find_skips_missed_rows_without_mark(tmp_path):
    """맞지 않는 행은 남기지 않고(<mark>도 없다), 맞는 행만 강조해 보인다. 찾기를 비우면 강조가 지워지고 모든 행이 돌아온다."""
    code = index_slice("function applyFind() {", "\nlet findTimer")
    prelude = GRID_HOST + """
const host = makeHost(), box = { value: "" }, count = { textContent: "" };
globalThis.$ = id => id === "rows" ? host : id === "row-find" ? box : id === "find-count" ? count : { value: "40" };
"""
    body = """
resultTable(host, { columns: ["a"], rows: [{ a: "abc" }, { a: "xyz" }, { a: "qqq" }] });
box.value = "a"; applyFind();
const found = rowsHtml(host), foundCount = count.textContent;
box.value = ""; applyFind();
const back = rowsHtml(host);
console.log(JSON.stringify({ found, foundCount, back, marks: back.filter(h => h.includes("<mark>")).length }));
"""
    got = run_node(tmp_path, body, parts=[STUBS, COMMON, prelude, code])
    assert got["found"] == ['<td><mark>a</mark>bc</td>'] and got["foundCount"] == "1 / 3행"  # xyz·qqq는 남지 않고, 맞는 abc만 강조
    assert len(got["back"]) == 3 and got["marks"] == 0  # 비우면 이전 강조가 남지 않는다


def test_big_int_column_header_is_right_aligned(tmp_path):
    table = index_slice("function gridSpec(", "// 검색과 [SQL 보기]")
    body = """
const S = { sorts: [], base: "t" };
const data = { columns: ["id", "이름"], rows: [{ id: "12345678901234567890", 이름: "가" }] };
const heads = h => h.match(/<th class="[^"]*"/g).map(x => x.includes("num"));
const host = makeHost(); resultTable(host, data);
console.log(JSON.stringify({ result: heads(host.html), search: heads(gridSpec(data).head) }));
"""
    assert run_node(tmp_path, body, parts=[STUBS, COMMON, GRID_HOST, table]) == {"result": [True, False], "search": [True, False]}


def test_usage_poll_skips_hidden_window(tmp_path):
    usage = between("common.js", "async function showUsage()", "showUsage();")
    body = """
(async () => {
  let calls = 0; globalThis.api = async () => { calls++; return { pc_memory: 1, pc_cpu: 1, memory_mb: 1, memory: 1, cpu: 1 }; };
  globalThis.$ = () => ({});
  document.hidden = true; await showUsage(); const hidden = calls;
  document.hidden = false; await showUsage();
  console.log(JSON.stringify({ hidden, shown: calls }));
})();
"""
    assert run_node(tmp_path, body, parts=[usage]) == {"hidden": 0, "shown": 1}


def test_draft_baseline_is_first_response_after_table_open(tmp_path):
    base = index_slice('$("base").onchange', "async function refreshColumns")
    draft = index_slice("let draftBase", "const searchDraft")
    prelude = """
const S = { base: "" }; let view = "cards", key = "k0"; const bodyKey = () => key;
const clearLists = () => {}, showSummaries = () => {}, refreshColumns = async () => { await sleep(5); };
const els = {}; globalThis.$ = id => els[id] ??= { value: "t1", classList: { add() {} } };
"""
    body = """
(async () => {
  await $("base").onchange();       // 테이블을 연 첫 응답
  key = "k1";                       // 그 직후(첫 초안 확인 전)의 편집
  console.log(JSON.stringify({ edited: draftChanged() }));
})();
"""
    # 고치기 전에는 기준을 첫 초안 확인 때 잡아서 그 사이의 편집이 기준에 섞여 초안으로 안 잡혔다
    assert run_node(tmp_path, body, parts=[prelude, draft, base]) == {"edited": True}


def test_prep_table_uses_shared_result_table():
    html = (STATIC / "prep" / "prep.html").read_text(encoding="utf-8")
    assert "tableHtml" not in html and 'resultTable($("a-table"), anaTable(' in html  # 전처리 표도 공용 resultTable(host, data)을 쓴다


