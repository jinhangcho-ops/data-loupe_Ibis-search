"""화면 크기 단위: CSS 길이는 rem으로 쓰고(바탕 글자 크기에 따라 함께 커진다), 화면 크기 설정(ui_scale)의 단계마다 html[data-scale] 규칙이 있다."""
import re

from conftest import ROOT
from core import settings

COMMON_CSS = ROOT / "static/core/common.css"
CSS_FILES = (COMMON_CSS, ROOT / "static/core/dash_edit.css", ROOT / "static/query/sentences.css", ROOT / "web/styles/dashboard.css")
HTML_GLOB = "static/*/*.html"
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)  # 설명 글의 px는 보지 않는다
PX_VALUE =re.compile(r"(\d*\.?\d+)px")
MAX_PX = 1.0  # 가는 선(1px 이하)은 px로 둔다
PX_ALLOWED_LINES = ("@media", "@container", "html { font-size:")  # 창 너비 조건과 바탕 글자 크기 줄만 px
SETTING = "ui_scale"
BASE_SCALE = "normal"  # 바탕 크기 그대로라 규칙이 없다


def big_px(text: str) -> list[str]:
    """px를 써도 되는 줄을 뺀 나머지에서 MAX_PX보다 큰 px 값이 든 줄."""
    lines = [line for line in CSS_COMMENT.sub("", text).splitlines() if not any(mark in line for mark in PX_ALLOWED_LINES)]
    return [line.strip() for line in lines if any(float(v) > MAX_PX for v in PX_VALUE.findall(line))]


def test_css_files_have_no_px_lengths() -> None:
    for path in CSS_FILES:
        assert big_px(path.read_text(encoding="utf-8")) == [], path.name


def test_html_style_blocks_have_no_px_lengths() -> None:
    pages = sorted(ROOT.glob(HTML_GLOB))
    assert pages
    for path in pages:
        assert big_px("\n".join(STYLE_BLOCK.findall(path.read_text(encoding="utf-8")))) == [], path.name


def test_ui_scale_default_is_a_choice() -> None:
    assert settings.DEFAULTS[SETTING] in settings.CHOICES[SETTING]


def test_every_scale_but_base_has_a_css_rule() -> None:
    css = COMMON_CSS.read_text(encoding="utf-8")
    scales = [s for s in settings.CHOICES[SETTING] if s != BASE_SCALE]
    assert scales and settings.DEFAULTS[SETTING] == BASE_SCALE
    assert sorted(re.findall(r"^html\[data-scale=(\w+)\] \{ --ui-scale:[\d.]+; \}", css, re.M)) == sorted(scales)
