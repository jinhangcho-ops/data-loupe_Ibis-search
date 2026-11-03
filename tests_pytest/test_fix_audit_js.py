"""화면 JS 감사 수정(E 묶음)을 node로 돌려 본다. node가 없으면 건너뛴다.
순수 함수는 static/ 파일을 이어 붙여 부르고, index.html·prep.html 안의 인라인 코드는 해당 구간만 잘라 가짜 화면(DOM) 위에서 돌린다.
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
    # lib 파일은 "lib/dash_calc.js"처럼 부른다
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

# 표 그리기(common.js mountTable)를 돌려 보는 가짜 host: tbody에 쓴 HTML을 모아 두고, 그 안의 <tr>을 칸(td) 가짜로 돌려준다.
# 펼친 칸(expandCell)은 expanded에 [줄 번호, 칸 번호]로 남고, tbodyWrites는 tbody를 몇 번 다시 썼는지.
GRID_HOST = r"""
let tbodyWrites = 0; const expanded = [];
function makeHost() {
  const tbody = { html: "", set innerHTML(v) { tbodyWrites++; this.html = v; }, get innerHTML() { return this.html }, getBoundingClientRect: () => ({ top: 0 }),
    querySelectorAll() {
      return [...this.html.matchAll(/<tr>([^]*?)<\/tr>/g)].map((m, p) => ({ dataset: {}, offsetHeight: 40,
        cells: m[1].split("<td").slice(1).map((_, c) => ({ dataset: {}, textContent: "x", replaceChildren() { expanded.push([p, c]); } })) }));
    } };
  return { isConnected: true, html: "", set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html }, querySelector: () => tbody, tbody };
}
const rowsHtml = host => [...host.tbody.html.matchAll(/<tr>([^]*?)<\/tr>/g)].map(m => m[1]);
"""


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
    got = run_node(tmp_path, body, files=["lib/dash_calc.js"])
    assert got == {"len": 200, "last": 200, "again": 5}  # 서버(dash_store)는 order 200개 넘으면 저장을 거부한다


# ---------- 6 비동기 경쟁 ----------


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
    assert "목록에 안 보이는 2개도 고름" in got["text"]


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


# ---------- 7 나머지 ----------


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
    """빈 찾기는 강조·거름이 남지 않은 표를 다시 그리지 않는다(1만 행을 한 글자마다 다시 쓰지 않게). 찾은 뒤 비우면 강조를 지우려 한 번은 다시 그린다."""
    code = index_slice("function applyFind() {", "\nlet findTimer")
    prelude = GRID_HOST + """
const host = makeHost(), box = { value: "" }, count = { textContent: "" };
globalThis.$ = id => id === "rows" ? host : id === "row-find" ? box : id === "find-count" ? count : { value: "40" };
"""
    body = """
resultTable(host, { columns: ["a"], rows: [{ a: "abc" }, { a: "xyz" }] });
const set = v => { box.value = v; applyFind(); return tbodyWrites; };
const empty = set(""), typed = set("a"), shownMark = host.tbody.html.includes("<mark>a</mark>bc"), cleared = set(""), again = set("");
console.log(JSON.stringify({ empty, typed, cleared, again, shownMark, noMarkLeft: !host.tbody.html.includes("<mark>"), count: count.textContent }));
"""
    got = run_node(tmp_path, body, parts=[STUBS, COMMON, prelude, code])
    assert got == {"empty": 0, "typed": 1, "cleared": 2, "again": 2, "shownMark": True, "noMarkLeft": True, "count": ""}


def test_search_draft_ignores_table_pick_only(tmp_path):
    code = index_slice("let draftBase", "const searchDraft")
    prelude = """
const S = { base: "" }; let view = "cards", key = "k0"; const bodyKey = () => key;
"""
    body = """
S.base = "t1"; const opened = draftChanged();       // 테이블만 고른 상태
key = "k1"; const edited = draftChanged();           // 조건을 바꿈
S.base = "t2"; key = "k2"; const other = draftChanged();   // 다른 테이블을 열면 그 직후가 기준
view = "sentence"; const sentence = draftChanged();
console.log(JSON.stringify({ opened, edited, other, sentence }));
"""
    assert run_node(tmp_path, body, parts=[prelude, code]) == {"opened": False, "edited": True, "other": False, "sentence": True}
