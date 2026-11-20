"""분석 화면을 시안 모양으로: [전처리 | 분석] 토글은 설정 패널 머리에, "무엇이 궁금해요?" 아래 고른 종류의 칸이 그 자리에서 펼쳐지고, 결과 머리에 제목과 [대시보드에 추가]가 한 줄.
글자·자리는 prep.html을 읽어 보고, showAnaForm은 그 구간만 잘라 가짜 화면 위에서 node로 돌린다."""
import re
import shutil

import pytest

import test_fix_ui67_c as ui67
from test_fix_audit_js import between, read, run_node
from test_ux19_prep import KINDS

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# .akinds 안의 차례(order)만 흉내 낸다: label 셋(a·b·c)은 처음부터 들어 있고, 칸(.a-form)은 밖에 있다가 after()로 들어온다.
AKINDS = r"""
const order = [], forms = {}, labels = {};
let picked = "a", moves = 0;
const node = (name, kind) => { const n = { name, hidden: false, dataset: { kind }, closest: () => n,
  classList: { toggle(c, on) { if (c === "hidden") n.hidden = on; } },
  get nextElementSibling() { return order[order.indexOf(n) + 1] ?? null; },
  after(x) { if (order.includes(x)) order.splice(order.indexOf(x), 1); order.splice(order.indexOf(n) + 1, 0, x); moves++; } }; return n; };
for (const k of ["a", "b", "c"]) { labels[k] = node("label-" + k, k); forms[k] = node("form-" + k, k); order.push(labels[k]); }
globalThis.document = {
  querySelectorAll: () => Object.values(forms),
  querySelector: s => s.includes(":checked") ? { value: picked, closest: () => labels[picked] } : forms[s.match(/data-kind="(\w+)"/)[1]] };
const seen = () => ({ order: order.map(n => n.name), shown: Object.values(forms).filter(f => !f.hidden).map(f => f.name), moves });
"""


def test_view_toggle_sits_on_top_of_the_panel():
    page = read("prep.html")
    assert page.count('id="views"') == 1  # 토글은 하나, 보이는 패널로 옮겨 다닌다
    side = between("prep.html", '<aside class="side">', "<h3>전처리 조건</h3>")
    assert re.fullmatch(r'<aside class="side">\s*<nav class="subnav" id="views"><button data-view="prep" class="on">전처리</button><button data-view="ana">분석</button></nav>\s*<header>', side)
    assert '$(VIEWS[name]).querySelector(".side").prepend($("views"));' in between("prep.html", "function showView(name)", "\n}\n")
    for view in ("prep-view", "ana"):  # 두 보기 모두 설정 패널이 있어야 토글이 갈 곳이 있다
        assert '<aside class="side">' in between("prep.html", f'id="{view}"', "</aside>"), view


def test_question_heading_keeps_all_kinds():
    page = read("prep.html")
    assert '<div class="hint ahead">무엇이 궁금해요?</div>' in page and "분석 종류</div>" not in page
    box = between("prep.html", '<div class="akinds"', "\n      </div>\n")
    assert re.findall(r'<label><input type="radio" name="akind" value="(\w+)"(?: checked)?> <span>[^<]+<small>[^<]+</small></span></label>', box) == KINDS
    assert [k for k in KINDS if page.count(f'class="a-form" data-kind="{k}"') + page.count(f'class="a-form hidden" data-kind="{k}"') + page.count(f'id="akind-{k}" data-kind="{k}"') != 1] == []  # 종류마다 칸 하나


@needs_node
def test_picked_form_opens_under_its_label(tmp_path):
    parts = [ui67.ELS, AKINDS, between("analysis.js", "const akind = ", "\n") + "\n", between("prep.html", "const anaForm = ", "\n") + "\n",
             between("analysis.js", "function showAnaForm()", "\n}\n") + "\n}\n"]
    body = """
picked = "b"; showAnaForm(); const first = seen();
showAnaForm(); const again = seen();
picked = "a"; showAnaForm();
console.log(JSON.stringify({ first, again: again.moves, last: seen() }));"""
    got = run_node(tmp_path, body, parts=parts)
    assert got["first"] == {"order": ["label-a", "label-b", "form-b", "label-c"], "shown": ["form-b"], "moves": 1}
    assert got["again"] == 1  # 이미 그 자리면 옮기지 않는다
    assert got["last"] == {"order": ["label-a", "form-a", "label-b", "form-b", "label-c"], "shown": ["form-a"], "moves": 2}


def test_picked_kind_and_form_share_one_accent_box():
    css = between("prep.html", "<style>", "</style>")
    assert ":is(.akinds, .a-more-list) > label:has(input:checked) { border-bottom:0; border-radius:var(--r-card) var(--r-card) 0 0; }" in css
    form = re.search(r"^  \.a-form \{([^}]*)\}", css, re.M).group(1)
    assert "border:1px solid var(--accent)" in form and "border-top:0" in form and "background:var(--accent-bg)" in form


def test_result_head_has_title_and_add_button():
    page = read("prep.html")
    head = between("prep.html", '<div class="big a-head">', '<div class="big a-answer" id="a-answer">')
    assert head.index('id="a-title"') < head.index('id="a-sub"') < head.index('id="a-add"') < head.index('id="a-pop"')
    assert all(f'id="{i}"' in head for i in ("a-dash", "a-dash-name", "a-dash-go"))
    assert 'id="a-add"' not in between("prep.html", '<div class="tool"><span class="hint m-0 flex-1" id="a-data"', "</div>")  # 도구 줄에는 데이터 설명과 [전체로 다시 구하기]만
    assert page.index('id="a-full"') < page.index('id="a-src"') < page.index('id="a-title"') < page.index('id="a-answer"') < page.index('id="a-box"')
