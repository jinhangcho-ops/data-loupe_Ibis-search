"""공유 계산(static/core/lib/dash_calc.js): 쿼리·분석 화면(<script src>)이 dash.js보다 먼저 부르는 순수 계산. 보통 스크립트라 import/export가 없고, 화면(DOM)·SERVER·Chart에 기대지 않으며,
다른 화면 파일에 같은 이름이 두 벌 남지 않는다. 대시보드 화면(Next)은 같은 본문의 TS 사본(web/lib/calc.ts)을 쓴다. 대시보드 전용이던 dash_grid·dash_rules·dash_svg는 옛 화면과 함께 지웠다."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
LIB = STATIC / "core" / "lib"
MOVED = {
    "dash_calc.js": ["shownum", "HEX", "hex6", "alpha", "MAX_ORDER", "labelIndex", "monday", "BUCKET", "rollup", "dayNumber", "shiftDay", "daysBetween", "zeroWhenEmpty",
                     "previousSpan", "ownRange", "anaView", "isPoints", "pointGroups", "outsideWindow"],
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
        assert text.startswith("// 순수 계산만. DOM·SERVER·Chart에 기대지 않는다"), name
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
    for gone in ("dash_grid.js", "dash_rules.js", "dash_svg.js"):
        assert not (LIB / gone).exists() and not list(STATIC.rglob(gone)), gone
    assert not (STATIC / "dashboard").exists()


def test_web_calc_has_the_same_names():
    """대시보드 화면의 사본(web/lib/calc.ts)도 같은 이름을 export한다(서버 dash_calc.own_range와 같은 규칙을 두 화면이 함께 지킨다)."""
    ts = (STATIC.parent / "web" / "lib" / "calc.ts").read_text(encoding="utf-8")
    for n in MOVED["dash_calc.js"]:
        assert re.search(rf"^export (?:function {n}\b|const {n}\b|const .*, {n} = )", ts, re.M), n


def test_screens_load_lib_before_the_old_file():
    """옛 화면은 lib/dash_calc.js도 dash.js도 더는 부르지 않는다(차트는 /chart 섬, 카드 계산은 web/lib/calc.ts). 파일은 calc.ts의 원본으로만 남는다."""
    a = '<script src="/static/core/lib/dash_calc.js"></script>'
    for page in ("query/index.html", "prep/prep.html"):
        assert a not in (STATIC / page).read_text(encoding="utf-8"), page
        assert "/static/core/dash.js" not in (STATIC / page).read_text(encoding="utf-8"), page  # dash.js는 지웠다(R4-D2b)
