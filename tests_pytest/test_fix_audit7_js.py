"""7차 묶음 7E(화면 JS·빌드)·7P(화면 성능)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit6_js.py와 같은 방식: 화면 파일에서 해당 함수만 잘라 가짜 화면(FAKE) 위에서 돌린다. 성능 항목은 동작이 같음을 본다."""
import json
import re
import shutil
import subprocess

import pytest
import yaml

from conftest import ROOT
from test_shortcuts_js import COMMON, DRAFT, FAKE, MENU

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
STATIC = ROOT / "static"


def page(name):
    return (STATIC / name).read_text(encoding="utf-8")


def fn(src, start, end="\n}\n"):
    a = src.index(start)
    return src[a:src.index(end, a) + len(end)]


def line(src, start):
    return next(x for x in src.splitlines() if x.startswith(start))


def run(tmp_path, body, *parts, fake=True):
    script = tmp_path / "t.js"
    script.write_text((FAKE if fake else "") + "\n".join(parts) + "\n" + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# ---------- 7E1 값 칸: 한글 조합 중 Enter·목록이 가리킨 항목이 있는 Enter는 칸의 추가 동작을 하지 않는다 ----------

@needs_node
def test_7e1_field_enter_does_not_add_while_composing_or_pointing(tmp_path):
    handler = line(page("prep/prep.html"), '$("a-fvalue").onkeydown')
    body = """
const field = {}; const $ = () => field; let added = 0; const addStep = () => { added++; };
let menuFor = null, menuAt = null;
""" + handler + """
const enter = extra => field.onkeydown({ key: "Enter", ctrlKey: false, metaKey: false, target: field, ...extra });
const out = {};
enter({}); out.plain = added;                              // 일반 Enter는 추가한다
enter({ isComposing: true }); out.composing = added;       // 한글 조합을 확정하는 Enter는 추가하지 않는다
menuFor = field; menuAt = {};
enter({}); out.pointing = added;                           // 목록이 가리킨 항목이 있으면 고르는 쪽이 맡는다(칸은 추가 안 함)
menuAt = null;
enter({}); out.nothingPointed = added;                     // 가리킨 항목이 없으면 추가한다
console.log(JSON.stringify(out));
"""
    got = run(tmp_path, body, fake=False)
    assert got == {"plain": 1, "composing": 1, "pointing": 1, "nothingPointed": 2}


# ---------- 7E2 쌓기 전 편집을 취소한 뒤에는 다시 하기가 새 가지로만 ----------

@needs_node
def test_7e2_cancelled_pending_edit_clears_old_redo(tmp_path):
    body = """
(async () => {
  let state = "S0";
  const u = Undo(() => state, async s => { state = s; }, { poll: 0, quiet: 600 });
  let t = 0;
  const edit = async v => { state = v; u.tick(t); t += 1000; u.tick(t); t += 1000; };
  u.tick(0);
  await edit("S1"); await edit("S2");
  await u.undo(); await u.undo();
  state = "X"; u.tick(t);
  await u.undo();
  const out = { afterCancel: state };
  out.r1 = await u.redo(); out.s1 = state;
  out.r2 = await u.redo(); out.s2 = state;
  console.log(JSON.stringify(out));
})();"""
    got = run(tmp_path, body, DRAFT.split("function Shortcuts")[0], fake=False)
    assert got == {"afterCancel": "S0", "r1": True, "s1": "X", "r2": False, "s2": "X"}


# ---------- 7E3 기간을 건 채 다시 구한 값은 저장하지 않는다 ----------


def test_7e3_doc_says_period_values_are_not_saved():
    doc = (ROOT / "docs" / "functions" / "dashboard_basics.md").read_text(encoding="utf-8")
    assert "기간을 걸어 둔 채 구한 값은 화면에만" in doc


# ---------- 7E4 목록 접근성 ----------

@needs_node
def test_7e4_combo_aria(tmp_path):
    body = """
const dl = new E("datalist"); ["서울", "부산"].forEach(v => { const o = new E("option"); o.value = v; dl.kids.push(o); }); lists.L = dl;
const inp = new E("input"); inp.setAttribute("data-list", "L");
const out = {};
fire("focusin", inp);
out.input = [inp.attrs.role, inp.attrs["aria-expanded"], inp.attrs["aria-controls"] === menuList.id && !!menuList.id];
out.list = menuList.attrs.role;
out.options = menuList.children.map(c => [c.attrs.role, !!c.id]);
out.unique = new Set(menuList.children.map(c => c.id)).size;
fire("keydown", inp, { key: "ArrowDown" });
out.active = inp.attrs["aria-activedescendant"] === menuList.children[0].id;
fire("keydown", inp, { key: "Escape" });
out.closed = [inp.attrs["aria-expanded"], inp.attrs["aria-activedescendant"] ?? null];
const sel = new E("select"); sel.options = [{ text: "가" }, { text: "나" }]; sel.selectedIndex = 0;
openMenu(sel);
out.select = [sel.attrs["aria-expanded"], sel.attrs["aria-activedescendant"] === menuList.children[0].id];
closeMenu();
out.selectClosed = sel.attrs["aria-expanded"];
console.log(JSON.stringify(out));
"""
    got = run(tmp_path, body, MENU)
    assert got["input"] == ["combobox", "true", True] and got["list"] == "listbox"
    assert got["options"] == [["option", True], ["option", True]] and got["unique"] == 2
    assert got["active"] is True and got["closed"] == ["false", None]
    assert got["select"] == ["true", True] and got["selectClosed"] == "false"


# ---------- 7E5 토스트는 display:none이 아니라 투명으로 숨긴다 ----------

def test_7e5_toast_hidden_by_opacity():
    css = (STATIC / "core" / "common.css").read_text(encoding="utf-8")
    rule = re.search(r"\.toast\.hidden\s*\{([^}]*)\}", css)
    assert rule and "opacity:0" in rule.group(1).replace(" ", "") and "display:block" in rule.group(1).replace(" ", "")
    assert "visibility" not in rule.group(1)  # visibility:hidden도 접근성 트리에서 빠진다


# ---------- 7E6 워크플로 ----------

def test_7e6_workflows():
    build = yaml.safe_load((ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8"))
    script = "\n".join(s.get("run", "") for s in build["jobs"]["release"]["steps"])
    edit = next(x for x in script.splitlines() if "gh release edit" in x)
    assert "--draft=false" in edit
    test = yaml.safe_load((ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8"))
    checkout = next(s for s in test["jobs"]["test"]["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False


# ---------- 7P2 #maxlen 입력은 150ms 디바운스 ----------

@needs_node
def test_7p2_maxlen_debounced(tmp_path):
    handler = next(x for x in page("query/index.html").splitlines() if '$("maxlen").oninput' in x)
    body = """
const timers = []; globalThis.setTimeout = (f, ms) => { timers.push([f, ms]); return timers.length; };
globalThis.clearTimeout = id => { if (id) timers[id - 1] = null; };
const field = {}; const $ = () => field; let drawn = 0; const renderTables = () => { drawn++; };
""" + handler + """
const out = {};
field.oninput(); field.oninput(); field.oninput();
out.beforeFlush = drawn;
timers.forEach(t => t && t[0]());
out.afterFlush = drawn; out.ms = timers.filter(Boolean).map(t => t[1]);
console.log(JSON.stringify(out));
"""
    got = run(tmp_path, body, fake=False)
    assert got == {"beforeFlush": 0, "afterFlush": 1, "ms": [150]}


# ---------- 7P4 자동 저장 응답에서 카드 값(data)을 뺀다 ----------

def test_7p4_save_response_has_no_card_data(client):
    card = {"kind": "chart", "title": "매출", "view": {"ratio": 1.5, "styles": [{"type": "bar"}]}, "data": {"values": [1, 2, 3]},
            "source": {"schema": "", "table": "orders", "body": {"base": "orders", "x": "매장", "series": [{"y": "금액", "agg": "sum"}]}}}
    got = client.post("/api/dash", json={"name": "응답", "cards": [card]}).get_json()
    saved = got["dashboard"]
    assert saved["id"] and saved["name"] == "응답" and saved["cards"][0]["id"] and saved["cards"][0]["layout"]
    assert all("data" not in c for c in saved["cards"])
    assert got["dashboards"][0]["cards"] == 1  # 목록은 그대로
    stored = client.post("/api/dash/open", json={"id": saved["id"]}).get_json()["dashboard"]
    assert stored["cards"][0]["data"]  # 저장된 값은 그대로 있다


# ---------- 7P6 문장 결과 차트는 차트 섬(iframe)을 처음 볼 때 한 번만 끼운다(R4-D2a: Chart.js 대신) ----------

@needs_node
def test_7p6_sentence_chart_island_once(tmp_path):
    show = fn(page("query/sentences.js"), "  function showResult(out, d) {", "\n  }\n")
    body = """
globalThis.document = { documentElement: { dataset: {} }, getElementById: () => null };
globalThis.addEventListener = () => {};
const made = [];
const el = (t, props = {}, ...kids) => { const e = Object.assign({ tag: t, dataset: {}, kids, classList: { toggle() {} }, querySelectorAll: () => [], append(...k) { this.kids.push(...k); } }, props); made.push(e); return e; };
let themeHooked = false, posted = 0;
const sentChart = () => ({ draw: { kind: "series" } }), resultTable = () => "", island = () => () => posted++;
""" + show + """
const out = { replaceChildren() {} }, d = { rows: [], notes: [] };
const chartButton = () => made.filter(b => b.textContent === "차트").at(-1);
showResult(out, d); chartButton().onclick(); chartButton().onclick();
const frames = made.filter(e => e.tag === "iframe");
console.log(JSON.stringify([frames.length, frames[0].src, frames[0].title, posted]));
"""
    assert run(tmp_path, body, fake=False) == [1, "/chart", "문장 결과 차트", 1]


# ---------- 7P7 지표 사전은 한 번 받으면 다시 받지 않는다 ----------

@needs_node
def test_7p7_catalog_reused_until_metric_changes(tmp_path):
    src = page("query/metric_catalog.js")
    head = line(src, "let mcCat") + "\n" + line(src, "const mcCatalog")
    body = "let calls = 0; const api = async () => ({ n: ++calls });\n" + head + """
(async () => {
  const a = await mcCatalog(), b = await mcCatalog();
  const out = { same: a === b, calls };
  mcStale = true; await mcCatalog(); out.afterReset = calls;
  console.log(JSON.stringify(out));
})();
"""
    assert run(tmp_path, body, fake=False) == {"same": True, "calls": 1, "afterReset": 2}
    assert "mcStale = true" in fn(src, "    async function save() {", "\n    }\n")
    assert "mcStale = true" in fn(page("query/sentences.js"), "    async function removeMetric(m) {", "\n    }\n")


# ---------- 7P8 사용량은 5초마다 ----------

def test_7p8_usage_interval():
    assert "setInterval(showUsage, 5000)" in COMMON
