"""공유 계산(static/core/lib/): 옛 화면(<script src>)과 새 web/ 앱이 함께 쓰는 순수 계산 파일. 보통 스크립트라 import/export가 없고, 화면(DOM)·SERVER·Chart에 기대지 않으며, 옛 파일에 같은 이름이 두 벌 남지 않는다."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
LIB = STATIC / "core" / "lib"
MOVED = {
    "dash_calc.js": ["shownum", "HEX", "hex6", "alpha", "MAX_ORDER", "labelIndex", "monday", "BUCKET", "rollup", "dayNumber", "shiftDay", "daysBetween", "zeroWhenEmpty",
                     "previousSpan", "ownRange", "anaView", "isPoints", "pointGroups", "outsideWindow"],
    "dash_grid.js": ["GRID_COLS", "toInt", "clampInt", "clampLayout", "overlaps", "compactLayout", "cellAt", "moveCard", "resizeCard", "sizeAt", "nextLayout"],
    "dash_rules.js": ["ruleNumber", "sameNum", "OPERATORS", "parseMatch", "compileRule", "compileRules", "matchRule", "ruleArrow", "kindOfValues", "ARROW_WORDS", "SCOPE_WORDS",
                      "parseRuleLine", "LEVEL_WORDS", "parseStateLine", "parseLinkLine", "readLines", "templateParts", "ARROW_NAMES", "ruleLine"],
    "dash_svg.js": ["TAU", "clamp", "slack", "overTarget", "underTarget", "polar", "arcPath", "ringDash", "gaugeSegments", "stripeCells", "blockCells", "crossDown", "belowPolygons",
                    "sparkPoints", "smoothPath", "showValue", "roundText", "showGap", "darken", "deviationCells", "autoGrid"],
}
DOM = re.compile(r"(?<![.\w$])(?:document|window|SERVER|Chart|getComputedStyle)\b|\$\(")  # 전역으로 쓰는 것만(card.data.window 같은 속성은 괜찮다)


def defines(text, name):
    """맨 앞 줄(최상위)에서 name을 정의하는지: function name(, const name =, const a = …, name = …"""
    return re.search(rf"^(?:async )?function {name}\(|^(?:const|let|var) {name} =|^const .*, {name} = ", text, re.M) is not None


def code(text):
    return re.sub(r"(^|\s)//.*", r"\1", text)  # 주석(첫 줄 설명 포함)은 뺀다


def test_lib_files_are_plain_pure_scripts():
    assert sorted(p.name for p in LIB.glob("*.js")) == sorted(MOVED)
    for name in MOVED:
        text = (LIB / name).read_text(encoding="utf-8")
        assert text.startswith("// 순수 계산만. DOM·SERVER·Chart에 기대지 않는다(web/도 가져다 쓴다)."), name
        assert not re.search(r"^\s*(import|export)\b", text, re.M), name  # 옛 화면이 <script src>로 그대로 쓴다
        assert not DOM.search(code(text)), (name, DOM.search(code(text)))


def test_moved_names_live_only_in_lib():
    old = [p for p in STATIC.rglob("*") if p.suffix in (".js", ".html") and LIB not in p.parents and "chart.umd" not in p.name]
    texts = {p: p.read_text(encoding="utf-8") for p in old}
    for name, names in MOVED.items():
        lib = (LIB / name).read_text(encoding="utf-8")
        for n in names:
            assert defines(lib, n), (name, n)
            assert not [p for p, t in texts.items() if defines(t, n)], (n, [str(p) for p, t in texts.items() if defines(t, n)])  # 두 벌이면 전역 이름이 겹쳐 파일 전체가 SyntaxError
    assert not (STATIC / "dashboard" / "dash_grid.js").exists()


def test_screens_load_lib_before_the_old_file():
    for page, pairs in {"dashboard/dashboard.html": [("lib/dash_calc.js", "core/dash.js"), ("lib/dash_svg.js", "dashboard/dash_cards.js"), ("lib/dash_rules.js", "dashboard/dash_rules.js"), ("lib/dash_grid.js", "dashboard/dash_edit.js")],
                        "query/index.html": [("lib/dash_calc.js", "core/dash.js")], "prep/prep.html": [("lib/dash_calc.js", "core/dash.js")]}.items():
        html = (STATIC / page).read_text(encoding="utf-8")
        for lib, old in pairs:
            a, b = f'<script src="/static/core/{lib}"></script>', f'<script src="/static/{old}"></script>'
            assert a in html and b in html and html.index(a) < html.index(b), (page, lib)
