"""차트 계산을 node로 돌려 본다. node가 없으면 건너뛴다. 값은 손으로 계산한 작은 예제다.
분석 화면: lib/dash_calc.js의 순수 계산(주의 월요일, 색 글자, 날짜 계산, 읽은 범위). 옛 Chart.js 설정(dash.js chartConfig·byUnit·pickColor)은 R4-D2b에서 지웠다(차트 섬이 web/의 같은 계산으로 그린다).
대시보드 화면: web/lib/calc.ts를 node가 타입만 지워 불러 카드 모양 계산·조건 색 규칙을 본다."""
import json
import shutil
import subprocess

import pytest

from dashboard import dash_calc
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

SCRIPT = """
const assert = require("assert");
// 주의 월요일
assert.strictEqual(monday("2026-01-11"), "2026-01-05");  // 일요일 → 그 주 월요일
assert.strictEqual(monday("2026-01-12"), "2026-01-12");
assert.strictEqual(monday("2024-02-29"), "2024-02-26");  // 윤년
assert.strictEqual(monday("2026-01-01"), "2025-12-29");  // 해를 넘는 주
// HEX 검사, 알파
for (const ok of ["#fff", "#FFF", "#11706f", "#11706F"]) assert(HEX.test(ok), ok);
for (const bad of ["fff", "#ff", "#ffff", "#12345", "#1234567", "#ggg", "red", ""]) assert(!HEX.test(bad), bad);
assert.strictEqual(hex6("#abc"), "#aabbcc");
assert.strictEqual(alpha("#abc", 0.5), "rgba(170, 187, 204, 0.5)");
// 날짜 계산
assert.strictEqual(shiftDay("2026-03-01", -1), "2026-02-28"); assert.strictEqual(shiftDay("2024-03-01", -1), "2024-02-29");
assert.strictEqual(daysBetween("2026-01-12", "2026-02-02"), 22);
// 읽은 날짜 범위(window) 밖으로 보는 기간이 나가면 다시 읽는다
const win = { data: { window: { start: "2025-12-21", end: "2026-02-02" } } };
assert(!outsideWindow(win, { start: "2026-01-12", end: "2026-02-02" }) && outsideWindow(win, { start: "2025-12-01", end: "2026-02-02" }) && outsideWindow(win, { start: "2026-01-12", end: "2026-03-01" }));
assert(!outsideWindow({ data: {} }, { start: "2020-01-01", end: "2030-01-01" }) && !outsideWindow(win, null) && !outsideWindow(win, { start: null, end: "2026-01-01" }));  // 범위가 없으면 해당 없음
console.log("ok");
"""

# 대시보드 카드의 순수 계산(web/lib/calc.ts). node가 TS의 타입만 지워 그대로 돌린다(옛 static/dashboard 화면의 같은 본문을 옮긴 것).
PURE = """
// 카드 모양 계산(web/lib/calc.ts): 호·고리·게이지 조각·칸·블록·교차·다각형·값 글자
const near = (a, b) => assert(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
const [px, py] = polar(0, 0, 10, Math.PI / 2); near(px, 10); near(py, 0);
assert.deepStrictEqual(polar(0, 0, 10, 0), [0, -10]);
assert.match(arcPath(0, 0, 10, 0, Math.PI / 2), /^M 0[.]00 -10[.]00 A 10 10 0 0 1 10[.]00 -?0[.]00$/);
assert.match(arcPath(0, 0, 10, 0, Math.PI * 1.5), / A 10 10 0 1 1 /);  // 반 바퀴를 넘으면 큰 호
assert.strictEqual(ringDash(10, 0.25).dash, "15.71 47.12"); assert.strictEqual(ringDash(10, 2).len, ringDash(10, 1).circ); assert.strictEqual(ringDash(10, -1).len, 0);
const segs = gaugeSegments([1, 1, 2, 0, null]);
assert.deepStrictEqual(segs.map(g => [g.index, g.share, g.max]), [[0, 0.25, false], [1, 0.25, false], [2, 0.5, true]]);  // 몫대로, 0·빈 값은 뺌, 가장 큰 항목에 max
near(segs[1].a0, segs[0].a1 + RING_GAP); near(segs[2].a1 + RING_GAP, TAU);  // 조각 사이 간격 0.06rad, 한 바퀴를 채운다
assert.deepStrictEqual(gaugeSegments([0, null]), []);
assert.deepStrictEqual(stripeCells(0.34, 10, 0.5), { filled: 3, target: 5 }); assert.deepStrictEqual(stripeCells(1.7, 10, null), { filled: 10, target: null }); assert.strictEqual(stripeCells(0, 10, 0.01).target, 1);
assert.deepStrictEqual(blockCells(45, 80, 10), { actual: 4, toTarget: 4, over: 0, scale: 1 });
assert.deepStrictEqual(blockCells(95, 80, 10), { actual: 9, toTarget: 0, over: 1, scale: 1 });  // 목표를 넘은 칸
assert.deepStrictEqual(blockCells(10000, null, 1, 200), { actual: 200, toTarget: 0, over: 0, scale: 50 });  // 칸이 너무 많으면 줄인다
assert.strictEqual(crossDown([5, 3, 6, 2, null, 1], 4), 2); assert.strictEqual(crossDown([1, 2, 3], 4), 0); assert.strictEqual(crossDown([4, 3], 4), 1);  // 목표와 같으면 아직 안 내려간 것
const yOf = v => 100 - v * 10;
assert.deepStrictEqual(belowPolygons([0, 10, 20, 30], [6, 2, 2, 6], 4, yOf), [[[5, 60], [10, 80], [20, 80], [25, 60]]]);  // 목표선(y=60)과 선분이 만나는 곳에서 시작·끝
assert.deepStrictEqual(belowPolygons([0, 10], [2, 6], 4, yOf), [[[0, 60], [0, 80], [5, 60]]]);  // 처음부터 아래
assert.deepStrictEqual(belowPolygons([0, 10], [5, 6], 4, yOf), []);
assert.strictEqual(sparkPoints([1, 2, 3], 10, 10, 0), "0.0,10.0 5.0,5.0 10.0,0.0"); assert.strictEqual(sparkPoints([2, null, 2], 10, 10, 0), "0.0,5.0 10.0,5.0");
assert.strictEqual(showValue(null), "—"); assert.strictEqual(showValue(0.256, "percent"), "25.6%"); assert.strictEqual(showValue(1234.5, "currency"), "₩1,235"); assert.strictEqual(showValue(1234.567), "1,234.57"); assert.strictEqual(showValue(3, "number", "건"), "3건");
assert.strictEqual(showGap(-0.017, "percent"), "1.7%p"); assert.strictEqual(showGap(-1500, "currency"), "₩1,500");
assert.strictEqual(darken("#ffffff", 0.5), "#808080"); assert.strictEqual(darken("#fff", 0), "#ffffff");
// 조건 색 규칙(dash_rules.js): 숫자·단어 식, 맞는 칸만, 위의 규칙 하나, 오류 문장, 화살표
const m = (text, v) => { const f = parseMatch(text); assert(!f.error, text + ": " + f.error); return f.test(v); };
assert(m("= 0", 0) && !m("= 0", 1) && m("> 100", 101) && !m("> 100", 100) && m(">= 1,000", 1000) && !m(">= 1,000", 999) && m("<= 5", 5) && m("!= 5", 4));
assert(m("< -5%", -0.06) && !m("< -5%", -0.05) && m("10 ~ 20", 10) && m("10 ~ 20", 20) && !m("10 ~ 20", 21) && m("20 ~ 10", 15));  // %는 비율, 사이는 양끝 포함
assert(m("1, 3, 7", 3) && !m("1, 3, 7", 4) && m("1,000", 1000) && !m("1,000", 1));  // 목록, 1,000은 천 단위 숫자 하나
assert(m("10, 200", 200) && !m("10, 200", 10200) && m("100, 200", 100) && m("₩1,000, ₩2,000", 2000) && !m("₩1,000, ₩2,000", 1) && m("1,3,7", 7));  // 쉼표+공백 목록은 숫자 하나로 붙여 읽지 않는다
assert(m("취소", "취소") && !m("취소", "취소선") && m("취소, 환불", "환불") && !m("취소, 환불", "배송") && m("취소", "취소".toLowerCase()));
assert(m("*지연*", "a지연b") && !m("*지연*", "정상") && m("ERR*", "err001") && !m("ERR*", "xerr") && m("*실패", "로그인 실패") && !m("*실패", "실패 처리"));  // 포함·시작·끝, 대소문자 무시
for (const [bad, why] of [["", "식을 적어"], ["> abc", "숫자가 아니에요"], ["10 ~ x", "양쪽은 숫자"], ["*", "앞뒤에만"], ["a*b*", "앞뒤에만"], [">", "숫자가 아니에요"]]) assert.match(parseMatch(bad).error, new RegExp(why), bad);
assert.strictEqual(ruleNumber("₩1,234.5"), 1234.5); assert.strictEqual(ruleNumber("70%"), 0.7); assert.strictEqual(ruleNumber("1,3"), null); assert.strictEqual(ruleNumber("abc"), null);
const rules = [{ column: "상태", match: "*지연*", color: "#e03131", arrow: "none", scope: "row" }, { column: "금액", match: "> 100", color: "#2b8a3e", arrow: "auto", scope: "cell" }, { column: "금액", match: "> 10", color: "#f08c00", arrow: "up", scope: "cell" },
               { column: "금액", match: "취소", color: "#000000", arrow: "none", scope: "cell" }, { column: "건수", match: "= 0", color: "red", arrow: "none", scope: "cell" }];
const compiled = compileRules(rules, { 상태: "word", 금액: "num", 건수: "num" });
assert.deepStrictEqual(compiled.map(c => !!c.error), [false, false, false, true, true]);  // 숫자 칸에 단어 식, 틀린 HEX는 오류
assert.match(compiled[3].error, /숫자 칸이라 단어 식은 쓸 수 없어요/); assert.match(compiled[4].error, /HEX가 아니에요/);
assert.match(compileRule({ column: "상태", match: "> 1", color: "#fff" }, { 상태: "word" }).error, /글자 칸이라 숫자 식은 쓸 수 없어요/);
assert.strictEqual(matchRule(compiled, "금액", 150), rules[1]);  // 여러 규칙이 맞으면 위의 규칙 하나
assert.strictEqual(matchRule(compiled, "금액", 50), rules[2]); assert.strictEqual(matchRule(compiled, "금액", 5), null);  // 맞지 않는 칸은 그대로
assert.strictEqual(matchRule(compiled, "상태", "배송 지연"), rules[0]); assert.strictEqual(matchRule(compiled, "상태", 7), null);  // 숫자 값에는 단어 식이 맞지 않는다
assert.strictEqual(matchRule(compiled, "건수", 0), null); assert.strictEqual(matchRule(compiled, "금액", null), null); assert.strictEqual(matchRule(compiled, "없는 컬럼", 1), null);
assert.deepStrictEqual(ruleArrow(rules[1], 150, 100), { text: "▲", color: "#2b8a3e", tone: "good" }); assert.strictEqual(ruleArrow(rules[1], 150, 100, "down").tone, "bad");
assert.deepStrictEqual(ruleArrow(rules[1], 80, 100), { text: "▼", color: "#2b8a3e", tone: "bad" }); assert.deepStrictEqual(ruleArrow(rules[1], 100, 100), { text: "=", color: null, tone: "neutral" }); assert.deepStrictEqual(ruleArrow(rules[1], 0.1201, 0.12, "up", x => x.toFixed(2)), { text: "=", color: null, tone: "neutral" });
assert.strictEqual(ruleArrow(rules[1], 150, null), null); assert.strictEqual(ruleArrow(rules[0], 1, 0), null); assert.strictEqual(ruleArrow(rules[2], 1, 5).text, "▲"); assert.strictEqual(ruleArrow({ ...rules[2], arrow: "down", color: "" }, 1, 5).color, null);
assert.deepStrictEqual([kindOfValues([null, "", 3]), kindOfValues(["a"]), kindOfValues([null])], ["num", "word", null]);
assert.deepStrictEqual(parseRuleLine("상태 | *지연* | #e03131 | 자동 | 줄").rule, { column: "상태", match: "*지연*", color: "#e03131", arrow: "auto", scope: "row" });
assert.strictEqual(parseRuleLine("금액 | > 1 | #fff").rule.arrow, "none");
for (const [bad, why] of [["금액 | > 1", "모양으로"], [" | > 1 | #fff", "컬럼 이름"], ["금액 | > 1 | #fff | 옆", "화살표"], ["금액 | > 1 | #fff | 위 | 열", "적용 범위"], ["금액 | > x | #fff", "숫자가 아니에요"], ["금액 | > 1 | 빨강", "HEX"]]) assert.match(parseRuleLine(bad).error, new RegExp(why), bad);
assert.deepStrictEqual(parseStateLine("cpu >= 90 이상").rule, { column: "cpu", op: ">=", value: 90, level: "bad" }); assert.deepStrictEqual(parseStateLine("상태 = DOWN 주의").rule, { column: "상태", op: "=", value: "DOWN", level: "warn" });
assert.match(parseStateLine("cpu 90").error, /모양으로/); assert.match(parseStateLine("cpu > 9 심각").error, /단계는 주의나 이상/);
assert.deepStrictEqual(parseLinkLine("a -> b").link, ["a", "b"]); assert.deepStrictEqual(parseLinkLine("라우터 → 서버 1").link, ["라우터", "서버 1"]); assert.match(parseLinkLine("a b").error, /출발/);
assert.deepStrictEqual(readLines(["a -> b", "", "나쁨", "c -> d"].join(String.fromCharCode(10)), parseLinkLine).map(r => r.link ?? r.error.slice(0, 2)), [["a", "b"], "‘출", ["c", "d"]]);
assert.deepStrictEqual(templateParts("오늘 {매출}은 {비교}"), [{ text: "오늘 " }, { blank: "매출" }, { text: "은 " }, { blank: "비교" }]);
// 배치도 처음 배치(종류별 줄), 편차 칸, 고유 개수의 기간 값
assert.deepStrictEqual(autoGrid([{ name: "a", kind: "서버" }, { name: "b", kind: "서버" }, { name: "c", kind: "스위치" }, { name: "d" }]), { a: [1 / 3, 1 / 4], b: [2 / 3, 1 / 4], c: [1 / 2, 2 / 4], d: [1 / 2, 3 / 4] });  // 종류마다 한 줄, 줄 안에서 고르게
assert.deepStrictEqual(autoGrid([]), {});
const dev = deviationCells([10, 20, null, 30], null, null);  // 기준 = 평균 20, 허용 폭 = 기준의 20% = 4
near(dev.base, 20); assert.deepStrictEqual(dev.cells.map(c => [c.v, c.dev, c.over]), [[10, -10, true], [20, 0, false], [null, null, false], [30, 10, true]]);
assert.deepStrictEqual(deviationCells([10, 12], 10, 3).cells.map(c => c.over), [false, false]); assert.deepStrictEqual(deviationCells([10, 14], 10, 3).cells.map(c => c.over), [false, true]); assert.strictEqual(deviationCells([null], null, null).base, null);
console.log("ok");
"""


LIB = (ROOT / "static" / "core" / "lib" / "dash_calc.js").read_text(encoding="utf-8")  # 분석 화면이 부르는 순수 계산
CALC = (ROOT / "web" / "lib" / "calc.ts").as_uri()  # 대시보드 화면(Next)의 순수 계산


def run_ts(tmp_path, body):
    """web/lib/calc.ts의 export를 전역에 풀어 놓고 body를 돌린다(node가 TS 타입만 지운다). 마지막 줄 출력을 돌려준다."""
    script = tmp_path / "calc_test.mjs"
    script.write_text(f"import assert from 'assert';\nObject.assign(globalThis, await import({json.dumps(CALC)}));\n" + body, encoding="utf-8")
    done = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout.strip().splitlines()[-1]


def test_dash_js(tmp_path):
    """분석 화면의 순수 계산(lib/dash_calc.js): 주의 월요일, 색 글자, 날짜 계산, 읽은 범위."""
    script = tmp_path / "dash_test.js"
    script.write_text(LIB + SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]


def test_calc_ts(tmp_path):
    """대시보드 카드의 모양 계산·조건 색 규칙·줄 읽기(web/lib/calc.ts)."""
    assert run_ts(tmp_path, PURE) == "ok"


OWN_CASES = [["day 2026-01-12 ~ 2026-02-02"], ["day 2026-01-01 ~ ", "day ~ 2026-03-31"], ["day 2026-01-01 ~ ", "when 2026-03-01 ~ 2026-03-31"], ["day 2026-03-31 ~ 2026-03-01"],
             ["day 2026-02-30 ~ 2026-03-01", "day 2026-03-01 ~ 2026-03-01"], ["  day   2026-12-31~2027-01-02 "], ["day 2026-01-01 00:00 ~ 2026-01-02"], [], ["엉터리"]]


def test_own_range_same_in_python_and_js(tmp_path):
    """dash_calc.own_range(서버)와 화면의 ownRange(쿼리·분석 화면의 lib/dash_calc.js, 대시보드의 web/lib/calc.ts)가 같은 입력에서 같은 기간·직전 기간을 낸다(열린 줄, 여러 줄, 거꾸로 된 줄, 없는 날, 기간 줄 없음)."""
    from dashboard import dash_calc
    want = []
    for periods in OWN_CASES:
        got = dash_calc.own_range({"source": {"body": {"periods": periods}}})
        want.append(got and {"range": got["range"], "prev": got["prev"]})
    cases = "const cases = %s;\nconsole.log(JSON.stringify(cases.map(p => ownRange({ source: { body: { periods: p } } }))));\n" % json.dumps(OWN_CASES, ensure_ascii=False)
    assert json.loads(run_ts(tmp_path, cases)) == want
    script = tmp_path / "own_test.js"
    script.write_text(LIB + cases, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == want
    assert want[0]["prev"]["text"] == "직전 22일" and want[1] is None and want[2] is not None and want[4]["range"]["start"] == "2026-03-01" and want[7] is None
