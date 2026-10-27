"""9차 묶음 9J(화면 JS)를 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit8_js.py와 같은 방식: common.js의 표 부분과 dashboard.html의 recompute를 가짜 화면 위에서 진짜 코드로 돌린다."""
from test_fix_audit7_js import fn, needs_node, page, run
from test_fix_audit7_vscroll import HARNESS, pieces
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


# ---------- 9J2 계산 중에 저장이 끼어들어도 바꾼 설정의 옛 저장 값은 비워서 쓴다 ----------

@needs_node
def test_9j2_recompute_with_period_empties_stored_even_if_a_save_lands_midway(tmp_path):
    ctl, dash = page("dashboard/dash_ctl.js"), page("dashboard/dashboard.html")
    a = dash.index("  recompute: async")
    recompute = dash[a:dash.index("\n  star:", a)]
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => ({ id: c.id, kind: c.kind, source: c.source, layout: c.layout, data: c.data }) };
""" + fn(ctl, "function showLive") + "\n" + next(x for x in ctl.splitlines() if x.startswith("const storedCard")) + r"""
let dashId = "d1", valuesDirty = false, valuesSeq = 0, lastSaved, wrote = [];
""" + next(x for x in dash.splitlines() if x.startswith("const cardSettings")) + chr(10) + next(x for x in dash.splitlines() if x.startswith("const sigOf")) + r"""
const needsValues = c => c;
const card = { id: 1, kind: "chart", source: { table: "region" }, layout: { x: 0 }, data: { labels: ["서울", "부산"] } };
const board = { cards: [card], ctx: () => ({ range: { start: "2026-01-01" } }), draw() {} };
const snap = () => JSON.stringify([[], {}, [cardSettings(card)]]);
const scheduleSave = () => { if (snap() === lastSaved && !valuesDirty) return; wrote.push(JSON.stringify(storedCard(card).data)); lastSaved = snap(); valuesDirty = false; };
lastSaved = snap();                                         // 방금 저장한 대시보드
const api = async () => {
  await null;
  scheduleSave();                                           // 계산 중에 실시간 갱신이 끝나며 저장 예약: 새 설정이 저장되고 lastSaved가 바뀐다
  return { data: { labels: ["사과"] } };
};
const panelHost = {
""" + recompute + r"""
};
(async () => {
  showLive(card, { labels: ["서울 기간"] });                // 기간을 건 화면 값, 저장 값은 stored
  card.source = { table: "product" };                       // 원천을 바꾸고
  await panelHost.recompute(card);
  console.log(JSON.stringify({ stored: storedCard(card).data, wrote, dirty: valuesDirty, shown: card.data.labels }));
})();
""", fake=False)
    assert got["stored"] == {} and got["shown"] == ["사과"]
    assert got["wrote"][-1] == "{}", got  # 비운 값이 서버에 쓰였다(옛 값이 새 설정 옆에 남지 않는다)


@needs_node
def test_9j2_period_applied_while_computing_still_empties_stored(tmp_path):
    ctl, dash = page("dashboard/dash_ctl.js"), page("dashboard/dashboard.html")
    a = dash.index("  recompute: async")
    recompute = dash[a:dash.index("\n  star:", a)]
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => ({ id: c.id, kind: c.kind, source: c.source, layout: c.layout, data: c.data }) };
""" + fn(ctl, "function showLive") + "\n" + next(x for x in ctl.splitlines() if x.startswith("const storedCard")) + r"""
let dashId = "d1", valuesDirty = false, valuesSeq = 0, lastSaved, wrote = [];
""" + next(x for x in dash.splitlines() if x.startswith("const cardSettings")) + chr(10) + next(x for x in dash.splitlines() if x.startswith("const sigOf")) + r"""
const needsValues = c => c;
const card = { id: 1, kind: "chart", source: { table: "region" }, layout: { x: 0 }, data: { labels: ["서울", "부산"] } };
let period = null;
const board = { cards: [card], ctx: () => period, draw() {} };
const snap = () => JSON.stringify([[], {}, [cardSettings(card)]]);
const scheduleSave = () => { if (snap() === lastSaved && !valuesDirty) return; wrote.push(JSON.stringify(storedCard(card).data)); lastSaved = snap(); valuesDirty = false; };
lastSaved = snap();                                         // 방금 저장한 대시보드
const api = async () => {
  await null;
  period = { range: { start: "2026-01-01" } }; showLive(card, card.data);  // 계산 중에 기간을 걸었다: 기간 갱신이 옛 설정의 값을 stored에 둔다
  scheduleSave();
  return { data: { labels: ["사과"] } };
};
const panelHost = {
""" + recompute + r"""
};
(async () => {
  showLive(card, { labels: ["서울 기간"] });                // 기간을 건 화면 값, 저장 값은 stored
  card.source = { table: "product" };                       // 원천을 바꾸고
  await panelHost.recompute(card);
  console.log(JSON.stringify({ stored: storedCard(card).data, wrote, dirty: valuesDirty, shown: card.data.labels }));
})();
""", fake=False)
    assert got["stored"] == {} and got["shown"] == ["사과"]
    assert got["wrote"][-1] == "{}", got  # 비운 값이 서버에 쓰였다(옛 값이 새 설정 옆에 남지 않는다)


# ---------- 10차: 기다리는 사이 기간을 풀면 지금 기간으로 다시, 옮기기만 한 카드는 저장 값을 둔다 ----------

@needs_node
def test_10_period_cleared_while_computing_does_not_save_the_period_value(tmp_path):
    ctl, dash = page("dashboard/dash_ctl.js"), page("dashboard/dashboard.html")
    a = dash.index("  recompute: async")
    recompute = dash[a:dash.index("\n  star:", a)]
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => ({ id: c.id, kind: c.kind, source: c.source, layout: c.layout, data: c.data }) };
""" + fn(ctl, "function showLive") + "\n" + next(x for x in ctl.splitlines() if x.startswith("const storedCard")) + r"""
let dashId = "d1", valuesDirty = false, valuesSeq = 0, lastSaved, wrote = [];
""" + next(x for x in dash.splitlines() if x.startswith("const cardSettings")) + chr(10) + next(x for x in dash.splitlines() if x.startswith("const sigOf")) + r"""
const needsValues = c => c;
const card = { id: 1, kind: "chart", source: { table: "region" }, layout: { x: 0 }, data: { labels: ["서울", "부산"] } };
let period = { range: { start: "2026-01-01" } };
const board = { cards: [card], ctx: () => period, draw() {} };
const snap = () => JSON.stringify([[], {}, [cardSettings(card)]]);
const scheduleSave = () => { if (snap() === lastSaved && !valuesDirty) return; wrote.push(JSON.stringify(storedCard(card).data)); lastSaved = snap(); valuesDirty = false; };
lastSaved = snap();                                         // 방금 저장한 대시보드
const api = async () => {
  await null;
  const asked = period; period = null; card.data = card.stored ?? card.data; delete card.stored;  // 계산 중에 기간을 풀었다(setRange가 전체 값으로 되돌림)
  return { data: { labels: [asked ? "기간 값" : "전체 값"] } };
};
const panelHost = {
""" + recompute + r"""
};
(async () => {
  showLive(card, { labels: ["서울 기간"] });                // 기간을 건 화면 값, 저장 값은 stored
  await panelHost.recompute(card);
  console.log(JSON.stringify({ stored: storedCard(card).data, wrote, dirty: valuesDirty, shown: card.data.labels }));
})();
""", fake=False)
    assert got["shown"] == ["전체 값"], got  # 기간 값이 아니라 지금(기간 없음) 값
    assert all("기간 값" not in w for w in got["wrote"]), got


@needs_node
def test_10_moving_a_card_then_recomputing_with_period_keeps_stored(tmp_path):
    ctl, dash = page("dashboard/dash_ctl.js"), page("dashboard/dashboard.html")
    a = dash.index("  recompute: async")
    recompute = dash[a:dash.index("\n  star:", a)]
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => ({ id: c.id, kind: c.kind, source: c.source, layout: c.layout, data: c.data }) };
""" + fn(ctl, "function showLive") + "\n" + next(x for x in ctl.splitlines() if x.startswith("const storedCard")) + r"""
let dashId = "d1", valuesDirty = false, valuesSeq = 0, lastSaved, wrote = [];
""" + next(x for x in dash.splitlines() if x.startswith("const cardSettings")) + chr(10) + next(x for x in dash.splitlines() if x.startswith("const sigOf")) + r"""
const needsValues = c => c;
const card = { id: 1, kind: "chart", source: { table: "region" }, layout: { x: 0 }, data: { labels: ["서울", "부산"] } };
let period = { range: { start: "2026-01-01" } };
const board = { cards: [card], ctx: () => period, draw() {} };
const snap = () => JSON.stringify([[], {}, [cardSettings(card)]]);
const scheduleSave = () => { if (snap() === lastSaved && !valuesDirty) return; wrote.push(JSON.stringify(storedCard(card).data)); lastSaved = snap(); valuesDirty = false; };
lastSaved = snap();                                         // 방금 저장한 대시보드
const api = async () => {
  await null;
  return { data: { labels: ["사과"] } };
};
const panelHost = {
""" + recompute + r"""
};
(async () => {
  showLive(card, { labels: ["서울 기간"] });                // 기간을 건 화면 값, 저장 값은 stored
  card.layout = { x: 3 };                                    // 옮기기만 했다
  await panelHost.recompute(card);
  console.log(JSON.stringify({ stored: storedCard(card).data, wrote, dirty: valuesDirty, shown: card.data.labels }));
})();
""", fake=False)
    assert got["stored"] == {"labels": ["서울", "부산"]} and got["shown"] == ["사과"], got


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
