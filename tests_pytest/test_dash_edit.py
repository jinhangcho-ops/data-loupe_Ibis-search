"""대시보드 편집 화면(5단계): 격자 배치 계산(세로 압축·이동·크기·칸 좌표)과 카운터 경고·끌어 놓기 규칙·보고서 자리·템플릿 모양을 node로,
1쪽 PDF(dash_pdf.py)의 구조(개체 위치 표·길이·JPEG 그대로)와 /api/dash/pdf, 내 템플릿 저장, 북극성·카운터 한계 검사, 화면 파일을 파이썬으로 본다. 값은 손으로 센다."""
import base64
import json
import re
import shutil
import subprocess

import pytest

from dashboard import dash_pdf
from core import dash_store
from core import server
from conftest import ROOT

STATIC = ROOT / "static"
url_of = lambda name: "/static/" + next(STATIC.rglob(name)).relative_to(STATIC).as_posix()  # 화면 파일의 주소


# ---------- 1쪽 PDF ----------

def fake_jpeg(width=300, height=200, parts=3, extra=b"\x00" * 40):
    """JPEG 표식만 갖춘 바이트(SOI, APP0, SOF0, 자료, EOI). dash_pdf는 그림을 풀지 않고 크기·성분 수만 읽는다."""
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + (8 + 3 * parts).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([parts]) + b"".join(bytes([i + 1, 0x11, 0]) for i in range(parts))
    return b"\xff\xd8" + app0 + sof + extra + b"\xff\xd9"


def test_jpeg_info_reads_size_and_parts():
    assert dash_pdf.jpeg_info(fake_jpeg(300, 200, 3)) == (300, 200, 3)
    assert dash_pdf.jpeg_info(fake_jpeg(5, 7, 1)) == (5, 7, 1)
    for bad, why in [(b"", "JPEG"), (b"\x89PNG\r\n", "JPEG"), (fake_jpeg(parts=4), "RGB"), (b"\xff\xd8\xff\xe0\x00\x02\xff\xd9", "크기"), (fake_jpeg(0, 10), "크기")]:
        with pytest.raises(ValueError, match=why):
            dash_pdf.jpeg_info(bad)


def test_pdf_structure_xref_and_stream():
    jpeg = fake_jpeg(300, 200)
    pdf = dash_pdf.make_pdf(jpeg)
    assert pdf.startswith(b"%PDF-1.4\n") and pdf.endswith(b"%%EOF\n")
    # xref 표: 개체마다 바이트 위치(10자리)가 "N 0 obj" 글자의 위치와 같다
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf)[1])
    assert pdf[start:start + 4] == b"xref"
    entries = re.findall(rb"(\d{10}) 00000 n \n", pdf[start:])
    assert len(entries) == 5 and pdf[start:].startswith(b"xref\n0 6\n0000000000 65535 f \n")
    for n, offset in enumerate(entries, 1):
        assert pdf[int(offset):].startswith(f"{n} 0 obj\n".encode()), n
    assert b"/Size 6 /Root 1 0 R" in pdf
    # 쪽 크기 = 그림 픽셀 × 0.75 = 225 × 150, 그림은 쪽 가득
    assert b"/MediaBox [0 0 225 150]" in pdf and b"q 225 0 0 150 0 0 cm /Im0 Do Q" in pdf
    # 그림 개체: JPEG를 그대로(DCTDecode), 길이가 맞고 RGB 8비트
    head = re.search(rb"<< /Type /XObject /Subtype /Image /Width 300 /Height 200 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length (\d+) >>\nstream\n", pdf)
    assert head and int(head[1]) == len(jpeg)
    body = pdf[head.end():head.end() + len(jpeg)]
    assert body == jpeg and pdf[head.end() + len(jpeg):].startswith(b"\nendstream\nendobj\n")
    # 내용 스트림 길이
    content = re.search(rb"4 0 obj\n<< /Length (\d+) >>\nstream\n", pdf)
    assert pdf[content.end():content.end() + int(content[1])] == b"q 225 0 0 150 0 0 cm /Im0 Do Q"
    assert b"/DeviceGray" in dash_pdf.make_pdf(fake_jpeg(10, 10, 1))


def test_pdf_limits_and_decode():
    with pytest.raises(ValueError, match="너무 커서"):
        dash_pdf.make_pdf(fake_jpeg(20000, 100))
    url = dash_pdf.DATA_URL + base64.b64encode(fake_jpeg()).decode()
    assert dash_pdf.decode_image(url) == fake_jpeg()
    for bad, why in [("data:image/png;base64,AAAA", "JPEG"), (None, "JPEG"), (dash_pdf.DATA_URL + "***", "올바르지")]:
        with pytest.raises(ValueError, match=why):
            dash_pdf.decode_image(bad)


def test_pdf_route_saves_or_downloads(client, tmp_path, monkeypatch):
    image = dash_pdf.DATA_URL + base64.b64encode(fake_jpeg()).decode()
    monkeypatch.setattr(server, "ask_save", None)  # 창 없이: PDF 자체를 돌려줘 화면이 내려받게 한다
    got = client.post("/api/dash/pdf", json={"image": image, "name": "주간/보고:1"}).get_json()
    assert got["window"] is False and got["name"] == "주간_보고_1.pdf" and base64.b64decode(got["pdf"].split(",")[1]).startswith(b"%PDF-1.4")
    target = tmp_path / "out"  # 확장자가 없으면 .pdf를 붙이고, 임시 파일은 남지 않는다
    seen = []
    monkeypatch.setattr(server, "ask_save", lambda name, types: seen.append((name, types)) or str(target))
    saved = client.post("/api/dash/pdf", json={"image": image, "name": "대시보드"}).get_json()
    assert saved["path"].endswith("out.pdf") and (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF") and not list(tmp_path.glob("*.part"))
    assert seen == [("대시보드.pdf", ("PDF (*.pdf)",))]
    monkeypatch.setattr(server, "ask_save", lambda name, types: None)
    assert client.post("/api/dash/pdf", json={"image": image}).get_json() == {"cancelled": True}
    bad = client.post("/api/dash/pdf", json={"image": "data:image/png;base64,AAAA"})
    assert bad.status_code == 400 and "JPEG" in bad.get_json()["error"]
    assert "/api/dash/pdf" in server.FREE and "/api/dash/templates" in server.FREE


# ---------- 내 템플릿과 북극성·카운터 ----------

def card(kind="kpi", title="핵심", **more):
    return {"kind": kind, "title": title, "layout": {"x": 0, "y": 0, "w": 4, "h": 4}, **more}


def test_my_templates_save_load_delete(folder):
    assert dash_store.load_templates() == []
    saved = dash_store.save_template({"name": "내 개요", "cards": [card(colors={"series": {"a": "#ff0000"}}, source={"table": "비밀"}, data={"values": 1}, view={"type": "line"}), card("chart", "추이")]})
    assert [t["name"] for t in saved] == ["내 개요"] and set(saved[0]["cards"][0]) == {"kind", "title", "layout", "colors"}  # source·data·view는 저장하지 않는다
    assert saved[0]["cards"][0]["colors"]["series"] == {"a": "#ff0000"}
    text = (server.settings.PATH.parent / dash_store.TEMPLATES).read_text(encoding="utf-8")
    assert "비밀" not in text and '"values"' not in text
    again = dash_store.save_template({"name": "내 개요", "cards": [card()]})  # 같은 이름은 바꾼다
    assert len(again) == 1 and len(again[0]["cards"]) == 1
    assert dash_store.delete_template(again[0]["id"]) == [] and dash_store.load_templates() == []
    for bad, why in [({"name": "", "cards": [card()]}, "이름"), ({"name": "x", "cards": []}, "카드가"), ({"name": "x", "cards": [card("nope")]}, "종류"),
                     ({"name": "x", "cards": [card(layout={"x": 0, "y": 0, "w": 1, "h": 4})]}, "12칸"), ({"name": "x", "cards": [card(colors={"series": {"a": "빨강"}})]}, "HEX")]:
        with pytest.raises(ValueError, match=why):
            dash_store.save_template(bad)


def test_template_routes(client, folder):
    got = client.post("/api/dash/templates", json={"name": "모니터링", "cards": [card("status", "상태")]}).get_json()["templates"]
    assert got[0]["name"] == "모니터링" and client.get("/api/dash/templates").get_json()["templates"] == got
    assert client.post("/api/dash/templates/delete", json={"id": got[0]["id"]}).get_json()["templates"] == []
    assert client.post("/api/dash/templates", json={"name": "x", "cards": [card("nope")]}).status_code == 400


def test_north_star_counter_limits(folder):
    cards = [{**card(), "id": "a1"}, {**card(title="환불률"), "id": "b2"}]
    base = {"name": "대시보드", "cards": cards}
    ok = dash_store.check_dashboard({**base, "north_star": {"card": "a1", "counters": ["b2"], "limits": {"b2": {"op": "<=", "value": 0.05}}}})
    assert ok["north_star"] == {"card": "a1", "counters": ["b2"], "limits": {"b2": {"op": "<=", "value": 0.05}}}
    assert dash_store.check_dashboard({**base, "north_star": {"card": "a1", "counters": ["b2"]}})["north_star"] == {"card": "a1", "counters": ["b2"]}  # 한계가 없으면 항목도 없다
    for limits, why in [({"a1": {"op": ">=", "value": 1}}, "한계"), ({"b2": {"op": "=", "value": 1}}, "연산자"), ({"b2": {"op": ">=", "value": "많이"}}, "숫자"), ({"b2": {"op": ">=", "value": True}}, "숫자"), ("x", "한계")]:
        with pytest.raises(ValueError, match=why):
            dash_store.check_dashboard({**base, "north_star": {"card": "a1", "counters": ["b2"], "limits": limits}})


def test_screens_have_edit_parts(client):
    html = client.get("/dashboard").get_data(as_text=True)
    for part in ('id="d-gallery"', 'id="d-gallery-box"', 'id="d-panel"', 'id="d-alerts"', 'id="d-template"', 'id="d-tpl-save"', 'id="d-report-png"', 'id="d-report-pdf"', "/static/core/dash_edit.css"):
        assert part in html, part
    for script in ("dash_grid.js", "dash_edit.js"):
        assert f'src="{url_of(script)}"></script>' in html and next(STATIC.rglob(script)).exists()
    for script in ("dash_templates.js", "dash_menu.js", "dash_shelf.js", "dash_panel.js", "dash_report.js"):  # 편집 도구·보고서는 처음 쓸 때 불러온다(need)
        assert f'"{url_of(script)}"' in html and f'src="{url_of(script)}"></script>' not in html and next(STATIC.rglob(script)).exists()
    assert "new EditBoard(" in html and "toTemplate(" in html and "counterAlerts(" in html
    query = client.get("/search").get_data(as_text=True)
    assert 'id="shelf-host"' in query and 'src="/static/core/dash_shelf.js"></script>' in query and "shelfPanel(" in query
    assert "saveReportPdf" in (STATIC / "dashboard" / "dash_report.js").read_text(encoding="utf-8") and "XMLSerializer" in (STATIC / "dashboard" / "dash_report.js").read_text(encoding="utf-8")


# ---------- node: 격자 배치·카운터 경고·끌어 놓기 규칙·보고서 자리 ----------

SCRIPT = """
const assert = require("assert");
globalThis.SERVER = { dash: JSON.parse(%(dash)s) };
const L = (x, y, w, h) => ({ x, y, w, h });
const item = (id, x, y, w, h) => ({ id, layout: L(x, y, w, h) });
// ---- 범위 맞추기: w·h는 2~12, x는 0 ~ 12-w
assert.deepStrictEqual(clampLayout(L(11, -3, 1, 40)), L(10, 0, 2, 12));
assert.deepStrictEqual(clampLayout(L(9, 2, 6, 4)), L(6, 2, 6, 4)); assert.deepStrictEqual(clampLayout({}), L(0, 0, 6, 6));
assert(overlaps(L(0, 0, 4, 4), L(3, 3, 4, 4)) && !overlaps(L(0, 0, 4, 4), L(4, 0, 4, 4)) && !overlaps(L(0, 0, 4, 4), L(0, 4, 4, 4)));  // 맞닿기만 하면 겹침이 아니다
// ---- 세로 압축: 움직인 카드를 먼저 놓고, 나머지는 (y, x) 순으로 겹치지 않는 가장 작은 y에
const base = [item("A", 0, 0, 6, 4), item("B", 6, 0, 6, 4), item("C", 0, 4, 12, 4)];
assert.deepStrictEqual(compactLayout(base), { A: L(0, 0, 6, 4), B: L(6, 0, 6, 4), C: L(0, 4, 12, 4) });  // 이미 붙어 있으면 그대로
// A를 오른쪽(6,0)으로: B는 A 아래(6,4), C는 둘 아래(0,8)
assert.deepStrictEqual(moveCard(base, "A", { x: 6, y: 0 }), { A: L(6, 0, 6, 4), B: L(6, 4, 6, 4), C: L(0, 8, 12, 4) });
// 빈틈은 위로 붙는다: C만 아래로 떠 있으면(y=20) A·B 바로 아래 4로
assert.deepStrictEqual(compactLayout([item("A", 0, 0, 6, 4), item("B", 6, 0, 6, 4), item("C", 0, 20, 12, 4)]), { A: L(0, 0, 6, 4), B: L(6, 0, 6, 4), C: L(0, 4, 12, 4) });
// 위로 끌어 올린 카드가 먼저 자리를 차지하고 겹친 카드는 밀려난다: C를 (0,0)으로 → A·B는 12칸 C 아래(y=4)
assert.deepStrictEqual(moveCard(base, "C", { x: 0, y: 0 }), { C: L(0, 0, 12, 4), A: L(0, 4, 6, 4), B: L(6, 4, 6, 4) });
// 범위 밖으로 끌면 맞춘다(x 100 → 12-w, y -5 → 0)
assert.deepStrictEqual(moveCard(base, "B", { x: 100, y: -5 }).B, L(6, 0, 6, 4));
assert.deepStrictEqual(moveCard([{ id: "A", layout: L(0, 0, 6, 8) }, { id: "B", layout: L(6, 0, 6, 8) }], "B", { x: 6, y: 30 }).B, L(6, 0, 6, 8));  // 빈 아래쪽에 놓아도 위로 붙는다(34차 감사)
// 서로 다른 높이에서 틈 채우기: 왼쪽 세로로 긴 카드 옆에 두 카드가 쌓인다
const tall = [item("T", 0, 0, 4, 10), item("U", 4, 0, 4, 4), item("V", 4, 6, 4, 3), item("W", 8, 9, 4, 2)];
assert.deepStrictEqual(compactLayout(tall), { T: L(0, 0, 4, 10), U: L(4, 0, 4, 4), V: L(4, 4, 4, 3), W: L(8, 0, 4, 2) });
// ---- 크기 바꾸기: 아래 카드가 밀려난다, 오른쪽 끝을 넘으면 x를 당긴다, 2~12로 맞춘다
assert.deepStrictEqual(resizeCard(base, "A", 6, 8), { A: L(0, 0, 6, 8), B: L(6, 0, 6, 4), C: L(0, 8, 12, 4) });
assert.deepStrictEqual(resizeCard(base, "B", 12, 4).B, L(0, 0, 12, 4)); assert.deepStrictEqual(resizeCard(base, "A", 1, 99).A, L(0, 0, 2, 12));
// ---- 포인터 → 칸: 칸 하나 + 간격 = 110(가로), 50(세로). 카드 안에서 잡은 자리는 뺀다
const m = { left: 100, top: 200, col: 110, row: 50 };
assert.deepStrictEqual(cellAt(100, 200, m), { x: 0, y: 0 }); assert.deepStrictEqual(cellAt(100 + 330, 200 + 150, m), { x: 3, y: 3 });
assert.deepStrictEqual(cellAt(100 + 160, 200 + 70, m), { x: 1, y: 1 }); assert.deepStrictEqual(cellAt(100 + 140, 200 + 80, m), { x: 1, y: 2 });  // 반올림
assert.deepStrictEqual(cellAt(100 + 330 + 60, 200 + 100 + 20, m, 60, 20), { x: 3, y: 2 });  // 잡은 자리(60, 20)를 뺀 카드 왼쪽 위 기준
// 손잡이: 가로 (dx + 간격)/110, 세로 (dy + 간격)/50 반올림, 2~12
assert.deepStrictEqual(sizeAt(450, 200, { col: 110, row: 50, gap: 10 }), { w: 4, h: 4 }); assert.deepStrictEqual(sizeAt(5, 5, { col: 110, row: 50, gap: 10 }), { w: 2, h: 2 });
assert.deepStrictEqual(sizeAt(9999, 9999, { col: 110, row: 50, gap: 10 }), { w: 12, h: 12 });
assert.deepStrictEqual(nextLayout([item("A", 0, 0, 6, 4), item("C", 0, 4, 12, 6)]), L(0, 10, 6, 8)); assert.deepStrictEqual(nextLayout([]), L(0, 0, 6, 8));
// ---- 보고서 자리: 칸 100px, 간격 10, 여백 24, 위 제목 줄 56
const lay = reportLayout([{ id: "A", layout: L(0, 0, 6, 4) }, { id: "B", layout: L(6, 4, 6, 5) }]);
assert.deepStrictEqual(lay.rects.A, { x: 24, y: 56, w: 6 * 100 + 5 * 10, h: 4 * 40 + 3 * 10 }); assert.deepStrictEqual(lay.rects.B, { x: 24 + 6 * 110, y: 56 + 4 * 50, w: 650, h: 5 * 40 + 4 * 10 });
assert.strictEqual(lay.width, 24 * 2 + 12 * 100 + 11 * 10); assert.strictEqual(lay.height, 56 + 9 * 50 - 10 + 24);
assert.strictEqual(reportLayout([]).height, 56 + 40 + 24);
// ---- 카운터 경고: 한계(>= 이상 유지, <= 이하 유지)를 벗어난 카운터만. 값은 scalar로 둔 간단한 카드
const kpi = (id, title, v) => ({ id, kind: "gauge", title, view: { format: "number" }, data: { values: { scalar: [v] } }, source: {} });
const cards = [kpi("n", "매출", 100), kpi("r", "환불률", 0.08), kpi("s", "만족도", 4.2), kpi("z", "지연", null), kpi("q", "한계 없음", 1)];
const star = { card: "n", counters: ["r", "s", "z", "q", "none"], limits: { r: { op: "<=", value: 0.05 }, s: { op: ">=", value: 4.0 }, z: { op: ">=", value: 1 } } };
const bad = counterAlerts(cards, star);
assert.deepStrictEqual(bad.map(a => a.id), ["r"]); assert(bad[0].text.includes("환불률") && bad[0].text.includes("벗어났어요") && bad[0].text.includes("이하"));
assert.deepStrictEqual(counterAlerts(cards, { ...star, limits: { ...star.limits, s: { op: ">=", value: 4.5 } } }).map(a => a.id), ["r", "s"]);
assert.deepStrictEqual(counterAlerts(cards, null), []); assert.deepStrictEqual(counterAlerts(cards, { card: "", counters: [], limits: {} }), []);
assert.deepStrictEqual(counterAlerts(cards, { ...star, limits: { r: { op: "<=", value: 0.08 } } }), []);  // 한계와 같으면 벗어난 것이 아니다
// ---- 지운 카드는 북극성·카운터·한계에서 빠진다(남으면 서버가 저장을 거부한다)
assert.deepStrictEqual(liveStar(star, cards.filter(c => !["n", "r"].includes(c.id))), { card: "", counters: ["s", "z", "q"], limits: { s: star.limits.s, z: star.limits.z } });
assert.deepStrictEqual(liveStar(null, cards), { card: "", counters: [], limits: {} });
// ---- 템플릿: 빈 틀 카드와 내 템플릿 모양
let n = 0; const made = templateCards(DASH_TEMPLATES[0], () => "id" + n++);
assert.strictEqual(made.length, 7); assert(made.every(c => Object.keys(c.source).length === 0 && isShell(c)) && made[3].view.type === "line" && made[3].kind === "chart");
assert.deepStrictEqual(DASH_TEMPLATES.map(t => t.id), ["overview", "goal", "monitor", "blank"]); assert.strictEqual(DASH_TEMPLATES[3].cards.length, 0);
for (const t of DASH_TEMPLATES) { const placed = compactLayout(t.cards.map((c, i) => ({ id: i, layout: c.layout }))); t.cards.forEach((c, i) => assert.deepStrictEqual(placed[i], c.layout, t.id + " 템플릿은 겹치지 않고 위로 붙어 있어야 한다")); }
assert.deepStrictEqual(Object.keys(toTemplate("내", [{ ...made[0], data: { a: 1 }, source: { table: "비밀" } }]).cards[0]), ["kind", "title", "layout", "colors"]);
// ---- 끌어 놓기 규칙(이유 문장)
const st = { x: "", series: [], split: "", conditions: [] };
assert(dropCheck("y", "store", "문자", st).reason.includes("합계·평균을 낼 수 없어요")); assert(dropCheck("y", "day", "날짜", st).reason.includes("날짜 컬럼"));
assert(dropCheck("y", "amt", "실수", st).ok && dropCheck("y", SHELF_ROWS, "", st).ok && !dropCheck("x", SHELF_ROWS, "", st).ok);
assert(dropCheck("split", "amt", "정수", st).reason.includes("숫자 컬럼") && dropCheck("split", "day", "날짜시간", st).reason.includes("날짜 컬럼") && dropCheck("split", "store", "문자", st).ok);
assert(dropCheck("split", "store", "문자", { ...st, x: "store" }).reason.includes("X축과 같은")); assert(dropCheck("x", "store", "문자", { ...st, split: "store" }).reason.includes("색(나누기)과 같은"));
assert(dropCheck("y", "amt", "실수", { ...st, series: [{ y: "amt", agg: "sum" }] }).reason.includes("이미 값에")); assert(dropCheck("y", "z", "실수", { ...st, series: Array(5).fill({ y: "q", agg: "sum" }) }).reason.includes("5개"));
assert(dropCheck("nope", "a", "실수", st).reason.includes("놓을 수 없는 칸")); assert(dropCheck("filter", "store", "문자", st).ok);
let next = applyDrop("x", "day", "날짜", st); next = applyDrop("y", "amt", "실수", next); next = applyDrop("y", SHELF_ROWS, "", next); next = applyDrop("split", "store", "문자", next); next = applyDrop("filter", "store", "문자", next, "강남", "=");
assert.deepStrictEqual(next, { x: "day", series: [{ y: "amt", agg: "sum" }, { y: null, agg: "count" }], split: "store", conditions: ["store = 강남"] });
assert.deepStrictEqual(st, { x: "", series: [], split: "", conditions: [] });  // 원래 상태는 바뀌지 않는다
assert.deepStrictEqual(setAgg(next.series, 0, "mean").map(s => s.agg), ["mean", "count"]); assert.deepStrictEqual(setAgg(next.series, 1, "sum"), next.series);  // (행 개수)는 바꾸지 않는다
assert.deepStrictEqual(shelfChips(next), { x: ["day"], y: ["sum (amt)", "행 개수"], split: ["store"], filter: ["store = 강남"] });
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_grid_alerts_shelf_in_node(tmp_path):
    dash = json.dumps({"types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS, "max_palette": dash_store.MAX_PALETTE}, ensure_ascii=False)
    script = tmp_path / "edit_test.js"
    parts = ["board.js", "dash.js", "dash_cards.js", "dash_rules.js", "dash_grid.js", "dash_edit.js", "dash_shelf.js", "dash_templates.js", "dash_report.js"]
    script.write_text("".join(next(STATIC.rglob(name)).read_text(encoding="utf-8") for name in parts) + SCRIPT.replace("%(dash)s", json.dumps(dash, ensure_ascii=False)), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]
