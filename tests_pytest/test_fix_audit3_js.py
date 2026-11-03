"""3차 감사 화면 JS 수정(3E 묶음)을 node로 돌려 본다. node가 없으면 건너뛴다.
test_fix_audit_js.py·test_fix_audit2_js.py와 같은 방식(static 파일을 이어 붙이거나 html 안의 인라인 구간을 잘라 가짜 화면 위에서 돌린다).
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다."""
import shutil

import pytest

from test_fix_audit_js import COMMON, GRID_HOST, STATIC, STUBS, index_slice, run_node


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


# ---------- 3E4 ----------


# ---------- 3E5 ----------  (위 test_recompute_* 두 개) / 3E6


# ---------- 3E8 ----------

SOURCE_CASES = [  # (저장한 열쇠, 지금 열쇠, 같은 연결인가) -- core/use_counts.same_source와 같은 표
    ("folder:C:/data", "folder:C:/data#a.csv", True),   # 예전 폴더만 있는 열쇠는 그 폴더의 어느 파일과도 같다
    ("folder:C:/data", "folder:C:/data", True),
    ("folder:C:/data#a.csv", "folder:C:/data#a.csv", True),
    ("folder:C:/data#a.csv", "folder:C:/data#b.csv", False),  # 파일까지 적힌 열쇠는 다른 파일과 다르다
    ("folder:C:/other", "folder:C:/data#a.csv", False),
    ("folder:C:/data", "folder:C:/data2#a.csv", False),
    ("", "folder:C:/data#a.csv", False),
    ("url:x", "folder:C:/data", False),
]


def test_same_source_table_js(tmp_path):
    import json
    line = next(l for l in (STATIC / "core" / "common.js").read_text(encoding="utf-8").splitlines() if l.startswith("const sameSource"))
    body = f"""
const cases = {json.dumps(SOURCE_CASES)};
console.log(JSON.stringify({{ got: cases.map(([a, b]) => sameSource(a, b)), missing: sameSource("folder:x", undefined), same: sameSource(undefined, undefined) }}));
"""
    got = run_node(tmp_path, body, parts=[line + "\n"])
    assert got["got"] == [c[2] for c in SOURCE_CASES]
    assert got["missing"] is False  # 지금 연결이 없으면(undefined) 같지 않다


def test_same_source_js_and_python_agree():
    from core.use_counts import same_source
    assert [same_source(a, b) for a, b, _ in SOURCE_CASES] == [c[2] for c in SOURCE_CASES]


# ---------- 3E9 ----------

def test_apply_find_matches_per_cell_and_keeps_expanded_cells(tmp_path):
    """칸마다 따로 찾고(칸 사이를 건너 "서울"|"시"가 "울시"로 일치하지 않는다), 펼쳐 둔 중첩 값 칸은 찾기 뒤에도 펼친 채(강조 글자로 굳지 않는다)."""
    code = index_slice("function applyFind() {", "\nlet findTimer")
    prelude = GRID_HOST + """
const host = makeHost(), box = { value: "" }, count = { textContent: "" };
globalThis.$ = id => id === "rows" ? host : id === "row-find" ? box : id === "find-count" ? count : { value: "40" };
"""
    body = """
resultTable(host, { columns: ["a", "b"], rows: [{ a: "서울", b: "시" }, { a: "a", b: { k: 1 } }, { a: "서울시청", b: "x" }] });
const g = host._grid;
box.value = "울시"; applyFind();
const split = { view: g.view, rows: rowsHtml(host) };
g.open.set(1, new Set([1])); expanded.length = 0;          // 1번 행의 중첩 값 칸을 펼쳐 둔 상태
box.value = "k"; applyFind();
console.log(JSON.stringify({ split, kView: g.view, expanded }));
"""
    got = run_node(tmp_path, body, parts=[STUBS, COMMON, prelude, code])
    assert got["split"]["view"] == [2] and len(got["split"]["rows"]) == 1 and "<mark>울시</mark>청" in got["split"]["rows"][0]  # 고치기 전에는 칸을 건너 "서울"|"시"도 일치로 봤다
    assert got["kView"] == [1] and got["expanded"] == [[0, 1]]  # 펼친 칸은 찾은 뒤 다시 그려도 펼친다(전에는 펼친 글을 칸 글자로 굳혔다)
