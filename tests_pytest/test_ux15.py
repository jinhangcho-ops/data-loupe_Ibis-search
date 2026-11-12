"""Phase 15: UI 평가 결함·다듬기(plans/phase-15.md). 화면 글자·배치는 정적 검사, 서버는 함수로 본다.
기존 코드를 고쳐야 통과하는 시험은 xfail(strict)이다. 그 항목을 끝낸 strict-coder가 표시를 지운다."""
import datetime as dt
import re
import shutil

import pytest

from conftest import ROOT
from test_fix_audit_js import between, read, run_node

WEB = ROOT / "web"


def web(path):
    return (WEB / path).read_text(encoding="utf-8")


# ---------- 15A 결함 ----------

def test_geomap_file_mode_text_fields():
    src = web("components/edit/AddCardMenu.tsx")
    geo = src[src.index('is("geomap") && ('):src.index('is("deviation") && (')]
    assert "f.gPath.trim() ?" in geo
    assert '<Txt label="영역 이름"' in geo and '<Txt label="도형 컬럼"' in geo  # 파일 방식은 글자로 적는다
    assert '<Sel label="영역 이름"' in geo  # 테이블 방식은 그대로 고르기


def test_check_name_none_is_value_error():
    from core import parse
    with pytest.raises(ValueError, match="값 컬럼을 고르세요"):
        parse.check_name(None, ["a"], "값 컬럼")
    with pytest.raises(ValueError, match="비슷한 이름: amt"):  # 없는 이름 안내는 그대로
        parse.check_name("amt2", ["amt"], "값 컬럼")


def test_candle_without_value_column_asks_to_pick():
    import ibis
    from dashboard import dash_calc
    t = ibis.memtable({"t": [dt.date(2025, 1, 1), dt.date(2025, 1, 2)], "v": [1.0, 2.0]})
    with pytest.raises(ValueError, match="양초 차트의 값을 고르세요"):  # 예전에는 TypeError('NoneType' object is not iterable)
        dash_calc.candle_data(t, "t", None, "day")


def test_collect_first_guide_follows_mode():
    box = between("collect.html", 'id="first-guide"', "</section>")
    assert box.count("[시험 보내기]") == 1
    api = re.search(r'<li class="for-api">(.*?)</li>', box)
    assert api and "[시험 보내기]" in api.group(1)
    assert '<li class="for-file hidden">' in box and "[파일 주소]" in box
    page = re.search(r'<li class="for-web hidden">(.*?)</li>', box)
    assert page and "[수집용 창 열기]" in page.group(1) and "[값 고르기]" in page.group(1)
    assert "[받는 방식]" in box and "[수집 시작]" in box  # test_ux14가 보는 글자


# ---------- 15B 카드 추가 창 ----------

def test_added_card_scrolls_into_view():
    grid = web("components/grid/Grid.tsx")
    assert "export function scrollToCard(id: string): void" in grid and "Sub-agent must implement" not in grid
    shell = grid[grid.index("export function useAddShell"):grid.index("function WhenLine")]
    assert "scrollToCard(card.id)" in shell
    bar = web("components/shell/Toolbar.tsx")
    assert "scrollToCard(full.id)" in bar and "scrollToCard(last.id)" in bar


def test_menu_required_marks_kind_change_and_words():
    fields = web("components/edit/menu/Fields.tsx")
    assert "need?: boolean" in fields and 'className="cm-need"' in fields and "aria-required" in fields
    src = web("components/edit/AddCardMenu.tsx")
    lines = src.splitlines()

    def line(marker):
        return next(x for x in lines if marker in x)
    assert 'setErr("")' in line('label="카드 종류"')  # 종류를 바꾸면 이전 종류의 오류를 지운다
    for marker in ('label="테이블"', 'label="가로축"', 'label="사용자 컬럼"', 'label="대상 컬럼"'):
        assert re.search(r"\bneed\b", line(marker)), marker
    assert 'need={is("gauge")}' in line('label="나눌 컬럼"')
    assert 'need={!is("boxplot")}' in line("X_LABEL[f.kind] && <Sel")
    for word in ("또는", "안에", "N일 안에 다시 온 사람"):
        assert f'<span className="cm-word">{word}</span>' in src, word


def test_menu_defaults_use_server_and_date_type():
    src = web("components/edit/AddCardMenu.tsx")
    assert "unitForDate(" in src and "consts.dash.max_cells" in src
    assert 'BUCKET_TIMED = "hour"' in src and 'FUNNEL_TIMED = "minute"' in src


def test_dash_constants_max_cells():
    from core import dash_store, server
    from dashboard import dash_app, dash_calc  # noqa: F401  late_constants를 단다
    assert server.constants("dashboard")["dash"]["max_cells"] == dash_calc.MAX_CELLS == dash_store.MAX_CELLS  # 화면 상수는 dash_store에서(dash_calc를 올리지 않게)
    assert "max_cells: number;" in web("lib/types.ts")  # 서버가 늘 보내므로 필수(없을 때 대신 쓸 값을 두지 않는다)


# ---------- 15C 카드 설정 패널 ----------

def test_panel_place_fields_folded_at_bottom():
    src = web("components/edit/CardPanel.tsx")
    fold = src.index("<summary>위치·크기</summary>")
    assert src.index("<StarSection") < fold and src.index("이 카드 다시 구하기") < fold
    assert src.index('<details className="pplace">') < src.index('className="prow"') < src.index("</details>", fold)


def test_tools_select_labels_color_reason_template_funnel():
    src = web("components/edit/panel/Tools.tsx")
    for old in ("기간 기준: ", "그림 모양: {n}", "아이콘: {n}", "값 축: {n}", "정렬: {", ">추이선 아래<", ">색</button>"):
        assert old not in src, old
    for label in ("기간 기준", "그림 모양", "아이콘", "큰 값 크기", "추이선 자리", "기간 단위", "표시 형식", "값 축", "정렬", "단위"):
        assert f'<label className="hint bset">{label}<' in src, label
    assert "title={why || undefined}" in src and "색 바꾸기" in src
    assert 'aria-label="문장 틀"' in src and "TEMPLATE_EMPTY" in src
    assert re.search(r'<label key=\{s\} className="hint bset"><input type="checkbox" aria-label=\{`걸린 시간 \$\{GAP_NAMES\[s\]\}`\}', src)  # 칸 먼저, aria-label은 그대로


def test_star_section_explains_terms():
    src = web("components/edit/panel/StarSection.tsx")
    assert "STAR_HELP" in src and "북극성은 대시보드에서 가장 중요한 지표 하나(★)" in src


# ---------- 15D 대시보드 ----------

def test_board_pick_line_only_when_editing_or_picked():
    src = web("app/dashboard/page.tsx")
    assert "n > 0 && (state.editing || picked > 0) && (" in src
    assert 'id="d-picked"' in src and 'id="d-all"' in src and 'id="d-none"' in src


def test_query_import_menu_item_no_wrap():
    bar = web("components/shell/Toolbar.tsx")
    assert re.search(r'<a role="menuitem"[^\n]*whiteSpace: "nowrap"[^\n]*>쿼리에서 가져오기</a>', bar)


# ---------- 15E 설정 ----------

SETTING_IDS = ("memory", "memory-gb", "threads", "scan-unknown", "too-few", "pc-stat-rows", "cloud-rows", "hash-exclude",
               "sample-conf", "sample-e-ratio", "sample-e-mean", "pilot-rows", "strata-h", "strata-min", "hash-buckets", "key-over", "pilot-first",
               "timezone", "week-start", "fiscal-start", "weekend", "work-start", "work-end", "bucket-minutes", "ewma-span",
               "viz-prefs", "viz-words", "viz-auto", "err-viz")


def test_app_settings_moved_to_top_settings():
    connect, status = read("connect.html"), read("status.html")
    panel = status[status.index('id="feat-panel"'):]
    for i in SETTING_IDS:
        assert f'id="{i}"' in panel, i
        assert f'id="{i}"' not in connect, i
    assert "conn-more" not in connect and 'class="app-set"' in panel and 'id="err-settings"' in panel
    assert panel.index('id="app-version"') < panel.index('id="memory"')  # 쓸 기능·업데이트 다음에
    block = between("common.js", "if (hasConnect) {\nshowRecentConnections();", "\n}\n")
    assert '$("memory")' not in block and "viz-words" not in block  # 연결 칸이 없는 화면(수집)에서도 돈다
    assert 'say("err-settings"' in read("common.js")


def test_css_words_need_and_settings():
    css = read("common.css")
    assert ".cardmenu .cm-word" in css and ".cm-need" in css and ".app-set .opt" in css
    assert ".conn-more" not in css


def test_messages_point_to_settings():
    for path in ("core/prep_ops.py", "dashboard/cloud_words.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "고급 설정" not in src and "위쪽 [설정]" in src, path


# ---------- 15F 쿼리 ----------

def test_summary_names_reach_query_screen():
    from core import result, server
    from query import search_app  # noqa: F401  late_constants를 단다
    names = server.constants("query")["summary_names"]
    assert names == result.SUMMARY_NAMES and set(result.SUMMARY_COLS) <= set(names)


def test_query_chart_first_and_pivot_labels():
    pane = between("index.html", 'id="pane-chart"', "</section>")
    at = pane.index('id="chart"')
    assert pane.index('id="series"') < at < pane.index('id="cgroup-box"') and at < pane.index('id="clabels"')  # 차트가 설정 칸들보다 위
    html = read("index.html")
    assert "SERVER.summary_names" in html
    for i, name in (("pv-rows", "행"), ("pv-rows2", "둘째 행"), ("pv-col", "열"), ("pv-val", "값 컬럼"), ("pv-sub", "소계"), ("pv-show", "칸 표시"),
                    ("pv-preset", "집계 묶음"), ("pv-key", "기준"), ("pv-target", "값"), ("pv-convert", "전환율")):
        assert f'<span class="cl">{name}</span><select id="{i}"' in html, i


# ---------- 15G 수집용 창 ----------

def test_picker_shows_picking_badge():
    js = read("collect_picker.js")
    assert '"-badge"' in js and "pointer-events:none" in js and "badges:" in js and "badge.remove()" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_collect_field_name_from_class(tmp_path):
    part = between("collect.html", "function fieldName(", "\nfunction applyPick(")
    body = 'console.log(JSON.stringify([fieldName("div.info > span.price", []), fieldName("span.price", ["price"]), ' \
           'fieldName("span.price", ["price", "price_2"]), fieldName("li.item span.a.big", ["x"]), fieldName("div > span", ["a", "b"])]));'
    assert run_node(tmp_path, body, parts=[part]) == ["price", "price_2", "price_3", "big", "컬럼3"]
    html = read("collect.html")
    assert "fieldName(p.selector" in html and "name: `컬럼${n}`" not in html


def test_topbar_links_open_static_panels():
    bar = web("components/shell/TopBar.tsx")
    assert 'PANEL_PARAM = "panel"' in bar and '["dest", "보낼 곳 설정"' in bar and '["feat", "설정"' in bar  # 대시보드에도 [보낼 곳 설정]·[설정]
    js = read("common.js")
    assert 'const PANEL_PARAM = "panel", PANELS = ["feat", "dest"]' in js and 'get(PANEL_PARAM)' in js and '"-open").click()' in js  # 홈이 그 창을 연다
