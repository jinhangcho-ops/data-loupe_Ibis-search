"""(Phase 20) 화면 묶음 E: A/B 검정의 사용자 칸·주의 글, 쉬운 보기의 설명 줄, 체크 칸 테두리, 창(.modal) 표시, 시험 도우미 func.
화면 스크립트는 해당 함수만 잘라 가짜 화면 위에서 node로 돌린다. 값은 시험 안에서 지어낸다."""
import re
import shutil

import pytest

from test_fix_audit_js import STATIC, func, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

WCAG_UI = 3.0  # WCAG 1.4.11: 글자가 아닌 조작 요소의 테두리는 바탕과 3:1 이상
STEP_PAGES = ("prep.html", "index.html", "collect.html")
ANA_KINDS = 13  # 분석 종류(name="akind") 수


# ---------- 20A 화면: 사용자 칸과 주의 글 ----------

def test_abtest_form_has_who_field():
    html = read("prep.html")
    form = html[html.index('id="akind-abtest"'):html.index('id="akind-basket"')]
    assert '<select id="a-bwho"></select>' in form and "한 사람이 여러 행이면 꼭 골라 주세요" in form
    assert 'keep("a-bwho", names(), ' in read("analysis.js")  # 다른 컬럼 칸처럼 컬럼 목록으로 채우고, 맨 위는 빈 값
    assert "사용자 단위 지표로 보려면 쿼리 → 지표" in form  # 지표의 A/B를 가리키는 한 줄


@needs_node
def test_abtest_request_carries_who(tmp_path):
    body = """
let vals = { "a-bgroup": "arm", "a-bmetric": "rate", "a-bval": "paid", "a-bctrl": "A", "a-bconf": "", "a-bwho": "uid" };
globalThis.$ = id => ({ value: vals[id] ?? "" });
const akind = () => "abtest", PCT = 100, pickers = {};
const picked = anaHow();
vals["a-bwho"] = "";
console.log(JSON.stringify([picked, anaHow().who]));
"""
    picked, blank = run_node(tmp_path, body, parts=[func("analysis.js","anaHow")])
    assert picked == {"kind": "abtest", "group": "arm", "metric": "rate", "value": "paid", "control": "A", "conf": None, "who": "uid"}
    assert blank is None  # 비우면 null(한 행 = 한 사용자)


@needs_node
def test_note_lines(tmp_path):
    body = """
const lines = noteLines({ notes: ["한 사람이 여러 행이에요", "<b>굵게</b>"] });
console.log(JSON.stringify([lines.map(x => [x.tag, x.className, x.textContent]), noteLines({}).length, noteLines({ notes: [] }).length]));
"""
    lines, none, empty = run_node(tmp_path, body, parts=[func("analysis.js","noteLines")])
    assert lines == [["div", "hint warn", "한 사람이 여러 행이에요"], ["div", "hint warn", "<b>굵게</b>"]]  # 글은 textContent로(태그로 읽지 않는다)
    assert none == 0 and empty == 0
    html = read("prep.html")
    assert 'id="a-notes"' in html and '$("a-notes").replaceChildren(...(d ? noteLines(d) : []))' in func("analysis.js","drawAna")


# ---------- 20D hints: 쉬운 보기의 설명 줄 ----------

@needs_node
def test_title_hints_adds_one_line_each(tmp_path):
    body = """
const added = [];
const node = (title, inner) => ({ title, next: null, querySelector: () => inner ? { title: inner } : null, get nextElementSibling() { return this.next; },
  after(x) { x.classList = { contains: c => x.className.split(" ").includes(c) }; this.next = x; added.push(x); } });
const marked = [node("분포를 봐요"), node("", "테이블을 골라요")], root = { querySelectorAll: q => q === "[data-hint]" ? marked : [] };
titleHints(root); titleHints(root);
console.log(JSON.stringify(added.map(x => [x.tag, x.className, x.textContent])));
"""
    got = run_node(tmp_path, body, parts=[func("common.js", "titleHints")])
    assert got == [["div", "hint easy-only", "분포를 봐요"], ["div", "hint easy-only", "테이블을 골라요"]]  # 두 번 불러도 한 줄씩


def test_title_hints_marked_on_steps_and_kinds():
    assert "html:not(.easy) .easy-only { display:none; }" in read("common.css")
    assert re.search(r"^advMore\(\);\ntitleHints\(\);$", read("common.js"), re.M)  # 화면이 뜰 때 한 번
    prep = read("prep.html")
    assert len(re.findall(r'<label><input type="radio" name="akind"', prep)) == ANA_KINDS == prep.count('name="akind"')  # 분석 종류 사이에는 설명 줄을 끼우지 않는다(고른 종류의 설명은 아래 구역에)
    for page in STEP_PAGES:  # 단계 머리마다. 단계 머리 말고는 붙이지 않는다
        html = read(page)
        steps = len(re.findall(r'<div class="step[ "]', html))
        assert steps and len(re.findall(r'<div class="sp" data-hint[ >]', html)) == steps, page
        assert html.count(" data-hint") == steps, page


# ---------- 20D bcheck ----------

def luminance(color):
    """#rrggbb·#rgb의 상대 휘도(WCAG)."""
    full = color[1:] if len(color) == 7 else "".join(c * 2 for c in color[1:])
    parts = [int(full[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted([luminance(a), luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_bcheck_border_is_visible():
    css = read("common.css")
    assert re.search(r"^\.bcheck \{[^}]*border:1\.5px solid var\(--line-strong\);", css, re.M)
    light, dark = css[css.index(":root {"):css.index(":root[data-theme=dark]")], css[css.index(":root[data-theme=dark]"):]
    ratios = [contrast(*[re.search(rf"--{name}:(#[0-9a-f]{{3,6}});", block)[1] for name in ("line-strong", "card")]) for block in (light, dark)]
    assert [round(r, 1) for r in ratios] == [3.4, 3.6] and min(ratios) >= WCAG_UI  # 카드 바탕 위: 밝은 화면 3.4:1, 어두운 화면 3.6:1 (고치기 전 --faint는 2.6·3.0)


# ---------- 20G modal_role ----------

def test_every_modal_is_a_dialog():
    seen = 0
    for path in sorted(STATIC.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'<div[^>]*class="modal[ "][^>]*>\s*<[^>]*>', text):  # 창 바탕(.modal)과 그 안의 첫 태그(.modal-box)
            seen += 1
            assert 'role="dialog"' in m[0] and 'aria-modal="true"' in m[0], f"{path.name}: {m[0][:80]}"
            assert re.search(r'aria-label(ledby)?="', m[0]), f"{path.name}: {m[0][:80]}"
            for ref in re.findall(r'aria-labelledby="([^"]+)"', m[0]):
                assert text.count(f'id="{ref}"') == 1, f"{path.name}: {ref}"
    assert seen
    made = 0
    for path in sorted([*STATIC.rglob("*.html"), *STATIC.rglob("*.js")]):  # 스크립트가 만드는 창: 만든 수만큼 role·aria-modal을 붙인다
        text = path.read_text(encoding="utf-8")
        n = text.count('className: "modal"')
        made += n
        assert text.count('setAttribute("role", "dialog")') >= n and text.count('setAttribute("aria-modal", "true")') >= n, path.name
    assert made


# ---------- 20E between → func ----------

def test_func_cuts_one_function():
    for name, head in (("showAdv", "function showAdv("), ("busy", "async function busy("), ("advMore", "function advMore(")):
        code = func("common.js", name)
        assert code.startswith(head) and len(re.findall(r"^(?:async )?function ", code, re.M)) == 1, name
    assert "keepAdv(list)" not in func("common.js", "showAdv")  # 다음 함수 앞에서 끊는다
    with pytest.raises(AttributeError):
        func("common.js", "없는함수")


# ---------- 20E 분석 스크립트 옮기기 ----------

def test_analysis_script_is_put_back_in_place(client):
    """분석 부분은 analysis.js에 있고, 내려주는 화면에서는 전처리 스크립트의 원래 자리에 그대로 끼워진다(스크립트를 나누지 않는다: 실행 차례가 그대로다)."""
    raw, part, page = read("prep.html"), read("analysis.js"), client.get("/prep").get_data(as_text=True)
    assert "function drawAna(" not in raw and raw.count("<!--include prep/analysis.js-->") == 1
    assert part in page and "<!--include" not in page
    assert page.index("function showView(") < page.index(part) < page.index("resume()", page.index(part))
    assert "<script" not in page[page.index("function showView("):page.index(part) + len(part)]
