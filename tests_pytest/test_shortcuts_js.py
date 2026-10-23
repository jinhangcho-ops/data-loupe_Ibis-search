"""단축키·되돌리기·title 말풍선·datalist 대신 목록을 node로 돌려 본다. node가 없으면 건너뛴다.
draft.js(Undo·Shortcuts)와 common.js의 해당 구간(말풍선, 입력 칸 목록)을 잘라 가짜 화면 위에서 돌린다(test_fix_audit3_js.py와 같은 방식)."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

STATIC = ROOT / "static" / "core"
DRAFT = (STATIC / "draft.js").read_text(encoding="utf-8")
COMMON = (STATIC / "common.js").read_text(encoding="utf-8")


def cut(start, end):
    a = COMMON.index(start)
    return COMMON[a:COMMON.index(end, a)]


TIP = "let comboQuiet = false;\n" + cut("const tipBox", "// 잠깐 보이는 안내") + cut("// 잠깐 보이는 안내", "\n}\n") + "\n}\n"
MENU = cut("const menuBox", 'addEventListener("blur", closeMenu);') + 'addEventListener("blur", closeMenu);\n'

# 가짜 화면: 이벤트 위임(document 리스너)과 요소의 속성·dataset·closest·classList만 흉내 낸다.
FAKE = r"""
const handlers = {}, timers = [];
globalThis.innerWidth = 800; globalThis.innerHeight = 600;
globalThis.addEventListener = () => {};
globalThis.setTimeout = f => { timers.push(f); return timers.length; };
globalThis.clearTimeout = id => { if (id) timers[id - 1] = null; };
const flush = () => { while (timers.length) { const f = timers.shift(); f && f(); } };
class E {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.attrs = {}; this.dataset = {}; this.kids = []; this.parentElement = null; this.cls = new Set(); this.style = {}; this.value = ""; this.type = ""; this._text = "";
    this.classList = { add: c => this.cls.add(c), remove: c => this.cls.delete(c), contains: c => this.cls.has(c), toggle: (c, on) => (on ?? !this.cls.has(c)) ? this.cls.add(c) : this.cls.delete(c) };
    this.rect = { left: 10, right: 60, top: 100, bottom: 120, width: 50, height: 20 }; this.offsetWidth = 100; this.offsetHeight = 30; this.focused = 0; this.sib = null; }
  set className(v) { this.cls = new Set(v.split(" ").filter(Boolean)); }
  set innerHTML(v) { this.kids = [new E("input"), new E("div")]; this.kids.forEach(k => { k.parentElement = this; }); }
  get children() { return this.kids; }
  replaceChildren(...k) { this.kids = k; k.forEach(x => { x.parentElement = this; }); }
  set textContent(v) { this._text = v; this.kids = []; }
  get textContent() { return this._text + this.kids.map(k => k.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith("data-")) this.dataset[k.slice(5)] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  get isConnected() { return true; }
  getBoundingClientRect() { return this.rect; }
  scrollIntoView() {}
  focus() { this.focused++; globalThis.activeEl = this; }
  contains(o) { for (let x = o; x; x = x.parentElement) if (x === this) return true; return false; }
  matches(sel) { return sel === "select" ? this.tagName === "SELECT" : sel === "input[data-list]" ? this.tagName === "INPUT" && "list" in this.dataset : sel === "[title]" ? "title" in this.attrs : sel === ".hidden" ? this.cls.has("hidden") : false; }
  closest(sel) { for (let x = this; x; x = x.parentElement) if (x.matches(sel)) return x; return null; }
  dispatchEvent(ev) { fire(ev.type, this, ev); return true; }
}
const lists = {};
const fire = (type, target, extra = {}) => { const ev = { type, target, defaultPrevented: false, stopped: false, isComposing: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra }; (handlers[type] ?? []).forEach(f => f(ev)); return ev; };
globalThis.document = { body: { appendChild: x => x }, createElement: t => new E(t), getElementById: id => lists[id] ?? null, querySelector: () => globalThis.modalOpen ? {} : null,
  addEventListener: (t, f) => { (handlers[t] ??= []).push(f); } };
globalThis.quietFocus = a => a.focus();
const flashes = []; globalThis.flash = t => flashes.push(t);
const tick = () => new Promise(r => setImmediate(r));
"""


def run(tmp_path, body, *parts):
    script = tmp_path / "t.js"
    script.write_text(FAKE + "\n".join(parts) + "\n" + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# ---------- K1 되돌리기 스택 ----------

def test_undo_stack(tmp_path):
    body = """
(async () => {
  let st = { v: 1 }; const restored = [];
  const U = Undo(() => st, async s => { restored.push(s.v); st = s; }, { poll: 0, quiet: 100, max: 3 });
  const set = (v, now) => { st = { v }; U.tick(now); };
  U.tick(0);                                        // 처음 상태 v1이 바탕
  set(2, 10); set(2, 300);                          // 100ms 넘게 조용 -> v1이 쌓임
  set(3, 400); set(3, 600);                         // v2가 쌓임
  const out = {};
  out.sizes1 = U.size();
  out.u1 = await U.undo(); out.v1 = st.v;           // 3 -> 2
  out.u2 = await U.undo(); out.v2 = st.v;           // 2 -> 1
  out.u3 = await U.undo(); out.v3 = st.v;           // 더 없음
  out.r1 = await U.redo(); out.rv1 = st.v;          // 1 -> 2
  out.r2 = await U.redo(); out.rv2 = st.v;          // 2 -> 3
  out.r3 = await U.redo(); out.rv3 = st.v;          // 더 없음
  U.tick(100000);                                   // 복원으로 생긴 변화는 새 기록이 아니다
  out.sizes2 = U.size();
  out.restored = restored;
  console.log(JSON.stringify(out));
})();
"""
    got = run(tmp_path, body, DRAFT)
    assert got["sizes1"] == [2, 0]
    assert (got["u1"], got["v1"], got["u2"], got["v2"], got["u3"], got["v3"]) == (True, 2, True, 1, False, 1)
    assert (got["r1"], got["rv1"], got["r2"], got["rv2"], got["r3"], got["rv3"]) == (True, 2, True, 3, False, 3)
    assert got["sizes2"] == [2, 0] and got["restored"] == [2, 1, 2, 3]


def test_undo_max_coalesce_pending_and_new_edit_clears_redo(tmp_path):
    body = """
(async () => {
  let st = { v: 0 };
  const U = Undo(() => st, async s => { st = s; }, { poll: 0, quiet: 100, max: 3 });
  U.tick(0);
  let now = 0;
  for (let v = 1; v <= 6; v++) { st = { v }; now += 500; U.tick(now); now += 500; U.tick(now); }   // 6번 바꿈 -> 기록은 3개까지
  const out = { max: U.size() };
  // 글자를 치는 동안(조용하기 전 연속 변화)은 한 번으로 묶인다
  for (const v of [10, 11, 12]) { st = { v }; now += 30; U.tick(now); }
  now += 500; U.tick(now);
  out.coalesced = U.size();
  await U.undo(); out.afterUndo = st.v;               // 12 -> 6 (묶인 한 번)
  // 쌓이기 전의 편집은 그 편집만 취소하고 다시 하기로 돌아온다
  st = { v: 99 }; now += 30; U.tick(now);
  out.pendingUndo = await U.undo(); out.pendingV = st.v;
  out.pendingRedo = await U.redo(); out.pendingRV = st.v;
  // 새 편집이 쌓이면 다시 하기는 지워진다
  await U.undo(); st = { v: 77 }; now += 500; U.tick(now); now += 500; U.tick(now);
  out.fwd = U.size()[1];
  // restore가 던지면 기록은 그대로
  let boom = false; const B = Undo(() => st, async () => { if (boom) throw new Error("x"); }, { poll: 0, quiet: 10 });
  B.tick(0); st = { v: 1 }; B.tick(5); B.tick(50); boom = true;
  out.thrown = await B.undo(); out.bsizes = B.size();
  // 상태가 없으면(null) 아무것도 하지 않는다
  const N = Undo(() => null, async () => {}, { poll: 0 }); N.tick(0);
  out.none = [await N.undo(), await N.redo()];
  console.log(JSON.stringify(out));
})();
"""
    got = run(tmp_path, body, DRAFT)
    assert got["max"] == [3, 0] and got["coalesced"] == [3, 0]
    assert got["afterUndo"] == 6 and got["pendingUndo"] is True and got["pendingV"] == 6 and got["pendingRedo"] is True and got["pendingRV"] == 99
    assert got["fwd"] == 0 and got["thrown"] is False and got["bsizes"] == [1, 0] and got["none"] == [False, False]


# ---------- K1·K5~K7 키 처리 ----------

def test_shortcuts_keys(tmp_path):
    body = """
(async () => {
  const calls = [], U = { undo: async () => { calls.push("undo"); return true; }, redo: async () => { calls.push("redo"); return true; } };
  let runOk = true, box = new E("input"), active = true;
  Shortcuts({ undo: U, save: () => calls.push("save"), run: () => { calls.push("run"); return runOk; }, find: () => box, active: () => active });
  const key = (k, extra = {}, target = new E("body")) => fire("keydown", target, { key: k, ctrlKey: true, shiftKey: false, altKey: false, ...extra });
  const text = new E("input"), check = new E("input"); check.type = "checkbox"; const area = new E("textarea");
  const out = {};
  out.z = key("z").defaultPrevented;
  out.zText = key("z", {}, text).defaultPrevented; out.zArea = key("z", {}, area).defaultPrevented;
  out.zCheck = key("z", {}, check).defaultPrevented;
  key("Z", { shiftKey: true }); key("y");                    // 다시 하기 둘
  out.zComp = key("z", { isComposing: true }).defaultPrevented;
  out.zAlt = key("z", { altKey: true }).defaultPrevented;
  out.zPlain = key("z", { ctrlKey: false }).defaultPrevented;
  out.save = key("s").defaultPrevented; out.runY = key("Enter").defaultPrevented;
  runOk = false; out.runN = key("Enter").defaultPrevented; runOk = true;
  key("Enter", { defaultPrevented: true });                  // 이미 처리된 키(SQL 칸의 Ctrl+Enter)는 run을 다시 부르지 않는다
  out.find = key("f").defaultPrevented; out.focused = box.focused;
  box.disabled = true; out.findOff = key("f").defaultPrevented; box.disabled = false;
  box.cls.add("hidden"); out.findHidden = key("f").defaultPrevented; box.cls.delete("hidden");
  globalThis.modalOpen = true; out.modal = key("z").defaultPrevented || key("s").defaultPrevented; globalThis.modalOpen = false;
  active = false; out.inactive = key("s").defaultPrevented; active = true;
  await tick();
  out.calls = calls; out.flashes = flashes;
  // 단추 누르기
  const b = new E("button"); let clicks = 0; b.click = () => clicks++;
  out.press = [pressBtn(b), pressBtn(null)]; b.disabled = true; out.press.push(pressBtn(b)); b.disabled = false;
  const wrap = new E("div"); wrap.cls.add("hidden"); b.parentElement = wrap; out.press.push(pressBtn(b)); out.clicks = clicks;
  console.log(JSON.stringify(out));
})();
"""
    got = run(tmp_path, body, DRAFT)
    assert got["z"] is True and got["zText"] is False and got["zArea"] is False and got["zCheck"] is True  # 글자 칸이면 창 엔진에 맡긴다
    assert got["zComp"] is False and got["zAlt"] is False and got["zPlain"] is False
    assert got["save"] is True and got["runY"] is True and got["runN"] is False
    assert got["find"] is True and got["focused"] == 1 and got["findOff"] is False and got["findHidden"] is False
    assert got["modal"] is False and got["inactive"] is False
    assert got["calls"] == ["undo", "undo", "redo", "redo", "save", "run", "run"]  # 이미 처리된 키(Ctrl+Enter)·모달·다른 탭·한글 조합 중에는 부르지 않는다
    assert got["flashes"][0] == "되돌렸어요" and "다시 했어요" in got["flashes"]
    assert got["press"] == [True, False, False, False] and got["clicks"] == 1


# ---------- S2 title 말풍선 ----------

def test_title_tooltip_delegation(tmp_path):
    body = """
const host = new E("button"); host.setAttribute("title", "저장합니다"); host.rect = { left: 790, right: 800, top: 100, bottom: 120, width: 10, height: 20 };
const inner = new E("span"); inner.parentElement = host; host.kids.push(inner);
const text = new E("button"); text.setAttribute("title", "글자 있는 단추"); text._text = "저장";
const out = {};
fire("mouseover", inner);                                    // 안쪽 요소 위에 올려도 title 가진 조상
out.moved = [host.getAttribute("title"), host.dataset.tip, host.getAttribute("aria-label")];   // 네이티브 툴팁이 안 뜨게 title은 치우고, 이름은 aria-label로
out.beforeDelay = tipBox.classList.contains("hidden");
flush();
out.shown = [tipBox.textContent, tipBox.classList.contains("hidden"), tipBox.style.left, tipBox.style.top];
fire("mouseout", inner, { relatedTarget: host });             // 조상 안에서 움직임: 그대로
out.stay = tipBox.classList.contains("hidden");
fire("mouseout", host, { relatedTarget: new E("div") });
out.restored = [host.getAttribute("title"), host.getAttribute("aria-label"), "tip" in host.dataset, tipBox.classList.contains("hidden")];
fire("focusin", text); flush();                                // 키보드 포커스(글자가 있는 단추는 aria-label을 달지 않는다)
out.focus = [tipBox.textContent, tipBox.classList.contains("hidden"), text.getAttribute("title"), text.getAttribute("aria-label")];
text.setAttribute("title", "바뀐 힌트");                      // 보이는 동안 코드가 title을 새로 달면 그것을 둔다
fire("focusout", text);
out.fresh = [text.getAttribute("title"), "tip" in text.dataset];
const lonely = new E("div"); fire("mouseover", lonely); out.none = tipBox.classList.contains("hidden");
fire("mouseover", host); fire("keydown", host); out.keyHide = [host.getAttribute("title"), tipBox.classList.contains("hidden")];
flash("되돌렸어요"); out.toast = [toastBox.textContent, toastBox.classList.contains("hidden")];
console.log(JSON.stringify(out));
"""
    got = run(tmp_path, body, TIP)
    assert got["moved"] == [None, "저장합니다", "저장합니다"] and got["beforeDelay"] is True
    left, top = float(got["shown"][2][:-2]), float(got["shown"][3][:-2])
    assert got["shown"][:2] == ["저장합니다", False] and left <= 800 - 100 - 4 and top == 126  # 화면 가장자리 안으로(오른쪽 끝 단추)
    assert got["stay"] is False
    assert got["restored"] == ["저장합니다", None, False, True]
    assert got["focus"] == ["글자 있는 단추", False, None, None] and got["fresh"] == ["바뀐 힌트", False]
    assert got["none"] is True and got["keyHide"] == ["저장합니다", True] and got["toast"] == ["되돌렸어요", False]


# ---------- S3 datalist 대신 목록 ----------

def test_combo_replaces_datalist(tmp_path):
    body = """
const dl = new E("datalist"); ["서울", "부산", "서울시"].forEach(v => { const o = new E("option"); o.value = v; dl.kids.push(o); }); lists.L = dl;
const inp = new E("input"); inp.setAttribute("data-list", "L");
const names = () => menuList.children.map(c => c.textContent), shut = () => menuBox.classList.contains("hidden");
const out = {};
fire("focusin", inp); out.all = names(); out.open = !shut();
inp.value = "서울"; fire("input", inp); out.filtered = names();            // 친 글자가 들어 있는 값만(같은 값은 뺀다)
inp.value = "ㅂ"; fire("input", inp); out.none = shut();
inp.value = "서"; fire("input", inp);
let events = 0; handlers.input.push(e => { if (e.target === inp) events++; });
menuList.onclick({ target: menuList.children[0] });                         // 고르면 칸에 들어간다
out.picked = [inp.value, shut(), events, menuFor];
// 키보드: Enter는 ↑↓로 가리킨 항목이 있을 때만 고르고, 없으면 칸의 Enter 동작(추가 등)에 맡긴다
inp.value = ""; fire("input", inp);
const enter0 = fire("keydown", inp, { key: "Enter" }); out.enterNone = [enter0.defaultPrevented, enter0.stopped, inp.value];
const down = fire("keydown", inp, { key: "ArrowDown" }); out.down = down.defaultPrevented;
const space = fire("keydown", inp, { key: " " }); out.space = space.defaultPrevented;  // Space는 칸이 받는다
const enter1 = fire("keydown", inp, { key: "Enter" }); out.enterPick = [enter1.defaultPrevented, enter1.stopped, inp.value, shut()];
inp.value = ""; fire("input", inp); fire("keydown", inp, { key: "Escape" }); out.esc = shut();
console.log(JSON.stringify(out));
"""
    got = run(tmp_path, body, MENU)
    assert got["all"] == ["서울", "부산", "서울시"] and got["open"] is True
    assert got["filtered"] == ["서울시"] and got["none"] is True
    assert got["picked"] == ["서울", True, 1, None]  # 고른 뒤 input 이벤트가 한 번 나가고 목록이 다시 뜨지 않는다
    assert got["enterNone"] == [False, False, ""] and got["down"] is True and got["space"] is False
    assert got["enterPick"] == [True, True, "서울", True] and got["esc"] is True
