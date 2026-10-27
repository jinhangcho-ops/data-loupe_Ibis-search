"""35차 감사: 산점도 점 요청도 대시보드 기간을 건다, 전처리 숫자 칸의 공백·16진수."""
import shutil

import pytest

from tests_pytest.test_dash_calc import db  # noqa: F401  db는 fixture

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_points_route_applies_dashboard_period(db):
    body = {"base": "orders", "x": "amt", "y": "amt", "date": "day", "ctx": {"range": {"start": "2026-01-05", "end": "2026-01-06"}}}
    assert db.post("/api/chart/points", json=body).get_json()["points"]["total"] == 4  # 1-05·1-06의 네 행(전체는 6)


@node
def test_prep_number_box_rejects_blank_and_hex(tmp_path):
    from test_fix_audit_js import run_node
    from conftest import ROOT
    line = next(l for l in (ROOT / "static" / "prep" / "prep.html").read_text(encoding="utf-8").splitlines() if l.startswith("const toNum"))
    got = run_node(tmp_path, "console.log(JSON.stringify([' ', '', '0x10', '-3.5', '1e3', ' 7 '].map(v => { const n = toNum(v); return Number.isNaN(n) ? 'NaN' : n; })));", parts=[line])
    assert got == [None, None, "NaN", -3.5, 1000, 7]
