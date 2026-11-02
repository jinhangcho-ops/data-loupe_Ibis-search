"""쿼리 화면 사용자 피드백 1차(F04·F05·F07·F09·F20·F31) 시험. 글자 확인 + node로 JS 조각 실행. 시험 데이터는 여기서 만든다."""
import re

from conftest import ROOT
from test_fix_audit_js import run_node

HTML = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")


def cut(start, end):
    """index.html에서 start로 시작해 end 직전까지(없으면 ValueError)."""
    a = HTML.index(start)
    return HTML[a:HTML.index(end, a)]


def test_f04_정렬_보일컬럼_입력칸에_컬럼_목록():
    assert '<datalist id="col-list"></datalist>' in HTML
    assert 'key === "sorts" || key === "columns" ? \' list="col-list"\'' in HTML  # 타이핑은 그대로, 목록에서도 고른다
    assert '$("col-list").innerHTML' in cut("async function refreshColumns", "// 실행하면 처리할 용량")


def test_f07_쪽_번호_계산(tmp_path):
    code = cut("// 쪽 번호 칸", "let lastPages")
    out = run_node(tmp_path, """
const f = (v, pages, limit) => { const i = { value: v }; return [pageOffset(i, pages, limit), i.value]; };
console.log(JSON.stringify([f("3", 5, 20), f("99", 5, 20), f("0", 5, 20), f("-4", 5, 20), f("", 5, 20), f("2.7", 5, 20), f("99", null, 20)]));
""", parts=[code])
    assert out == [[40, ""], [80, ""], [0, ""], [0, ""], [0, ""], [20, ""], [1960, ""]]


def test_f07_쪽_번호_칸과_연결():
    assert 'id="page-go"' in HTML and 'id="sql-go"' in HTML
    assert 'aria-label="쪽 번호로 이동"' in HTML
    assert "lastPages = d.count === null ? null" in cut("function showBig", "// offset행을 건너뛰고")
    assert 'runSearch(pageOffset($("page-go"), lastPages' in HTML
    assert 'runSql(pageOffset($("sql-go"), sqlPages' in HTML and "sqlPages = d.count === null ? null" in HTML


def test_f09_내보내기_형식_단추가_늘_보임():
    assert '"save-open"' not in HTML and '"save-pop"' not in HTML  # 드롭다운 코드는 지웠다
    for fmt in ("csv", "xlsx", "parquet"):
        assert re.search(rf'<button class="btn" data-export="{fmt}" title="[^"]+" aria-label="[^"]+">', HTML)
    assert 'busy(b, async' in cut('querySelectorAll("[data-export]")', "// 전처리로 보내기")


def test_f05_이전다음_위에서_휠로_쪽_넘기기(tmp_path):
    code = cut("// 이전·다음 위에서 휠", "// 결과 표의 머리글")
    out = run_node(tmp_path, """
const clicks = [], els = {};
globalThis.$ = id => els[id] ??= { id, click() { clicks.push(id); } };  // 핸들러는 onwheel 속성(옛 시험의 가짜 요소와도 맞음)
$("prev"); $("next");
""" + code + """
const fire = (id, deltaY, timeStamp) => $(id).onwheel({ deltaY, timeStamp, preventDefault() {} });
fire("prev", 100, 1000);   // 아래 -> 다음
fire("next", 100, 1100);   // 쿨다운 안: 무시
fire("next", -100, 1400);  // 위 -> 이전
console.log(JSON.stringify(clicks));
""")
    assert out == ["next", "prev"]


def test_f20_차트_XY_바꾸기(tmp_path):
    assert 'id="cswap"' in HTML and 'aria-label="X축과 Y축 바꾸기"' in HTML
    code = cut('$("cswap").onclick', "// 차트 비율")
    out = run_node(tmp_path, """
const els = { cx: { value: "날짜" }, cy: { value: "금액" }, draw: { click() { calls.push("draw"); } } }, calls = [];
globalThis.$ = id => els[id];
globalThis.fillValueColumns = () => calls.push("fill");
globalThis.shelf = { refresh: () => calls.push("shelf") };
""" + code.replace('$("cswap").onclick', "const swap").rstrip().rstrip(";") + """;
swap();
const a = [els.cx.value, els.cy.value];
els.cy.value = ""; swap();  // Y가 비면 바꾸지 않는다
console.log(JSON.stringify([a, els.cx.value, calls]));
""")
    assert out == [["금액", "날짜"], "금액", ["fill", "shelf", "draw"]]


def test_f31_env_찾아보기():
    assert 'id="llm-pick"' in HTML and 'aria-label="LLM 설정 파일 찾아보기"' in HTML
    code = cut('$("llm-pick").onclick', 'api("/api/llm")')
    assert '"/api/pick", { kind: "env" }' in code and "d.path" in code and "catch" in code
