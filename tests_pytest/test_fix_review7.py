"""7차 리뷰에서 찾은 것. 화면마다 함께 올라가는 스크립트의 최상위 이름이 겹치면 뒤에 올라간 파일 전체가 SyntaxError로 멈춘다
(대시보드에서 common.js의 GRID_MIN과 dash_grid.js의 GRID_MIN이 겹쳐 clampLayout 등이 없어졌다)."""
import re

import pytest

from conftest import ROOT

STATIC = ROOT / "static"


def declared(text):
    """줄 맨 앞의 const·let·class 이름(한 줄에 쉼표로 여럿 선언한 것도)과 function 이름."""
    lexical, functions = [], re.findall(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", text, re.M)
    for kind, rest in re.findall(r"^(const|let|class)\s+(.*)$", text, re.M):
        if kind == "class":
            lexical.append(re.match(r"[\w$]+", rest).group(0))
            continue
        depth, quote, cur, parts = 0, None, "", []
        for ch in rest:  # 괄호·따옴표 밖의 쉼표로 나눈다(const a = f(1, 2), b = 3;)
            if quote:
                quote = None if ch == quote else quote
            elif ch in "\"'`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            elif ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
                continue
            cur += ch
        parts.append(cur)
        lexical += [n for n in (p.split("=")[0].strip() for p in parts) if re.fullmatch(r"[A-Za-z_$][\w$]*", n)]
    return lexical, functions


def page_scripts(html):
    """화면이 부르는 스크립트(정적 src, need()로 늦게 부르는 파일 전부, 인라인)를 (이름, 글) 목록으로."""
    lazy = sorted({s for f in STATIC.rglob("*.js") for s in re.findall(r"""need\(\s*["'`](/static/[^"'`]+\.js)""", f.read_text(encoding="utf-8"))})
    srcs = re.findall(r'<script src="(/static/[^"]+\.js)"', html)
    found = [(s, (ROOT / s.lstrip("/")).read_text(encoding="utf-8")) for s in dict.fromkeys(srcs + lazy) if (ROOT / s.lstrip("/")).exists()]
    return found + [(f"inline{i}", t) for i, t in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S))]


@pytest.mark.parametrize("name", sorted(str(p.relative_to(STATIC)) for p in STATIC.rglob("*.html")))
def test_scripts_on_one_page_do_not_redeclare_a_top_level_name(name):
    lexical, functions, clashes = {}, {}, []
    for where, text in page_scripts((STATIC / name).read_text(encoding="utf-8")):
        names, fns = declared(text)
        for n in names:
            if n in lexical and lexical[n] != where or n in functions:
                clashes.append(f"{n}: {lexical.get(n) or functions.get(n)} / {where}")
            lexical.setdefault(n, where)
        for n in fns:
            if n in lexical:
                clashes.append(f"{n}: {lexical[n]} / {where}")
            functions.setdefault(n, where)
    assert not clashes


def test_declared_reads_every_name_of_a_comma_list():
    assert declared("const A = f(1, 2), B = 'x,y', C = 3;\nlet d;\nclass E {}\nfunction g() {}\n  const inner = 1;") == (["A", "B", "C", "d", "E"], ["g"])
