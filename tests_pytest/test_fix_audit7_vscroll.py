"""7차 7P1: 큰 결과 표는 보이는 행만 tr로 그린다(common.js mountTable). node로 가짜 화면 위에서 돌려 본다. node가 없으면 건너뛴다.
확인: (1) 1만 행에서도 tr이 보이는 수 근처뿐이고 위·아래 빈 줄이 전체 행 높이를 채운다 (2) 스크롤하면 그 위치의 행이 그려진다
(3) 작은 표는 예전과 같은 HTML (4) 찾기는 안 보이는 행의 값도 찾는다 (5) 펼친 중첩 값은 다시 그려도 펼친 채 (6) aria 행 수."""
import json
import re

import pytest

from conftest import ROOT
from test_fix_audit7_js import needs_node, page, run

COMMON = page("core/common.js")


def line(src, start):
    return next(x for x in src.splitlines() if x.startswith(start))


def pieces():
    """common.js에서 표 그리기에 필요한 부분만(esc·el, 중첩 값 도우미부터 클릭 처리까지, numCol)."""
    a = COMMON.index("const isNested")
    b = COMMON.index("// 메시지 칸에 글자를 쓴다")
    return "\n".join([line(COMMON, "const esc = "), line(COMMON, "const el = "), COMMON[a:b]])


HARNESS = r"""
let scrollY = 0, maxlenReads = 0, maxlenValue = "40";
globalThis.innerHeight = 600;
const handlers = {};
globalThis.document = { documentElement: {}, body: {}, addEventListener: (t, f) => { (handlers[t] ??= []).push(f); }, createElement: () => ({ append() {} }) };
globalThis.getComputedStyle = () => ({ overflowY: "visible" });
globalThis.requestAnimationFrame = f => f();
const overrides = {};
const $ = id => { if (id in overrides) return overrides[id]; if (id === "maxlen") { maxlenReads++; return { value: maxlenValue }; } return null; };
"""

HELPERS = r"""
function makeHost() {
  const tbody = {
    html: "", set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html }, getBoundingClientRect: () => ({ top: -scrollY }),
    querySelectorAll(sel) {
      const re = sel === "tr[data-p]" ? /<tr data-p="(\d+)"[^>]*>(.*?)<\/tr>/g : /<tr>(.*?)<\/tr>/g, out = [];
      let m, k = 0;
      while ((m = re.exec(this.html))) {
        const cells = (m[2] ?? m[1]).split("<td").slice(1).map(() => ({ dataset: {}, textContent: "x", replaceChildren() { this.expanded = true; } }));
        out.push({ dataset: { p: m[2] === undefined ? undefined : m[1] }, cells, offsetTop: k++ * 40, offsetHeight: 40 });
      }
      return out;
    },
  };
  return { isConnected: true, parentElement: null, html: "", set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html }, querySelector: () => tbody, tbody };
}
const countTr = host => (host.tbody.html.match(/<tr data-p=/g) ?? []).length;
const firstP = host => +/<tr data-p="(\d+)"/.exec(host.tbody.html)[1];
const lastP = host => { const all = [...host.tbody.html.matchAll(/<tr data-p="(\d+)"/g)]; return +all.at(-1)[1]; };
const pads = host => [...host.tbody.html.matchAll(/class="vpad"[^>]*style="height:(\d+)px"/g)].map(m => +m[1]);
const big = (n, cols = 20) => {
  const columns = Array.from({ length: cols }, (_, i) => "col" + i), rows = [];
  for (let i = 0; i < n; i++) { const r = {}; for (const c of columns) r[c] = c === "col0" ? "row-" + i : i; rows.push(r); }
  return { columns, rows };
};
"""


def js(tmp_path, body):
    return run(tmp_path, body, HARNESS, pieces(), HELPERS, fake=False)


@needs_node
def test_7p1_10000_rows_draw_only_near_visible(tmp_path):
    body = r"""
const host = makeHost(), d = big(10000);
resultTable(host, d);
const first = { n: countTr(host), from: firstP(host), to: lastP(host), pads: pads(host), aria: /aria-rowcount="(\d+)"/.exec(host.html)[1],
  rowindex: /<tr data-p="0" aria-rowindex="(\d+)"/.exec(host.tbody.html)[1], trInSkeleton: (host.html.match(/<tr/g) ?? []).length };
scrollY = 200000;                       // 5000번째 줄 근처(줄 높이 40px)
gridRedraw();
const mid = { n: countTr(host), from: firstP(host), to: lastP(host), pads: pads(host), has5000: host.tbody.html.includes("row-5000<") };
scrollY = 400000 - 600;                 // 맨 끝
gridRedraw();
const end = { to: lastP(host), n: countTr(host), pads: pads(host) };
console.log(JSON.stringify({ first, mid, end }));
"""
    got = js(tmp_path, body)
    f, m, e = got["first"], got["mid"], got["end"]
    assert f["trInSkeleton"] == 1 and f["aria"] == "10001" and f["rowindex"] == "2"  # 머리글 한 줄만, 전체 행 수 알림, 0번 행은 2번째 줄
    assert f["from"] == 0 and 15 <= f["n"] <= 80 and f["pads"] == [(10000 - f["n"]) * 40]  # 아래 빈 줄이 안 그린 행의 높이만큼
    assert m["has5000"] and 15 <= m["n"] <= 80 and 4900 < m["from"] <= 5000 <= m["to"]
    assert sum(m["pads"]) + m["n"] * 40 == 10000 * 40  # 전체 높이가 늘 전체 행 기준
    assert e["to"] == 9999 and e["n"] <= 80 and len(e["pads"]) == 1  # 끝에서는 아래 빈 줄이 없다


@needs_node
def test_7p1_small_table_html_is_unchanged(tmp_path):
    body = r"""
const rows = [{ a: 1234567, b: "가나다", c: { x: 1, y: [1, 2] }, d: "<b>&" }, { a: 2, b: "x".repeat(60), c: null, d: "12345678901234567" }];
for (let i = 0; i < 198; i++) rows.push({ a: i, b: "r" + i, c: [i], d: "" });      // 200행 = 예전처럼 전부 그린다
const columns = ["a", "b", "c", "d"], host = makeHost();
resultTable(host, { columns, rows });
const old = `<table><thead><tr>${columns.map(c => `<th class="${numCol(rows, c) ? "num" : ""}">${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
  rows.map(r => "<tr>" + columns.map(c => cell(r[c])).join("") + "</tr>").join("") + "</tbody></table>";
const empty = makeHost(); resultTable(empty, { columns, rows: [] });
const over = makeHost(); resultTable(over, { columns, rows: rows.concat([{ a: 0, b: "", c: 0, d: "" }]) });
console.log(JSON.stringify({ same: host.html === old, rows: (host.html.match(/<tr>/g) ?? []).length, noAria: !host.html.includes("aria-"), empty: empty.html, overVirtual: over.html.includes("aria-rowcount") }));
"""
    got = js(tmp_path, body)
    assert got == {"same": True, "rows": 201, "noAria": True, "empty": '<span class="empty">결과가 없습니다.</span>', "overVirtual": True}


@needs_node
def test_7p1_find_searches_rows_that_are_not_drawn(tmp_path):
    body = r"""
const host = makeHost(), d = big(10000);
d.rows[7777].col3 = "Needle-XYZ 값"; d.rows[4242].col5 = "xneedle-xyz";
resultTable(host, d);
const g = host._grid;
const before = /<tr data-p="\d+"[^>]*>(?:(?!<\/tr>).)*needle/i.test(host.tbody.html);   // 그린 행(data-p) 안에는 아직 없다(크기 재기 줄에는 있다)
gridFind(g, "  needle-xyz ");
const one = { view: g.view, marked: host.tbody.html.includes("<mark>Needle-XYZ</mark>"), n: countTr(host), p0: firstP(host) };
gridFind(g, "no-such-value");
const none = { view: g.view.length, n: countTr(host) };
gridFind(g, "");
const cleared = { view: g.view, n: countTr(host), want: g.want, marks: host.tbody.html.includes("<mark>") };
gridFind(g, "1,234");                                // 숫자 칸은 쉼표가 든 보이는 글자로 찾는다: 1234는 모든 컬럼에 있어 많은 행이 맞는다
console.log(JSON.stringify({ before, one, none, cleared, rowsHit: g.view.length > 1 }));
"""
    got = js(tmp_path, body)
    assert got["before"] is False  # 처음 그린 창(0번 근처)에는 없다
    assert got["one"] == {"view": [4242, 7777], "marked": True, "n": 2, "p0": 0}
    assert got["none"] == {"view": 0, "n": 0}
    assert got["cleared"]["view"] is None and got["cleared"]["want"] == "" and got["cleared"]["marks"] is False and got["cleared"]["n"] <= 80


@needs_node
def test_7p1_find_on_small_table_and_count_text(tmp_path):
    index = page("query/index.html")
    a = index.index("function applyFind() {")
    code = index[a:index.index("\nlet findTimer", a)]
    body = r"""
const host = makeHost(), box = { value: "" }, count = { textContent: "" };
Object.assign(overrides, { rows: host, "row-find": box, "find-count": count });
""" + code + """
const d = { columns: ["a", "b"], rows: [{ a: "apple", b: 1 }, { a: "banana", b: 2 }, { a: "grape", b: 3 }] };
resultTable(host, d);
box.value = "AN"; applyFind();
const found = { count: count.textContent, html: host.tbody.html.includes("b<mark>an</mark>ana"), apple: host.tbody.html.includes("apple") };
box.value = ""; applyFind();
const cleared = { count: count.textContent, rows: (host.tbody.html.match(/<tr>/g) ?? []).length };
const before = maxlenReads; box.value = ""; applyFind(); const idle = maxlenReads === before;   // 빈 찾기는 다시 그리지 않는다
console.log(JSON.stringify({ found, cleared, idle }));
"""
    got = js(tmp_path, body)
    assert got == {"found": {"count": "1 / 3행", "html": True, "apple": False}, "cleared": {"count": "", "rows": 3}, "idle": True}


@needs_node
def test_7p1_open_nested_cell_stays_open_when_redrawn_and_maxlen_read_once(tmp_path):
    body = r"""
const host = makeHost(), d = big(1000, 3);
resultTable(host, d);
const g = host._grid;
g.open.set(3, new Set([1]));            // 3번 행의 1번 칸을 펼쳐 둔 상태
scrollY = 100000; gridRedraw();         // 멀리 갔다가
scrollY = 0; gridRedraw();              // 돌아오면
const again = g.extra.has(3);           // 펼쳐 둔 줄은 늘어난 높이를 기억한다(다시 그릴 때 잰다)
maxlenReads = 0;
const h2 = makeHost(); resultTable(h2, big(150, 20));   // 20컬럼 x 150행 = 3000칸
console.log(JSON.stringify({ again, maxlenReads }));
"""
    got = js(tmp_path, body)
    assert got["again"] is True
    assert got["maxlenReads"] <= 2  # 칸마다(3000번) 읽지 않고 표마다 한 번(재 그림 포함 소수)
