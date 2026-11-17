"""쿼리 화면을 시안 모양으로: 쉬운 보기의 기본 단계(데이터·조인·기간·조건), 결과 머리 한 줄(탭·행 수·내보내기), SQL 탭, 단계 번호 원.
화면 글자·배치는 index.html·common.css를 읽어 정적으로 본다."""
import re

from test_fix_audit_js import between, read
from test_ux19_query import folded, step, tree


def test_easy_view_shows_data_join_period_condition():
    ids, html = tree(), read("index.html")
    order = re.findall(r'data-step="(\w+)"', html[html.index('id="flow"'):html.index("<footer>")])
    assert order == ["data", "union", "joins", "periods", "conditions", "calcs", "sorts", "columns"]  # DOM 순서는 그대로
    shown = [key for key in order if "adv" not in step(html, key).split(">")[0]]
    assert shown == ["data", "joins", "periods", "conditions"]
    assert 'class="step adv"' in step(html, "sorts") and folded(ids, "sort-col") and not folded(ids, "join-blocks")
    for key in order:  # 단계 제목 앞의 선 그림 아이콘
        assert re.search(r'<i>\d</i><svg width="18" height="18"[^>]*stroke="currentColor"[^>]*aria-hidden="true">', step(html, key)), key
    assert "S.sorts.length" in between("index.html", "function showFlowGuide()", "\n}\n")  # 접힌 정렬에 값이 있으면 펼친다


def test_four_tabs_and_sql_lives_in_its_tab():
    ids, html = tree(), read("index.html")
    tabs = between("index.html", '<div class="tabs2">', "</div>")
    assert re.findall(r'data-tab="(\w+)"[^>]*>([^<]+)<', tabs) == [("rows", "표"), ("summary", "집계"), ("chart", "차트"), ("sql", "SQL")]
    assert any(a.get("id") == "pane-sql" for a in ids["sql"]) and ids["sql"][0]["<tag>"] == "pre"
    assert any(a.get("id") == "pane-sql" for a in ids["plan-show"])
    assert "SQL 보기<" not in html and ids["sql-show"][0].get("data-tab") == "sql"  # [SQL 보기] 단추는 없고 그 id는 SQL 탭이 잇는다
    assert '["rows", "summary", "chart", "sql"].forEach(t => $("pane-" + t)' in html
    show = between("index.html", '$("sql-show").onclick', "\n});")
    assert 'showTab("sql");' in show and 'api("/api/sql", query(skip))' in show
    assert 'if (st.pane === "sql") $("sql-show").onclick();' in between("index.html", "async function restore(st)", "\n}\n")


def test_result_head_is_one_row():
    ids, html = tree(), read("index.html")
    head = html[html.index('<div class="rhead">'):html.index('<div class="big">')]
    assert head.index('class="tabs2"') < head.index('id="big-count"') < head.index('id="export-open"')
    for name in ("big-count", "export-open", "sql-show"):
        assert ids[name][1].get("class") == "rhead" or ids[name][2].get("class") == "rhead", name
    assert ".rhead { display:flex;" in html and ".rhead #big-count { margin-left:auto;" in html
    tools = between("index.html", 'id="result-tools"', '<section class="pane" id="pane-rows">')  # 찾기·전처리로 보내기·태그는 탭 줄 아래 도구 줄에
    assert head.index("rhead") < html.index('id="result-tools"') and all(f'id="{name}"' in tools for name in ("row-find", "handoff", "label-open", "label-mode"))
    foot = between("index.html", "<footer>", "</footer>")
    assert foot.index('id="saved-open" class="ghost"') < foot.index('id="search" class="btn p"') and "style=" not in foot
    assert "#side > footer { flex-direction:column; align-items:stretch;" in html


def test_step_number_is_always_accent_and_steps_use_dividers():
    css = read("common.css")
    number = re.search(r"^\.step \.sp i \{([^}]*)\}", css, re.M)[1]
    assert "background:var(--accent);" in number and "color:var(--on-accent);" in number
    box = re.search(r"^\.step \{([^}]*)\}", css, re.M)[1]
    assert "border-top:1px solid var(--line);" in box and "border:" not in box and "radius" not in box
    assert ".step.open .sp i" not in css and re.search(r"^\.step\.open \{ background:var\(--accent-bg\); \}", css, re.M)
    assert ".step.open .edx { display:block; }" in css and ".step .edx { display:none;" in css  # 펼침·접힘은 그대로
