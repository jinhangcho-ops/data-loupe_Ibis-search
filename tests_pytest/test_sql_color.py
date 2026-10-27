"""SQL 편집기 색칠(sqlColor): 키워드만 파랑, 따옴표·주석 안의 낱말은 그대로, HTML은 이스케이프. node가 없으면 건너뛴다."""
import json
import shutil
import subprocess

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def color(sql):
    src = (ROOT / "static/query/index.html").read_text(encoding="utf-8")
    body = src[src.index("const SQL_WORDS"):src.index("\n{\n  const box = $(\"sql-text\")")]
    js = "const esc = v => String(v ?? '').replace(/[&<>\"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#39;'}[c]));\n" + body + f"\nconsole.log(JSON.stringify(sqlColor({json.dumps(sql)})));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_keywords_blue_but_not_inside_strings_or_comments():
    got = color("SELECT 매장 FROM t WHERE note = 'select' -- from\nand \"order\" < 3")
    assert got == ('<span class="sk">SELECT</span> 매장 <span class="sk">FROM</span> t <span class="sk">WHERE</span> note = '
                   '<span class="ss">&#39;select&#39;</span> <span class="sc">-- from</span>\n<span class="sk">and</span> &quot;order&quot; &lt; 3')


def test_words_glued_to_hangul_are_not_keywords():
    assert color("상태and selection") == "상태and selection"
