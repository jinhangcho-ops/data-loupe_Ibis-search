"""데이터 연결 창 다듬기(C1~C9): 칸 순서·줄 나눔·아래 줄. 화면 글자와 배치를 정적 파일로 본다."""
import re

from test_fix_audit_js import between, read

CARD_MIN_HEIGHT = "min-height:min(33rem, 90vh)"


def test_path_input_has_its_own_line_and_two_buttons_share_the_next():
    top = between("connect.html", '<div id="folder-top"', '<div id="url-top"')
    rows = re.findall(r'<div class="row[^"]*">.*?</div>', top)
    assert len(rows) == 2
    assert 'id="path"' in rows[0] and "<button" not in rows[0]  # C1: 입력 칸은 한 줄 전체
    assert rows[1].startswith('<div class="row conn-two">') and rows[1].index('id="pick-folder"') < rows[1].index('id="find" class="btn">파일 찾기<')
    assert ".conn-two { flex-wrap:wrap; }" in read("common.css")  # 좁은 창·큰 글자에서는 아랫줄로


def test_user_and_password_share_one_line():
    html = read("connect.html")
    row = re.search(r'<div class="row conn-two">(<input id="user"[^>]*>)(<input id="password" type="password"[^>]*>)</div>', html)
    assert row and "비워" not in row[0]  # C6
    assert html.count("없으면 비워 두세요") == 1
    assert '<div class="field-label">아이디와 비밀번호 <span class="hint">없으면 비워 두세요</span></div>' in html
    assert '<button class="btn" id="drv-open"' in html  # C7: 보통 단추


def test_foot_has_hint_cancel_connect_in_one_line():
    foot = between("connect.html", '<div class="conn-foot">', "</div>")
    assert foot.index('<span class="hint">') < foot.index('id="conn-cancel"') < foot.index('id="connect"')  # C8
    css = read("common.css")
    assert CARD_MIN_HEIGHT in css and "margin-top:auto" in between("common.css", ".conn-foot {", "}")  # 탭이 바뀌어도 [연결]은 아래 그 자리
    assert ".conn-foot .btn { height:var(--h-lg);" in css  # C9: 주 단추 높이
    assert ".conn-card .lead:has(+ .conn-for:not(:empty)) { display:none; }" in css  # C5: 안내 글은 한 줄만


def test_order_is_tabs_then_address_then_recent_then_rest():
    html, _js, css = read("connect.html"), read("common.js"), read("common.css")
    marks = ['class="seg2"', 'id="folder-top"', 'id="path"', 'id="url-top" class="hidden"', 'id="url"', 'id="recent-conn"', 'id="folder-box"', 'id="kinds"', 'id="url-box" class="hidden"', 'id="user"', 'id="err-connect"']
    at = [html.index(m) for m in marks]
    assert at == sorted(at)  # C4: 글의 순서가 곧 화면·키보드 순서
    assert not re.search(r"\border:", between("common.css", "/* 연결 카드:", ".field-label"))  # order로 자리만 옮기지 않는다
    assert "#folder-top, #url-top, #folder-box, #url-box { display:contents; }" in css and ".hidden { display:none !important; }" in css  # 숨김이 이긴다
    change = between("common.js", 'document.querySelectorAll("input[name=kind]")', "showConnectLabel();")
    assert all(f'$("{box}").classList.toggle("hidden", {hide});' in change for box, hide in (("folder-top", "!folder"), ("folder-box", "!folder"), ("url-top", "folder"), ("url-box", "folder")))
