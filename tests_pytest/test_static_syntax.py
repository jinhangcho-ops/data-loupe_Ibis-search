"""화면 파일 전체의 JS 문법 확인(node --check). 조각만 잘라 돌리는 시험은 한 줄 주석이 catch를 삼킨 것 같은 문법 오류를 못 잡아, 실제 화면이 통째로 멈춘 적이 있다."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없음")


def check(path):
    got = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, encoding="utf-8")
    return got.stderr.strip()


@pytest.mark.parametrize("page", sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "static").rglob("*.html")))
def test_inline_scripts_parse(page, tmp_path):
    text = (ROOT / page).read_text(encoding="utf-8")
    for i, m in enumerate(INLINE.finditer(text)):
        js = tmp_path / f"inline{i}.js"
        js.write_text(m.group(1), encoding="utf-8")
        line = text[:m.start(1)].count("\n") + 1
        assert check(js) == "", f"{page} {line}번째 줄부터의 스크립트"


@pytest.mark.parametrize("script", sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "static").rglob("*.js") if not p.name.endswith(".min.js")))
def test_js_files_parse(script):
    assert check(ROOT / script) == ""
