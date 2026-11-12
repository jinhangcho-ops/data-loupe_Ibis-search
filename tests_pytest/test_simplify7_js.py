"""7차 /simplify G4: 화면 JS에서 겹치던 것을 한 곳으로 모았다(imageExt는 board.js, showAdded·aggName·차트 섬 도우미 island는 common.js만. R4-D2b에서 dash.js와 Chart.js 그림 도우미를 지웠다)."""
import re
import shutil
import subprocess

import pytest

from conftest import ROOT

STATIC = ROOT / "static"


def text(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def test_helpers_live_in_one_place():
    for name, home in {"imageExt": "core/board.js", "showAdded": "core/common.js", "aggName": "core/common.js", "island": "core/common.js"}.items():
        owners = [p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.suffix in (".js", ".html") and re.search(rf"(?:function\s+{name}\b|(?:const|let)\s+{name}\s*=)", p.read_text(encoding="utf-8"))]
        assert owners == [home], (name, owners)
    assert "aggName" not in text("core/funcs.js") and "Funcs.aggName" not in text("query/index.html")
    assert "showAdded(\"err-ana\", added.dashboard)" in text("prep/analysis.js")


def test_image_ext():
    assert 'const imageExt = type => type === "jpeg" ? "jpg" : "png";' in text("core/board.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_show_added_text_and_link(tmp_path):
    body = r"""
const assert = require("assert");
const box = { kids: [], append(k) { this.kids.push(k); } }, said = [];
global.say = (id, msg, ok) => said.push([id, msg, ok]);
global.$ = id => box;
global.el = (tag, props) => ({ tag, ...props });
global.SERVER = {};
""" + re.search(r"function showAdded\(id, added\) \{[\s\S]*?\n\}\n", text("core/common.js"))[0] + r"""
showAdded("err-ana", { id: "a b", name: "내 대시보드", cards: 3 });
assert.deepStrictEqual(said, [["err-ana", "‘내 대시보드’ 대시보드에 넣었어요. 지금 카드는 3개예요. ", true]]);
assert.deepStrictEqual(box.kids, [{ tag: "a", href: "/dashboard?open=a%20b", textContent: "대시보드에서 보기" }]);
console.log("ok");
"""
    script = tmp_path / "added.js"
    script.write_text(body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]
