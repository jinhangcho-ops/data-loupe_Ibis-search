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
const S = { cols: [["name"], ["city"]], sorts: [], conditions: [], columns: [], calcs: [], origins: { name: "t", city: "t" } };  // 둘 다 원래 컬럼(계산 컬럼은 조건 항목이 없다)
const els = { quotes: { checked: true }, rows: { _grid: null }, "err-search": {} };
globalThis.$ = id => els[id] ??= mk("x", {});
document.getElementById = id => $(id);
const wrap = v => !$("quotes").checked || !/(^|\s)or(\s|$)|,|^['\"]/i.test(v) ? v : v.includes("'") ? `"${v}"` : `'${v}'`;
const gridAt = (g, k) => g.view ? g.view[k] : k;
const added = [], sorted = [], undone = [];
const addItem = async (key, line) => { added.push(line); return true; };
const sortBy = (c, d) => sorted.push(c + " " + d);
const flash = () => {};
const searchUndo = { undo: () => undone.push(1) };
const inExplore = () => true, connected = "other";  // 연결 종류(머리글 메뉴가 PrepMenu에 flat으로 넘긴다)
const PrepMenu = { items: ({ column, onPick }) => [["글자 › 앞뒤 공백 지우기", () => onPick("trim", true)], ["숫자 › 반올림", null, "숫자 컬럼에만 쓸 수 있어요"]] };  // (R5) 머리글 메뉴의 전처리 단계(가짜)
const cellAt = (tag, index, tr) => { const c = { tagName: tag, cellIndex: index, closest: s => s === "tr" ? tr : null }; return { closest: s => s.includes("#rows") ? c : null }; };
const row = p => ({ dataset: p === undefined ? {} : { p: String(p) }, sectionRowIndex: 0, classList: { contains: () => false } });
"""


def ctx(tmp_path, body):
    code = index_slice("// 우클릭 메뉴:", "document.oncontextmenu")
    return run_node(tmp_path, body, files=["rowpick.js"], parts=[PRELUDE, code])  # cellValue는 RowPick.quote로 인용한다


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
  const th = ctxItems(cellAt("TH", 1, row()));
  await th.find(i => i[0] === "글자 › 앞뒤 공백 지우기")[1]();  // 전처리 단계는 계산 줄로
  console.log(JSON.stringify({ added, sorted, l0: labels(t0), ln: labels(tn), th: th.map(i => [i[0], !!i[1]]) }));
})();
"""
    got = ctx(tmp_path, body)
    assert got["added"] == ["name != 'a, b'", "name = 'a, b'", "city = 'city'", "name is not null", "name is null", "전처리 trim city true"]
    assert got["sorted"] == ["name desc"]
    assert got["l0"] == ["이 값 제외", "이 값만", "오름차순 정렬", "내림차순 정렬"]
    assert got["th"] == [["오름차순 정렬", True], ["내림차순 정렬", True], ["이 컬럼 빼기", True], ["값 골라서 제외·만 보기…", True], ["이 값으로 다른 테이블 찾기…", True],
                         ["글자 › 앞뒤 공백 지우기", True], ["숫자 › 반올림", False]]  # (R5) 머리글은 정렬 + 컬럼 빼기·값 고르기·찾기 + 전처리 단계(안 맞는 단계는 실행 없음)


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
    common = (STATIC / "core" / "common.js").read_text(encoding="utf-8")  # 우클릭 메뉴는 common.js ctxMenu 한 벌(B12), 쿼리 화면은 부르기만 한다
    for s in ['role", "menu"', 'role", "menuitem"', 'aria-label", label', 'e.key === "Escape"', '"mousedown"']:
        assert s in common
    assert "document.oncontextmenu = e => ctxMenu(e, " in HTML


def test_label_replace():
    """라벨 바꾸기는 차트 섬(web/lib/island.ts relabel)이 적용한다: 화면은 #clabels 글을 그대로 보낸다(R4-D2b)."""
    assert 'id="clabels"' in HTML and 'relabel: $("clabels").value' in HTML and "function labeled(" not in HTML


def test_not_null_help():
    assert "* is not null" in HTML
