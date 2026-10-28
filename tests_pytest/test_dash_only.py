"""대시보드만 보기: 메뉴·도구·패널과 카드의 선택·설정 단추를 숨기고, Esc·[보기 끝내기]로 돌아오며, 보기 중에는 카드를 눌러도 선택되지 않는다."""
from test_fix_audit_js import between, read


def test_dash_only_button_hides_menus_and_card_controls():
    html = read("dashboard.html")
    assert 'id="d-only"' in html and 'id="d-only-exit" class="btn hidden"' in html
    rule = between("dashboard.html", "body.dash-only :is(", "}")
    for part in (".topbar", ".d-tool", ".board-ctl", "#d-panel", "#d-gallery-box", ".bresize", ".bhead .bcheck", ".bhead button"):
        assert part in rule, part
    assert "body.dash-only .bhead { pointer-events:none; }" in html  # 카드 머리를 끌어 옮기거나 이름을 고치지 못하게


def test_dash_only_exits_with_escape_and_blocks_card_selection():
    js = between("dashboard.html", "function dashOnly(on) {", "// ---------- 설정 창")
    assert 'e.key === "Escape"' in js and '$("d-only-exit").onclick = () => dashOnly(false)' in js
    assert 'board.toggle = function (...a) { if (!document.body.classList.contains("dash-only")) pickCard.apply(this, a); };' in js  # 카드 선택만 막는다
    assert "}, true);" not in js  # 잡기 단계에서 클릭을 막으면 범례·목록 값 펼치기도 막힌다
    assert '$("d-none").click()' in js  # 켤 때 선택을 푼다


def test_kpi_spark_is_a_smooth_curve_that_fills_the_card(tmp_path):
    from test_fix_audit_js import run_node
    fn = between("dash_cards.js", "function smoothPath(points) {", "\n}\n") + "\n}\n"
    got = run_node(tmp_path, fn + 'console.log(JSON.stringify([smoothPath("0,10 5,0 10,10"), smoothPath("0,1 9,2")]));')
    assert got == ["M0,10 Q5,0 7.5,5.0 L10,10", "M0,1 L9,2"]  # 가운데 점은 곡선의 조절점, 두 점이면 직선
    assert "flex:1 1 34px" in read("common.css") and '"vector-effect": "non-scaling-stroke"' in read("dash_cards.js")
