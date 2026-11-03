"""9차 묶음 9J(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit8_js.py와 같은 방식: common.js의 표 부분을 가짜 화면 위에서 진짜 코드로 돌린다."""
from test_fix_audit7_js import needs_node, run
from test_fix_audit7_vscroll import pieces
from test_fix_audit8_js import HARNESS8, HELPERS8


def js(tmp_path, body):
    return run(tmp_path, body, HARNESS8, pieces(), HELPERS8, fake=False)


# ---------- 9J1 글자 수를 바꾸면 크기 재기 줄도 새 글자 수로 다시 만든다 ----------

@needs_node
def test_9j1_sizer_follows_maxlen_change_without_remount(tmp_path):
    got = js(tmp_path, r"""
const rows = Array.from({ length: 400 }, (_, i) => ({ k: "k" + i, v: (i === 7 ? "가" : "a").repeat(120) }));
const host = makeHost();
resultTable(host, { columns: ["k", "v"], rows });
const sizerLen = () => Math.max(...[...host.tbody.html.matchAll(/<tr class="vsize"[\s\S]*?<\/tr>/g)].flatMap(m => [...m[0].matchAll(/>([a가]{5,}…?)</g)].map(x => x[1].length)));
const before = sizerLen();
maxlenValue = "0";                    // 글자 40 -> 0(모두): 쿼리 화면은 피벗·SQL 결과 표를 다시 올리지 않는다
scrollY = 4000; gridDraw(host._grid);
const after = sizerLen();
maxlenValue = "40"; scrollY = 8000; gridDraw(host._grid);
console.log(JSON.stringify({ before, after, back: sizerLen() }));
""")
    assert got == {"before": 41, "after": 120, "back": 41}


# ---------- 9J3 크기 재기 줄은 가장 긴 중첩 값의 행을 고른다 ----------

@needs_node
def test_9j3_sizer_picks_longest_nested_value(tmp_path):
    got = js(tmp_path, r"""
const rows = Array.from({ length: 300 }, (_, i) => ({ k: "k", v: i === 0 ? [] : i === 5 ? Array.from({ length: 12 }, (_, j) => "item" + j) : [i] }));
const host = makeHost();
resultTable(host, { columns: ["k", "v"], rows });
console.log(JSON.stringify({ longest: host.tbody.html.match(/<tr class="vsize"[\s\S]*?<\/tr>/g).some(t => t.includes("item11")) }));
""")
    assert got == {"longest": True}


# ---------- 9J5 중첩 값 칸은 키보드(Tab, Enter, 스페이스)로도 펼친다 ----------

@needs_node
def test_9j5_nested_cell_is_focusable_and_toggles_with_keys(tmp_path):
    got = js(tmp_path, r"""
const html = cell({ a: 1 });
const td = { dataset: { full: "{\n  a: 1\n}" }, textContent: "{a: 1}", closest(sel) { return sel === "td.nest" ? this : null; }, matches(sel) { return sel === "td.nest"; }, replaceChildren(x) { this.expanded = !!x; } };
let prevented = 0;
const key = (k, mods = {}) => handlers.keydown.forEach(f => f({ key: k, ...mods, target: td, preventDefault() { prevented++; } }));
key("a"); key("Enter", { ctrlKey: true }); const none = td.dataset.short === undefined;  // Ctrl+Enter는 다시 실행 단축키에 넘긴다
key("Enter"); const opened = td.dataset.short !== undefined;
key(" "); const closed = td.dataset.short === undefined;
console.log(JSON.stringify({ tab: html.includes('class="nest" tabindex="0"'), none, opened, closed, prevented }));
""")
    assert got == {"tab": True, "none": True, "opened": True, "closed": True, "prevented": 2}
