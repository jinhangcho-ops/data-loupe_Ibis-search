"""옛 공유 계산(static/core/lib/*.js)은 모두 지웠다: 대시보드·차트 섬은 web/lib/calc.ts 한 벌을 쓰고, 쿼리·분석 화면은 lib를 부르지 않는다.
지운 파일이 다시 생기지 않고, web/lib/calc.ts가 옮겨 온 이름을 모두 export하는지 본다."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
LIB = STATIC / "core" / "lib"
NAMES = {  # 옛 lib 파일 → web/lib/calc.ts가 export하는 이름
    "dash_calc.js": ["shownum", "HEX", "hex6", "alpha", "MAX_ORDER", "labelIndex", "monday", "BUCKET", "rollup", "dayNumber", "shiftDay", "daysBetween", "zeroWhenEmpty",
                     "previousSpan", "ownRange", "anaView", "isPoints", "pointGroups", "outsideWindow"],
}
def test_old_lib_files_are_gone():
    for gone in ("dash_calc.js", "dash_grid.js", "dash_rules.js", "dash_svg.js"):
        assert not (LIB / gone).exists() and not list(STATIC.rglob(gone)), gone
    assert not (STATIC / "dashboard").exists()


def test_web_calc_has_the_same_names():
    """web/lib/calc.ts가 옮겨 온 이름을 모두 export한다(서버 dash_calc.own_range와 같은 규칙을 지킨다)."""
    ts = (STATIC.parent / "web" / "lib" / "calc.ts").read_text(encoding="utf-8")
    for n in NAMES["dash_calc.js"]:
        assert re.search(rf"^export (?:function {n}\b|const {n}\b|const .*, {n} = )", ts, re.M), n


def test_screens_load_lib_before_the_old_file():
    """쿼리·분석 화면은 lib/dash_calc.js도 dash.js도 부르지 않는다(차트는 /chart 섬, 카드 계산은 web/lib/calc.ts)."""
    a = '<script src="/static/core/lib/dash_calc.js"></script>'
    for page in ("query/index.html", "prep/prep.html"):
        assert a not in (STATIC / page).read_text(encoding="utf-8"), page
        assert "/static/core/dash.js" not in (STATIC / page).read_text(encoding="utf-8"), page  # dash.js는 지웠다(R4-D2b)
