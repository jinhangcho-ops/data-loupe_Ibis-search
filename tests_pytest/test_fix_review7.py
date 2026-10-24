"""7차 리뷰에서 찾은 것. 화면마다 함께 올라가는 스크립트의 최상위 이름이 겹치면 뒤에 올라간 파일 전체가 SyntaxError로 멈춘다
(대시보드에서 common.js의 GRID_MIN과 dash_grid.js의 GRID_MIN이 겹쳐 clampLayout 등이 없어졌다)."""
import re

import pytest

from conftest import ROOT

STATIC = ROOT / "static"


def pattern_names(text):
    """풀어 쓴 선언 `[a, b = 1, ...c]`·`{a, b: c, d: [e]}`의 새 이름들(키 `b:`와 기본값 `= …`은 이름이 아니다). 맨 앞 괄호가 닫힐 때까지만 본다."""
    depth, end = 0, len(text)
    for i, ch in enumerate(text):
        depth += ch in "[{(" or -(ch in "]})")
        if depth == 0:
            end = i + 1
            break
    body = re.sub(r"=\s*[^,\]}]*", "", text[:end])  # 기본값
    body = re.sub(r"[\w$]+\s*:", "", body)  # 객체 키(별명은 ':' 뒤에 남는다)
    return re.findall(r"[A-Za-z_$][\w$]*", body)


def declared(text):
    """줄 맨 앞의 const·let·var·class 이름(쉼표로 여럿 선언한 것은 여러 줄에 걸쳐도)과 function 이름."""
    lexical, functions = [], re.findall(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", text, re.M)
    for kind, at in ((m.group(1), m.end()) for m in re.finditer(r"^(const|let|var|class)\s+", text, re.M)):
        if kind == "class":
            lexical.append(re.match(r"[\w$]+", text[at:]).group(0))
            continue
        depth, quote, cur, parts, i = 0, None, "", [], at
        while i < len(text):  # 괄호·따옴표 밖의 쉼표로 나눈다(const a = f(1, 2), b = 3;). 줄이 쉼표·연산자로 끝나면 다음 줄로 이어진다
            ch = text[i]
            i += 1
            if quote:
                if ch == "\\":
                    cur += ch + text[i:i + 1]
                    i += 1
                    continue
                quote = None if ch == quote else quote
            elif ch in "\"'`":
                quote = ch
            elif ch == "/" and text[i:i + 1] == "/":  # 줄 끝 주석
                i = text.find(chr(10), i) % (len(text) + 1)  # 줄 끝까지(없으면 글 끝)
                continue
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            elif ch == chr(10) and depth == 0 and not re.search(r"[,=+\-*/&|?:(]\s*$", cur) and not re.match(r"\s*[.?:+\-*/&|,]", text[i:]):
                break
            elif ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
                continue
            cur += ch
        parts.append(cur)
        for part in parts:
            if part.lstrip()[:1] in ("[", "{"):  # const [A, B] = …, const {a, b: c = 1} = …: 풀어 쓴 이름들
                lexical += pattern_names(part.lstrip())
            elif re.fullmatch(r"[A-Za-z_$][\w$]*", part.split("=")[0].strip()):
                lexical.append(part.split("=")[0].strip())
    return lexical, functions


LAZY = re.compile(r"""["'`](/static/[^"'`\s]+?\.js)["'`]""")


def page_scripts(html):
    """화면이 부르는 스크립트(정적 src, <!--include-->한 조각 안의 것, 화면·스크립트 글 안에 이름이 적힌 /static/….js 전부 = need(상수)로 늦게 부르는 것까지, 인라인)를 (이름, 글) 목록으로."""
    html = re.sub(r"<!--include ([^>]+?)-->", lambda m: (STATIC / m[1].strip()).read_text(encoding="utf-8"), html)
    inline = [(f"inline{i}", t) for i, t in enumerate(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S))]
    found, todo = {}, re.findall(r"<script\b[^>]*\bsrc=\"(/static/[^\"]+\.js)\"", html) + LAZY.findall(html)
    for _, t in inline:
        todo += LAZY.findall(t)
    while todo:  # 불러온 스크립트가 또 늦게 부르는 파일까지
        s = todo.pop(0)
        if s not in found and (ROOT / s.lstrip("/")).exists():
            found[s] = (ROOT / s.lstrip("/")).read_text(encoding="utf-8")
            todo += LAZY.findall(found[s])
    return list(found.items()) + inline


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


def test_declared_reads_destructured_names():
    """9J4: 풀어 쓴 선언의 이름도 겹침 검사에 든다(별명은 ':' 뒤, 키·기본값은 이름이 아니다)."""
    got = declared("const [AFTER_LOW, AFTER_HIGH] = f(), z = 1;\nconst {a, b: c = 2, d: [e, ...rest]} = obj;\nlet {x} = y;")[0]
    assert got == ["AFTER_LOW", "AFTER_HIGH", "z", "a", "c", "e", "rest", "x"]
