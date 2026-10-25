"""2차 감사 화면 JS 수정(2E 묶음)을 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit_js.py와 같은 방식: static 파일을 이어 붙이거나 html 안의 인라인 코드 구간을 잘라 가짜 화면 위에서 돌린다.
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다."""
import shutil

import pytest

from test_fix_audit_js import COMMON, GRID_HOST, STATIC, STUBS, between, index_slice, run_node

# draft.js의 isTyping(대시보드 typing()이 쓴다)
_D = (__import__("pathlib").Path(__file__).resolve().parents[1] / "static" / "core" / "draft.js").read_text(encoding="utf-8")
ISTYPING = _D[_D.index("const isTyping"):_D.index("\n", _D.index("const isTyping")) + 1]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

DASH_HTML = (STATIC / "dashboard" / "dashboard.html").read_text(encoding="utf-8")


def dash_slice(start, end):
    a = DASH_HTML.index(start)
    return DASH_HTML[a:DASH_HTML.index(end, a)]


# 대시보드 화면의 저장 부분을 돌리는 가짜 환경. failing이 참이면 서버가 저장을 거부한다.
SAVE_PRELUDE = """
const els = {}; globalThis.$ = id => els[id] ??= mk("x", { value: "" });
let dashId = "A", dashName = "A", dashMeta = {}, dashboards = [], lastSaved = "s0", quiet = false, saveTimer = null, valuesDirty = false, valuesSeq = 0, current = "s0";
const board = { cards: [], palette: [], star: {}, picked: new Set(), ctx: () => undefined, draw() {}, set(c) { this.cards = c; } };
const snapshot = () => current, metaOf = () => ({}), liveStar = s => s, needsValues = c => c, boardChanged = () => {};
const ctl = { set: async () => {} }; let panel = null;
globalThis.say = (id, text) => { globalThis.said = text; };
const dashDraft = { clear() {} };
globalThis.DashBoard = { plain: c => c };
const storedCard = c => c;
let failing = false, opened = [], saved = 0;
globalThis.api = async (url, body) => {
  if (url.includes("/get")) { opened.push(decodeURIComponent(url.split("id=")[1])); return { dashboard: { name: "B", palette: [], cards: [] } }; }
  if (url.includes("card/compute")) return { data: { v: 1 } };
  if (failing) throw new Error("서버가 거부했어요");
  if (url === "/api/dash") saved++;
  return { dashboard: { north_star: {} } };
};
"""
SAVE = dash_slice("async function save()", "// 임시 저장(초안)")
OPEN = dash_slice("let openSeq", '$("d-new").onclick')


def test_failed_save_keeps_dashboard_and_pick_reverts(tmp_path):
    """저장이 거부되면 다른 대시보드로 옮기지 않고 고르는 칸을 되돌린다(편집을 버리지 않는다)."""
    body = """
(async () => {
  current = "edited"; $("d-pick").value = "B"; failing = true;
  await $("d-pick").onchange(); await sleep(0);
  const failed = { pick: $("d-pick").value, opened: [...opened], msg: said };
  failing = false; $("d-pick").value = "B";
  await $("d-pick").onchange(); await sleep(0);
  console.log(JSON.stringify({ failed, okOpened: opened, saved }));
})();
"""
    got = run_node(tmp_path, body, parts=[SAVE_PRELUDE, SAVE, OPEN])
    assert got["failed"]["pick"] == "A" and got["failed"]["opened"] == []  # 고치기 전에는 저장이 실패해도 B를 열어 편집이 사라진다
    assert "저장하지 못해" in got["failed"]["msg"]
    assert got["okOpened"] == ["B"] and got["saved"] == 1


def test_refresh_does_not_reopen_when_save_fails(tmp_path):
    refresh = next(line for line in DASH_HTML.splitlines() if line.startswith('document.addEventListener("dash-refreshed"'))
    body = """
(async () => {
  current = "edited"; failing = true;
  handler({ detail: { results: [{ state: "ok", dashboard: "A" }] } }); await sleep(5);
  console.log(JSON.stringify({ opened }));
})();
"""
    prelude = SAVE_PRELUDE + "let handler; document.addEventListener = (n, f) => { handler = f; };\nconst typing = () => false; $(\"d-panel\").contains = () => false;\n"
    helper = dash_slice("function reopenAfterRefresh", 'document.addEventListener("dash-refreshed"')
    assert run_node(tmp_path, body, parts=[prelude, SAVE, OPEN, helper, refresh]) == {"opened": []}


def test_recompute_values_are_saved_then_cleared(tmp_path):
    """값 다시 구하기는 snapshot(값 제외)에 나타나지 않아도 저장 대상이다. 저장하면 표시를 지운다."""
    recompute = dash_slice("  recompute: async", "  star: () =>")
    prelude = SAVE_PRELUDE + "const scheduleSave = () => {};\nconst host = {" + recompute + "};\n"
    body = """
(async () => {
  const card = { id: "c", data: { since: "" } }; board.cards = [card];
  await host.recompute(card);
  const marked = valuesDirty;
  await saveIfDirty();
  const first = saved, cleared = !valuesDirty;
  await saveIfDirty();                                  // 다시 바뀐 것이 없으면 저장하지 않는다
  console.log(JSON.stringify({ marked, first, cleared, second: saved }));
})();
"""
    got = run_node(tmp_path, body, parts=[prelude, SAVE, OPEN])
    assert got == {"marked": True, "first": 1, "cleared": True, "second": 1}  # 고치기 전에는 값만 바뀐 카드를 저장하지 않았다


def test_live_run_schedules_pending_edit_and_defers_while_typing(tmp_path):
    live = dash_slice("  liveRun: async () => {", "  changed: async fields")
    typing = ISTYPING + dash_slice("const typing = ", "const snapshot = ")
    schedule = dash_slice("function scheduleSave()", "\n\n// ---------- 대시보드 고르기")
    prelude = SAVE_PRELUDE + """
let connected = true, ran = 0;
globalThis.liveCards = async () => { ran++; current = "edited-while-live"; };   // 갱신 중에 사용자가 카드를 고쳤다
document.activeElement = null;
$("d-grid").contains = n => !!n?.inGrid;
const opts = {""" + live + "};\n"
    body = """
(async () => {
  const sub = []; globalThis.setTimeout = (f, ms) => { sub.push(ms); return 1; };
  document.activeElement = { tagName: "INPUT", type: "text", inGrid: true };
  await opts.liveRun(); const typed = { ran, sub: [...sub] };
  document.activeElement = null;
  await opts.liveRun();
  console.log(JSON.stringify({ typed, ran, sub, state: $("d-state").textContent, quiet }));
})();
"""
    got = run_node(tmp_path, body, parts=[prelude, SAVE, typing, schedule])
    assert got["typed"] == {"ran": 0, "sub": []}  # 입력 칸에 커서가 있으면 이번 회는 건너뛴다
    assert got["ran"] == 1 and got["sub"] == [700] and got["state"] == "저장 중…" and got["quiet"] is False  # 갱신 중에 한 편집은 끝나고 저장이 예약된다(고치기 전에는 영영 저장 안 됨)


def test_better_select_for_unique_count_but_mode_only_when_mergeable(tmp_path):
    body = """
const walk = n => typeof n === 'string' ? [] : [...(n.tag === 'option' ? [n.textContent] : []), ...n.children.flatMap(walk)];
const mkcard = agg => ({ kind: 'kpi', view: {}, source: {}, data: { by_unit: { day: { labels: ['2026-01-01'], series: [{ agg }] } } } });
const ctx = { changed() {}, draw() {}, compares: new Map(), range: null, connKey: '', dateSelect: () => '', svgTools: () => mk('div'), colorEditor: () => '' };
const out = {};
for (const agg of ['sum', 'nunique']) out[agg] = walk(DashBoard.prototype.tools.call(ctx, mkcard(agg)));
console.log(JSON.stringify(out));
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js"], parts=["SERVER.merge = ['sum']; const SVG_KINDS = ['kpi'];\n"])
    assert got["sum"] == ["기간 비교", "시점 비교", "늘수록 좋음", "줄수록 좋음"]
    assert got["nunique"] == ["늘수록 좋음", "줄수록 좋음"]  # 고치기 전에는 고유 개수 카드에 좋은 방향 선택칸이 없었다


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


def test_set_range_recompares_once_when_recomputing(tmp_path):
    body = """
(async () => {
  let compares = 0, computed = 0;
  const b = Object.create(DashBoard.prototype);
  const card = { id: "c", kind: "recent_rows", source: { date: "d" }, data: {} };
  Object.assign(b, { cards: [card], hadRange: false, compares: new Map(), draw() {},
    async recompare() { compares++; }, recompute: async cards => { computed += cards.length; await b.recompare(); } });
  await b.setRange({ start: "2026-01-01", end: "2026-01-02" }, null);   // recent_rows는 다시 구하지 않는다: 비교만 한 번
  const plain = { compares, computed };
  card.kind = "line"; compares = 0; computed = 0;
  await b.setRange({ start: "2026-01-01", end: "2026-01-02" }, null);   // 다시 구하는 카드: 구하는 쪽이 끝에 비교하니 한 번
  console.log(JSON.stringify({ plain, redo: { compares, computed } }));
})();
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js"], parts=["const SVG_KINDS = ['kpi'];\n"])
    assert got["plain"] == {"compares": 1, "computed": 0} and got["redo"] == {"compares": 1, "computed": 1}  # 고치기 전에는 구하는 경우 비교가 두 번


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


def test_report_copies_color_scheme_and_numeric_variant():
    text = (STATIC / "dashboard" / "dash_report.js").read_text(encoding="utf-8")
    a = text.index("const INLINE_PROPS")
    props = text[a:text.index('.split(" ")', a)].replace('" +', " ").replace('"', " ").split()
    assert "color-scheme" in props and "font-variant-numeric" in props
