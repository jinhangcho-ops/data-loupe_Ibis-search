"""8차 묶음 8E(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit7_vscroll.py와 같은 방식: common.js의 표 부분을 가짜 화면 위에서 진짜 함수로 돌린다."""
import pytest

from test_fix_audit7_js import fn, needs_node, page, run
from test_fix_audit7_vscroll import HARNESS, pieces

HARNESS8 = HARNESS + r"""
let hidden = false;
const observed = new Set();
globalThis.ResizeObserver = class { constructor(f) { this.f = f; } observe(e) { observed.add(e); } unobserve(e) { observed.delete(e); } };
"""

HELPERS8 = r"""
function makeHost() {
  const tbody = {
    html: "", isConnected: true, set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html }, getBoundingClientRect: () => ({ top: hidden ? 0 : -scrollY }),
    querySelectorAll(sel) {
      const re = sel === "tr[data-p]" ? /<tr data-p="(\d+)"[^>]*>(.*?)<\/tr>/g : /<tr>(.*?)<\/tr>/g, out = [];
      let m, k = 0;
      while ((m = re.exec(this.html))) {
        const cells = (m[2] ?? m[1]).split("<td").slice(1).map(() => ({ dataset: {}, textContent: "x", replaceChildren() {} }));
        out.push({ dataset: { p: m[2] === undefined ? undefined : m[1] }, cells, offsetTop: hidden ? 0 : k++ * 40, offsetHeight: hidden ? 0 : 40 });
      }
      return out;
    },
  };
  return { isConnected: true, parentElement: null, html: "", set innerHTML(v) { this.html = v; if (v === "") tbody.isConnected = false; }, get innerHTML() { return this.html },
    querySelector: () => tbody, tbody, getClientRects: () => hidden ? [] : [1] };
}
const big = (n, cols = 20) => {
  const columns = Array.from({ length: cols }, (_, i) => "col" + i), rows = [];
  for (let i = 0; i < n; i++) { const r = {}; for (const c of columns) r[c] = c === "col0" ? "row-" + i : i; rows.push(r); }
  return { columns, rows };
};
const drawn = host => [...host.tbody.html.matchAll(/<tr data-p="(\d+)"[^>]*>(.*?)<\/tr>/g)].map(m => m[2]);
"""


def js(tmp_path, body, extra=""):
    return run(tmp_path, body, HARNESS8, pieces(), HELPERS8, extra, fake=False)


# ---------- 8E1 가려진 탭에서 찾기를 바꾸면, 보일 때 맞는 행으로 다시 그린다 ----------

@needs_node
def test_8e1_find_changed_while_hidden_redraws_when_shown(tmp_path):
    got = js(tmp_path, r"""
const host = makeHost();
hidden = true;
resultTable(host, big(1000));
gridFind(host._grid, "row-99");          // 가려진 채 찾기(집계 탭을 보는 동안)
hidden = false;
gridRedraw();                             // 결과 탭으로 돌아옴: ResizeObserver
const rows = drawn(host);
console.log(JSON.stringify({ n: rows.length, allMatch: rows.every(r => r.includes("row-99")), mark: host.tbody.html.includes("<mark>"), view: host._grid.view.length }));
""")
    assert got == {"n": 11, "allMatch": True, "mark": True, "view": 11}


@needs_node
def test_8e1_find_cleared_while_hidden_redraws_when_shown(tmp_path):
    got = js(tmp_path, r"""
const host = makeHost();
resultTable(host, big(1000));
gridFind(host._grid, "row-9");           // 111행이 맞는다
const before = drawn(host).length > 11;
hidden = true;
gridFind(host._grid, "");                // 가려진 채 찾기 지움
hidden = false;
gridRedraw();
const rows = drawn(host);
console.log(JSON.stringify({ before, first: rows[0].includes("row-0<") && rows[1].includes("row-1<"), mark: host.tbody.html.includes("<mark>") }));
""")
    assert got == {"before": True, "first": True, "mark": False}


# ---------- 8E2 그릴 때마다 크기 재기 줄을 다시 만들지 않는다 ----------

@needs_node
def test_8e2_sizer_built_once_across_redraws(tmp_path):
    got = js(tmp_path, r"""
const real = gridSizer; let calls = 0;
gridSizer = (g, max) => { calls++; return real(g, max); };
const host = makeHost();
resultTable(host, big(5000, 5));
const first = host.tbody.html.match(/<tr class="vsize".*?<\/tr>/g);
scrollY = 100000; gridRedraw();
scrollY = 150000; gridRedraw();
const last = host.tbody.html.match(/<tr class="vsize".*?<\/tr>/g);
console.log(JSON.stringify({ calls, same: JSON.stringify(first) === JSON.stringify(last), has: first.length > 0 }));
""")
    assert got == {"calls": 1, "same": True, "has": True}


# ---------- 8E4 버려진 표는 지켜보기를 풀고, 비워진 host의 표는 다시 그리지 않는다 ----------

@needs_node
def test_8e4_dropped_grids_are_unobserved(tmp_path):
    got = js(tmp_path, r"""
for (let run = 0; run < 5; run++) { const h = makeHost(); resultTable(h, big(500, 3)); h.isConnected = false; }   // sentences.js showResult: 실행마다 새 host
const before = observed.size;
gridRedraw();
console.log(JSON.stringify({ before, grids: GRIDS.size, observed: observed.size }));
""")
    assert got == {"before": 5, "grids": 0, "observed": 0}


@needs_node
def test_8e4_cleared_host_is_not_redrawn_and_remount_stays_observed(tmp_path):
    got = js(tmp_path, r"""
const a = makeHost(); resultTable(a, big(500, 3));
a.innerHTML = "";                               // index.html #pivot처럼 host를 비우고 _grid는 남김
const aBody = a.tbody.html; scrollY = 100000; gridRedraw();
const b = makeHost(); resultTable(b, big(500, 3));
const old = b._grid; resultTable(b, big(600, 3));  // 같은 host에 다시 그림: 옛 표만 버리고 host는 계속 지켜본다
gridRedraw();
console.log(JSON.stringify({ aUntouched: a.tbody.html === aBody, aGone: !observed.has(a), bObserved: observed.has(b), grids: GRIDS.size, oldGone: !GRIDS.has(old) }));
""")
    assert got == {"aUntouched": True, "aGone": True, "bObserved": True, "grids": 1, "oldGone": True}


# ---------- 8E6 크기 재기: 한글은 폭 2로 센다 ----------

@needs_node
def test_8e6_sizer_counts_wide_characters(tmp_path):
    got = js(tmp_path, r"""
const rows = Array.from({ length: 300 }, (_, i) => ({ name: i === 7 ? "가나다라마바사아자차" : "abcdefghijkl" }));
const host = makeHost(); resultTable(host, { columns: ["name"], rows });
const size = /class="vsize"[^>]*>(.*?)<\/tr>/.exec(host.tbody.html)[1];
console.log(JSON.stringify({ korean: size.includes("가나다라마바사아자차"), ascii: size.includes("abcdefghijkl") }));
""")
    assert got == {"korean": True, "ascii": False}


# ---------- 8E7 강조: 소문자로 바꾸면 길이가 달라지는 글자 ----------

@needs_node
def test_8e7_lit_keeps_positions_when_lowercase_changes_length(tmp_path):
    got = js(tmp_path, r"""
console.log(JSON.stringify({ dotted: lit("İstanbul", "stan"), plain: lit("A<b>c", "<b>"), none: lit("abc", "z"), upper: lit("ABC", "b") }));
""")
    assert got == {"dotted": "İstanbul", "plain": "A<mark>&lt;b&gt;</mark>c", "none": "abc", "upper": "A<mark>B</mark>C"}


# ---------- 8E8 이어붙인 출처 컬럼은 보이는 글(자르지 않은) 그대로 찾는다 ----------

@needs_node
def test_8e8_source_column_found_by_full_text(tmp_path):
    spec = fn(page("query/index.html"), "function gridSpec(")
    got = js(tmp_path, r"""
const S = { sorts: [], base: "t" };
""" + spec + r"""
const long = "x".repeat(50) + "needle";
const data = { columns: ["출처", "v"], rows: [{ 출처: long, v: "a" }, { 출처: "짧음", v: "b" }] };
const host = makeHost(); mountTable(host, gridSpec(data, { 출처: "이어붙인 테이블" }));
gridFind(host._grid, "needle");
const plain = makeHost(); mountTable(plain, gridSpec({ columns: ["출처"], rows: [{ 출처: long }] }, {}));
gridFind(plain._grid, "needle");   // 이어붙인 표가 아니면 다른 컬럼처럼 잘린 글로 찾는다
console.log(JSON.stringify({ union: host._grid.view, plain: plain._grid.view }));
""")
    assert got == {"union": [0], "plain": []}


# ---------- 8E3 기간을 건 채 설정을 바꾼 카드: 저장 값에 옛 설정의 값이 남지 않는다 ----------

@needs_node
def test_8e3_recompute_with_period_does_not_keep_old_stored_values(tmp_path):
    ctl, dash = page("dashboard/dash_ctl.js"), page("dashboard/dashboard.html")
    a = dash.index("  recompute: async")
    recompute = dash[a:dash.index("\n  star:", a)]
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => ({ id: c.id, source: c.source }) };
""" + fn(ctl, "function showLive") + "\n" + next(x for x in ctl.splitlines() if x.startswith("const storedCard")) + r"""
let dashId = "d1", valuesDirty = false, valuesSeq = 0, period = { from: "2026-01-01" }, lastSaved;
""" + next(x for x in dash.splitlines() if x.startswith("const cardSettings")) + "\n" + next(x for x in dash.splitlines() if x.startswith("const sigOf")) + r"""
const needsValues = c => c, scheduleSave = () => {};
let reply;
const api = async () => ({ data: reply });
const card = { id: 1, source: { table: "region" }, data: { labels: ["서울", "부산"] } };
const board = { cards: [card], ctx: () => period, draw() {} };
lastSaved = JSON.stringify([[], {}, [cardSettings(card)]]);   // 방금 저장한 대시보드
const panelHost = {
""" + recompute + r"""
};
(async () => {
  reply = { labels: ["서울"] };
  await panelHost.recompute(card);                 // 기간을 건 채 구함(설정은 그대로 = 저장된 값을 둔다)
  const kept = JSON.stringify(storedCard(card).data);
  card.source = { table: "product" }; reply = { labels: ["사과", "배"] };
  await panelHost.recompute(card);                 // 원천을 바꿈
  const saved = JSON.stringify(storedCard(card));
  period = null; reply = { labels: ["사과"] };
  await panelHost.recompute(card);                 // 기간을 풀면 새 값이 저장 대상
  console.log(JSON.stringify({ kept, saved, after: storedCard(card).data, shown: card.data.labels }));
})();
""", fake=False)
    assert got == {"kept": '{"labels":["서울","부산"]}', "saved": '{"id":1,"source":{"table":"product"},"data":{}}', "after": {"labels": ["사과"]}, "shown": ["사과"]}


# ---------- 8E9 다른 대시보드를 열면 첫 그리기에는 다시 애니메이션 ----------

@needs_node
def test_8e9_open_dash_resets_board_animation(tmp_path):
    dash = page("dashboard/dashboard.html")
    got = run(tmp_path, r"""
let openSeq = 0, dashId = "", dashName = "", dashMeta = {}, quiet = false, lastSaved = "", valuesDirty = false, seenAnimated;
const $ = () => ({ value: "", textContent: "" }), metaOf = () => ({}), needsValues = c => c, snapshot = () => "", boardChanged = () => {};
const panel = { close() {} }, dashUndo = { reset() {} }, ctl = { set: async () => {} };
const board = { animated: true, set() { seenAnimated = this.animated; } };
const api = async url => ({ dashboard: { name: "n", north_star: {}, palette: ["#000"], cards: [] } });
""" + fn(dash, "async function openDash(") + r"""
openDash("d2").then(() => console.log(JSON.stringify({ seenAnimated })));
""", fake=False)
    assert got == {"seenAnimated": False}


# ---------- 8E10 제목 없는 칠한 카드 그림은 비율대로 ----------

@needs_node
def test_8e10_untitled_painted_card_keeps_ratio_height(tmp_path):
    edit, board = page("dashboard/dash_edit.js"), page("core/board.js")
    a = edit.index("  async pictures(")
    method = edit[a:edit.index("\n  }\n", a) + 4]
    got = run(tmp_path, r"""
const sizes = [];
function canvas() {
  const c = { width: 0, height: 0, getContext: () => ({ fillRect() {}, fillText() {}, drawImage() {}, set fillStyle(v) {}, set font(v) {}, set textBaseline(v) {} }), toDataURL: () => "data:x" };
  return c;
}
global.document = { createElement: () => canvas(), documentElement: { dataset: { theme: "light" } } };
global.getComputedStyle = () => ({ getPropertyValue: n => n });
""" + board + r"""
const paintedCanvas = async (card, mode, width, height, scale) => { sizes.push(height); return Object.assign(canvas(), { width: width * scale, height: height * scale }); };
const grid = { querySelector: () => null };
const obj = {
""" + method + r"""
};
(async () => {
  const mk = title => ({ title, paint: () => null, id: "1" });
  const out = {};
  for (const title of ["", "이름"]) {
    const card = mk(title), self = Object.assign(Object.create(obj), { cards: [card], picked: new Set([card]), grid });
    sizes.length = 0; await self.pictures("wide", "light", "png"); out[title || "none"] = sizes[0];
  }
  console.log(JSON.stringify(out));
})();
""", fake=False)
    assert got == {"none": 450, "이름": 414}
