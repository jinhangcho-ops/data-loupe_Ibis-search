"""7차 /simplify G4: 화면 JS에서 겹치던 것을 한 곳으로 모았다(titledPicture·imageExt는 board.js, showAdded는 dash.js, aggName은 dash.js만)."""
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

STATIC = ROOT / "static"


def text(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def test_helpers_live_in_one_place():
    for name, home in {"titledPicture": "core/board.js", "imageExt": "core/board.js", "showAdded": "core/dash.js", "aggName": "core/dash.js"}.items():
        owners = [p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.suffix in (".js", ".html") and re.search(rf"(?:function\s+{name}\b|(?:const|let)\s+{name}\s*=)", p.read_text(encoding="utf-8"))]
        assert owners == [home], (name, owners)
    assert "aggName" not in text("core/funcs.js") and "Funcs.aggName" not in text("query/index.html")
    assert "showAdded(\"err-ana\", added.dashboard)" in text("prep/prep.html")


SCRIPT = r"""
const assert = require("assert");
const calls = [];
function canvas() {
  const c = { width: 0, height: 0, ops: [], getContext: () => ({
    set fillStyle(v) { c.ops.push(["fill", v]); }, set font(v) { c.ops.push(["font", v]); }, set textBaseline(v) {},
    fillRect: (...a) => c.ops.push(["rect", ...a]), fillText: (...a) => c.ops.push(["text", ...a]), drawImage: (_, x, y) => c.ops.push(["draw", x, y]) }),
    toDataURL: t => { calls.push(c); return "data:" + t; } };
  return c;
}
global.document = { createElement: () => canvas(), documentElement: { dataset: { theme: "light" } } };
global.getComputedStyle = () => ({ getPropertyValue: n => n });
let destroyed = 0;
global.Chart = class { constructor(c) { c.drawn = true; } destroy() { destroyed++; } };
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_chart_picture_same_layout_with_and_without_title(tmp_path):
    body = SCRIPT + text("core/board.js") + r"""
chartPicture((colors, options) => ({}), "light", "png", 400, 300, 2, "");
let c = calls.pop();
assert.deepStrictEqual([c.width, c.height], [400, 300]);
assert.deepStrictEqual(c.ops, [["fill", "--card"], ["rect", 0, 0, 400, 300], ["draw", 0, 0]]);
const url = chartPicture((colors, options) => ({}), "dark", "jpeg", 400, 300, 2, "이름");
c = calls.pop();
assert.strictEqual(url, "data:image/jpeg");
assert.deepStrictEqual([c.width, c.height], [400, 300 - 36 + 72]);
assert.deepStrictEqual(c.ops, [["fill", "--card"], ["rect", 0, 0, 400, 336], ["fill", "--text"], ["font", '600 30px "Segoe UI", "Malgun Gothic", sans-serif'], ["text", "이름", 28, 36, 344], ["draw", 0, 72]]);
assert.strictEqual(destroyed, 2);
assert.strictEqual(imageExt("jpeg"), "jpg"); assert.strictEqual(imageExt("png"), "png");
console.log("ok");
"""
    script = tmp_path / "pic.js"
    script.write_text(body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_show_added_text_and_link(tmp_path):
    body = r"""
const assert = require("assert");
const box = { kids: [], append(k) { this.kids.push(k); } }, said = [];
global.say = (id, msg, ok) => said.push([id, msg, ok]);
global.$ = id => box;
global.el = (tag, props) => ({ tag, ...props });
global.SERVER = {};
""" + text("core/board.js") + text("core/dash.js") + r"""
showAdded("err-ana", { id: "a b", name: "내 대시보드", cards: 3 });
assert.deepStrictEqual(said, [["err-ana", "‘내 대시보드’ 대시보드에 넣었어요. 지금 카드는 3개예요. ", true]]);
assert.deepStrictEqual(box.kids, [{ tag: "a", href: "/dashboard?open=a%20b", textContent: "대시보드에서 보기" }]);
console.log("ok");
"""
    script = tmp_path / "added.js"
    script.write_text(body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]
