"""R5 Q1 쿼리 화면(static/query/index.html): 공용 스크립트(달력·전처리 메뉴·행 고르기·새 컬럼) 연결, 휠 연속 넘김, 쪽 칸 너비, 컬럼 빼기, 기간별 표, 지표 기간·비교,
테이블 바꿀 때 release, 보일 컬럼 고르기, 내보내기 단추 펼침, 탭 이름(조인), 조건 우변 컬럼. 글자 확인 + 헤드리스 Edge(없거나 websocket-client가 없으면 건너뜀)로
DuckDB 파일(tmp_path)에 진짜 연결해 화면을 눌러 본다. 다운로드는 막고, 전처리로 보내기는 fetch를 가로채 본문만 본다."""
import json
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import ROOT, kill_edge, spawn_edge

HTML = (ROOT / "static" / "query" / "index.html").read_text(encoding="utf-8")
EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)


def test_shared_scripts_load_in_order():
    order = ["common.js", "funcs.js", "prepmenu.js", "rowpick.js", "calendar.js", "newcol.js"]
    at = [HTML.index(f'<script src="/static/core/{name}"></script>') for name in order]
    assert at == sorted(at)
    assert "PrepMenu.load()" in HTML


def test_elements_exist():
    for id_ in ("per-cal", "per-col", "col-pick", "col-pick-add", "rows-tools", "newcol-open", "pt-add", "pt-run", "pt-unit", "pt-show", "pt-out", "pt-chart", "pt-csv",
                "met-period", "met-cal", "met-cmp", "met-pcol", "bmode", "bcol2"):
        assert f'id="{id_}"' in HTML, id_
    assert re.findall(r'<option value="(\w*)"', HTML[HTML.index('id="met-cmp"'):HTML.index("</select>", HTML.index('id="met-cmp"'))]) == ["", "prev", "same_weekday", "yoy", "mom", "wow"]


def test_datalist_and_export_popup_gone():
    assert "<datalist" not in HTML and 'list="col-list"' not in HTML
    assert 'id="export-pop"' not in HTML and 'id="export-open"' not in HTML
    assert len(re.findall(r'<button class="btn" data-export="(?:csv|xlsx|parquet|tsv|fixed)"', HTML)) == 5


def test_contract_calls_in_code():
    assert '"/api/release"' in HTML and '"/api/period_table"' in HTML and "/api/date_range?" in HTML and "&confirmed=1" in HTML
    assert "selectable: true" in HTML and "addCol: openNewCol" in HTML and "RowPick.bar($(\"rows-tools\")" in HTML
    assert "shown: {" in HTML  # 전처리로 보내기에 지금 보이는 쪽
    assert "PrepMenu.items(" in HTML and "PrepMenu.valuesBox(" in HTML and "RowPick.valueLines(" in HTML
    assert "g.columns[c.cellIndex - (g.selectable ? 1 : 0)]" in HTML  # 고르기 칸만큼 밀린 칸 번호(고르기 칸 없는 표는 그대로)
    for id_ in ("page-go", "sql-go"):
        assert f'$("{id_}")' in HTML
    assert "Math.max(3, " in HTML and ".size = " in HTML


METRICS = """
metrics:
  매출:
    format: number
    numerator: {agg: sum, column: 금액}
"""

PAGE = r"""(async () => {
  const out = {}, sleep = ms => new Promise(r => setTimeout(r, ms));
  const until = async (f, what, ms = 15000) => { const t = Date.now(); while (!f()) { if (Date.now() - t > ms) throw new Error("기다림 초과: " + what); await sleep(40); } };
  const calls = [], real = window.fetch;
  window.fetch = (u, o) => {  // 요청을 센다. 전처리로 보내기는 화면을 떠나지 않게 가짜 답
    calls.push([String(u), o?.body ?? null]);
    if (String(u).includes("/api/handoff")) return Promise.resolve(new Response(JSON.stringify({ url: "#handed" }), { headers: { "Content-Type": "application/json" } }));
    return real(u, o);
  };
  const n = part => calls.filter(c => c[0].includes(part)).length;
  const idle = () => until(() => !$("search").disabled, "검색 끝");
  const menu = target => { target.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 60, clientY: 60 })); return [...document.querySelectorAll("[role=menuitem]")]; };
  const th = name => [...$("rows").querySelectorAll("th")].find(t => t.firstChild?.textContent === name);
  try {
    await until(() => [...$("base").options].some(o => o.value === "orders"), "테이블 목록", 40000);
    await PrepMenu.load();
    $("limit").value = "5"; $("base").value = "orders"; $("base").dispatchEvent(new Event("change"));
    await until(() => $("rows")._grid, "첫 검색"); await idle();
    out.pick = !!$("rows").querySelector("td.pick input") && !!$("rows").querySelector("th.addcol button");
    // P7 휠 5번 → 쪽 표시는 바로 6쪽, 조회는 한 번
    const q0 = n("/api/query");
    for (let i = 0; i < 5; i++) $("next").dispatchEvent(new WheelEvent("wheel", { deltaY: 100, bubbles: true, cancelable: true }));
    out.wheelShown = $("page-info").textContent;
    await sleep(500); await idle();
    out.wheelQueries = n("/api/query") - q0; out.wheelSkip = skip; out.wheelPage = $("page-info").textContent;
    // P8 쪽 칸 너비
    $("page-go").value = "12345"; $("page-go").dispatchEvent(new Event("input")); out.pgSize = $("page-go").size;
    $("page-go").value = ""; $("page-go").dispatchEvent(new Event("input")); out.pgSize0 = $("page-go").size;
    // 칸 우클릭: 고르기 칸만큼 밀려도 그 칸의 컬럼
    const td = $("rows").querySelector("tbody tr").children[1 + $("rows")._grid.columns.indexOf("금액")];
    out.condWant = `금액 = ${$("rows")._grid.rows[0]["금액"]}`;
    menu(td).find(b => b.textContent === "이 값만").click();  // 누른 항목이 초점이 든 메뉴를 지워도(focusout) 실행된다
    await until(() => S.conditions.length, "값 조건"); out.cond = S.conditions.map(c => c.line ?? c);
    S.conditions = []; renderList("conditions");
    // 우클릭 메뉴는 누른 자리에 뜬다(표 왼쪽 위에 붙지 않는다). 키보드로 열면(좌표 0) 그 칸 아래에
    const cellR = $("rows").querySelector("tbody tr:nth-child(3)").children[3].getBoundingClientRect(), at = [Math.round(cellR.left + 30), Math.round(cellR.top + 5)];
    $("rows").querySelector("tbody tr:nth-child(3)").children[3].dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: at[0], clientY: at[1] }));
    const mr = ctxBox.getBoundingClientRect(); out.menuAt = [at, [Math.round(mr.left), Math.round(mr.top)]];
    const kCell = $("rows").querySelector("tbody tr").children[2], thR = kCell.getBoundingClientRect();  // 칸 메뉴는 짧아 화면 아래로 밀려 올라가지 않는다
    kCell.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 0, clientY: 0 }));
    const kr = ctxBox.getBoundingClientRect(); out.keyAt = [[Math.round(thR.left), Math.round(thR.bottom)], [Math.round(kr.left), Math.round(kr.top)]];
    closeCtx();
    // 키보드: 머리글은 초점을 받고(tabindex 0), 메뉴 키로 열고 Esc면 닫혀 초점이 그 칸으로, 밖으로 초점이 나가면 닫힌다
    const hth = th("고객"); out.thTab = hth.tabIndex; hth.focus();
    hth.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 0, clientY: 0 }));
    out.kbOpen = !!ctxBox && ctxBox.contains(document.activeElement);
    document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    out.kbBack = !ctxBox && document.activeElement === hth;
    hth.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 0, clientY: 0 }));
    $("row-find").focus(); out.kbBlur = !ctxBox;
    // P5-값: 값 골라 제외. (R2) 따옴표 해석을 꺼 두어도 프로그램이 만든 줄은 늘 따옴표 해석을 켠 채로 넣는다
    $("quotes").checked = false;
    menu(th("고객")).find(b => b.textContent.startsWith("값 골라서")).click();
    await until(() => document.querySelectorAll(".pm-values input").length > 2, "값 목록");
    const vb = document.querySelector(".pm-box"), boxes = [...vb.querySelectorAll(".pm-values input")];
    boxes.find(c => c._v === "c1").click(); boxes.find(c => c._v === "c2").click();
    [...vb.querySelectorAll("button")].find(b => b.textContent === "고른 값 제외").click();
    await until(() => S.conditions.length, "값 고르기 조건"); out.valueCond = S.conditions.map(c => c.line ?? c); out.valueCondRaw = S.conditions[0];
    $("quotes").checked = true;
    S.conditions = []; renderList("conditions");
    // P4 머리글 우클릭: 정렬·컬럼 빼기·값 고르기·전처리 단계
    let items = menu(th("고객"));
    out.thMenu = items.map(b => b.textContent); out.thOff = items.filter(b => b.disabled).length;
    out.statsItem = (b => b && [b.disabled, b.title])(items.find(b => b.textContent.includes("라벨 인코딩")));  // 표 전체 통계를 읽는 단계는 쿼리 화면에서 흐리게 + 안내
    items.find(b => b.textContent.endsWith("앞뒤 공백 지우기")).click();
    await until(() => S.calcs.length && $("calcs").querySelector("li"), "전처리 줄"); await idle(); await sleep(300); out.calcs = [...S.calcs];  // (RB5) 서버 검사·컬럼 다시 받기가 끝난 뒤
    // (R1) 제자리 전처리로 값이 바뀐 컬럼은 조건이 전처리보다 먼저 걸려 틀리므로 값 조건 항목을 내지 않는다
    out.trimmedTd = menu($("rows").querySelector("tbody tr").children[1 + $("rows")._grid.columns.indexOf("고객")]).map(b => b.textContent); closeCtx();
    out.trimmedTh = menu(th("고객")).map(b => b.textContent); closeCtx();
    // (AB1, phase-10 결정 2) 제자리 계산 줄(곱하기 등)은 그 줄이 바꾸는 컬럼만 값 조건 항목·조건 칸 컬럼 목록에서 막는다
    await addItem("calcs", "곱하기 2 금액", $("err-search"));
    out.inplaceTd = menu($("rows").querySelector("tbody tr").children[1 + $("rows")._grid.columns.indexOf("id")]).map(b => b.textContent); closeCtx();
    out.inplaceBcol = [...$("bcol").options].map(o => o.value);
    S.calcs.pop(); renderList("calcs"); await refreshColumns();
    out.bcolBack = [...$("bcol").options].map(o => o.value);
    // P5 이 컬럼 빼기
    menu(th("할인")).find(b => b.textContent === "이 컬럼 빼기").click();
    await sleep(100); await idle();
    out.columns = [...S.columns]; out.gridCols = $("rows")._grid.columns;
    // (AB3) 보일 컬럼이 정해진 뒤 계산 줄을 직접 더해도(찾기 ③·계산 폼·직접 쓰기 길) 새 컬럼이 보일 컬럼 끝에 붙는다
    await addItem("calcs", "식 금액 * 2 이름 두배", $("err-search")); out.columnsCalc = [...S.columns];
    // (RB3) 그 계산 줄을 ×로 지우면 보일 컬럼에서도 빠지고, 그 뒤 조건을 더할 수 있다(남아 있으면 서버가 "'두배' 컬럼이 없어요"로 모두 막았다)
    $("calcs").querySelector(`button[data-i="${S.calcs.length - 1}"]`).click();
    await until(() => !S.columns.includes("두배") && !S.calcs.some(l => String(l).includes("두배")), "지운 줄의 보일 컬럼", 8000); await sleep(300);
    out.afterDrop = { columns: [...S.columns], cond: await addItem("conditions", "id > 3", $("err-search")), err: $("err-search").textContent };
    S.conditions = []; renderList("conditions"); $("err-search").textContent = "";
    // P1 달력 → 기간 줄(범위는 컬럼별로 한 번만 묻는다)
    out.perCols = [...$("per-col").options].map(o => o.value);
    $("per-col").value = "주문일"; $("per-cal").click();
    await until(() => document.querySelector(".cal .cal-day"), "달력");
    out.calOff = document.querySelector('.cal [data-d="2026-03-07"]').disabled;
    document.querySelector('.cal [data-d="2026-03-02"]').click(); document.querySelector('.cal [data-d="2026-02-03"]').click();
    await until(() => S.periods.length, "기간 줄"); out.periods = [...S.periods];
    $("per-cal").click(); await until(() => document.querySelector(".cal .cal-day"), "달력 다시");
    out.rangeCalls = n("/api/date_range");
    document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    S.periods = []; renderList("periods");
    // P9 [+] 두 곳 → 같은 상자, 미리보기, 추가
    $("rows").querySelector("th.addcol button").click();
    await until(() => document.querySelector(".nc-box"), "새 컬럼 상자(머리글)");
    document.querySelector(".nc-box").closest(".modal").click();
    $("newcol-open").click();
    await until(() => document.querySelector(".nc-box .nc-form select"), "새 컬럼 상자(도구줄)");
    const nc = document.querySelector(".nc-box");
    nc.querySelector('.nc-form select[aria-label="A"]').value = "금액"; nc.querySelector('.nc-form select[aria-label="B"]').value = "할인";
    nc.querySelector(".nc-form").dispatchEvent(new Event("change"));
    nc.querySelector(".nc-foot input").value = "합";
    [...nc.querySelectorAll("button")].find(b => b.textContent === "미리보기").click();
    await until(() => nc.querySelector(".nc-preview table"), "미리보기 표");
    out.preview = [...nc.querySelectorAll(".nc-preview th")].map(t => t.textContent);
    [...nc.querySelectorAll("button")].find(b => b.textContent === "추가").click();
    await until(() => S.calcs.some(l => l.endsWith("이름 합")), "새 컬럼 줄"); out.calcs2 = [...S.calcs];
    await until(() => S.columns.includes("합"), "보일 컬럼에 새 컬럼"); out.columns2 = [...S.columns];  // 컬럼을 뺀 뒤(보일 컬럼이 정해짐) 만든 새 컬럼도 보인다
    // 서버가 거부하는 줄이면 상자는 입력을 둔 채 이유를 보이고, 계산 목록은 그대로
    const calcsBefore = [...S.calcs];
    $("newcol-open").click(); await until(() => document.querySelector(".nc-box .nc-method"), "새 컬럼 상자(거부)");
    const nc2 = document.querySelector(".nc-box");
    nc2.querySelector('.nc-method[data-id="expr"]').click();
    nc2.querySelector(".nc-form textarea").value = "없는컬럼 + 1"; nc2.querySelector(".nc-foot input").value = "나쁨";
    [...nc2.querySelectorAll("button")].find(b => b.textContent === "추가").click();
    await until(() => nc2.querySelector(".err").textContent, "거부 안내");
    out.badAdd = { open: nc2.isConnected, msg: nc2.querySelector(".err").textContent, same: JSON.stringify(S.calcs) === JSON.stringify(calcsBefore) };
    nc2.closest(".modal").click();
    // P12 행 고르기 → 막대
    $("rows").querySelector("td.pick input").click();
    out.barShown = !document.querySelector("#rows-tools .rowpick").classList.contains("hidden");
    // P10 넘기기 본문의 shown
    $("handoff").click(); await until(() => n("/api/handoff"), "넘기기");
    const hb = JSON.parse(calls.find(c => c[0].includes("/api/handoff"))[1]);
    out.shown = { columns: hb.shown.columns, rows: hb.shown.rows.length, row0: hb.shown.rows[0].length, offset: hb.shown.offset, total: hb.shown.total };
    // P12 고른 행 빼기 → 컬럼 조합 조건으로 다시 검색(65 → 64행)
    const q1 = n("/api/query");
    [...document.querySelectorAll("#rows-tools .rowpick button")].find(b => b.textContent === "선택한 행 빼기").click();
    await until(() => n("/api/query") > q1, "고른 행 조건으로 검색"); await idle();
    out.pickCond = S.conditions.map(c => c.line ?? c); out.pickCount = last.count;
    S.conditions = []; renderList("conditions");
    // F06 형식 단추가 펼쳐져 보인다, F04 보일 컬럼 고르기
    out.exportShown = [...document.querySelectorAll("[data-export]")].filter(b => b.offsetParent).map(b => b.dataset.export);
    out.colPick = [...$("col-pick").options].map(o => o.value);
    // F12 조건 우변 컬럼
    $("bcol").value = "금액"; $("bcol").dispatchEvent(new Event("change"));
    $("bmode").value = "column"; $("bmode").dispatchEvent(new Event("change"));
    out.bvalHidden = $("bval").classList.contains("hidden");
    $("bop").value = ">"; $("bcol2").value = "할인"; $("badd").click();
    await until(() => S.conditions.length, "컬럼 비교 조건"); out.colCond = S.conditions.map(c => c.line ?? c);
    // F10 탭 이름에 조인 테이블
    await addItem("joins", "customers left 고객=id", $("err-join"));
    await sleep(100); out.tabName = $("wtab-list").textContent;
    // P3 기간별 표
    clearLists();
    showTab("summary");
    $("pt-add").click();
    await until(() => [...$("pt-tables").querySelectorAll("select")][1]?.options.length, "기간별 표 줄");
    $("pt-unit").value = "month"; $("pt-run").click();
    await until(() => $("pt-out")._grid, "기간별 표");
    out.pt = { columns: $("pt-out")._grid.columns, rows: $("pt-out")._grid.rows.map(r => $("pt-out")._grid.columns.map(c => r[c])) };
    $("pt-show").value = "share"; $("pt-show").dispatchEvent(new Event("change"));
    out.ptShare = $("pt-out")._grid.columns; out.ptShare0 = $("pt-out")._grid.rows[0][$("pt-out")._grid.columns[2]];
    out.ptChart = !$("pt-box").classList.contains("hidden");
    showTab("rows");
    // P13 지표 기간·비교
    showView("metric");
    await until(() => document.querySelector("#met-list .met"), "지표 목록");
    $("met-period").value = "2026-02-01 ~ 2026-02-28"; $("met-cmp").value = "prev";
    document.querySelector("#met-list .met").click();
    await until(() => metDrawn?.raw?.changes, "비교 결과");
    out.met = [$("met-big").textContent, $("met-sub").textContent];
    out.metBody = JSON.parse(calls.filter(c => c[0].includes("/api/metrics/run")).at(-1)[1]);
    showView("explore");
    // P17 탭 등록(@탭이름) · 머리글 우클릭 "이 값으로 다른 테이블 찾기…" → 미리보기 수치 → ① 새 탭(@orders semi)
    const ref0 = tabRef(tabAt); out.ref0 = ref0;
    await until(() => calls.some(c => c[0].includes("/api/tabs/register") && JSON.parse(c[1]).name === ref0 && JSON.parse(c[1]).body.base === "orders"), "탭 등록");
    // 서버 등록부만 비면(연결 renew 흉내) 화면이 알아채 다시 등록한다
    const c0 = calls.length;
    await real("/api/tabs/forget", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: ref0 }) });
    await until(() => calls.slice(c0).some(c => c[0].includes("/api/tabs/register") && JSON.parse(c[1]).name === ref0), "다시 등록", 10000);
    menu(th("고객")).find(b => b.textContent === "이 값으로 다른 테이블 찾기…").click();
    const lk = document.querySelector(".lk-box"), lsel = label => lk.querySelector(`select[aria-label="${label}"]`);
    out.lookupFrom = lsel("기준 컬럼").value;
    out.lookupTargets = [...lsel("대상 테이블").options].map(o => o.value);
    lsel("대상 테이블").value = "customers"; lsel("대상 테이블").dispatchEvent(new Event("change"));
    await until(() => [...lsel("대상 키 컬럼").options].some(o => o.value === "id"), "대상 키");
    lsel("대상 키 컬럼").value = "id";
    [...lk.querySelectorAll("button")].find(b => b.textContent === "미리보기").click();
    await until(() => lk.querySelector("p.hint").textContent.includes("없는 값"), "미리보기 수치"); out.lookupNote = lk.querySelector("p.hint").textContent;
    [...lk.querySelectorAll("button")].find(b => b.textContent === "적용").click();
    await until(() => tabs.length === 2 && S.joins.length && last?.rows, "찾기 새 탭"); await idle();
    out.lookupTab = { base: S.base, joins: [...S.joins], count: last.count, name: tabName(tabAt), targetsNow: [...$("u-add").querySelectorAll("optgroup option")].map(o => o.value) };
    await goTab(0);
    // F01 테이블을 바꾸면 앞 테이블을 놓는다
    const r0 = n("/api/release");
    $("base").value = "customers"; $("base").dispatchEvent(new Event("change"));
    await until(() => n("/api/release") > r0, "release");
    out.release = JSON.parse(calls.filter(c => c[0].includes("/api/release")).at(-1)[1]);
    // (B1) 초안에 탭 번호를 담는다: 다음 화면에서 [이어서 하기]로 되살려도 @탭N이 같아야 찾기 탭의 조인이 같은 탭을 가리킨다
    out.refsBefore = tabs.map((t, i) => tabRef(i)); out.joinsBefore = tabs[1].st?.search?.body?.joins ?? [];
    await sleep(4500);  // 초안 저장(2초 조용 + 간격)
  } catch (e) { out.error = String(e && e.stack || e) + ` / 오류 칸: ${$("err-search").textContent} / 탭 ${tabs.length}, 기준 ${S.base}, 조인 ${JSON.stringify(S.joins)} / JS 오류 ${JSON.stringify(window.__errs)}`; }
  return JSON.stringify({ ...out, errs: window.__errs || [] });
})()"""


PAGE2 = r"""(async () => {
  const out = {}, sleep = ms => new Promise(r => setTimeout(r, ms));
  const until = async (f, what, ms = 20000) => { const t = Date.now(); while (!f()) { if (Date.now() - t > ms) throw new Error("기다림 초과: " + what); await sleep(40); } };
  try {
    await until(() => [...document.querySelectorAll(".modal button")].some(b => b.textContent === "이어서 하기"), "초안 팝업");
    [...document.querySelectorAll(".modal button")].find(b => b.textContent === "이어서 하기").click();
    await until(() => tabs.length === 2 && !tabBusy, "탭 둘");
    out.refsAfter = tabs.map((t, i) => tabRef(i));
    out.joinsAfter = tabAt === 1 ? [...S.joins] : tabs[1].st?.search?.body?.joins ?? [];
    await addTab(); out.fresh = tabRef(tabAt);
  } catch (e) { out.error2 = String(e && e.stack || e); }
  return JSON.stringify(out);
})()"""


@pytest.fixture
def shop(folder, tmp_path):
    """orders(id 0~64, 고객 c0~c4, 금액 = id×10, 할인 = id, 주문일 2026-01-01 + id일), customers(id c0~c4) DuckDB 파일에 진짜로 연결하고 지표 파일을 골라 둔다."""
    duckdb = pytest.importorskip("duckdb")
    from core import server
    from query.search_app import app
    path = tmp_path / "shop.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table orders as select i::int as id, 'c' || (i % 5) as 고객, i * 10.0 as 금액, i * 1.0 as 할인, date '2026-01-01' + i::int as 주문일 from range(65) t(i)")
    raw.execute("create table customers as select 'c' || i as id, '이름' || i as 이름 from range(5) t(i)")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    (tmp_path / "metrics.yaml").write_text(METRICS, encoding="utf-8")
    assert client.post("/api/metrics", json={"path": str(tmp_path / "metrics.yaml")}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


@pytest.mark.skipif(EDGE is None, reason="Edge가 없다")
def test_query_screen_headless(shop, tmp_path):
    websocket = pytest.importorskip("websocket")  # websocket-client는 로컬 전용(CI에는 없음)
    from werkzeug.serving import make_server
    from core import server
    from query.search_app import app
    http = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    port = http.server_port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        cdp = s.getsockname()[1]
    profile = tempfile.mkdtemp(prefix="q1edge_", dir=tmp_path)
    spawn_edge([EDGE, "--headless=new", f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", f"--user-data-dir={profile}", "--no-first-run",
                       "--disable-extensions", "--window-size=1440,900", "about:blank"])
    ws, n = None, [0]

    def call(method, **params):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == n[0]:
                return msg.get("result", msg)
    try:
        t0 = time.time()
        while ws is None and time.time() - t0 < 30:
            try:
                page = next(p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json", timeout=1)) if p["type"] == "page")
                ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=120)
            except Exception:
                time.sleep(0.3)
        assert ws, "Edge에 연결하지 못함"
        call("Browser.setDownloadBehavior", behavior="deny")  # 이 시험은 내려받지 않는다(혹시 일어나도 막는다)
        call("Page.enable")
        call("Page.addScriptToEvaluateOnNewDocument", source="window.__errs=[];addEventListener('error',e=>__errs.push(e.message));addEventListener('unhandledrejection',e=>__errs.push(String(e.reason)));")
        call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
        call("Emulation.setFocusEmulationEnabled", enabled=True)  # 창이 앞에 없어도 focus·focusout이 일어나게(헤드리스)
        call("Page.navigate", url=f"http://127.0.0.1:{port}/?key={server.KEY}")
        time.sleep(1.5)
        call("Page.navigate", url=f"http://127.0.0.1:{port}/search")
        time.sleep(2)
        out = json.loads(call("Runtime.evaluate", expression=PAGE, awaitPromise=True, returnByValue=True)["result"]["value"])
        call("Page.navigate", url=f"http://127.0.0.1:{port}/search")  # 다시 열어 초안 [이어서 하기]
        time.sleep(2)
        out.update(json.loads(call("Runtime.evaluate", expression=PAGE2, awaitPromise=True, returnByValue=True)["result"]["value"]))
    finally:
        if ws:
            ws.close()
        kill_edge(profile)
        http.shutdown()
    assert "error" not in out, out.get("error")
    assert out["errs"] == [], out["errs"]
    assert out["pick"]
    assert out["wheelShown"] == "6 / 13쪽" and out["wheelQueries"] == 1 and out["wheelSkip"] == 25 and out["wheelPage"] == "6 / 13쪽"
    assert out["pgSize"] == 6 and out["pgSize0"] == 3
    assert out["cond"] == [out["condWant"]]
    assert out["thMenu"][:4] == ["오름차순 정렬", "내림차순 정렬", "이 컬럼 빼기", "값 골라서 제외·만 보기…"] and len(out["thMenu"]) > 10 and out["thOff"] > 0
    assert re.fullmatch(r"전처리 trim 고객 true", out["calcs"][0])
    assert out["valueCond"] == ["고객 except c1, c2"] and out["valueCondRaw"] == "고객 except c1, c2"  # (R2) {line, quotes: false}가 아니다
    assert out["trimmedTd"] == ["오름차순 정렬", "내림차순 정렬"] and "값 골라서 제외·만 보기…" not in out["trimmedTh"] and "이 컬럼 빼기" in out["trimmedTh"]  # (R1)
    assert out["inplaceTd"] == ["이 값 제외", "이 값만", "오름차순 정렬", "내림차순 정렬"] and "금액" not in out["inplaceBcol"] and "id" in out["inplaceBcol"] and "id" in out["bcolBack"] and "고객" not in out["bcolBack"]  # (AB1) 곱하기 줄은 금액만, 없으면 trim 건 고객만 막힘
    assert out["columnsCalc"] == ["id", "고객", "금액", "주문일", "두배"]  # (AB3)
    assert out["statsItem"][0] is False, out["statsItem"]  # 쿼리 화면에서도 통계 단계가 켜진다(결정 1)
    assert out["afterDrop"] == {"columns": ["id", "고객", "금액", "주문일"], "cond": True, "err": ""}, out["afterDrop"]  # (RB3)
    assert out["columns"] == ["id", "고객", "금액", "주문일"] and "할인" not in out["gridCols"]
    assert "주문일" in out["perCols"] and out["calOff"] is True
    assert out["periods"] == ["주문일 2026-02-03 ~ 2026-03-02"] and out["rangeCalls"] == 1
    assert "합" in out["preview"] and out["calcs2"][-1] == "식 금액 + 할인 이름 합" and out["columns2"] == ["id", "고객", "금액", "주문일", "합"]
    assert out["thTab"] == 0 and out["kbOpen"] and out["kbBack"] and out["kbBlur"]
    assert out["badAdd"]["open"] and "없는컬럼" in out["badAdd"]["msg"] and out["badAdd"]["same"], out["badAdd"]
    assert out["barShown"] and out["pickCond"][0].startswith("(id, 금액, 주문일) except (") and out["pickCount"] == 64
    (x, y), (mx, my) = out["menuAt"]
    assert abs(mx - x) <= 2 and abs(my - y) <= 2, out["menuAt"]  # 우클릭한 자리(옛 코드는 상자 너비를 화면 너비로 재서 왼쪽 끝 0에 붙었다)
    (kx, ky), (mkx, mky) = out["keyAt"]
    assert abs(mkx - kx) <= 2 and abs(mky - ky) <= 2, out["keyAt"]  # 키보드로 열면 그 칸 아래
    assert out["shown"]["offset"] == 0 and out["shown"]["total"] == 65 and out["shown"]["rows"] == 5 and out["shown"]["row0"] == len(out["shown"]["columns"])
    assert out["exportShown"] == ["csv", "xlsx", "parquet", "tsv", "fixed"]
    assert "고객" in out["colPick"]
    assert out["bvalHidden"] and out["colCond"] == ["금액 > 할인"]
    assert "orders + customers" in out["tabName"]
    assert out["pt"]["columns"] == ["기간", "orders", "orders 비중 %"] and [r[1] for r in out["pt"]["rows"]] == [31, 28, 6] and out["ptChart"]  # 비중 % 함께가 기본(Phase 10 결정 9)
    assert out["ptShare"] == ["기간", "orders", "orders 비중 %"] and out["ptShare0"] == "47.69%"
    assert out["metBody"]["period"] == {"start": "2026-02-01", "end": "2026-02-28"} and out["metBody"]["compare"] == "prev"
    assert out["met"][0] == "12,460" and "지난 기간 대비 +168.0%" in out["met"][1]
    assert out["release"] == {"schema": "", "base": "orders", "tab": out["ref0"]}  # (A5) 부르는 탭의 등록 이름도
    assert "error2" not in out, out.get("error2")
    assert out["refsAfter"] == out["refsBefore"] and out["joinsAfter"] == out["joinsBefore"] == [f"{out['ref0']} semi id=고객"], (out["refsBefore"], out["refsAfter"])
    assert out["fresh"] not in out["refsAfter"]  # 되살린 뒤 새 탭은 겹치지 않는 번호
    assert out["lookupFrom"] == "고객" and "customers" in out["lookupTargets"] and out["ref0"] not in out["lookupTargets"]  # 지금 탭 자신은 대상이 아니다
    assert out["lookupNote"] == "이 컬럼 값 5개 중 대상에 있는 값 5개, 없는 값 0개"
    assert out["ref0"].startswith("@탭")  # 등록 이름은 탭 번호(같은 테이블 탭이 여럿이어도 다르다)
    assert out["lookupTab"] == {"base": "customers", "joins": [f"{out['ref0']} semi id=고객"], "count": 5, "name": "찾기: customers", "targetsNow": [out["ref0"]]}  # 행 합치기 목록에 "탭 orders (가공 결과)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_sentence_share_column_shown_as_percent(tmp_path):
    """문장 결과 표의 "비중 %" 열(0~100)은 소수 1자리 + %로 보이고, 결과(d)의 값은 숫자 그대로(차트·보내기)."""
    src = (ROOT / "static" / "query" / "sentences.js").read_text(encoding="utf-8")
    a = src.index("  function showResult(out, d) {")
    show = src[a:src.index("\n  }\n", a) + 4]
    script = tmp_path / "t.js"
    script.write_text("""
globalThis.document = { documentElement: { dataset: {} }, getElementById: () => null };
globalThis.addEventListener = () => {};
const el = (t, props = {}, ...kids) => Object.assign({ tag: t, dataset: {}, kids, classList: { toggle() {} }, querySelectorAll: () => [], append(...k) { this.kids.push(...k); } }, props);
const shown = [], sentChart = () => null, island = () => () => {}, resultTable = (host, d) => shown.push(d.rows.map(r => r["비중 %"]));
""" + show + """
const d = { columns: ["대상", "비중 %"], rows: [{ "대상": "a", "비중 %": 47.6923 }, { "대상": "b", "비중 %": null }], notes: [] };
showResult({ replaceChildren() {} }, d);
showResult({ replaceChildren() {} }, { columns: ["대상"], rows: [{ "대상": "a" }], notes: [] });
console.log(JSON.stringify({ shown, kept: d.rows[0]["비중 %"] }));
""", encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {"shown": [["47.7%", None], [None]], "kept": 47.6923}


# ---------- 화면 리뷰 고침(q1c): 휠 끝 쪽·재시도, 탭 등록 이름, release ----------

def _wheel(tmp_path, setup, body):
    """index.html의 휠 구간을 가짜 $ 위 node로 돌린다."""
    from test_fix_audit_js import run_node
    a = HTML.index("// 이전·다음 위에서 휠")
    code = HTML[a:HTML.index("// 결과 표의 머리글", a)]
    return run_node(tmp_path, body, parts=["const els = {}, reads = [];\nglobalThis.$ = id => els[id] ??= { id, textContent: \"\", disabled: false, value: \"\" };\n" + setup + "\n", code])


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_wheel_unknown_count_stops_at_last_page(tmp_path):
    """행 수를 모를 때: [다음]이 꺼진 마지막 쪽에서는 더 가지 않고, 켜져 있으면 한 쪽까지만 간다(빈 쪽을 읽지 않는다)."""
    setup = "let searched = { limit: \"10\" }, skip = 20, lastPages = null;\nconst runSearch = async offset => { reads.push(offset); skip = offset; return true; };"
    body = """
const fire = n => { for (let i = 0; i < n; i++) $("next").onwheel({ deltaY: 100, preventDefault() {} }); };
(async () => {
  $("next").disabled = true; fire(4); const last = $("page-info").textContent; await sleep(300);
  $("next").disabled = false; fire(4); const more = $("page-info").textContent; await sleep(300);
  console.log(JSON.stringify({ last, more, reads }));
})();"""
    assert _wheel(tmp_path, setup, body) == {"last": "3쪽", "more": "4쪽", "reads": [30]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_wheel_retries_while_another_search_runs(tmp_path):
    """다른 검색이 도는 중(runSearch가 busy에 막혀 undefined)이면 가리킨 쪽을 버리지 않고 잠시 뒤 다시 읽는다."""
    setup = "let searched = { limit: \"10\" }, skip = 0, lastPages = 9, busyNow = 2;\nconst runSearch = async offset => { if (busyNow-- > 0) return undefined; reads.push(offset); skip = offset; return true; };"
    body = """
(async () => {
  for (let i = 0; i < 3; i++) $("next").onwheel({ deltaY: 100, preventDefault() {} });
  await sleep(900);
  console.log(JSON.stringify({ reads, shown: $("page-info").textContent }));
})();"""
    assert _wheel(tmp_path, setup, body) == {"reads": [30], "shown": "4 / 9쪽"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_tab_ref_is_stable_per_tab_and_release_only_unused_table(tmp_path):
    """등록 이름은 탭마다 바뀌지 않는 번호(@탭N): 같은 테이블 탭 둘도 다르고, 이름·조인이 바뀌어도 그대로. 탭을 닫을 때 다른 탭이 쓰지 않는 테이블만 release."""
    from test_cr_r4a import STUB, BLOCK
    from test_fix_audit_js import run_node
    body = r"""
(async () => {
  const calls = []; apiHook = async (u, b) => { calls.push([u, b?.base]); return {}; };
  S.base = "orders"; await addTab(); S.base = "orders";
  const same = [tabRef(0), tabRef(1)];
  tabs[1].name = "고객"; tabs[1].auto = false; const renamed = tabRef(1);
  await addTab(); S.base = "users";
  await closeTab(2);                 // users는 아무 탭도 안 쓴다 → release
  await closeTab(1);                 // orders는 탭 0이 아직 쓴다 → release 안 함
  console.log(JSON.stringify({ same, renamed, release: calls.filter(c => c[0] === "/api/release").map(c => c[1]) }));
})();"""
    got = run_node(tmp_path, body, files=["draft.js"], parts=[STUB, BLOCK])
    assert got["same"][0] != got["same"][1] and all(r.startswith("@탭") and " " not in r for r in got["same"])
    assert got["renamed"] == got["same"][1]
    assert got["release"] == ["users"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_release_sends_calling_tab(tmp_path):
    """(A5) release 요청에 부르는 탭의 등록 이름(tab)을 같이 보낸다: 서버 등록부에 그 탭의 옛 검색(0.3초 뒤에 바뀜)이 남아 있어도 서버가 그 탭은 사용 검사에서 뺀다.
    탭을 닫을 때는 닫은 탭의 이름."""
    from test_cr_r4a import STUB, BLOCK
    from test_fix_audit_js import run_node
    body = r"""
(async () => {
  const calls = []; apiHook = async (u, b) => { calls.push([u, b]); return {}; };
  S.base = "orders"; await addTab(); S.base = "users";
  const closed = tabRef(1);
  await closeTab(1);
  console.log(JSON.stringify({ closed, sent: calls.filter(c => c[0] === "/api/release").map(c => c[1]) }));
})();"""
    got = run_node(tmp_path, body, files=["draft.js"], parts=[STUB, BLOCK])
    assert [(b["base"], b["tab"]) for b in got["sent"]] == [("users", got["closed"])]


# ---------- 옛 화면 감사 B 고침: 계산 컬럼 거르기(B2), 닫기 확인(B7) ----------

@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_computed_columns_get_no_condition_items(tmp_path):
    """(B2) 계산 컬럼(S.origins에 없는 새 컬럼)은 조건이 계산보다 먼저 걸려 쓸 수 없다: 칸 메뉴는 정렬만, 머리글은 값 고르기 없이, 달력 날짜 목록에서 빠지고,
    고른 행 막대(RowPick.bar의 columns: plainCol)는 원래 컬럼으로만 조건을 만든다."""
    from test_fix_audit_js import index_slice, run_node
    prelude = r"""
const S = { cols: [["name", "문자"], ["두배", "실수"], ["주문일", "날짜"], ["새날", "날짜"]], origins: { name: "t", 주문일: "t" }, sorts: [], conditions: [], columns: [], calcs: [] };
const els = { quotes: { checked: true }, rows: { _grid: null }, "err-search": {} };
globalThis.$ = id => els[id] ??= mk("x", {});
document.getElementById = id => $(id);  // rowpick.js가 따옴표 해석 칸을 본다
const added = [], runSearch = async () => true;
const wrap = v => v, gridAt = (g, k) => g.view ? g.view[k] : k, addItem = async (k, l) => { added.push(l); return true; }, sortBy = () => {}, flash = () => {}, inExplore = () => true;
const searchUndo = { undo() {} }, PrepMenu = { items: () => [] }, connected = "other";
const cellAt = (tag, index, tr) => { const c = { tagName: tag, cellIndex: index, closest: s => s === "tr" ? tr : null }; return { closest: s => s.includes("#rows") ? c : null }; };
const row = p => ({ dataset: p === undefined ? {} : { p: String(p) }, sectionRowIndex: 0, classList: { contains: () => false } });
"""
    code = index_slice("// 우클릭 메뉴:", "document.oncontextmenu") + index_slice("const allDates", "\n") + "\n" + index_slice("const rowKeyPick", '$("rows-tools").append') + "\n"
    body = r"""
$("rows")._grid = { columns: ["name", "두배"], rows: [{ name: "a", 두배: 2 }, { name: "rock 'n roll", 두배: 4 }], selected: new Set([0, 1]) };
const labels = t => ctxItems(t).map(i => i[0]);
ctxItems(cellAt("TD", 0, row(1))).find(i => i[0] === "이 값만")[1]();  // (AB4) 가운데 따옴표 값은 반대 따옴표로(RowPick.quote와 같은 규칙)
console.log(JSON.stringify({ tdPlain: labels(cellAt("TD", 0, row(0))), tdCalc: labels(cellAt("TD", 1, row(0))), thPlain: labels(cellAt("TH", 0, row())), thCalc: labels(cellAt("TH", 1, row())),
  dates: allDates(), only: added[0], drop: (rowPick.el.children[1].onclick(), added.at(-1)) }));  // [선택한 행 빼기](막대 안내 글 다음 단추)
"""
    got = run_node(tmp_path, body, files=["rowpick.js"], parts=[prelude, code])
    assert got["tdPlain"] == ["이 값 제외", "이 값만", "오름차순 정렬", "내림차순 정렬"] and got["tdCalc"] == ["오름차순 정렬", "내림차순 정렬"]
    assert "값 골라서 제외·만 보기…" in got["thPlain"] and "값 골라서 제외·만 보기…" not in got["thCalc"] and "이 컬럼 빼기" in got["thCalc"]
    assert got["dates"] == ["주문일"]
    assert got["only"] == "name = \"rock 'n roll\"" and got["drop"] == "(name) except (a), (\"rock 'n roll\")"


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_close_tab_used_by_other_tab_asks_first(tmp_path):
    """(B7) 다른 탭이 이 탭의 결과(@탭N)를 기준이나 조인으로 쓰면 닫기 전에 묻는다. 취소하면 그대로, 닫기를 고르면 닫는다. 아무도 안 쓰면 묻지 않는다."""
    from test_cr_r4a import STUB, BLOCK
    from test_fix_audit_js import run_node
    stub = "let asked = [], answer = false;\n" + STUB.replace("confirmBox = async () => true", "confirmBox = async (title, text) => { asked.push(text); return answer; }")
    assert stub != "let asked = [], answer = false;\n" + STUB
    body = r"""
(async () => {
  S.base = "orders"; await addTab(); S.base = "customers"; S.joins = [`${tabRef(0)} semi id=고객`];
  await closeTab(0); const kept = tabs.length;
  answer = true; await closeTab(0); const closed = tabs.length;
  const n = asked.length; await addTab(); S.base = "x"; await closeTab(1);
  console.log(JSON.stringify({ kept, closed, asked: n, after: asked.length, text: asked[0] }));
})();"""
    got = run_node(tmp_path, body, files=["draft.js"], parts=[stub, BLOCK])
    assert got["kept"] == 2 and got["closed"] == 1 and got["asked"] == 2 and got["after"] == 2 and got["text"].startswith("'customers + ")  # 쓰는 탭 이름을 알린다


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_close_tab_asks_when_used_by_union_and_auto_name_shows_tab_name(tmp_path):
    """행 합치기에 @탭N을 넣은 탭도 "쓰는 탭"으로 보고 닫기 전에 묻는다. 자동 탭 이름은 조인 상대가 @탭N이면 그 탭의 보이는 이름(정한 이름, 없으면 기준 테이블)."""
    from test_cr_r4a import STUB, BLOCK
    from test_fix_audit_js import run_node
    stub = "let asked = [], answer = false;\n" + STUB.replace("confirmBox = async () => true", "confirmBox = async (title, text) => { asked.push(text); return answer; }")
    body = r"""
(async () => {
  S.base = "orders"; await addTab(); S.base = "customers"; S.joins = [`${tabRef(0)} semi id=고객`]; S.union = { tables: [], how: "left" };
  const auto0 = tabName(1);
  tabs[0].name = "주문탭"; tabs[0].auto = false; const named = tabName(1);
  S.joins = []; S.union = { tables: [tabRef(0)], how: "left" };
  await closeTab(0);
  console.log(JSON.stringify({ auto0, named, asked: asked.length, kept: tabs.length }));
})();"""
    got = run_node(tmp_path, body, files=["draft.js"], parts=[stub, BLOCK])
    assert got == {"auto0": "customers + orders", "named": "customers + 주문탭", "asked": 1, "kept": 2}


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_wheel_failure_shows_current_page_again(tmp_path):
    """(R8) 휠로 가리킨 쪽을 읽다 실패하면 쪽 표시를 지금 쪽으로 되돌린다."""
    setup = "let searched = { limit: \"10\" }, skip = 0, lastPages = 9;\nconst runSearch = async offset => { reads.push(offset); return false; };"
    body = """
(async () => {
  for (let i = 0; i < 3; i++) $("next").onwheel({ deltaY: 100, preventDefault() {} });
  const moved = $("page-info").textContent;
  await sleep(400);
  console.log(JSON.stringify({ moved, back: $("page-info").textContent, reads }));
})();"""
    assert _wheel(tmp_path, setup, body) == {"moved": "4 / 9쪽", "back": "1 / 9쪽", "reads": [30]}


def test_expand_branch_removed():
    """(R6) expand는 구조 컬럼 단계(need struct)라 목록 컬럼의 '펼치기' 갈래는 쓰이지 않아 지웠다."""
    assert 'id === "expand"' not in HTML


def test_inplace_prep_line_changes_values_before_conditions(shop):
    """(R1 근거) 제자리 전처리(전처리 trim)로 값이 바뀌어도 조건은 그 앞에 걸린다: 바뀐 값으로 만든 조건은 0행이라 화면이 그 컬럼에는 값 조건을 내지 않는다."""
    import duckdb
    from core import server
    path = server.state["opened"]["file"]
    server.state["con"] = None
    shop.post("/api/disconnect", json={})
    raw = duckdb.connect(path)
    raw.execute("create table pad as select 1 as id, ' a ' as s union all select 2, 'b'")
    raw.close()
    from pathlib import Path as P
    assert shop.post("/api/connect", json={"kind": "folder", "path": str(P(path).parent), "file": path}).status_code == 200
    body = {"base": "pad", "calcs": ["전처리 trim s true"], "limit": 10, "offset": 0, "count": True}
    shown = shop.post("/api/query", json=body).get_json()["rows"]["rows"]
    assert {r["s"] for r in shown} == {"a", "b"}  # 화면에는 다듬은 값
    assert shop.post("/api/query", json={**body, "conditions": ["s = a"]}).get_json()["count"] == 0  # 그 값으로 만든 조건은 못 찾는다


def test_newcol_gets_value_list_and_bar_gets_plain_columns():
    """(R4) 새 컬럼 상자의 값 바꿔 쓰기 목록은 /api/values(colValues), (R3) 고른 행 막대는 rowCol 컬럼만 본다(다시 만드는 코드 없음)."""
    a = HTML.index("function openNewCol()")
    assert "values: colValues" in HTML[a:HTML.index("\n}\n", a)]
    assert "columns: rowCol" in HTML and "rowCondition" not in HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_close_tab_asks_when_used_by_other_tab_calc(tmp_path):
    """(AB2) 다른 탭이 계산 줄(찾기 ③ `그룹집계 … 테이블 @탭N …`)로 이 탭을 쓰면 닫기 전에 묻는다."""
    from test_cr_r4a import STUB, BLOCK
    from test_fix_audit_js import run_node
    stub = "let asked = [], answer = false;\n" + STUB.replace("confirmBox = async () => true", "confirmBox = async (title, text) => { asked.push(text); return answer; }")
    body = r"""
(async () => {
  S.base = "orders"; await addTab(); S.base = "customers"; S.calcs = [`그룹집계 count() 테이블 ${tabRef(0)} 키 id=고객 이름 n`];
  await closeTab(0);
  console.log(JSON.stringify({ asked: asked.length, kept: tabs.length }));
})();"""
    assert run_node(tmp_path, body, files=["draft.js"], parts=[stub, BLOCK]) == {"asked": 1, "kept": 2}


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_unpivot_blocks_value_conditions_and_lookup_name_is_safe(tmp_path):
    """(RB2) 세로로 줄은 행을 늘려 행 고르기만 막는다(값 조건은 된다). (RB4) 찾기 ③ 기본 새 컬럼 이름은 서버 이름 규칙에 맞게 다듬는다."""
    from test_fix_audit_js import index_slice, run_node
    code = index_slice("const INPLACE", "const valuesBody") + index_slice("const safeName", "\n") + "\n"
    body = r'''
const S = { origins: { 금액: "t", id: "t" }, calcs: [], cols: [["금액", "실수"], ["id", "정수"], ["할인", "실수"]] };
const a = plainCol("금액"); S.calcs = ["세로로 금액, 할인"]; const b = plainCol("id"), r = rowCol("id");
console.log(JSON.stringify({ a, b, r, names: ["sales-2024", "2024_orders", "주문 내역", "ok_1"].map(safeName) }));'''
    assert run_node(tmp_path, body, files=["newcol.js"], parts=[code]) == {"a": True, "b": True, "r": False, "names": ["sales_2024", "_2024_orders", "주문_내역", "ok_1"]}


def test_lookup_uses_shared_gcol():
    """(RB8) 찾기 ③의 값 컬럼 인용은 새 컬럼 상자와 같은 NewCol.gcol 한 벌을 쓴다."""
    a = HTML.index("function openLookup(")
    assert "NewCol.gcol(val.value)" in HTML[a:] and "const gcol = " not in HTML
