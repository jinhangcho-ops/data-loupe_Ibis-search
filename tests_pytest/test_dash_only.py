"""KPI 카드의 추이 곡선: 가운데 점을 조절점으로 한 부드러운 곡선(web/lib/calc.ts smoothPath)이 카드의 남는 높이를 채운다.
(옛 화면의 [대시보드만 보기] 글자 확인 시험은 옛 화면과 함께 지웠다.)"""
import json
import shutil

import pytest

from test_dash_js import run_ts
from test_fix_audit_js import read


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_kpi_spark_is_a_smooth_curve_that_fills_the_card(tmp_path):
    got = json.loads(run_ts(tmp_path, 'console.log(JSON.stringify([smoothPath("0,10 5,0 10,10"), smoothPath("0,1 9,2")]));'))
    assert got == ["M0,10 Q5,0 7.5,5.0 L10,10", "M0,1 L9,2"]  # 가운데 점은 곡선의 조절점, 두 점이면 직선
    assert "flex:1 1 34px" in read("common.css")
