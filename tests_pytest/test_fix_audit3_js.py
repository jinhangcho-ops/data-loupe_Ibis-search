"""3차 감사 화면 JS 수정(3E 묶음)을 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit_js.py·test_fix_audit2_js.py와 같은 방식(static 파일을 이어 붙이거나 html 안의 인라인 구간을 잘라 가짜 화면 위에서 돌린다).
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다."""
import shutil

import pytest

from test_fix_audit_js import COMMON, GRID_HOST, STATIC, STUBS, index_slice, run_node
from test_fix_audit2_js import DASH_HTML, dash_slice

# draft.js의 isTyping(대시보드 typing()이 쓴다)
_D = (__import__("pathlib").Path(__file__).resolve().parents[1] / "static" / "core" / "draft.js").read_text(encoding="utf-8")
ISTYPING = _D[_D.index("const isTyping"):_D.index("\n", _D.index("const isTyping")) + 1]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# 대시보드 화면의 저장·실시간 부분을 돌리는 가짜 환경. 진짜 snapshot·save·scheduleSave를 잘라 쓴다.
PRELUDE = """
const els = {}; globalThis.$ = id => els[id] ??= mk("x", { value: "" });
let dashId = "A", dashName = "A", dashMeta = {}, dashboards = [], lastSaved = "", quiet = false, saveTimer = null, valuesDirty = false, valuesSeq = 0, connected = true;
const board = { cards: [], palette: [], star: { card: "", counters: [], limits: {} }, picked: new Set(), ctx: () => undefined, draw() {}, set(c) { this.cards = c; }, recompare: async () => {}, connKey: "" };
const metaOf = () => ({}), liveStar = s => s, needsValues = c => c, boardChanged = () => {};
globalThis.say = (id, text) => { globalThis.said = text; };
let clears = 0; const dashDraft = { clear() { clears++; } };
globalThis.DashBoard = { plain: ({ id, title, data }) => ({ id, title, data }) };
const timers = []; const realTimeout = setTimeout; globalThis.setTimeout = (f, ms) => { timers.push(ms); return timers.length; };
const tick = () => new Promise(r => realTimeout(r, 0));
let gate = Promise.resolve(), computeGate = Promise.resolve(), bodies = [];
globalThis.api = async (url, body) => {
  if (url === "/api/dash") { bodies.push(JSON.stringify(body.cards)); await gate; return { dashboard: { cards: [], north_star: {} } }; }
  if (url.includes("card/compute")) { await computeGate; return { data: { v: 2 } }; }
  return {};
};
"""
SNAPSHOT = dash_slice("const snapshot =", "async function save()")
SAVE = dash_slice("async function save()", "// 임시 저장(초안)")
SCHEDULE = dash_slice("function scheduleSave()", "\n\n// ---------- 대시보드 고르기")
SAVE_IF_DIRTY = dash_slice("const saveIfDirty", '$("d-pick").onchange')
RECOMPUTE = "const host = {" + dash_slice("  recompute: async card", "  star: () =>") + "};\n"
CORE = [PRELUDE, SNAPSHOT, SAVE, SCHEDULE, SAVE_IF_DIRTY, RECOMPUTE]


# ---------- 3E1 ----------

def test_edit_during_save_stays_dirty(tmp_path):
    """저장 요청이 나가 있는 동안의 편집은 저장된 것으로 보이지 않고, 서버 응답으로 북극성이 덮이지도 않는다."""
    body = """
(async () => {
  const card = { id: "a", title: "old", data: { v: 1 } }; board.cards = [card]; lastSaved = snapshot();
  let release; gate = new Promise(r => { release = r; });
  card.title = "edit1";
  const p = save(); await tick();
  card.title = "edit2"; board.star.card = "S";   // 요청 중 편집
  release(); await p;
  const dirty = snapshot() !== lastSaved, star = board.star.card, during = clears;
  gate = Promise.resolve(); await saveIfDirty();
  const second = bodies.at(-1), n = bodies.length;
  await saveIfDirty();                            // 이제 깨끗하다
  console.log(JSON.stringify({ dirty, star, during, second, n, final: bodies.length, clean: snapshot() === lastSaved, cleared: clears }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_ctl.js"], parts=CORE)
    assert got["dirty"] is True and got["star"] == "S" and got["during"] == 0  # 고치기 전에는 요청 중 편집이 저장된 것으로 보였다
    assert "edit2" in got["second"] and got["n"] == 2 and got["final"] == 2 and got["clean"] and got["cleared"] == 1


def test_recompute_during_save_keeps_values_dirty_and_schedules(tmp_path):
    """요청 중에 값을 다시 구하면 그 값은 아직 저장 안 됐다(3E1). 다시 구하면 저장도 예약된다(3E5)."""
    body = """
(async () => {
  const card = { id: "a", title: "t", data: { since: "" } }; board.cards = [card]; lastSaved = snapshot();
  let release; gate = new Promise(r => { release = r; });
  const p = save(); await tick();
  await host.recompute(card);
  const scheduled = [...timers];
  release(); await p;
  console.log(JSON.stringify({ dirty: valuesDirty, scheduled, data: card.data }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_ctl.js"], parts=CORE)
    assert got["dirty"] is True and got["data"] == {"v": 2}
    assert 700 in got["scheduled"]  # 고치기 전에는 값만 바뀐 카드를 저장할 타이머가 없었다


def test_recompute_answer_for_other_dashboard_is_ignored(tmp_path):
    body = """
(async () => {
  const card = { id: "a", title: "t", data: { old: 1 } }; board.cards = [card]; lastSaved = snapshot();
  let release; computeGate = new Promise(r => { release = r; });
  const p = host.recompute(card); await tick();
  dashId = "B"; release(); await p;
  console.log(JSON.stringify({ dirty: valuesDirty, data: card.data, timers }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_ctl.js"], parts=CORE)
    assert got == {"dirty": False, "data": {"old": 1}, "timers": []}


# ---------- 3E2 ----------

def test_save_failure_not_hidden_by_success_text(tmp_path):
    fill = dash_slice("async function fillCard(", "// ---------- 카드 모음")
    refresh = dash_slice("async function refreshDash()", '$("d-refresh").onclick')
    rename = dash_slice('$("d-rename-go").onclick', "let sure")
    prelude = """
globalThis.save = async () => { $("err-dash").textContent = "서버가 거부했어요"; return false; };
const computeCard = async () => {}, isShell = () => false, timed = (k, fn) => fn(), busy = (b, fn) => fn(), showList = () => {};
const panelHost = { prepare: async () => {} }, panelOpen = {};
panel = { builderOpen: true };
"""
    body = """
(async () => {
  let posted = 0; const inner = api; globalThis.api = async (u, b) => { if (u === "/api/dash") posted++; return inner(u, b); };
  const target = { view: {}, source: {} }; board.cards = [target];
  said = ""; await fillCard(target, { kind: "metric", source: {}, view: {} });
  const filled = said;
  board.cards = [{ id: "c", title: "T", kind: "metric", source: { schema: "", table: "t" }, data: {} }];
  said = ""; await refreshDash();
  const refreshed = said;
  $("d-rename").value = "새 이름"; said = ""; await $("d-rename-go").onclick();
  console.log(JSON.stringify({ filled, refreshed, renamed: said, posted }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_ctl.js"], parts=[PRELUDE, "let panel = null;\n", prelude, SNAPSHOT, fill, refresh, rename])
    assert got["filled"] == ""  # 고치기 전에는 저장이 실패해도 "데이터를 정했어요."를 보였다
    assert "저장하지 못" in got["refreshed"] and "서버가 거부했어요" in got["refreshed"]
    assert got["renamed"] == "" and got["posted"] == 0  # 저장이 실패하면 이름도 바꾸지 않는다


# ---------- 3E3 ----------

def test_live_tick_reports_skipped_turn(tmp_path):
    body = """
(async () => {
  globalThis.setTimeout = () => 1;
  const c = Object.create(DashCtl.prototype), notes = [];
  Object.assign(c, { liveOn: true, gen: 1, state: { live: "x" }, times: [], seconds: 3600, note(el, text) { notes.push(text); }, visible: () => true, liveBtn: {}, liveNote: {}, liveRun: async () => false });
  await c.tick(1);
  c.liveRun = async () => {}; await c.tick(1);
  console.log(JSON.stringify(notes.map(t => t.split(" ")[0])));
})();
"""
    assert run_node(tmp_path, body, files=["dash_ctl.js"]) == ["입력", "갱신함"]  # 고치기 전에는 건너뛴 회도 갱신함


def test_dash_live_run_returns_false_when_typing(tmp_path):
    live = dash_slice("  liveRun: async () => {", "  changed: async fields")
    typing = ISTYPING + dash_slice("const typing = ", "const snapshot = ")
    body = """
(async () => {
  globalThis.liveCards = async () => {};
  document.activeElement = { tagName: "INPUT", type: "text" }; $("d-grid").contains = n => !!n;
  const skipped = await opts.liveRun();
  document.activeElement = null;
  console.log(JSON.stringify({ skipped, ran: (await opts.liveRun()) !== false }));
})();
"""
    got = run_node(tmp_path, body, parts=[PRELUDE, SNAPSHOT, SAVE, SCHEDULE, typing, "const opts = {" + live + "};\n"])
    assert got["skipped"] is False and got["ran"] is True


# ---------- 3E4 ----------

def test_live_and_range_values_are_not_stored(tmp_path):
    body = """
(async () => {
  globalThis.DashBoard = { plain: ({ id, data }) => ({ id, data }) };
  let next = { v: "live" }; globalThis.api = async () => ({ results: [{ id: "c", data: next }] });
  const mkboard = c => ({ cards: [c], ctx: () => undefined, recompare: async () => {}, connKey: "" });
  const c = { id: "c", source: { key: "k" }, data: { v: "saved" } }, b = mkboard(c);
  await liveCards(b, true);
  const afterLive = { shown: c.data.v, stored: storedCard(c).data.v };
  next = { v: "range" }; await recomputeRange(b, [c], true);
  const afterRange = { shown: c.data.v, stored: storedCard(c).data.v };
  c.data = { v: "chosen" };                        // 다시 구하기처럼 일부러 바꾼 값은 저장 대상
  console.log(JSON.stringify({ afterLive, afterRange, chosen: storedCard(c).data.v }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_ctl.js"])
    assert got["afterLive"] == {"shown": "live", "stored": "saved"}  # 고치기 전에는 실시간 값이 그대로 저장됐다
    assert got["afterRange"] == {"shown": "range", "stored": "saved"}
    assert got["chosen"] == "chosen"


# ---------- 3E5 ----------  (위 test_recompute_* 두 개) / 3E6

def test_refreshed_event_only_reopens_open_dashboard_and_waits_while_typing(tmp_path):
    helper = dash_slice("function reopenAfterRefresh", 'document.addEventListener("dash-refreshed"')
    listener = next(line for line in DASH_HTML.splitlines() if line.startswith('document.addEventListener("dash-refreshed"'))
    typing = ISTYPING + dash_slice("const typing = ", "const snapshot = ")
    prelude = """
let handler; document.addEventListener = (n, f) => { handler = f; };
const opened = [], later = []; globalThis.setTimeout = f => { later.push(f); return 1; };
globalThis.saveIfDirty = async () => {}; globalThis.openDash = async id => { opened.push(id); };
let focus = null; Object.defineProperty(document, "activeElement", { get: () => focus });
$("d-grid").contains = n => !!n?.inGrid; $("d-panel").contains = n => !!n?.inPanel;
"""
    body = """
(async () => {
  const ok = d => ({ detail: { results: [{ state: "ok", dashboard: d }] } });
  handler(ok("X")); await tick();                    // 다른 대시보드 결과
  const other = [...opened];
  focus = { tagName: "TEXTAREA", inPanel: true };
  handler(ok("A")); await tick();                    // 패널에 포커스
  const waiting = { opened: [...opened], later: later.length };
  focus = null; later.shift()(); await tick(); await tick();
  console.log(JSON.stringify({ other, waiting, opened, said: globalThis.said }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_ctl.js"], parts=[PRELUDE, SNAPSHOT, SAVE, SAVE_IF_DIRTY, typing, prelude, helper, listener])
    assert got["other"] == []  # 고치기 전에는 열린 대시보드가 결과에 없어도 다시 열었다
    assert got["waiting"] == {"opened": [], "later": 1} and got["opened"] == ["A"]


# ---------- 3E7 ----------

def test_range_answers_arriving_late_are_dropped(tmp_path):
    body = """
(async () => {
  globalThis.DashBoard = { plain: c => c };
  const waits = []; globalThis.api = () => new Promise(r => waits.push(r));
  const c = { id: "c", source: {}, data: { v: 0 } }, b = { cards: [c], ctx: () => undefined, recompare: async () => {}, connKey: "" };
  const first = recomputeRange(b, [c], true), second = recomputeRange(b, [c], true);
  waits[1]({ results: [{ id: "c", data: { v: "new" } }] }); await second;
  waits[0]({ results: [{ id: "c", data: { v: "old" } }] }); await first;
  console.log(JSON.stringify({ v: c.data.v }));
})();
"""
    assert run_node(tmp_path, body, files=["dash_ctl.js"]) == {"v": "new"}  # 고치기 전에는 늦은 옛 응답이 새 값을 덮었다


def test_apply_ignores_stale_period_parse(tmp_path):
    body = """
(async () => {
  const c = Object.create(DashCtl.prototype), ranges = [], waits = {};
  const input = v => ({ value: v }), box = { classList: { toggle() {} } };
  Object.assign(c, { ready: Promise.resolve(), today: "2026-01-01", state: { as_of: "", period: "" }, asof: input(""), per: input("A"), asofNote: {}, perNote: {}, past: box,
    note() {}, stopLive() {}, changed() {}, board: { setRange: async r => { ranges.push(r); } } });
  globalThis.api = (url, body) => url.includes("/day") ? Promise.resolve({ date: "2026-01-01", label: "" })
    : new Promise(r => { waits[body.text] = () => r({ span: { label: body.text }, context: { range: body.text } }); });
  const a = c.apply(false); await new Promise(r => setTimeout(r, 0));
  c.per.value = "B"; const b = c.apply(false); await new Promise(r => setTimeout(r, 0));
  waits.B(); await b; waits.A(); await a;
  console.log(JSON.stringify({ ranges, period: c.state.period }));
})();
"""
    assert run_node(tmp_path, body, files=["dash_ctl.js"]) == {"ranges": ["B"], "period": "B"}  # 고치기 전에는 A가 B를 덮었다


# ---------- 3E8 ----------

SOURCE_CASES = [  # (저장한 열쇠, 지금 열쇠, 같은 연결인가) -- core/use_counts.same_source와 같은 표
    ("folder:C:/data", "folder:C:/data#a.csv", True),   # 예전 폴더만 있는 열쇠는 그 폴더의 어느 파일과도 같다
    ("folder:C:/data", "folder:C:/data", True),
    ("folder:C:/data#a.csv", "folder:C:/data#a.csv", True),
    ("folder:C:/data#a.csv", "folder:C:/data#b.csv", False),  # 파일까지 적힌 열쇠는 다른 파일과 다르다
    ("folder:C:/other", "folder:C:/data#a.csv", False),
    ("folder:C:/data", "folder:C:/data2#a.csv", False),
    ("", "folder:C:/data#a.csv", False),
    ("url:x", "folder:C:/data", False),
]


def test_same_source_table_js(tmp_path):
    import json
    line = next(l for l in (STATIC / "core" / "common.js").read_text(encoding="utf-8").splitlines() if l.startswith("const sameSource"))
    body = f"""
const cases = {json.dumps(SOURCE_CASES)};
console.log(JSON.stringify({{ got: cases.map(([a, b]) => sameSource(a, b)), missing: sameSource("folder:x", undefined), same: sameSource(undefined, undefined) }}));
"""
    got = run_node(tmp_path, body, parts=[line + "\n"])
    assert got["got"] == [c[2] for c in SOURCE_CASES]
    assert got["missing"] is False  # 지금 연결이 없으면(undefined) 같지 않다


def test_same_source_js_and_python_agree():
    from core.use_counts import same_source
    assert [same_source(a, b) for a, b, _ in SOURCE_CASES] == [c[2] for c in SOURCE_CASES]


# ---------- 3E9 ----------

def test_apply_find_matches_per_cell_and_keeps_expanded_cells(tmp_path):
    """칸마다 따로 찾고(칸 사이를 건너 "서울"|"시"가 "울시"로 일치하지 않는다), 펼쳐 둔 중첩 값 칸은 찾기 뒤에도 펼친 채(강조 글자로 굳지 않는다)."""
    code = index_slice("function applyFind() {", "\nlet findTimer")
    prelude = GRID_HOST + """
const host = makeHost(), box = { value: "" }, count = { textContent: "" };
globalThis.$ = id => id === "rows" ? host : id === "row-find" ? box : id === "find-count" ? count : { value: "40" };
"""
    body = """
resultTable(host, { columns: ["a", "b"], rows: [{ a: "서울", b: "시" }, { a: "a", b: { k: 1 } }, { a: "서울시청", b: "x" }] });
const g = host._grid;
box.value = "울시"; applyFind();
const split = { view: g.view, rows: rowsHtml(host) };
g.open.set(1, new Set([1])); expanded.length = 0;          // 1번 행의 중첩 값 칸을 펼쳐 둔 상태
box.value = "k"; applyFind();
console.log(JSON.stringify({ split, kView: g.view, expanded }));
"""
    got = run_node(tmp_path, body, parts=[STUBS, COMMON, prelude, code])
    assert got["split"]["view"] == [2] and len(got["split"]["rows"]) == 1 and "<mark>울시</mark>청" in got["split"]["rows"][0]  # 고치기 전에는 칸을 건너 "서울"|"시"도 일치로 봤다
    assert got["kView"] == [1] and got["expanded"] == [[0, 1]]  # 펼친 칸은 찾은 뒤 다시 그려도 펼친다(전에는 펼친 글을 칸 글자로 굳혔다)
