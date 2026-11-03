"""8차 묶음 8E(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit7_vscroll.py와 같은 방식: common.js의 표 부분을 가짜 화면 위에서 진짜 함수로 돌린다."""

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


