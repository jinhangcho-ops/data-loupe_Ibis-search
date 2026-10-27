"""5차 묶음 5E(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit4_js.py와 같은 방식: 화면 파일에서 해당 함수만 잘라 가짜 화면(FAKE) 위에서 돌린다."""
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


def test_5e1_select_menu_ignores_ctrl_keys(tmp_path):
    body = """
const sel = new E("select"); sel.options = [new E("option")];
const out = {};
for (const [k, extra] of [["Enter", { ctrlKey: true }], ["Enter", { metaKey: true }], [" ", { ctrlKey: true }], ["ArrowDown", { ctrlKey: true }], ["Enter", {}]]) {
  const ev = fire("keydown", sel, { key: k, ctrlKey: false, metaKey: false, ...extra });
  (out.prevented ??= []).push(ev.defaultPrevented);
}
console.log(JSON.stringify(out));"""
    got = run(tmp_path, body, MENU)
    assert got["prevented"] == [False, False, False, False, True]


def test_5e2_prep_table_switch_undo_does_not_return_to_old_table(tmp_path):
    html = page("prep/prep.html")
    onchange = [l for l in html.splitlines() if l.startswith('$("base").onchange')][0]
    body = """
const els = {}; const $ = id => els[id] ??= { value: "", replaceChildren() {}, textContent: "" };
let draftBase = "", token = 0, status = "", prof = { t: "A" }, preview = null, shot = null, edits = { x: 1 }, copies = [], filters = [], dedupe = [], sorts = [], drops = [], deriv = [], formulas = [], pca = {}, geoSteps = [], geoTables = [], geoRefCols = [], picked = "", specView = null, first = null;
const resetRun = () => {}, fail = () => {}, showAll = () => {};
const S = { base: "A" }; const restored = [];
%s
const config = () => ({ base: S.base, edits });
const prepUndo = Undo(() => prof ? config() : null, async c => { restored.push(c.base); S.base = c.base; edits = c.edits; prof = { t: c.base }; }, { poll: 0, quiet: 600 });
async function openTable(base) { S.base = base; resetTable(); prof = { t: base }; edits = { y: 1 }; }
%s
(async () => {
  let t = 0;
  prepUndo.tick(t);
  edits = { x: 2 }; prepUndo.tick(t += 1000); prepUndo.tick(t += 1000);
  $("base").value = "B";
  await $("base").onchange();       // 테이블 B로
  prepUndo.tick(t += 100); prepUndo.tick(t += 1000);
  const sizes = prepUndo.size();
  const ok = await prepUndo.undo();
  console.log(JSON.stringify({ sizes, ok, restored }));
})();""" % (fn(html, "function resetTable() {"), onchange)
    got = run(tmp_path, body, DRAFT.split("// 글자를 쓰는 칸")[0].split("// ---------- 화면 편집 되돌리기·단축키 ----------")[1], fake=False)
    assert got["restored"] == [] and got["ok"] is False  # B의 첫 실제 상태가 기준이라 A로 돌아가지 않는다


def test_5e2_undo_reset_is_ignored_while_restoring(tmp_path):
    part = DRAFT.split("// 글자를 쓰는 칸")[0].split("// ---------- 화면 편집 되돌리기·단축키 ----------")[1]
    body = """
(async () => {
  let st = { v: 1 }; let u;
  u = Undo(() => st, async s => { st = s; u.reset(); }, { poll: 0, quiet: 100 });  // 복원 안에서 테이블을 다시 열어 reset이 불린다
  u.tick(0); st = { v: 2 }; u.tick(10); u.tick(200);
  st = { v: 3 }; u.tick(300); u.tick(500);
  const ok = await u.undo();
  console.log(JSON.stringify({ ok, sizes: u.size() }));
})();"""
    got = run(tmp_path, body, part, fake=False)
    assert got == {"ok": True, "sizes": [1, 1]}


def test_5e3_add_dash_pop_stays_open_on_menu_click():
    html = page("prep/prep.html")
    assert '.closest(".pop, .menu")' in fn(html, 'document.addEventListener("click", e => { if (!e.target.closest', "\n")


def _dash_funcs():
    html = page("dashboard/dashboard.html")
    return fn(html, "let restoreSeq", "\n") + fn(html, "const lastVals", "\n") + fn(html, "const sigOf", "\n") + fn(html, "const rememberVals", "\n") + fn(html, "async function restoreDash(d) {")


DASH_BODY = """
(async () => {
  const src = x => ({ key: "k", table: "t", body: { x } });
  const card = { id: "c1", kind: "chart", source: src("a"), title: "t", data: { values: [1] } };
  let dashId = "d1", quiet = false; const dashboards = [{ id: "d1" }];
  const board = { cards: [card], palette: [], star: {}, set(c) { this.cards = c; } };
  const $ = () => ({ value: "" }); const needsValues = c => (c.note = undefined, c); const boardChanged = () => {};
  let connected = %s; const recomputed = []; const panelHost = { recompute: async c => { recomputed.push(c.id); } }; const say = () => {};
  %s
  const before = { id: "d1", palette: [], north_star: {}, cards: [{ id: "c1", kind: "chart", source: src("a"), title: "t" }] };
  rememberVals();
  board.cards[0].source = src("b"); board.cards[0].data = { values: [2] };   // 차트 설정을 바꾸면 값도 다시 구해진다
  rememberVals();
  await restoreDash(before);                                                  // Ctrl+Z
  await new Promise(r => setImmediate(r));
  const c = board.cards[0];
  console.log(JSON.stringify({ recomputed, note: c.note ?? null, data: c.data }));
})();"""


def test_5e4_dash_restore_recomputes_when_source_differs(tmp_path):
    got = run(tmp_path, DASH_BODY % ("true", _dash_funcs()), fake=False)
    assert got["recomputed"] == ["c1"]


def test_5e4_dash_restore_notes_when_not_connected(tmp_path):
    got = run(tmp_path, DASH_BODY % ("false", _dash_funcs()), fake=False)
    assert got["recomputed"] == [] and "다시 구해야" in got["note"] and got["data"] == {}


def test_5e5_shortcuts_commit_keeps_combo_quiet_and_reports_undo_failure(tmp_path):
    part = DRAFT.split("화면 되돌리기를 쓴다." + chr(10))[1]
    body = """
(async () => {
  const quiet = []; globalThis.quietFocus = a => { quiet.push(a.tagName); a.focus(); };
  const inp = new E("input"); inp.blur = () => {}; globalThis.document.activeElement = inp;
  const calls = [];
  Shortcuts({ save: () => calls.push("save"), undo: { undo: async () => false, redo: async () => false, why: () => "예전 표가 없어요" } });
  fire("keydown", new E("body"), { key: "s", ctrlKey: true, shiftKey: false, altKey: false });
  fire("keydown", new E("body"), { key: "z", ctrlKey: true, shiftKey: false, altKey: false });
  await tick(); await tick();
  const inX = () => true; const u = { undo: async () => true, redo: async () => false, why: () => "w" };
  const s = scoped(u, () => false);
  console.log(JSON.stringify({ quiet, calls, flashes, scoped: [await s.undo(), await scoped(u, inX).undo(), scoped(u, inX).why()] }));
})();"""
    got = run(tmp_path, body, part)
    assert got["quiet"] == ["INPUT"] and got["calls"] == ["save"]
    assert any("예전 표가 없어요" in f for f in got["flashes"])
    assert got["scoped"] == [False, True, "w"]


def test_5e5_undo_why_reports_restore_error(tmp_path):
    part = DRAFT.split("// 글자를 쓰는 칸")[0].split("// ---------- 화면 편집 되돌리기·단축키 ----------")[1]
    body = """
(async () => {
  let st = { v: 1 };
  const u = Undo(() => st, async () => { throw new Error("그 표가 없어요"); }, { poll: 0, quiet: 100 });
  u.tick(0); st = { v: 2 }; u.tick(10); u.tick(200);
  const ok = await u.undo();
  console.log(JSON.stringify({ ok, why: u.why() }));
})();"""
    assert run(tmp_path, body, part, fake=False) == {"ok": False, "why": "그 표가 없어요"}


def test_5e5_static_edits():
    prep, index, css, common = page("prep/prep.html"), page("query/index.html"), page("core/common.css"), page("core/common.js")
    add = fn(prep, "function addStep() {")
    assert "closeMenu()" in add
    assert "scoped(prepUndo, inPrep)" in prep and "scoped(searchUndo, inExplore)" in index
    assert "undo: () => inPrep()" not in prep and "undo: () => inExplore()" not in index
    assert "max-height:60vh" in [l for l in css.splitlines() if l.startswith(".tip, .toast")][0]
    assert "$(\"limit\").value = b.limit ?? \"\"" in index
    assert "searchUndo.reset()" in index
    assert "!comboQuiet" in [l for l in common.splitlines() if 'addEventListener("focusin", e => { const h = tipHost' in l][0]
