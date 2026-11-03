"""F14·F24 우클릭 메뉴, F20 라벨 바꾸기, F11 도움말(결과 표·차트 화면). node로 해당 구간만 돌린다."""
import json
import re
import shutil
import subprocess

import pytest

from test_fix_audit_js import FAKE_DOM, STATIC, index_slice, run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
HTML = (STATIC / "query" / "index.html").read_text(encoding="utf-8")

PRELUDE = r"""
const S = { cols: [["name"], ["city"]], sorts: [], conditions: [] };
const els = { quotes: { checked: true }, rows: { _grid: null }, "err-search": {} };
globalThis.$ = id => els[id] ??= mk("x", {});
const wrap = v => !$("quotes").checked || !/(^|\s)or(\s|$)|,|^['\"]/i.test(v) ? v : v.includes("'") ? `"${v}"` : `'${v}'`;
const gridAt = (g, k) => g.view ? g.view[k] : k;
const added = [], sorted = [], undone = [];
const addItem = async (key, line) => { added.push(line); return true; };
const sortBy = (c, d) => sorted.push(c + " " + d);
const flash = () => {};
const searchUndo = { undo: () => undone.push(1) };
const inExplore = () => true;
const cellAt = (tag, index, tr) => { const c = { tagName: tag, cellIndex: index, closest: s => s === "tr" ? tr : null }; return { closest: s => s.includes("#rows") ? c : null }; };
const row = p => ({ dataset: p === undefined ? {} : { p: String(p) }, sectionRowIndex: 0, classList: { contains: () => false } });
"""


def ctx(tmp_path, body):
    code = index_slice("// 우클릭 메뉴:", "let ctxMenu")
    return run_node(tmp_path, body, parts=[PRELUDE, code])


def test_td_menu_exclude_only_sort(tmp_path):
    body = """
(async () => {
  $("rows")._grid = { columns: ["name", "city"], rows: [{ name: "a, b", city: "city" }, { name: null, city: "x" }] };
  const labels = t => ctxItems(t).map(i => i[0]);
  const t0 = cellAt("TD", 0, row(0)), t1 = cellAt("TD", 1, row(0)), tn = cellAt("TD", 0, row(1));
  const go = async (t, label) => { await ctxItems(t).find(i => i[0] === label)[1](); };
  await go(t0, "이 값 제외"); await go(t0, "이 값만");
  await go(t1, "이 값만");  // 값이 컬럼 이름(city)과 같으면 감싼다
  await go(tn, "빈 값 제외"); await go(tn, "빈 값만");
  await go(t0, "내림차순 정렬");
  console.log(JSON.stringify({ added, sorted, l0: labels(t0), ln: labels(tn), th: labels(cellAt("TH", 1, row())) }));
})();
"""
    got = ctx(tmp_path, body)
    assert got["added"] == ["name != 'a, b'", "name = 'a, b'", "city = 'city'", "name is not null", "name is null"]
    assert got["sorted"] == ["name desc"]
    assert got["l0"] == ["이 값 제외", "이 값만", "오름차순 정렬", "내림차순 정렬"]
    assert got["th"] == ["오름차순 정렬", "내림차순 정렬"]  # 머리글은 값 항목 없이 정렬만


def test_blank_menu_undo_and_native_menu_kept(tmp_path):
    body = """
const blank = { closest: () => null }, input = { closest: s => s.includes("input") ? {} : null };
const items = ctxItems(blank);
items[0][1]();
console.log(JSON.stringify({ n: items.map(i => i[0]), undone: undone.length, input: ctxItems(input) }));
"""
    got = ctx(tmp_path, body)
    assert got == {"n": ["되돌리기"], "undone": 1, "input": None}


def test_sort_by_direction_in_source():
    assert "async function sortBy(col, dir)" in HTML and 'return sortBy(th.firstChild' in HTML


def test_menu_accessibility_and_close():
    for s in ['role", "menu"', 'role", "menuitem"', 'aria-label", label', 'e.key === "Escape"', '"mousedown"']:
        assert s in HTML


def test_label_replace():
    """라벨 바꾸기는 차트 섬(web/lib/island.ts relabel)이 적용한다: 화면은 #clabels 글을 그대로 보낸다(R4-D2b)."""
    assert 'id="clabels"' in HTML and 'relabel: $("clabels").value' in HTML and "function labeled(" not in HTML


def test_not_null_help():
    assert "* is not null" in HTML
