// Phase 9(plans/phase-9.md): 시각화 자동 정렬·렌더링 가드레일의 화면 규칙(web/lib/autoviz.ts).
// 새 함수가 스켈레톤이면 실패하는 것이 정상이다. 수치표 TABLE은 서버 core/viz_rules.AUTO_PARAMS를 JSON으로 옮긴 모양(시험 안에서 지어냄).
import { describe, expect, it } from "vitest";
import type { Dataset, SCard, Series } from "@/components/cards/chart/series";
import { shownum } from "@/lib/calc";
import {
  LABEL_FONT, autoEnv, autoParams, autoSeries, autoUnit, axisOrder, barDomain, calendarIndex, clipBox, csvText, currentWords, fromSymlog, hexVertices, isDiverging, labelKeys, levelIndex,
  lineDomain, logHint, lttb, needsTurn, othersName, percentDomain, pinTail, refValues, scatterTier, semanticTone, seriesTable, setWords, shortNum, spaghettiPick, stackIndex, tailKind,
  thinRows, toSymlog, topOthers, userIndex, valueIndex, y1Domain,
  type AutoEnv, type AutoTable, type SemanticWords,
} from "@/lib/autoviz";

// 서버 viz_rules.DEFAULT_WORDS와 같은 낱말(시험 안에서 지어냄, 사용자가 바꿀 수 있는 목록)
const WORDS: SemanticWords = {
  good: ["증가", "상승", "성장"], bad: ["감소", "이탈", "손실"], other: ["기타", "그 밖", "Others"], null: ["(빈 값)", "None"],
};

const TABLE = {
  sort: { group_by: ["", null] }, pin: {}, topn: { v: [15, 2, 50], h: [25, 2, 50] }, round: { max: [6, 2, 20], keep: [5, 1, 19] }, horizontal: { from: [10, 2, 50] },
  spaghetti: { max: [7, 2, 50], keep: [5, 1, 49], mode: ["gray", ["gray", "small"]] }, zero: {}, yzoom: { pad: [0.1, 0, 1] }, log_hint: { ratio: [100, 2, 1e6], skew: [2, 0, 100] },
  sync: {}, resample: { mode: ["lttb", ["lttb", "rollup"]], px: [1, 1, 20] },
  density: { faint: [1000, 1, 1e9], hex: [10000, 1, 1e9], alpha: [0.8, 0.05, 1], faint_alpha: [0.2, 0.05, 1], cols: [40, 5, 200] },
  num: { style: ["ko", ["ko", "en"]] }, refs: { lines: [["p80"], ["mean", "median", "p80"]] }, semantic: {},
  bins: { rule: ["fd", ["fd", "sturges"]], min: [10, 1, 100], max: [50, 1, 100] }, tail: { lo: [0.01, 0, 0.5], hi: [0.99, 0.5, 1] },
} as unknown as AutoTable;

const ds = (label: string, values: (number | null)[], extra: Partial<Dataset> = {}): Dataset => ({ label, values, ...extra });
const ser = (labels: string[], datasets: Dataset[], time = false): Series => ({ labels, datasets, time, chartable: true });
const card = (view: Record<string, unknown> = {}, extra: Record<string, unknown> = {}): SCard =>
  ({ id: "c1", kind: "chart", title: "t", layout: { x: 0, y: 0, w: 6, h: 6 }, view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view },
     source: { body: { series: [{ agg: "sum" }] } }, data: {}, ...extra }) as unknown as SCard;
const env = (over: Partial<AutoEnv> = {}): AutoEnv =>
  ({ width: 0, table: TABLE, aggs: ["sum"], rest: null, truncated: false, xKind: "text", scale: undefined, levels: undefined, order: "auto", weekStart: "mon", words: WORDS, ...over });
const names = (n: number) => Array.from({ length: n }, (_, i) => `l${String(i).padStart(2, "0")}`);

describe("수치표(autoParams)", () => {
  it("키가 없으면 기본 켬·기본 수치, false면 끔, 객체면 그 수치만 바꾼다", () => {
    const p = autoParams({ topn: { v: 10 }, pin: false }, TABLE);
    expect(p.topn).toEqual({ v: 10, h: 25 });
    expect(p.pin).toBeNull();
    expect(p.round).toEqual({ max: 6, keep: 5 });
    expect(p.refs).toEqual({ lines: ["p80"] });
    expect(autoParams(undefined, TABLE).sort).toEqual({ group_by: "" });
  });
});

describe("축 정렬(A)", () => {
  it("요일·월은 달력 순서, 섞이거나 모르는 이름이면 null, 빈 값은 맨 뒤", () => {
    expect(calendarIndex(["수", "월", "일", "화"], "mon")).toEqual([1, 3, 0, 2]);
    expect(calendarIndex(["수", "월", "일", "화"], "sun")).toEqual([2, 1, 3, 0]);
    expect(calendarIndex(["Feb", "jan", "Dec"], "mon")).toEqual([1, 0, 2]);
    expect(calendarIndex(["3월", "12월", "1월"], "mon")).toEqual([2, 0, 1]);
    expect(calendarIndex(["월", null, "화"], "mon")).toEqual([0, 2, 1]);
    expect(calendarIndex(["월", "1월"], "mon")).toBeNull();
    expect(calendarIndex(["서울", "월"], "mon")).toBeNull();
    expect(calendarIndex(["월"], "mon")).toBeNull();
  });
  it("순서형은 받은 단계 차례, 단계에 없는 값은 원래 차례로 뒤, 빈 값은 맨 뒤", () => {
    expect(levelIndex(["Gold", "Bronze", "x", "Silver"], ["Bronze", "Silver", "Gold"])).toEqual([1, 3, 0, 2]);
    expect(levelIndex([null, "Gold", "Bronze"], ["Bronze", "Gold"])).toEqual([2, 1, 0]);
  });
  it("값 큰 순(같으면 원래 차례), 빈 값은 맨 뒤", () => {
    expect(valueIndex([3, null, 5, 3])).toEqual([2, 0, 3, 1]);
  });
  it("라벨마다 열쇠: 합계 또는 대표 계열", () => {
    const d = [ds("A", [1, null, 3]), ds("B", [2, null, "4" as unknown as number])];
    expect(labelKeys(d, "")).toEqual([3, null, 7]);
    expect(labelKeys(d, "B")).toEqual([2, null, 4]);
    expect(() => labelKeys(d, "C")).toThrow("대표 계열");
  });
  it("누적 순서: 합(절댓값)이 큰 계열이 바닥(먼저)", () => {
    expect(stackIndex([ds("A", [1, 1]), ds("B", [5, -5]), ds("C", [3, null])])).toEqual([1, 2, 0]);
  });
  it("양수와 음수가 함께 있어야 양방향", () => {
    expect(isDiverging([3, -2, 0])).toBe(true);
    expect(isDiverging([3, 0, null])).toBe(false);
  });
  it("'기타'·빈 값은 맨 뒤(기타 다음 빈 값), 나머지 차례는 그대로", () => {
    expect([tailKind("기타", WORDS), tailKind("기타(외 3개)", WORDS), tailKind("None", WORDS), tailKind(null, WORDS), tailKind("", WORDS), tailKind("서울", WORDS)])
      .toEqual(["other", "other", "null", "null", "null", null]);
    expect(pinTail(["a", "None", "기타", "b"], [3, 2, 1, 0], WORDS)).toEqual([3, 0, 2, 1]);
  });
  it("낱말 목록은 사용자가 바꾼 것을 쓴다(기타(외 K개)는 목록과 상관없이 기타)", () => {
    const mine: SemanticWords = { good: [], bad: [], other: ["나머지"], null: ["미입력"] };
    expect([tailKind("나머지", mine), tailKind("기타", mine), tailKind("미입력", mine), tailKind("None", mine), tailKind("기타(외 2개)", mine)]).toEqual(["other", null, "null", null, "other"]);
  });
  it("사용자 정렬: 원래 순서·값 내림·값 오름·이름(숫자는 숫자 차례), 빈 값은 맨 뒤", () => {
    const s = ser(["b10", "a", "b2", "c"], [ds("v", [1, 3, 2, null])]);
    expect(userIndex(s, "x")).toEqual([0, 1, 2, 3]);
    expect(userIndex(s, "value")).toEqual([1, 2, 0, 3]);
    expect(userIndex(s, "value_asc")).toEqual([0, 2, 1, 3]);
    expect(userIndex(s, "name")).toEqual([1, 2, 0, 3]);
  });
  it("규칙 고르기: 시간·숫자·사용자 지정은 그대로, 달력·순서형·누적·묶은·양방향·명목형", () => {
    const e = env();
    expect(axisOrder(ser(["b", "a"], [ds("v", [1, 2])], true), e, "", "").rule).toBe("time");
    expect(axisOrder(ser(["2", "1"], [ds("v", [1, 2])]), { ...e, xKind: "num" }, "", "")).toEqual({ rule: "number", index: [0, 1], stack: null });
    expect(axisOrder(ser(["b", "a"], [ds("v", [1, 2])]), { ...e, order: "x" }, "", "")).toEqual({ rule: "user", index: [0, 1], stack: null });
    expect(axisOrder(ser(["b", "a", "c"], [ds("v", [1, 2, 3])], true), { ...e, xKind: "date", order: "value_asc" }, "", "").index).toEqual([0, 1, 2]);
    expect(axisOrder(ser(["b", "a", "c"], [ds("v", [3, 2, 1])], true), { ...e, xKind: "date", order: "value_asc" }, "", "")).toEqual({ rule: "user", index: [2, 1, 0], stack: null });
    expect(axisOrder(ser(["b", "a"], [ds("v", [1, 2])]), { ...e, xKind: undefined }, "", "").rule).toBe("user");
    expect(axisOrder(ser(["수", "월"], [ds("v", [9, 1])]), e, "", "")).toEqual({ rule: "calendar", index: [1, 0], stack: null });
    expect(axisOrder(ser(["Gold", "Bronze"], [ds("v", [9, 1])]), { ...e, scale: "ordinal", levels: ["Bronze", "Gold"] }, "", "").index).toEqual([1, 0]);
    expect(axisOrder(ser(["a", "b"], [ds("A", [1, 5]), ds("B", [9, 0])]), e, "stacked", "")).toEqual({ rule: "stack", index: [0, 1], stack: [1, 0] });
    expect(axisOrder(ser(["a", "b"], [ds("A", [1, 5]), ds("B", [9, 0])]), e, "group", "A")).toEqual({ rule: "grouped", index: [1, 0], stack: null });
    expect(axisOrder(ser(["a", "b", "c", "d"], [ds("v", [3, -2, 5, -7])]), e, "", "")).toEqual({ rule: "diverging", index: [2, 0, 1, 3], stack: null });
    expect(axisOrder(ser(["a", "b", "c"], [ds("v", [1, 3, 2])]), e, "", "")).toEqual({ rule: "nominal", index: [1, 2, 0], stack: null });
  });
});

describe("고카디널리티(B)", () => {
  it("라벨이 칸 폭보다 넓으면 꺾어야 한다(너비를 모르면 아님)", () => {
    const l = ["가나다라마", "가나다라마", "가나다라마", "가나다라마"];
    expect(needsTurn(l, 200, LABEL_FONT)).toBe(true);
    expect(needsTurn(l, 400, LABEL_FONT)).toBe(false);
    expect(needsTurn(l, 0, LABEL_FONT)).toBe(false);
  });
  it("상위 N + 기타: 큰 N개를 지금 차례 그대로, 합칠 수 있으면 기타 값, 없으면 빼기만", () => {
    const labels = ["a", "b", "c", "d", "e"], d = [ds("v", [5, 1, 4, 2, 3])];
    expect(othersName(2, false)).toBe("기타(외 2개)");
    expect(othersName(2, true)).toBe("기타(외 2개 이상)");
    expect(topOthers(labels, d, 3, ["sum"], null)).toEqual({ labels: ["a", "c", "e", "기타(외 2개)"], datasets: [ds("v", [5, 4, 3, 3])], cut: 2, merged: true });
    expect(topOthers(labels, d, 3, [null], null)).toEqual({ labels: ["a", "c", "e"], datasets: [ds("v", [5, 4, 3])], cut: 2, merged: false });
    expect(topOthers(labels, d, 3, ["sum"], [10]).labels.at(-1)).toBe("기타(외 2개 이상)");
    expect(topOthers(labels, d, 3, ["sum"], [10]).datasets[0].values.at(-1)).toBe(13);
    expect(topOthers(labels, d, 3, ["max"], null).datasets[0].values.at(-1)).toBe(2);
    expect(topOthers(labels, d, 5, ["sum"], null).datasets).toBe(d);
  });
  it("스파게티: 합(절댓값)이 큰 keep개만 색(원래 차례)", () => {
    expect(spaghettiPick([ds("a", [1]), ds("b", [9]), ds("c", [5]), ds("d", [-7])], 2)).toEqual([1, 3]);
  });
});

describe("축 범위·눈금(C)", () => {
  it("막대는 0을 포함한 아래 끝", () => {
    expect(barDomain([3, 5])).toBe(0);
    expect(barDomain([-2, 5, null])).toBe(-2);
    expect(barDomain([])).toBe(0);
  });
  it("선은 최소~최대에 여백, 모두 0 이상이면 0 아래로 내려가지 않는다", () => {
    expect(lineDomain([50, 60], 0.1)).toEqual({ domain: [49, 61], zero: false });
    const wide = lineDomain([1, 100], 0.1)!;
    expect(wide.domain[0]).toBe(0); expect(wide.domain[1]).toBeCloseTo(109.9); expect(wide.zero).toBe(true);
    expect(lineDomain([5, 5], 0.1)).toEqual({ domain: [4.5, 5.5], zero: false });
    expect(lineDomain([0, 0], 0.1)).toEqual({ domain: [-1, 1], zero: true });
    expect(lineDomain([null], 0.1)).toBeNull();
  });
  it("로그 축 제안: 최대가 중앙값의 ratio배 이상이거나 왜도가 skew 넘음, 음수·0이 있으면 SymLog", () => {
    expect(logHint([1, 1, 2, 2, 300], 100, 2)).toBe("log");
    expect(logHint([-5, 1, 2, 2, 300], 100, 2)).toBe("symlog");
    expect(logHint([1, 1, 1, 1, 1, 1, 1, 1, 1, 50], 100, 2)).toBe("log");
    expect(logHint([1, 2, 3, 4, 5], 100, 2)).toBeNull();
    expect(logHint([1, 500], 100, 2)).toBeNull();
  });
  it("SymLog 바꾸기와 되돌리기", () => {
    expect(toSymlog(9)).toBeCloseTo(1);
    expect(toSymlog(-99)).toBeCloseTo(-2);
    expect(toSymlog(0)).toBe(0);
    expect(fromSymlog(toSymlog(1234.5))).toBeCloseTo(1234.5);
  });
  it("보조 축 0~100%: 값이 모두 0~1이면 [0, 1]", () => {
    expect(percentDomain([0.2, 1, null])).toEqual([0, 1]);
    expect(percentDomain([0.2, 1.5])).toBeNull();
    expect(percentDomain([null])).toBeNull();
  });
  it("보조 축 범위: 비율이면 0~100% 고정, 사용자가 정한 범위(%)가 먼저, 비율이 아니면 null", () => {
    expect(y1Domain([0.5, 0.512], undefined)).toEqual([0, 1]);
    const mine = y1Domain([0.5, 0.512], { min: 50, max: 51.2 })!;
    expect(mine[0]).toBeCloseTo(0.5); expect(mine[1]).toBeCloseTo(0.512);
    expect(y1Domain([0.5, 0.512], { min: 40 })).toEqual([0.4, 1]);
    expect(y1Domain([2, 3], { min: 1 })).toBeNull();
  });
  it("상자 그림 보이는 범위: 그룹들의 P1~P99(상자는 늘 안에), 분위가 없으면 null", () => {
    expect(clipBox([{ q1: 2, q3: 5, p01: 1, p99: 9 }, { q1: 0, q3: 20, p01: 0.5, p99: 12 }])).toEqual({ lo: 0, hi: 20 });
    expect(clipBox([{ q1: 2, q3: 5 }])).toBeNull();
  });
});

describe("대용량·인코딩(D)", () => {
  it("LTTB: 처음·끝은 남기고 칸마다 넓이가 가장 큰 점", () => {
    expect(lttb([0, 1, 0, 5, 0, 1, 0], 3)).toEqual([0, 3, 6]);
    expect(lttb([1, 2, 3], 5)).toEqual([0, 1, 2]);
  });
  it("행 줄이기: 계열마다 고른 점을 합친다, 기준 이하이면 같은 배열", () => {
    const rows = [0, 1, 0, 5, 0, 1, 0].map((v, i) => ({ label: String(i), s0: v, s1: i === 1 ? 9 : 0 }));
    expect(thinRows(rows, ["s0", "s1"], 3).map(r => r.label)).toEqual(["0", "1", "3", "6"]);
    expect(thinRows(rows, ["s0"], 10)).toBe(rows);
    expect(thinRows(rows, ["s0"], null)).toBe(rows);
  });
  it("자동 묶기 단위: 너비(px / 점 간격)에 들어가는 가장 작은 단위", () => {
    const c = { day: 1000, week: 143, month: 33, year: 3 };
    expect(autoUnit(c, 200, 1)).toBe("week");
    expect(autoUnit(c, 20, 1)).toBe("year");
    expect(autoUnit(c, 2000, 1)).toBe("day");
    expect(autoUnit(c, 0, 1)).toBe("day");
  });
  it("산점도 3단계: 1,000 미만 · 1,000~10,000 · 넘으면 육각", () => {
    const p = autoParams(undefined, TABLE).density;
    expect(scatterTier(999, p)).toEqual({ mode: "dots", alpha: 0.8, r: 3 });
    expect(scatterTier(1000, p)).toEqual({ mode: "faint", alpha: 0.2, r: 2 });
    expect(scatterTier(10000, p)!.mode).toBe("faint");
    expect(scatterTier(10001, p)!.mode).toBe("hex");
    expect(scatterTier(50000, null)).toBeNull();  // 규칙을 끄면 지금 모양(Charts가 정한 투명도)
  });
  it("육각 꼭짓점: 위 꼭짓점부터 반시계, 반지름 w/√3", () => {
    const v = hexVertices(0, 0, Math.sqrt(3));
    expect(v).toHaveLength(6);
    expect(v[0][0]).toBeCloseTo(0); expect(v[0][1]).toBeCloseTo(1);
    expect(v[1][0]).toBeCloseTo(-Math.sqrt(3) / 2); expect(v[1][1]).toBeCloseTo(0.5);
  });
  it("숫자 줄이기: 한국식 만·억·조, 영어식 K·M·B, 끄면 그대로", () => {
    expect(shortNum(123456789, "ko")).toBe("1.2억");
    expect(shortNum(15000, "ko")).toBe("1.5만");
    expect(shortNum(10000, "ko")).toBe("1만");
    expect(shortNum(-15000, "ko")).toBe("-1.5만");
    expect(shortNum(100000000, "en")).toBe("100M");
    expect(shortNum(1234, "en")).toBe("1.2K");
    expect(shortNum(9999, "ko")).toBe(shownum(9999));
    expect(shortNum(123456789, null)).toBe(shownum(123456789));
  });
  it("참조선 값: 평균·중앙값", () => {
    expect(refValues([1, 2, 3, 10, null])).toEqual({ mean: 4, median: 2.5 });
    expect(refValues([null])).toBeNull();
  });
  it("의미 색: 기타·빈 값은 회색, 부정 낱말이 긍정보다 먼저", () => {
    const t = (n: string) => semanticTone(n, WORDS);
    expect([t("이탈 수"), t("매출 증가"), t("기타(외 3개)"), t("None"), t("서울"), t("감소 후 증가")]).toEqual(["bad", "good", "none", "none", null, "bad"]);
    expect(semanticTone("매출 증가", { ...WORDS, good: [] })).toBeNull();  // 사용자가 지운 낱말
  });
  it("낱말은 상수를 받으면 한 번 정해 두고 색 고르기가 읽는다", () => {
    setWords(WORDS);
    expect(currentWords()).toBe(WORDS);
  });
  it("차트 데이터 표와 CSV(수식 막기: = + - @ 탭 CR로 시작하는 글자 앞에 ')", () => {
    const tbl = seriesTable("지역", ser(["a,b", "=1", " -2"], [ds("v", [1, null, -3])]));
    expect(tbl).toEqual({ columns: ["지역", "v"], rows: [{ 지역: "a,b", v: 1 }, { 지역: "=1", v: null }, { 지역: " -2", v: -3 }] });
    expect(csvText(tbl)).toBe("지역,v\r\n\"a,b\",1\r\n'=1,\r\n' -2,-3");
  });
});

describe("한데 묶기(autoSeries·autoEnv)", () => {
  it("카드 값에서 축 종류·순서 정보·서버 기타 값·사용자 정렬을 읽는다", () => {
    const c = card({}, { data: { x_kind: "text", x_scale: "ordinal", x_levels: ["a", "b"], values: { labels: [], series: [], rest: [5] } } });
    (c.source as { body: Record<string, unknown> }).body.order = "value_asc";
    expect(autoEnv(c, 300, TABLE, "mon", WORDS)).toEqual({ width: 300, table: TABLE, aggs: ["sum"], rest: [5], truncated: true, xKind: "text", scale: "ordinal", levels: ["a", "b"], order: "value_asc", weekStart: "mon", words: WORDS });
    // 정렬이 없는 기존 카드는 지금 순서(x) 그대로(사용자 결정 1), 새 카드는 auto를 담아 만든다
    expect(autoEnv(card(), 0, TABLE, "sun", WORDS)).toMatchObject({ rest: null, truncated: false, order: "x", xKind: undefined, weekStart: "sun" });
    const fresh = card();
    (fresh.source as { body: Record<string, unknown> }).body.order = "auto";
    expect(autoEnv(fresh, 0, TABLE, "mon", WORDS).order).toBe("auto");
  });
  it("명목형 한 계열은 큰 순", () => {
    const r = autoSeries(card(), ser(["a", "b", "c"], [ds("v", [1, 3, 2])]), env());
    expect(r.s.labels).toEqual(["b", "c", "a"]);
    expect(r.card.auto.rule).toBe("nominal");
    expect(r.card.view.type).toBe("");
  });
  it("범주 10개 이상이면 가로 막대, 가로는 25개 + 기타", () => {
    const r = autoSeries(card(), ser(names(30), [ds("v", names(30).map((_, i) => i))]), env());
    expect(r.card.view.type).toBe("hbar");
    expect(r.s.labels).toHaveLength(26);
    expect(r.s.labels[0]).toBe("l29");
    expect(r.s.labels.at(-1)).toBe("기타(외 5개)");
    expect(r.s.datasets[0].values.at(-1)).toBe(10);
  });
  it("세로 막대는 15개, 합칠 수 없는 집계면 기타 없이 빼고 안내", () => {
    const c = card({ auto: { horizontal: false } });
    const r = autoSeries(c, ser(names(20), [ds("v", names(20).map((_, i) => i))]), env({ aggs: ["mean"] }));
    expect(r.s.labels).toHaveLength(15);
    expect(r.notes.join(" ")).toContain("5개");
  });
  it("규칙을 모두 끄면 받은 모양 그대로(같은 객체)", () => {
    const s = ser(names(30), [ds("v", names(30).map((_, i) => i))]);
    const r = autoSeries(card({ auto: { sort: false, pin: false, topn: false, horizontal: false } }), s, env());
    expect(r.s).toBe(s);
  });
  it("사용자 정렬이 자동 정렬보다 먼저", () => {
    const s = ser(["a", "b", "c"], [ds("v", [1, 3, 2])]);
    expect(autoSeries(card(), s, env({ order: "x" })).s.labels).toEqual(["a", "b", "c"]);
    expect(autoSeries(card(), s, env({ order: "value_asc" })).s.labels).toEqual(["a", "c", "b"]);
    expect(autoSeries(card(), ser(["수", "월"], [ds("v", [1, 2])]), env({ order: "name" })).card.auto.rule).toBe("user");
  });
  it("모든 데이터: 상위 N·기타·파이 상한을 풀고 전부(종류·정렬은 그대로)", () => {
    const r = autoSeries(card({ all_data: true }), ser(names(30), [ds("v", names(30).map((_, i) => i))]), env());
    expect(r.s.labels).toHaveLength(30);
    expect(r.s.labels[0]).toBe("l29");
    expect(r.card.view.type).toBe("hbar");
    expect(autoSeries(card({ type: "pie", all_data: true }), ser(names(8), [ds("v", [1, 2, 3, 4, 5, 6, 7, 8])]), env()).s.labels).toHaveLength(8);
    const line = ser(names(50).map((_, i) => `2026-01-${i}`), [ds("v", names(50).map((_, i) => i % 7), { type: "line" })], true);
    expect(autoSeries(card({ type: "line", all_data: true }), line, env({ xKind: "date", width: 10 })).card.auto.thin).toBeNull();
    expect(autoSeries(card({ type: "line" }), line, env({ xKind: "date", width: 10 })).card.auto.thin).toBe(10);
  });
  it("콤보 보조 축: 오른쪽 값이 비율이면 0~100%, 사용자가 정한 보조 축 범위(%)가 먼저", () => {
    const s = ser(["a", "b"], [ds("매출", [100, 200]), ds("전환율", [0.5, 0.512])]);
    expect(autoSeries(card({ type: "combo" }), s, env({ order: "x" })).card.auto.y1Pct).toEqual([0, 1]);
    const mine = autoSeries(card({ type: "combo", ranges: { y: {}, y1: { min: 50, max: 51.2 } } }), s, env({ order: "x" })).card.auto.y1Pct!;
    expect(mine[0]).toBeCloseTo(0.5); expect(mine[1]).toBeCloseTo(0.512);
  });
  it("파이는 6개 넘으면 큰 5개 + 기타, 12시부터 시계 방향", () => {
    const r = autoSeries(card({ type: "pie" }), ser(names(8), [ds("v", [1, 2, 3, 4, 5, 6, 7, 8])]), env());
    expect(r.s.labels).toEqual(["l07", "l06", "l05", "l04", "l03", "기타(외 3개)"]);
    expect(r.s.datasets[0].values.at(-1)).toBe(6);
    expect(r.card.auto.clockwise).toBe(true);
  });
  it("선이 7개 넘으면 큰 5개만 색, 나머지는 회색", () => {
    const d = Array.from({ length: 10 }, (_, k) => ds(`s${k}`, [k, k], { type: "line" }));
    const r = autoSeries(card({ type: "line" }), ser(["2026-01-01", "2026-01-02"], d, true), env({ xKind: "date" }));
    expect(r.card.auto.gray).toEqual([0, 1, 2, 3, 4]);
  });
  it("누적은 큰 계열이 바닥, 색 순서는 원래 계열 차례로 남는다(색이 정렬을 따라 바뀌지 않게)", () => {
    const r = autoSeries(card({ type: "stacked" }), ser(["a", "b"], [ds("A", [1, 1]), ds("B", [5, 5])]), env());
    expect(r.s.datasets.map(d => d.label)).toEqual(["B", "A"]);
    expect(r.card.colors.order).toEqual(["A", "B"]);
  });
  it("기타·빈 값은 큰 순이어도 맨 뒤", () => {
    expect(autoSeries(card(), ser(["None", "a", "기타", "b"], [ds("v", [9, 1, 8, 2])]), env()).s.labels).toEqual(["b", "a", "기타", "None"]);
  });
  it("요일은 달력 순서, 순서형은 받은 단계 순서", () => {
    expect(autoSeries(card(), ser(["수", "월", "화"], [ds("v", [1, 2, 3])]), env()).s.labels).toEqual(["월", "화", "수"]);
    expect(autoSeries(card(), ser(["Gold", "Bronze", "Silver"], [ds("v", [9, 1, 5])]), env({ scale: "ordinal", levels: ["Bronze", "Silver", "Gold"] })).s.labels).toEqual(["Bronze", "Silver", "Gold"]);
  });
  it("양방향은 큰 양수부터 작은 음수, 0 기준선", () => {
    const r = autoSeries(card(), ser(["a", "b", "c", "d"], [ds("v", [3, -2, 5, -7])]), env());
    expect(r.s.labels).toEqual(["c", "a", "b", "d"]);
    expect(r.card.auto.zeroLine).toBe(true);
  });
  it("선 그래프는 동적 Y축(0부터 아님 표시), 축 범위를 정했으면 쓰지 않는다", () => {
    const s = ser(["2026-01-01", "2026-01-02"], [ds("v", [50, 60], { type: "line" })], true);
    expect(autoSeries(card({ type: "line" }), s, env({ xKind: "date" })).card.auto).toMatchObject({ yDomain: [49, 61], notZero: true });
    expect(autoSeries(card({ type: "line", ranges: { y: { min: 0 }, y1: {} } }), s, env({ xKind: "date" })).card.auto.yDomain).toBeNull();
  });
  it("값 차이가 크면 로그 축을 안내만 한다(바꾸지 않음)", () => {
    const r = autoSeries(card(), ser(["a", "b", "c", "d", "e"], [ds("v", [1, 1, 2, 2, 300])]), env());
    expect(r.notes.join(" ")).toContain("로그");
    expect(r.card.view.scale).toBeUndefined();
  });
});
