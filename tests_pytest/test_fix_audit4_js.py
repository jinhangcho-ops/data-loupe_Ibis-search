"""4차 묶음 4E(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_shortcuts_js.py의 가짜 화면(FAKE)과 같은 방식: 화면 파일에서 해당 함수만 잘라 돌린다."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT
from test_shortcuts_js import DRAFT, FAKE, MENU, TIP

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

STATIC = ROOT / "static"


def page(name):
    return (STATIC / name).read_text(encoding="utf-8")


def fn(src, start, end="\n}\n"):
    a = src.index(start)
    return src[a:src.index(end, a) + len(end)]


def run(tmp_path, body, *parts, fake=True):
    script = tmp_path / "t.js"
    script.write_text((FAKE if fake else "") + "\n".join(parts) + "\n" + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_4e1_dash_restore_keeps_last_values(tmp_path):
    html = page("dashboard/dashboard.html")
    funcs = fn(html, "let restoreSeq", "\n") + fn(html, "const lastVals", "\n") + fn(html, "const sigOf", "\n") + fn(html, "const rememberVals", "\n") + fn(html, "async function restoreDash(d) {")
    body = """
(async () => {
  const card = { id: "c1", kind: "number", source: { key: "k", table: "t" }, title: "매출", data: { values: [1, 2, 3] }, liveData: { x: 1 }, stored: { values: [9] } };
  let dashId = "d1", quiet = false; const dashboards = [{ id: "d1" }];
  const board = { cards: [card], palette: [], star: {}, set(c) { this.cards = c; } };
  const $ = () => ({ value: "" }); const needsValues = c => c; const boardChanged = () => {};
  const connected = false, panelHost = {}, say = () => {};
  %s
  const before = { id: "d1", palette: [], north_star: {}, cards: [{ id: "c1", kind: "number", source: { key: "k", table: "t" }, title: "매출" }] };
  rememberVals();                 // dashState가 0.5초마다 하는 일
  board.cards = [];               // 카드를 지움
  await restoreDash(before);      // Ctrl+Z
  const out = { deleted: board.cards.map(c => [c.id, c.data, c.stored]) };
  rememberVals();
  // 종류가 바뀐 되돌리기도 예전 값을 둔다
  board.set([{ ...board.cards[0], kind: "chart", data: { other: 1 } }]);
  rememberVals();
  await restoreDash(before);
  out.kindChanged = board.cards.map(c => c.data);
  // 다른 대시보드의 같은 카드 id는 섞이지 않는다
  dashId = "d2"; dashboards.push({ id: "d2" }); board.cards = [];
  await restoreDash({ ...before, id: "d2" });
  out.other = board.cards.map(c => c.data);
  console.log(JSON.stringify(out));
})();""" % funcs
    got = run(tmp_path, body, fake=False)
    assert got["deleted"] == [["c1", {"values": [1, 2, 3]}, {"values": [9]}]]
    assert got["kindChanged"] == [{}]  # 6E3: 종류가 바뀐 카드는 예전 값을 버린다
    assert got["other"] == [{}]


def test_4e2_prep_restore_drops_removed_copy_edits(tmp_path):
    html = page("prep/prep.html")
    body = """
let edits = {}, copies = [], drops = [], filters = [], dedupe = [], sorts = [], formulas = [], pca = {}, geoSteps = [];
const prof = { columns: [{ name: "a" }, { name: "b" }] };
const $ = id => ({ value: "", checked: false });
const setKeep = () => {}, showTarget = () => {}, fail = m => { console.log(JSON.stringify({ fail: m })); process.exit(0); };
%s
const newEdit = () => ({ keep: true, rename: "", ops: {} });
const mk = (e, c) => ({ edits: structuredClone(e), copies: c, drops: [], filters: [], dedupe: [], sorts: [], formulas: [], pca: { columns: [], n: 2 }, geo: [], downcast: false, target: { kind: "file", format: "csv", name: "" } });
edits = { a: newEdit(), b: newEdit() };
const A = mk(edits, []);
copies.push({ from: "a", name: "a_복사" }); edits["a_복사"] = newEdit();
restore(A);
const keys = Object.keys(edits);
restore(mk(edits, [...copies]));   // 지금 저장되는 모양을 다시 열어도 실패하지 않는다
console.log(JSON.stringify({ keys, copies }));""" % fn(html, "function restore(c) {")
    got = run(tmp_path, body, fake=False)
    assert got == {"keys": ["a", "b"], "copies": []}


def test_4e5_combo_filters_after_composition_end(tmp_path):
    body = """
const dl = new E("datalist"); [["상품명", "문자"], ["상품코드", ""], ["주문일", "날짜"]].forEach(([v, l]) => { const o = new E("option"); o.value = v; o.label = l; dl.kids.push(o); }); lists.L = dl;
const inp = new E("input"); inp.setAttribute("data-list", "L");
const names = () => menuList.children.map(c => c.textContent);
const out = {};
fire("focusin", inp); out.hints = menuList.children.map(c => c.getAttribute("data-hint"));
inp.value = "주문"; fire("input", inp, { isComposing: true }); out.composing = names();
fire("compositionend", inp); out.afterEnd = names();
console.log(JSON.stringify(out));"""
    got = run(tmp_path, body, MENU)
    assert got["hints"] == ["문자", None, "날짜"]  # 라벨이 없으면 힌트도 없다
    assert got["composing"] == ["상품명", "상품코드", "주문일"] and got["afterEnd"] == ["주문일"]


def test_4e6_tip_hides_when_host_gone_and_skips_mouse_focus(tmp_path):
    body = """
const host = new E("button"); host.setAttribute("title", "저장해요");
fire("mouseover", host); flush();
const out = { shown: !tipBox.classList.contains("hidden") };
Object.defineProperty(host, "isConnected", { value: false });
fire("mouseover", new E("div"));
out.hiddenAfterMove = tipBox.classList.contains("hidden");
// 마우스로 눌러 생긴 포커스는 말풍선을 띄우지 않는다
const b = new E("button"); b.setAttribute("title", "누른 단추");
fire("mousedown", b); fire("focusin", b); flush();
out.clickFocus = [tipBox.classList.contains("hidden"), b.getAttribute("title")];
console.log(JSON.stringify(out));"""
    got = run(tmp_path, body, TIP)
    assert got == {"shown": True, "hiddenAfterMove": True, "clickFocus": [True, "누른 단추"]}


def test_4e4_4e8_shortcuts_commit_and_hidden_find(tmp_path):
    body = """
const calls = [], inp = new E("input"); inp.blur = () => calls.push("blur"); inp.focus = () => calls.push("focus");
document.activeElement = inp;
const box = new E("input"); box.offsetParent = null; let focused = 0; box.focus = () => focused++;
Shortcuts({ save: () => calls.push("save"), run: () => { calls.push("run"); return true; }, find: () => box });
const key = k => fire("keydown", new E("body"), { key: k, ctrlKey: true, shiftKey: false, altKey: false });
key("s"); key("Enter");
const f = key("f");             // 숨은 칸(offsetParent null)에는 포커스도 못 주고 기본 찾기도 막지 않는다
console.log(JSON.stringify({ calls, findPrevented: f.defaultPrevented, focused }));"""
    got = run(tmp_path, body, DRAFT)
    assert got["calls"] == ["blur", "focus", "save", "blur", "focus", "run"]
    assert got["findPrevented"] is False and got["focused"] == 0


def test_4e_static_edits():
    query, dash, prep = page("query/index.html"), page("dashboard/dashboard.html"), page("prep/prep.html")
    assert 'view === "cards" ? searchState() : null' in query                    # 4E3
    assert 'if (!e.target.closest(".menu")) $("d-add-pop")' in dash              # 4E7
    assert "prepUndo.reset()" in fn(prep, "function resetTable() {")             # 4E8(5E2: prof를 비운 뒤 reset)
    assert "draftBase" not in fn(query, "async function restoreSearch(d) {")      # 4E8
    for src in (query, dash, prep, page("core/common.js"), page("core/dash_shelf.js")):
        assert not [ln for ln in src.splitlines() if 'e.key === "Enter")' in ln and "onkeydown" in ln]   # Enter 칸은 Ctrl+Enter를 제외
