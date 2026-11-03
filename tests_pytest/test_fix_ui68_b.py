"""화면 점검 접근성(JS 묶음): × 단추 이름, 차트 캔버스 대체 글, Esc로 떠 있는 창 닫기, 정렬한 머리글의 aria-sort."""
from test_fix_audit_js import between, read, run_node


def test_x_buttons_have_names():
    assert 'textContent: "×", title: "필터 넣기 취소", ariaLabel: "필터 넣기 취소"' in read("dash_shelf.js")


ESC = between("common.js", "// Esc로 마지막에 뜬 창", "\n});\n") + "\n});\n"
ESC_HOST = r"""
let onKey = null, modals = [];
document.addEventListener = (n, f) => { if (n === 'keydown') onKey = f; };
document.querySelectorAll = sel => { assert.strictEqual(sel, '.modal:not(.hidden)'); return modals.filter(m => !m.hidden); };
globalThis.MouseEvent = class { constructor(type, o) { this.type = type; Object.assign(this, o); } };
const modal = (closes, hidden = false) => {
  const m = { hidden, isConnected: true, calls: [], classList: { contains: c => c === 'hidden' && m.hidden } };
  m.dispatchEvent = ev => { const e = { ...ev, target: m }; m.calls.push(e.type); if (closes && m.onclick) m.onclick(e); };
  if (closes) m.onclick = e => { if (e.target === m) m.hidden = true; };
  return m;
};
const key = (k, o = {}) => { const e = { key: k, defaultPrevented: false, isComposing: false, ...o, preventDefault() { this.defaultPrevented = true; } }; onKey(e); return e; };
"""


def test_escape_closes_last_open_modal_through_its_backdrop_click(tmp_path):
    body = ESC + r"""
const a = modal(true), b = modal(true), gone = modal(true, true);
modals = [a, b, gone];
const out = {};
out.first = [key('Escape').defaultPrevented, a.hidden, b.hidden, b.calls];  // 마지막에 열린 b만 닫힌다
out.menu = [key('Escape', { defaultPrevented: true }).defaultPrevented, a.hidden];  // 목록이 Esc를 썼으면 창은 그대로
out.ime = [key('Escape', { isComposing: true }).defaultPrevented, a.hidden];  // 한글 조합 중에는 그대로
out.other = [key('Enter').defaultPrevented, a.hidden];
out.second = [key('Escape').defaultPrevented, a.hidden];
const ask = modal(false); modals = [ask];  // 질문 창(바깥 누르기 닫기 없음)은 남고, 다른 Esc 처리도 막지 않는다
out.ask = [key('Escape').defaultPrevented, ask.hidden, ask.calls];
console.log(JSON.stringify(out));
"""
    got = run_node(tmp_path, ESC_HOST + body)
    assert got == {"first": [True, False, True, ["click"]], "menu": [True, False], "ime": [False, False],
                   "other": [False, False], "second": [True, True], "ask": [False, False, ["click"]]}


def test_sorted_header_has_aria_sort(tmp_path):
    spec = between("index.html", "function gridSpec(", "// 검색과 [SQL 보기]")
    body = r"""
globalThis.esc = s => s; globalThis.numCol = () => false; globalThis.S = { base: "", sorts: [] };
const heads = sorts => { S.sorts = sorts; return gridSpec({ columns: ["a", "b"], rows: [] }).head; };
console.log(JSON.stringify([heads(["a desc"]), heads(["b"]), heads([])]));
"""
    down, up, none = run_node(tmp_path, spec + body)
    assert down == '<th class="sorted" aria-sort="descending">a ↓</th><th class="">b</th>'
    assert up == '<th class="">a</th><th class="sorted" aria-sort="ascending">b ↑</th>'
    assert "aria-sort" not in none
