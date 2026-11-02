"""화면 디자인 점검 A 묶음(접근성·스타일 CSS). 각 시험은 고치기 전 CSS에서 실패한다."""
import re

from test_fix_audit_js import read

MIX = "color-mix(in srgb, var(--accent) 45%, transparent)"


def rule(css, selector):
    m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", css, re.M)
    assert m, selector
    return m.group(1)


def test_a1_small_text_uses_muted():
    css = read("common.css")
    th = rule(css, "th small")
    assert "var(--muted)" in th and "var(--faint)" not in th and "font-size:12px" in th
    assert "color:var(--muted)" in re.search(r"\.choice em \{[^}]*\}", read("home.html")).group(0)


def test_a1_faint_kept_for_decor():
    css = read("common.css")
    assert "var(--faint)" in rule(css, ".crumb i") and "var(--faint)" in rule(css, ".cn")


def test_a2_search_box_focus_ring():
    assert MIX in rule(read("common.css"), ".q:focus-within")


def test_a3_input_focus_ring():
    focus = rule(read("common.css"), "input:focus, select:focus, textarea:focus")
    assert MIX in focus and "accent-line" not in focus


def test_a4_target_size():
    css = read("common.css")
    assert "min-height:24px" in rule(css, ".btype")
    ghost = rule(css, ".chip .ghost")
    assert "min-width:24px" in ghost and "min-height:24px" in ghost


def test_a5_transition_only_colors():
    css = read("common.css")
    m = re.search(r"^(\.btn, \.ib[^{]*)\{([^}]*)\}", css, re.M)
    assert m and ".tabs2 button" in m.group(1) and ".btype" in m.group(1)
    assert "transition: border-color .15s, background-color .15s, color .15s;" in m.group(2)
    assert "transform" not in m.group(2) and "width" not in m.group(2)


def test_a6_reduced_motion():
    css = read("common.css")
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert "transition-duration:0s !important" in block
    assert ".bar.unknown > div" in block and "animation:none" in block
