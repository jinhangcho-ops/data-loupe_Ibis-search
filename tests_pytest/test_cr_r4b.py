"""F32: 분석 탭의 화면 쪽 범위 자르기 함수(anaSpan·anaSlice)를 node로 돌려 본다."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = "".join((Path(__file__).resolve().parent.parent / "static/prep" / name).read_text(encoding="utf-8") for name in ("prep.html", "analysis.js"))  # 분석 부분은 analysis.js에 있다
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def fn(name):
    m = re.search(rf"^function {name}\(.*?^}}", HTML, re.S | re.M)
    assert m, name
    return m.group(0)


def run(tmp_path, body):
    js = tmp_path / "t.js"
    js.write_text("\n".join(fn(n) for n in ("anaSpan", "anaSlice")) + f"\nconsole.log(JSON.stringify({body}));", encoding="utf-8")
    return json.loads(subprocess.run(["node", str(js)], capture_output=True, text=True, check=True, encoding="utf-8").stdout)


TREND = "{labels:['a','b','c','d'],values:[1,2,3,4],counts:[5,6,7,8]}"
VALS = "{values:{labels:['x','y','z'],counts:[3,2,1],nulls:4,other:5}}"


def test_slice_trend_and_hist(tmp_path):
    got = run(tmp_path, f"(()=>{{const d={TREND};const s=anaSlice(d,'trend',1,3);return [anaSpan(d,'trend'),s.labels,s.values,s.counts,d.labels.length]}})()")
    assert got == [4, ["b", "c"], [2, 3], [6, 7], 4]  # 원본은 그대로
    h = run(tmp_path, "anaSlice({histogram:{edges:[0,1,2,3],counts:[5,6,7]}},'hist',1,3).histogram")
    assert h == {"edges": [1, 2, 3], "counts": [6, 7]}


def test_slice_values_keeps_tail_only_at_end(tmp_path):
    mid = run(tmp_path, f"anaSlice({VALS},'values',0,2).values")
    assert (mid["labels"], mid["nulls"], mid["other"]) == (["x", "y"], 0, 0)
    end = run(tmp_path, f"anaSlice({VALS},'values',1,3).values")
    assert (end["labels"], end["nulls"], end["other"]) == (["y", "z"], 4, 5)


def test_values_slice_keeps_percent_base(tmp_path):
    # 범위로 자른 값 빈도 표도 비율은 전체(3+2+1+4+5=15) 기준
    assert run(tmp_path, f"anaSlice({VALS},'values',0,2).values.total") == 15


def test_other_views_and_html_wiring(tmp_path):
    assert run(tmp_path, "[anaSpan({},'heat'),anaSlice({a:1},'heat',0,1)]") == [0, {"a": 1}]
    for part in ('id="a-lo"', 'id="a-hi"', 'id="a-rows"', 'id="a-gh"', 'id="a-th"', "aria-valuetext"):
        assert part in HTML
