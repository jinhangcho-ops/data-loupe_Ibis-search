"""M1: 컬럼 머리글 메뉴를 자주 쓰는 항목 + 묶음 3개(하위 메뉴)로. 메뉴는 JS 자료 구조라 node로 돌려 본다(node가 없으면 건너뜀).
항목은 진짜 단계 목록(prep_ops.LABELS)으로 만들고, 하위 메뉴의 키보드·자리는 common.js의 메뉴 구간을 작은 가짜 화면 위에서 돌린다."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT, prep_page
from core import prep_ops

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node가 없다")

PREPMENU = "static/core/prepmenu.js"
TOP_MAX = 9  # 첫 단계 항목 수 상한
BUNDLES = ["글자 다듬기", "타입 바꾸기", "새 컬럼 만들기"]
HEAD_TOP = ["오름차순 정렬", "내림차순 정렬", "이 컬럼 빼기", "값 골라서 제외·만 보기…", "이 값으로 다른 테이블 찾기…"]  # 쿼리 화면 머리글 메뉴의 자주 쓰는 5개
ARGS = '{ column: "c", type: %s, sample: () => [], onPick() {} }'

RUNNER = r"""
const fs = require("fs"), vm = require("vm"), [file, ops, call] = process.argv.slice(2);
const ctx = vm.createContext({ api: async () => ({ ops: JSON.parse(ops) }), console });
vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
vm.runInContext("PrepMenu.load()", ctx).then(() => console.log(JSON.stringify(vm.runInContext(call, ctx))));
"""


def node(tmp_path, script, *args):
    path = tmp_path / "run.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run([NODE, str(path), *args], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def menu_of(tmp_path, file, call):
    """prepmenu.js(file)에 진짜 단계 목록을 넣고 call(식)의 값을 돌려준다."""
    return node(tmp_path, RUNNER, str(file), json.dumps(prep_ops.LABELS, ensure_ascii=False), call)


def flat(menu):
    return [label for _, _, kids in menu for label, _ in kids]


@pytest.mark.parametrize("kind", ["문자", "실수", "날짜시간"])
def test_menu_is_three_bundles(tmp_path, kind):
    """첫 단계에는 묶음 3개만 나오고(흐린 항목 없음), 단계는 하나도 빠지지 않고 한 번씩 하위 메뉴에 들어간다. 못 쓰는 단계는 하위 메뉴 안에서만 흐리다."""
    menu = menu_of(tmp_path, ROOT / PREPMENU, f"PrepMenu.menu({ARGS % json.dumps(kind, ensure_ascii=False)}).map(b => [b[0], b[1], b[3].map(i => [i[0], !!i[1]])])")
    assert [name for name, _, _ in menu] == BUNDLES and all(run is None and kids for _, run, kids in menu)
    assert len(HEAD_TOP) + len(menu) <= TOP_MAX
    assert sorted(flat(menu)) == sorted(f"{o['group']} › {o['label']}" for o in prep_ops.LABELS)
    assert [label for label, _ in menu[1][2]] == ["타입 › 타입 바꾸기"] and all(label.startswith("새 컬럼 › ") for label, _ in menu[2][2])
    live = {label: on for _, _, kids in menu for label, on in kids}
    assert live["타입 › 타입 바꾸기"] and live["글자 › 앞뒤 공백 지우기"] is (kind == "문자") and live["숫자 › 반올림"] is (kind == "실수") and live["새 컬럼 › 날짜에서 뽑기"] is (kind == "날짜시간")


def test_old_items_all_kept(tmp_path):
    """고치기 전(HEAD) 메뉴의 항목 글자가 고친 뒤 메뉴에 모두 있다. 화면 두 곳은 묶음 메뉴(PrepMenu.menu)를 쓰고 쿼리 머리글의 자주 쓰는 5개는 첫 단계에 그대로다."""
    old = subprocess.run(["git", "show", f"HEAD:{PREPMENU}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if old.returncode:
        pytest.skip("git에서 고치기 전 파일을 읽지 못했다")
    before = tmp_path / "old_prepmenu.js"
    before.write_text(old.stdout, encoding="utf-8")
    call = ARGS % '"문자"'
    was = menu_of(tmp_path, before, f"PrepMenu.items({call}).map(i => i[0])")
    now = menu_of(tmp_path, ROOT / PREPMENU, f"PrepMenu.menu({call}).map(b => [b[0], b[1], b[3].map(i => [i[0], !!i[1]])])")
    assert len(was) == len(prep_ops.LABELS) and set(was) <= set(flat(now))
    query, prep = (ROOT / "static/query/index.html").read_text(encoding="utf-8"), prep_page()
    assert "...PrepMenu.menu({" in query and "...PrepMenu.menu({" in prep and "PrepMenu.items(" not in query + prep
    head = query[query.index("function headItems("):query.index("async function dropColumn(")]
    assert all(f'"{label}"' in query for label in HEAD_TOP) and [label for label in HEAD_TOP[2:] if f'["{label}"' in head] == HEAD_TOP[2:]


# 가짜 화면: 메뉴가 쓰는 것(자식·속성·초점·사각형·이벤트)만 흉내 낸다. 초점이 든 요소를 지우면 진짜 창처럼 focusout이 난다.
FAKE = r"""
let active = null, X = 100; const docL = {};
globalThis.innerWidth = 1000; globalThis.innerHeight = 800;
function fire(target, name, ev) { for (let x = target; x; x = x.parent) { x["on" + name]?.({ ...ev, target }); (x.L[name] ?? []).forEach(f => f({ ...ev, target })); } }
class E {
  constructor() { this.kids = []; this.attrs = {}; this.style = {}; this.L = {}; this.parent = null; this.disabled = false; }
  get children() { return this.kids; }
  append(...kids) { for (const k of kids) { k.parent = this; this.kids.push(k); } }
  contains(x) { for (; x; x = x.parent) if (x === this) return true; return false; }
  remove() { if (this.contains(active)) { const was = active; active = null; fire(was, "focusout", { relatedTarget: null }); } if (this.parent) this.parent.kids = this.parent.kids.filter(k => k !== this); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(n, f) { (this.L[n] ??= []).push(f); }
  getBoundingClientRect() { return String(this.className).includes("ctx-sub") ? { width: 200, height: 100 } : this.attrs["aria-haspopup"] ? { left: X, right: X + 150, top: 40 } : { left: 0, top: 0, width: 150, height: 100 }; }
  focus() { const was = active; if (was === this) return; active = this; if (was) fire(was, "focusout", { relatedTarget: this }); }
  click() { this.onclick?.(); }
}
const el = (tag, props = {}, ...kids) => { const e = new E(); Object.assign(e, props); e.append(...kids); return e; };
const document = { get activeElement() { return active; }, body: new E(), addEventListener(n, f) { (docL[n] ??= []).push(f); } };
const key = k => fire(active, "keydown", { key: k, preventDefault() {} }), esc = () => docL.keydown.forEach(f => f({ key: "Escape", stopPropagation() {} }));
"""

STEPS = r"""
const out = {}, ran = [], opener = new E();
const items = [["가", () => ran.push("가")], ["나", null, "이유"], ["묶음", null, "", [["하나", null, "못 써요"], ["둘", () => ran.push("둘")], ["셋", () => ran.push("셋")]]]];
const open = () => { opener.focus(); ctxMenu({ target: opener, clientX: 10, clientY: 10, preventDefault() {} }, items); return ctxBox.children[0].children; };
let [a, b, g] = open();
out.roles = [ctxBox.children[0].attrs.role, a.attrs.role, g.attrs["aria-haspopup"], g.attrs["aria-expanded"], a.attrs["aria-haspopup"] ?? null];
out.off = [a.disabled, b.disabled, g.disabled, b.title];
out.first = active.textContent; key("ArrowDown"); out.down = active.textContent;  // 흐린 "나"를 건너 묶음으로
key("ArrowRight");
const sub = ctxBox.children[1];
out.opened = [ctxBox.children.length, g.attrs["aria-expanded"], sub.attrs.role, active.textContent, sub.children.map(x => [x.textContent, x.disabled, x.attrs.role]), sub.style.left];
key("ArrowDown"); out.subDown = active.textContent;
key("ArrowLeft"); out.left = [ctxBox.children.length, g.attrs["aria-expanded"], active === g];
g.click(); out.enter = [ctxBox.children.length, active.textContent];  // Enter는 단추의 click
esc(); out.esc1 = [!!ctxBox, ctxBox.children.length, active === g];
esc(); out.esc2 = [ctxBox === null, active === opener];
[a, b, g] = open(); X = 900; g.onmouseenter();
out.hover = [ctxBox.children.length, active === a, ctxBox.children[1].style.left];  // 가리키면 열리되 초점은 그대로, 오른쪽이 모자라면 왼쪽으로
a.onmouseenter(); out.away = ctxBox.children.length;
g.click(); ctxBox.children[1].children[1].click();
out.ran = [ran, ctxBox === null];
console.log(JSON.stringify(out));
"""


def test_submenu_keyboard_and_side(tmp_path):
    """묶음은 →·Enter·가리키기로 옆에 펼치고 ←·Esc로 그것만 닫는다(한 번 더 Esc면 메뉴 전체, 초점은 연 곳으로). 오른쪽에서 잘리면 왼쪽에 펼친다."""
    text = (ROOT / "static/core/common.js").read_text(encoding="utf-8")
    start = text.index("let ctxBox = null")
    out = node(tmp_path, FAKE + text[start:text.index("\n", text.index('if (e.key === "Escape" && ctxBox)'))] + STEPS)
    assert out["roles"] == ["menu", "menuitem", "menu", "false", None] and out["off"] == [False, True, False, "이유"]
    assert out["first"] == "가" and out["down"] == "묶음"
    assert out["opened"] == [2, "true", "menu", "둘", [["하나", True, "menuitem"], ["둘", False, "menuitem"], ["셋", False, "menuitem"]], "250px"]
    assert out["subDown"] == "셋" and out["left"] == [1, "false", True]
    assert out["enter"] == [2, "둘"] and out["esc1"] == [True, 1, True] and out["esc2"] == [True, True]
    assert out["hover"] == [2, True, "700px"] and out["away"] == 1
    assert out["ran"] == [["둘"], True]
