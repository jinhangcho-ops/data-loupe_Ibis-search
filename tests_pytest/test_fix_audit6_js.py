"""6차 묶음 6E(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit5_js.py와 같은 방식: 화면 파일에서 해당 함수만 잘라 가짜 화면(FAKE) 위에서 돌린다."""
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


def test_6e1_scoped_why_is_empty_outside_tab(tmp_path):
    body = """
(async () => {
  let st = { v: 1 };
  const u = Undo(() => st, async () => { throw new Error("그 테이블이 없어요"); }, { poll: 0, quiet: 0 });
  u.tick(0); st = { v: 2 }; u.tick(1000);
  let inX = true; const sc = scoped(u, () => inX);
  const out = { ok: await sc.undo(), why: sc.why() };
  inX = false;
  out.otherOk = await sc.undo(); out.otherWhy = sc.why();
  console.log(JSON.stringify(out));
})();"""
    got = run(tmp_path, body, DRAFT.split("function Shortcuts")[0])
    assert got["ok"] is False and got["why"] == "그 테이블이 없어요"
    assert got["otherOk"] is False and got["otherWhy"] == ""


def test_6e2_setview_keeps_limit_and_restore_clears_it():
    html = page("query/index.html")  # 흐름 → 단계 카드로 옮길 때 쪽 크기를 지키던 줄은 흐름 보기와 함께 없앴다
    # 되돌리기·초안 복원 쪽은 limit이 없던 상태로 돌아간다(loadBody 자체는 그대로)
    assert '$("limit").value = b.limit ?? "";' in html


def test_6e4_ctrl_enter_does_not_pick_menu_item(tmp_path):
    body = """
const out = [];
let picked = 0; pickMenu = () => { picked++; };
menuFor = new E("input"); menuAt = {};
for (const extra of [{ ctrlKey: true }, { metaKey: true }, {}]) {
  const ev = fire("keydown", menuFor, { key: "Enter", ctrlKey: false, metaKey: false, ...extra });
  out.push([picked, ev.stopped]);
}
menuFor = new E("select"); menuAt = {};
picked = 0;
for (const extra of [{ ctrlKey: true }, {}]) fire("keydown", menuFor, { key: "Enter", ctrlKey: false, metaKey: false, ...extra });
console.log(JSON.stringify({ out, selPicked: picked }));"""
    got = run(tmp_path, body, MENU)
    assert got["out"] == [[0, False], [0, False], [1, True]]
    assert got["selPicked"] == 1


def test_6e4_prep_open_table_throws_when_strict(tmp_path):
    html = page("prep/prep.html")
    body = """
const els = {}; const $ = id => els[id] ??= { value: "", checked: false, textContent: "" };
let token = 0, status = "", prof = null, edits = {}, statsOn = false, draftBase = "", pickers = {}, preview = null;
const S = { schema: "", base: "" }; const fails = [];
const resetTable = () => {}, fail = m => fails.push(m), timed = async () => { throw new Error("표를 못 열어요"); };
const api = async () => { throw new Error("x"); }, showAll = () => {}, renderGrid = () => {}, loadFirst = async () => {}, computeStats = async () => {};
%s
(async () => {
  const out = {};
  try { await openTable("T", null, true); out.strict = "no throw"; } catch (e) { out.strict = e.message; }
  await openTable("T"); out.soft = fails;
  console.log(JSON.stringify(out));
})();""" % fn(html, "async function openTable(")
    got = run(tmp_path, body, fake=False)
    assert got["strict"] == "표를 못 열어요" and got["soft"] == ["표를 못 열어요"]


def test_6e4_open_config_passes_strict():
    html = page("prep/prep.html")
    assert "await openTable(c.base, c, true);" in html


def test_6e4_flash_failure_lasts_longer_and_why_is_cut(tmp_path):
    body = """
const delays = []; const st = globalThis.setTimeout; globalThis.setTimeout = (f, ms) => { delays.push(ms); return 1; };
flash("a"); flash("b", 4000);
console.log(JSON.stringify({ delays }));"""
    got = run(tmp_path, body, TIP)
    assert got["delays"] == [1500, 4000]
    assert "String(why).slice(0, 80)" in DRAFT and "ok ? 1500 : 4000" in DRAFT


def test_6e4_docs_match_reality():
    doc = (ROOT / "docs" / "functions" / "drafts.md").read_text(encoding="utf-8")
    assert "쿼리는 표를 바꿔도 되돌리기 기록이 남고" in doc
    assert "연결이나 테이블을 바꾸면 되돌리기 기록이 비워집니다" not in doc
