"""Phase 14: 처음 쓰는 사람 기준 화면 다듬기. 기능·id는 그대로 두고 안내·접기·묶기만 바꿨는지 본다."""
import re

from test_fix_audit_js import between, read


def test_collect_first_guide_and_folded_placeholders():
    html = read("collect.html")
    assert html.index('id="first-guide"') < html.index("<h3>요청</h3>")  # 처음 안내가 요청 카드보다 위
    assert "[받는 방식]" in between("collect.html", 'id="first-guide"', "</section>") and "[수집 시작]" in html
    assert '<details class="hint"><summary>자리 표시 쓰기</summary>' in html and "쓸 수 있는 자리 표시:" not in html
    assert '$("first-guide").classList.toggle("hidden", !!c)' in html  # 저장한 구성을 불러오면 숨긴다


def test_metric_file_folded_until_used():
    html = read("index.html")
    box = between("index.html", '<details id="met-file">', "</details>")
    assert 'id="met-path"' in box and 'id="met-pick"' in box and 'id="met-read"' in box and 'id="met-info"' in box
    assert 'if (d.path || d.error) $("met-file").open = true' in html
    assert "이름을 붙여 둔 계산" in html


def test_prep_read_settings_folded():
    box = between("prep.html", '<details id="read-box"', "</details>")
    for i in ('id="sample"', 'id="sample-way"', 'id="stats-on"', 'id="scope-row"', 'id="prep-where"', 'id="read-sum"'):
        assert i in box, i
    html = read("prep.html")
    assert "body:not([data-conn=url]) #where-row { display:none; }" in html
    assert 'document.body.dataset.conn = info?.kind ?? ""' in read("common.js")


def test_integrations_dev_card_gone_and_ga4_hints():
    html = read("integrations.html")
    assert "새 서비스 추가하기" not in html and "SENDERS" not in html
    assert "무엇별로 볼까요" in html and "무엇을 셀까요" in html and "28daysAgo(28일 전)" in html


def test_sql_llm_settings_folded():
    box = between("index.html", '<details id="llm-box">', "</details>")
    assert 'id="llm-name"' in box and 'id="llm-env"' in box and 'id="llm-read"' in box
    assert 'id="nl-q"' not in box  # 질문 칸은 접지 않는다


def test_export_buttons_in_one_menu():
    html = read("index.html")
    pop = between("index.html", 'id="export-pop"', "</div></div>")
    assert re.findall(r'data-export="(\w+)"', pop) == ["csv", "xlsx", "parquet", "tsv", "fixed"] and 'id="export-tables"' in pop
    assert not re.search(r'data-export="\w+"', html.replace(pop, ""))  # 메뉴 밖에 형식 단추가 남지 않는다
    assert '"export-pop"].forEach' in html  # 밖을 누르면 닫힌다


def test_small_touches():
    assert all(f'value="{k}"' in read("prep.html") for k in ("distribution", "seasonal"))
    assert len(re.findall(r'<label><input type="radio" name="akind"', read("prep.html"))) == len(re.findall(r'name="akind"', read("prep.html")))
    assert 'value="."' not in read("connect.html")
    assert "데이터 연결 필요" not in read("home.html")  # Phase 16: 기능 카드("데이터 연결부터")는 없어지고 연결 칸이 펼쳐져 있다
