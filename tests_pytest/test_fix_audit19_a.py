"""감사 19A1: 엑셀 칸에 2026-10을 치면 날짜(2026-10-01)로 저장된다. 목표 파일에서 '2026-10'으로 찾혀야 한다."""
import datetime as dt

import xlsxwriter

from core import targets


def test_excel_date_cell_period(tmp_path):
    p = tmp_path / "targets.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    ws = wb.add_worksheet("목표")
    ws.write_row(0, 0, ["이름", "값", "기간"])
    ws.write(1, 0, "매출"); ws.write(1, 1, 1000); ws.write_datetime(1, 2, dt.datetime(2026, 10, 1), wb.add_format({"num_format": "yyyy-mm"}))
    ws.write(2, 0, "방문"); ws.write(2, 1, 7); ws.write_datetime(2, 2, dt.datetime(2026, 11, 1), wb.add_format({"num_format": "yyyy-mm"}))
    ws.write(3, 0, "가입"); ws.write(3, 1, 50); ws.write_number(3, 2, 2026)
    ws.write(4, 0, "주문"); ws.write(4, 1, 9); ws.write(4, 2, "2026-Q4")
    wb.close()
    targets.clear()
    rows = targets.read(p)["rows"]
    assert [r["period"] for r in rows] == ["2026-10", "2026-11", "2026", "2026-Q4"]
    assert targets.lookup(rows, "매출", "2026-10")["row"]["value"] == 1000
    assert targets.lookup(rows, "방문", "2026-11")["row"]["value"] == 7
    assert targets.lookup(rows, "가입", "2026")["row"]["value"] == 50


def test_csv_period_text_kept(tmp_path):
    p = tmp_path / "targets.csv"
    p.write_text("이름,값,기간\n매출,1000,2026-10\n방문,7,2026-10-01 00:00:00\n", encoding="utf-8")
    targets.clear()
    rows = targets.read(p)["rows"]
    assert [r["period"] for r in rows] == ["2026-10", "2026-10-01 00:00:00"]  # CSV는 사람이 쓴 글자 그대로
