"""화면 JS 감사 수정(E 묶음)을 node로 돌려 본다. node가 없으면 건너뛴다.
순수 함수는 static/ 파일을 이어 붙여 부르고, index.html·dashboard.html 안의 인라인 코드는 해당 구간만 잘라 가짜 화면(DOM) 위에서 돌린다.
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다(값은 손으로 센 작은 예제)."""
import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

STATIC = ROOT / "static"

# 가짜 화면: 요소(속성·자식·이벤트)만 흉내 낸다.
FAKE_DOM = r"""
const assert = require('assert');
class FakeEl {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.listeners = {}; this.style = { setProperty() {} }; this.dataset = {}; this.classList = { add() {}, remove() {}, toggle() {} }; this.captured = 0; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = kids; }
  addEventListener(n, f) { (this.listeners[n] ??= []).push(f); }
  removeEventListener(n, f) { this.listeners[n] = (this.listeners[n] ?? []).filter(x => x !== f); }
  setPointerCapture() { this.captured++; }
  createSVGPoint() { return { x: 0, y: 0, matrixTransform() { return { x: this.x, y: this.y }; } }; }
  getScreenCTM() { return { inverse() { return {}; } }; }
  get textContent() { return this.children.map(c => typeof c === 'string' ? c : c.textContent ?? '').join('') + (this._text ?? ''); }
  set textContent(v) { this._text = v; this.children = []; }
  click() { this.clicked = true; }
}
const mk = (tag, props = {}, ...kids) => { const e = new FakeEl(tag); Object.assign(e, props); e.append(...kids); return e; };
globalThis.document = { createElement: t => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), documentElement: { dataset: { theme: 'light' } }, addEventListener() {}, cookie: '' };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#000000' });
globalThis.el = mk;
globalThis.svg = (tag, attrs = {}, ...kids) => { const e = new FakeEl(tag); for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v); e.append(...kids); return e; };
globalThis.SERVER = { dash: { types: {}, time_types: [], units: {}, max_palette: 12, aggs: [] }, merge: ['sum', 'count', 'mean', 'min', 'max'], agg_names: {} };
const sleep = ms => new Promise(r => setTimeout(r, ms));
"""


def read(name):
    return next(STATIC.rglob(name)).read_text(encoding="utf-8")


def between(name, start, end):
    """파일에서 start로 시작해 end 직전까지."""
    text = read(name)
    a = text.index(start)
    return text[a:text.index(end, a)]


def run_node(tmp_path, body, files=(), parts=(), env=None):
    """FAKE_DOM + static 파일들 + 잘라 온 구간들 + body를 이어 붙여 돌리고 마지막 줄(JSON)을 돌려준다."""
    script = tmp_path / "t.js"
    script.write_text(FAKE_DOM + "".join(read(f) + "\n" for f in files) + "".join(parts) + "\n" + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**__import__("os").environ, **(env or {})})
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


COMMON = between("common.js", "const esc = ", 'document.addEventListener("click"')  # esc·fill·el·download·shown·cell. 위의 el 가짜를 지우지 않게 el은 뺀다
COMMON = re.sub(r"const el = [^\n]*\n", "", COMMON)
STUBS = "globalThis.$ = () => null;\n"


# ---------- 3·4 esc·fill·shown·cell ----------

def test_esc_quotes_and_fill_keeps_value_whitespace(tmp_path):
    body = """
const sel = {}; fill(sel, ["금액 ", "a  b", 'q"x']);
console.log(JSON.stringify({ esc: esc(`a"b'c<&`), html: sel.innerHTML }));
"""
    got = run_node(tmp_path, body, parts=[STUBS, COMMON])
    assert got["esc"] == "a&quot;b&#39;c&lt;&amp;"
    assert '<option value="금액 ">금액 </option>' in got["html"] and '<option value="a  b">' in got["html"] and 'value="q&quot;x"' in got["html"]  # 공백이 줄지 않게 value를 명시한다


def test_numbers_show_full_value_in_title_and_big_ints_align_right(tmp_path):
    body = """
const cells = [0.00045, 1234, 1e21, "12345678901234567890", "abc"].map(cell);
console.log(JSON.stringify({ cells, small: shown(0.00045), plain: shown(1234) }));
"""
    got = run_node(tmp_path, body, parts=[STUBS, COMMON])
    assert got["small"] == ["0", "0.00045"] and got["plain"] == ["1,234", ""]  # 쉼표만 다르면 title을 달지 않는다
    assert got["cells"][0] == '<td class="num" title="0.00045">0</td>' and got["cells"][1] == '<td class="num">1,234</td>'
    assert got["cells"][3] == '<td class="num">12345678901234567890</td>' and got["cells"][4] == "<td>abc</td>"  # 큰 정수(글자)는 숫자 칸처럼


def test_download_revokes_blob_url(tmp_path):
    body = """
const revoked = []; URL.revokeObjectURL = u => revoked.push(u);
globalThis.setTimeout = (f) => f();
download("blob:x", "a.csv"); download("data:y", "b.png");
console.log(JSON.stringify(revoked));
"""
    assert run_node(tmp_path, body, parts=[STUBS, COMMON]) == ["blob:x"]


# ---------- 쿠키 ----------

def test_saved_cookie_reads_any_value(tmp_path):
    head = (STATIC / "core" / "head.html").read_text(encoding="utf-8")
    code = head[head.index("const saved = name"):head.index("// 화면 모드")]
    body = """
const read = c => { document.cookie = c; return saved("board_ratio"); };
console.log(JSON.stringify([read("theme=dark; board_ratio=4:3; x=1"), read('board_ratio="a b"'), read("board_ratio=%EC%A0%84%EB%B6%80"), read("x=1"), read("board_ratio=100%")]));
"""
    assert run_node(tmp_path, body, parts=[code]) == ["4:3", "a b", "전부", None, "100%"]  # 고치기 전에는 "4"


# ---------- 2 색 순서 ----------

def test_label_order_stops_growing(tmp_path):
    body = """
const order = []; const idx = [];
for (let i = 0; i < 300; i++) idx.push(labelIndex(order, "라벨" + i));
console.log(JSON.stringify({ len: order.length, last: idx[299], again: labelIndex(order, "라벨5") }));
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js"])
    assert got == {"len": 200, "last": 200, "again": 5}  # 서버(dash_store)는 order 200개 넘으면 저장을 거부한다


# ---------- 6 비동기 경쟁 ----------

def test_recompare_ignores_late_old_response(tmp_path):
    body = """
(async () => {
  const card = { id: "c", view: {}, data: { by_unit: { day: { labels: ["2026-01-02", "2026-01-03"], series: [{ name: "a", agg: "sum", sum: [1, 2], count: [1, 1] }] } } } };
  const b = Object.create(DashBoard.prototype);
  Object.assign(b, { cards: [card], range: { start: "2026-01-03", end: "2026-01-03" }, prev: { start: "2026-01-02", end: "2026-01-02", text: "직전" }, draw() { this.drawn = (this.drawn ?? 0) + 1; } });
  const waits = []; globalThis.api = () => new Promise(r => waits.push(r));
  const p1 = b.recompare(); await sleep(0);
  const p2 = b.recompare(); await sleep(0);
  waits[1]({ results: [{ t: "new" }] }); await p2;
  waits[0]({ results: [{ t: "old" }] }); await p1;
  console.log(JSON.stringify({ got: b.compares.get(card), drawn: b.drawn }));
})();
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js"])
    assert got == {"got": {"t": "new"}, "drawn": 1}


def test_live_restart_keeps_single_chain(tmp_path):
    body = """
(async () => {
  const c = Object.create(DashCtl.prototype); let active = 0, warm = false, maxActive = 0;
  Object.assign(c, { liveOn: false, gen: 0, state: { live: "x" }, times: [], seconds: 0, note() {}, visible: () => true, liveBtn: {}, liveNote: {}, liveRun: async () => { active++; if (warm) maxActive = Math.max(maxActive, active); await sleep(30); active--; } });
  globalThis.api = async () => ({ seconds: 0.01 });
  await c.toggleLive(); await sleep(5);
  c.stopLive("멈춤"); await c.toggleLive();   // 앞 조회가 아직 도는 중에 멈췄다 다시 시작
  await sleep(40); warm = true;               // 앞 조회는 끝났다. 이제부터 동시에 도는 조회가 있으면 사슬이 둘이다
  await sleep(300);
  c.stopLive("");
  console.log(JSON.stringify({ maxActive }));
})();
"""
    assert run_node(tmp_path, body, files=["dash_ctl.js"])["maxActive"] == 1  # 고치기 전에는 사슬이 둘이라 2

def test_picked_files_outside_drawn_list_survive_restore_and_connect(tmp_path):
    flat = between("common.js", "const FLAT = ", "// 연결 칸(connect.html)이 있는 화면에서만")
    body = """
const boxes = ["a", "b"].map(v => ({ value: v, checked: false }));
document.querySelectorAll = () => boxes;
const els = {}; globalThis.$ = id => els[id] ??= { value: FLAT, textContent: "", classList: { toggle() {} } };
restoreFlat(["b", "z1", "z2"]);   // z1·z2는 화면 목록(2000개)에 안 그려진 파일
boxes[0].checked = true;          // 사용자가 a도 체크
console.log(JSON.stringify({ picked: pickedFiles(), text: els["flat-count"].textContent }));
"""
    got = run_node(tmp_path, body, parts=[flat])
    assert got["picked"] == ["a", "b", "z1", "z2"]
    assert "2개는 목록 밖" in got["text"]


def test_prep_recipe_sends_count_false_only_when_stats_off(tmp_path):
    html = (STATIC / "prep" / "prep.html").read_text(encoding="utf-8")
    code = html[html.index("function recipe()"):html.index("// [미리보기]·[실행]·[중복 세기]가 함께 쓴다")]
    prelude = """
const cols = () => [], edits = {}, OPS = {}, pca = { columns: [], n: 2 }, filters = [], copies = [], drops = [], sorts = [], formulas = [], geoSteps = [], dedupe = [], keepNow = () => null, S = { schema: "", base: "t" };
globalThis.$ = () => ({ checked: false });
let statsOn = true;
"""
    body = """
const on = recipe(); statsOn = false; const off = recipe();
console.log(JSON.stringify({ on: "count" in on, off: off.count }));
"""
    got = run_node(tmp_path, body, parts=[prelude, code])
    assert got == {"on": False, "off": False}


def test_open_dash_latest_response_wins_and_refresh_saves_only_when_dirty(tmp_path):
    html = (STATIC / "dashboard" / "dashboard.html").read_text(encoding="utf-8")
    code = html[html.index("let openSeq"):html.index('$("d-new").onclick')]
    refresh = next(line for line in html.splitlines() if line.startswith('document.addEventListener("dash-refreshed"'))
    prelude = """
const els = {}; globalThis.$ = id => els[id] ??= mk("x", { value: "" });
let dashId = "", dashName = "", dashMeta = {}, lastSaved = "", quiet = false, current = "s0", panel = null, saves = 0, valuesDirty = false;
const metaOf = () => ({}), needsValues = c => c, boardChanged = () => {}, snapshot = () => current;
const ctl = { set: async () => {} };
const board = { cards: [], set(c) { this.cards = c; }, star: {} };
globalThis.say = () => {}; async function save() { saves++; lastSaved = current; return true; }
const waits = {}; globalThis.api = url => url.includes("/get") ? new Promise(r => { waits[decodeURIComponent(url.split("id=")[1])] = r; }) : Promise.resolve({});
let handler; document.addEventListener = (n, f) => { handler = f; };
"""
    body = """
(async () => {
  const a = openDash("A"), b = openDash("B");
  waits.B({ dashboard: { name: "B", palette: [], cards: [{ id: "b1" }] } }); await b;
  waits.A({ dashboard: { name: "A", palette: [], cards: [{ id: "a1" }] } }); await a;
  const open = { dashId, cards: board.cards.map(c => c.id) };
  // 최신화 뒤: 저장할 것이 없으면 save를 부르지 않고 바로 연다
  lastSaved = current = "same"; saves = 0;
  handler({ detail: { results: [{ state: "ok" }] } }); await sleep(0);
  const clean = saves;
  current = "edited"; handler({ detail: { results: [{ state: "ok" }] } }); await sleep(0);
  console.log(JSON.stringify({ open, clean, dirty: saves }));
})();
"""
    got = run_node(tmp_path, body, parts=[prelude, code, refresh])
    assert got["open"] == {"dashId": "B", "cards": ["b1"]}  # 고치기 전에는 늦게 온 A가 B의 id 위에 A 카드를 덮는다
    assert got["clean"] == 0 and got["dirty"] == 1


def test_card_panel_open_last_click_wins(tmp_path):
    body = """
(async () => {
  const p = Object.create(CardPanel.prototype); const shown = [];
  const gates = {};
  Object.assign(p, { host: { prepare: card => new Promise(r => { gates[card] = r; }) }, card: null, show() { shown.push(this.card); } });
  const a = p.open("A"), b = p.open("B");
  gates.B(); await b; gates.A(); await a;
  console.log(JSON.stringify({ shown, now: p.card }));
})();
"""
    got = run_node(tmp_path, body, files=["dash_panel.js"], parts=["const isShell = () => false;\n"])
    assert got == {"shown": ["B"], "now": "B"}


# ---------- 7 나머지 ----------

def test_map_dots_keep_elements_while_dragging(tmp_path):
    body = """
const entities = [{ name: "a", kind: "x", level: "ok", reasons: [], impact: [] }, { name: "b", kind: "x", level: "bad", reasons: [], impact: [] }];
const card = { view: { positions: {} }, data: { values: { entities } } };
const root = paintMap(card, { line: "#ccc", text: "#555", ink: "#000", card: "#fff" }, null);
const dots = root.children[2], g0 = dots.children[0], circle = g0.children[0], before = circle.attrs.cx;
g0.onpointerdown({ pointerId: 1 });
g0.listeners.pointermove[0]({ clientX: 100, clientY: 50 });
console.log(JSON.stringify({ same: dots.children[0] === g0 && dots.children.length === 2, moved: circle.attrs.cx !== before, cx: circle.attrs.cx, label: g0.children[1].attrs.x, captured: g0.captured }));
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js", "dash_cards.js"], parts=["globalThis.URL = URL;\n"])
    assert got["same"] and got["moved"] and got["captured"] == 1  # 점을 다시 만들면 포인터 캡처가 풀린다
    assert got["cx"] == got["label"]


def test_board_draw_survives_one_bad_card(tmp_path):
    body = """
globalThis.Chart = class { destroy() {} };
const grid = mk("div", { offsetParent: {} }); const good = { title: "good", ratio: 2, config: () => ({}) }, bad = { title: "bad", config: () => { throw new Error("깨짐"); } };
const b = new Board(grid, { empty: "" }); b.set([good, bad]);
console.log(JSON.stringify({ n: grid.children.length, text: grid.children[1].textContent }));
"""
    got = run_node(tmp_path, body, files=["board.js"])
    assert got["n"] == 2 and "깨짐" in got["text"]


def test_no_target_text_when_value_missing(tmp_path):
    body = """
const colors = { series: ["#111", "#222"], line: "#ccc", ink: "#000", text: "#555", card: "#fff" };
const sum = paintSummary({ data: { text: "문장", values: { scalar: [null] }, target: { value: 10 } }, view: {}, title: "t" }, colors);
const day = { labels: ["2026-01-01", "2026-01-02"], series: [{ name: "a", agg: "sum", sum: [5, 7], count: [1, 1] }] };
const trend = paintTargetTrend({ title: "t", range: { start: "2026-02-01", end: "2026-02-02" }, view: { window: { from: "2026-01-01", to: "2026-01-02" } }, data: { by_unit: { day }, target: { value: 6 } } }, colors);
const texts = []; (function walk(n) { if (typeof n === "string") return; if (n.tag === "text") texts.push(n.textContent); n.children.forEach(walk); })(trend);
console.log(JSON.stringify({ sum: sum.textContent, texts }));
"""
    got = run_node(tmp_path, body, files=["board.js", "dash.js", "dash_cards.js"])
    assert "0%" not in got["sum"] and "목표 대비 —" in got["sum"]
    assert any(t == "—" for t in got["texts"]) and not any("목표보다" in t for t in got["texts"])


def test_inline_styles_only_needed_properties(tmp_path):
    body = """
const names = ["display", "color", "animation-delay", "transition-duration", "cursor", "margin-top"];
globalThis.getComputedStyle = () => ({ [Symbol.iterator]: () => names[Symbol.iterator](), getPropertyValue: n => n === "display" ? "block" : n === "color" ? "rgb(1, 2, 3)" : n === "cursor" ? "pointer" : "1s" });
const src = mk("div", {}, mk("span")), copy = mk("div", {}, mk("span"));
inlineStyles(src, copy);
console.log(JSON.stringify({ style: copy.attrs.style, child: copy.children[0].attrs.style.length > 0 }));
"""
    got = run_node(tmp_path, body, files=["dash_report.js"], parts=["const GRID_COLS = 12, GRID_ROW = 60, clampLayout = l => l;\n"])
    assert "display:block" in got["style"] and "color:rgb(1, 2, 3)" in got["style"] and got["child"]
    assert "animation-delay" not in got["style"] and "cursor" not in got["style"]  # 350여 개를 모두 박던 것을 그리는 데 쓰는 것만


def test_report_date_is_local(tmp_path):
    body = """
(async () => {
  const RealDate = Date; globalThis.Date = class extends RealDate { constructor(...a) { a.length ? super(...a) : super("2026-01-01T16:00:00Z"); } };
  const texts = []; const ctx = new Proxy({}, { get: (t, k) => k === "fillText" ? (s) => texts.push(s) : k === "measureText" ? () => ({ width: 1 }) : () => {}, set: () => true });
  document.createElement = () => ({ width: 0, height: 0, getContext: () => ctx });
  await reportCanvas({ cards: [] }, "제목");
  console.log(JSON.stringify(texts));
})();
"""
    texts = run_node(tmp_path, body, files=["board.js", "dash_report.js"], parts=["const GRID_COLS = 12, GRID_ROW = 60, clampLayout = l => l;\n"], env={"TZ": "Asia/Seoul"})
    assert "2026-01-02" in texts  # UTC로 자르면 2026-01-01


def test_funcs_load_retries_after_failure(tmp_path):
    body = """
(async () => {
  let n = 0; globalThis.api = async () => { if (++n === 1) throw new Error("실패"); return { funcs: [{ id: "sum" }], aliases: {} }; };
  const first = await Funcs.load().then(() => "ok", e => e.message);
  const second = await Funcs.load().then(c => Object.keys(c.byId), e => e.message);
  console.log(JSON.stringify([first, second]));
})();
"""
    assert run_node(tmp_path, body, files=["funcs.js"]) == ["실패", ["sum"]]


def test_file_list_is_capped(tmp_path):
    body = """
(async () => {
  const flat = Array.from({ length: 5000 }, (_, i) => "f" + i + ".csv"), made = {};
  const stubs = { kinds: { querySelectorAll: () => [] }, path: { value: "x" }, deep: { checked: false }, file: mk("select"), "flat-list": mk("div") };
  globalThis.$ = id => stubs[id];
  globalThis.api = async () => ({ files: [], flat });
  globalThis.Option = class { constructor(t, v) { this.t = t; } };
  let msg = ""; globalThis.connMsg = (t) => { msg = t; };
  globalThis.FLAT = "*flat"; globalThis.showFlat = () => {}; globalThis.flatBoxes = () => []; globalThis.offList = new Set();
  globalThis.checks = (box, values) => { made.n = values.length; };
  await listFiles();
  console.log(JSON.stringify({ n: made.n, msg }));
})();
"""
    part = between("common.js", "const LIST_MAX", 'if (hasConnect) $("find")')
    got = run_node(tmp_path, body, parts=[STUBS, COMMON, part])
    assert got["n"] == 2000 and "2,000" in got["msg"]


# ---------- 검색 화면(index.html 인라인) ----------

def index_slice(start, end):
    html = (STATIC / "query" / "index.html").read_text(encoding="utf-8")
    a = html.index(start)
    return html[a:html.index(end, a)]


def test_header_sort_reverts_when_server_rejects(tmp_path):
    code = index_slice('$("rows").onclick = async e', '$("sql-show").onclick')
    prelude = """
const els = {}; globalThis.$ = id => els[id] ??= mk("x", { disabled: false });
const S = { sorts: ["a asc"] }; const rendered = []; const renderList = k => rendered.push(JSON.stringify(S.sorts));
let ok = false; const runSearch = async () => { rendered.push("run:" + JSON.stringify(S.sorts)); return ok; };
"""
    body = """
(async () => {
  const th = { firstChild: { textContent: "b ↑" } }, e = { target: { closest: () => th } };
  await $("rows").onclick(e);
  const failed = [...S.sorts];
  ok = true; await $("rows").onclick(e);
  console.log(JSON.stringify({ failed, kept: S.sorts, rendered }));
})();
"""
    got = run_node(tmp_path, body, parts=[prelude, code])
    assert got["failed"] == ["a asc"] and got["kept"] == ["b asc"]  # 서버가 거부하면 이전 정렬로(칩이 박히지 않게)


def test_apply_find_skips_when_empty_and_nothing_marked(tmp_path):
    code = index_slice("let finding = false", '$("row-find").oninput')
    prelude = """
let queries = 0, want = "";
const rows = [mk("tr", { textContent: "abc" })]; rows[0].querySelectorAll = () => []; rows[0].classList = { toggle() {} };
globalThis.$ = id => id === "row-find" ? { value: want } : id === "rows" ? { querySelectorAll: () => { queries++; return rows; } } : { textContent: "" };
"""
    body = """
applyFind(); const empty = queries;
want = "a"; applyFind(); const typed = queries;
want = ""; applyFind(); const cleared = queries;   // 강조가 남아 있으니 한 번은 지운다
applyFind(); const again = queries;
console.log(JSON.stringify({ empty, typed, cleared, again }));
"""
    assert run_node(tmp_path, body, parts=[prelude, code]) == {"empty": 0, "typed": 1, "cleared": 2, "again": 2}


def test_search_draft_ignores_table_pick_only(tmp_path):
    code = index_slice("let draftBase", "const searchDraft")
    prelude = """
const S = { base: "" }; let view = "cards", key = "k0"; const bodyKey = () => key;
"""
    body = """
S.base = "t1"; const opened = draftChanged();       // 테이블만 고른 상태
key = "k1"; const edited = draftChanged();           // 조건을 바꿈
S.base = "t2"; key = "k2"; const other = draftChanged();   // 다른 테이블을 열면 그 직후가 기준
view = "flow"; const flow = draftChanged();
console.log(JSON.stringify({ opened, edited, other, flow }));
"""
    assert run_node(tmp_path, body, parts=[prelude, code]) == {"opened": False, "edited": True, "other": False, "flow": True}
