"""(Phase 19) 묶음 P: 분석 화면(전처리·분석 탭)을 처음 쓰는 사람도 알아보게. 19A1(전처리 타입 쉬운 말), 19E1~3, 19F1~2.
글자·접기 표시·id 유지는 prep.html을 읽어 보고, 화면 코드는 해당 구간만 잘라 node로 돌린다. 모두 고치기 전 코드에서 실패한다."""
import re
import shutil

import pytest

import test_fix_ui67_c as ui67
from test_fix_audit_js import between, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
KINDS = ["distribution", "correlation", "groups", "trend", "funnel", "retention", "rfm", "pareto", "chisq", "abtest", "basket", "decompose", "seasonal"]
OPTIONAL = ["a-ftrim", "a-rspan", "a-masof", "a-mtx", "a-pa", "a-pb", "a-bconf", "a-ksup", "a-ktop", "a-swin", "a-sk"]


def html():
    return read("prep.html")


def form(kind):
    return between("prep.html", f'data-kind="{kind}"', "\n      </div>\n")


def test_19a1_types_in_plain_words():
    page = html()
    assert "KIND_TEXT" not in page  # 정수·소수·참·거짓 대신 공용 typeWord
    assert 'textContent: typeWord(type), title: type' in between("prep.html", "function pcol(", "function renderCols()")
    stats = between("prep.html", "function statsHtml(", "const copyName")
    assert '${esc(typeWord(c.type))}' in stats and 'title="원래 타입: ${esc(c.type)}"' in stats


@needs_node
def test_19a1_column_list_shows_type_word(tmp_path):
    parts = [between("common.js", "function typeWord", "advMore();\n"), "const heads = new Map();\n", between("prep.html", "function pcol(", "function renderCols()")]
    body = "const r = pcol('주문번호', 'int32', true, () => {}); const t = r.children[2]; console.log(JSON.stringify([t.textContent, t.title]));"
    assert run_node(tmp_path, body, parts=parts) == ["숫자", "int32"]


def test_19e1_lead_and_step_titles():
    side = between("prep.html", "<h3>전처리 조건</h3>", "<footer")
    assert '<p class="lead">분석하기 전에 데이터를 다듬어요(행 빼기·고치기·새 컬럼). 원본 데이터는 바뀌지 않아요.</p>' in side
    steps = re.findall(r'<i>\d</i><span class="t"( title="[^"]+")?>([^<]+)</span>', side)
    assert [n for _, n in steps] == ["데이터", "행 거르기", "컬럼 정리", "새 컬럼", "중복 제거", "정렬", "저장"]
    assert all(t for t, _ in steps)


def test_19e2_one_schema_hidden_and_tag_words():
    page = html()
    assert 'id="schema"' in page and "#schema:has(" not in page  # 스키마 하나면 숨기기는 common.js showConnected가 모든 화면에(시스템 카탈로그는 세지 않음)
    assert "> 태그 붙이기(쿼리에서 행에 단 표시를 컬럼으로)</label>" in page and 'id="lb-on"' in page
    assert "태그를 쓰려면 쿼리 화면 결과 표의 [태그]에서 먼저 켜 주세요" in page


def test_19e3_footer_says_what_happens():
    foot = between("prep.html", '<footer class="has-lead">', "</footer>")
    assert 'id="preview"' in foot and 'id="run"' in foot
    lead = between("prep.html", 'id="prep-foot-lead">', "</p>")
    assert "앞 20행" in lead and "저장하지 않아요" in lead and "테이블 전체" in lead and "Parquet·CSV 파일" in lead and "같은 DB의 새 테이블" in lead


def test_19e_peak_finder_folded():
    step = between("prep.html", 'data-step="filters"', 'data-step="clean"')
    assert '<div class="edx" data-adv>' in step
    adv = between("prep.html", '<div class="adv">\n            <div class="hint" style="margin-top:8px">데이터가 너무 많을 때', '<div class="cn">')
    assert all(f'id="{i}"' in adv for i in ("pk-col", "pk-unit", "peak-find", "pk-list"))
    assert 'id="f-in"' not in adv  # 조건 칸은 접지 않는다


def test_19f1_question_names_and_two_lines():
    page = html()
    labels = re.findall(r'<label title="[^"]+"><input type="radio" name="akind" value="(\w+)"(?: checked)?> <span>([^<]+\?)<small>\(([^)]+)\)</small></span></label>', page)
    assert [k for k, _, _ in labels] == KINDS
    assert dict((k, o) for k, _, o in labels)["pareto"] == "파레토" and dict((k, q) for k, q, _ in labels)["basket"] == "같이 사는 상품은?"
    assert 'class="akinds"' in page and "seg4" not in page
    for k in KINDS:
        assert re.search(r'<p class="lead"><b>이걸 알려 줘요:</b> [^<]+<br><b>필요한 컬럼:</b> [^<]+</p>', form(k)), k


def test_19f_optional_fields_folded():
    for i in OPTIONAL:
        page = html()
        assert re.search(rf'<div class="adv"><div class="hint">[^<]+</div><(input|select) id="{i}"', page), i
    for k in ("funnel", "retention", "rfm", "pareto", "abtest", "basket", "seasonal"):
        assert f'data-kind="{k}" data-adv>' in html(), k


def test_19f2_full_rerun_words():
    btn = between("prep.html", '<button id="a-full"', "</button>")
    assert btn.endswith(">표본 말고 전체 데이터로 다시 구하기") and "표본(미리 읽은 일부 행) 말고 테이블 전체 데이터로" in btn
    assert '<label class="a-rows hidden" id="a-rows-box">보일 행 수 <input type="number" id="a-rows"' in html()


@needs_node
def test_19f2_rows_box_only_with_result(tmp_path):
    part = between("prep.html", "function drawAna()", "\n}\n") + "\n}\n"
    stubs = ("let A = { result: null, view: 'groups', lo: 0, hi: 0 }; const VIEW_NAMES = {}, VIEW_RATIOS = {};"
             "const anaTitle = () => 't', anaNote = () => 'n', anaTable = () => ({}), anaSpan = () => 0, anaSlice = d => d, showRange = () => {}, resultTable = () => {}, sankeySvg = () => 's', funnelSentence = () => 'f';"
             "const SERVER = { analysis: { whole: [] } }, NEW_KINDS = [], ADD_TITLE = '', anaAuto = {}, gapTable = () => [], segTables = () => [], pairBlock = () => [], gradeLine = () => '', sortedTable = () => {}, anaRows = r => r, anaDraw = () => {};"
             "globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' }); globalThis.document = { documentElement: {} };")
    body = """
$("a-rows-box").hidden = true; drawAna(); const none = $("a-rows-box").hidden;
A.result = { kind: "groups", views: ["groups"], values: [1] }; $("ana").hidden = true; drawAna();
console.log(JSON.stringify({ none, some: $("a-rows-box").hidden }));"""
    assert run_node(tmp_path, body, parts=[ui67.ELS, stubs, part]) == {"none": True, "some": False}
