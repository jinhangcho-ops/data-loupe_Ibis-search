// Phase 20(plans/phase-20.md) 묶음 D: 통화 기호 설정(currency)과 코호트 카드의 진행 중인 마지막 기간(cohort_partial).
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { compileRules, currencySymbol, parseMatch, ruleNumber, setCurrency, showGap, showValue } from "@/lib/calc";
import { PAINTERS } from "@/components/cards/SvgCards";
import type { P } from "@/components/cards/svg/common";
import type { Card, CardData } from "@/lib/types";

const src = (path: string) => readFileSync(fileURLToPath(new URL("../" + path, import.meta.url)), "utf-8");

describe("currency: 값 글자와 조건 색 규칙이 설정 기호를 쓴다", () => {
  afterEach(() => setCurrency(""));
  it("showValue·showGap이 setCurrency로 받은 기호를 부호 뒤에 붙인다", () => {
    for (const symbol of ["₩", "$", "€", "US$", ".*(", "<b>&"]) {
      setCurrency(symbol);
      expect(currencySymbol()).toBe(symbol);
      expect(showValue(1234.5, "currency")).toBe(symbol + "1,235");
      expect(showValue(-1234.5, "currency", "원")).toBe("-" + symbol + "1,235원");
      expect(showGap(-1500, "currency")).toBe(symbol + "1,500");
      expect(showValue(1234.567)).toBe("1,234.57");  // 다른 값 모양은 그대로
    }
  });
  it("ruleNumber가 맨 앞의 설정 기호만 뗀다(기호의 정규식 글자는 글자 그대로)", () => {
    for (const symbol of ["$", "€", "US$", ".*(", "^", "\\", "[a]"]) {
      setCurrency(symbol);
      expect(ruleNumber(symbol + "1,234.5")).toBe(1234.5);
      expect(ruleNumber("-" + symbol + " 500")).toBe(-500);
      expect(ruleNumber(symbol + "-500")).toBe(-500);
      expect(ruleNumber("70%")).toBe(0.7);
      expect(ruleNumber("1,3")).toBeNull();
      expect(ruleNumber("₩100")).toBeNull();  // 설정한 기호가 아니면 숫자가 아니다
      expect(ruleNumber("1" + symbol + "2")).toBeNull();  // 숫자 사이의 기호는 떼지 않는다
    }
    setCurrency(".");
    expect(ruleNumber("1.5")).toBe(1.5);  // 기호가 소수점과 같아도 숫자 안은 건드리지 않는다
    setCurrency("$");
    expect(ruleNumber("abc")).toBeNull();
  });
  it("조건 색 규칙의 식이 설정 기호가 붙은 숫자를 읽는다", () => {
    setCurrency("$");
    const between = parseMatch("$1,000 ~ $2,000"), list = parseMatch("$1,000, $2,000");
    expect(between.kind).toBe("num");
    expect(between.test!(1500) && !between.test!(2500)).toBe(true);
    expect(list.test!(2000) && !list.test!(1)).toBe(true);
    expect(parseMatch(">= $1,000").test!(1000)).toBe(true);
    expect(compileRules([{ column: "amt", match: "> $10", color: "#112233", arrow: "none", scope: "cell" }])[0].error).toBe("");
  });
  it("상수를 받으면 기호를 정하고, 설정 창에 칸이 있다(기본값·글자 수는 서버 상수)", () => {
    expect(src("lib/constants.tsx")).toContain("setCurrency(c.currency_symbol)");
    const dialog = src("components/shell/SettingsDialog.tsx");
    expect(dialog).toContain("saveSetting({ currency_symbol: symbol })");
    expect(dialog).toContain("consts?.defaults.currency_symbol");
    expect(dialog).toContain("maxLength={consts?.currency_max}");
    expect(src("lib/calc.ts")).not.toMatch(/roundText\([^)]*"₩"\)|\/₩/);  // 화면 계산에 기호를 박지 않는다
  });
});

describe("cohort_partial: 진행 중인 마지막 기간의 칸을 흐리게", () => {
  const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111"] };
  const draw = (data: CardData) => {
    const card = { id: "c", kind: "cohort", title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
      view: { type: "", unit: "week", compare: { mode: "span" }, better: "up" }, source: { body: {} }, data } as unknown as Card;
    return renderToStaticMarkup(createElement(PAINTERS.cohort as ComponentType<P>, { card, colors: THEME, range: undefined, merge: [] }));
  };
  const cohorts = [{ start: "2026-01-05", size: 2, retained: [1, 0.5] }, { start: "2026-01-12", size: 1, retained: [1] }];
  it("partial이면 코호트마다 마지막 칸만 흐리다", () => {
    const html = draw({ values: { periods: [0, 1], cohorts, partial: true }, note: "마지막 기간은 진행 중이라 낮게 보여요." });
    expect(html.match(/opacity:/g)).toHaveLength(2);
    expect(html).toMatch(/2026-01-05 코호트, 1: 50\.0% \(1명\) · 진행 중인 기간"[^>]*opacity:0\.45/);
    expect(html).toMatch(/2026-01-12 코호트, 0: 100\.0% \(1명\) · 진행 중인 기간"[^>]*opacity:0\.45/);
    expect(html).toContain('title="2026-01-05 코호트, 0: 100.0% (2명)"');  // 다 지난 기간은 그대로
  });
  it("partial이 없는 저장된 옛 카드는 지금처럼", () => {
    const html = draw({ values: { periods: [0, 1], cohorts } });
    expect(html).not.toContain("opacity");
    expect(html).not.toContain("진행 중");
    expect(html).toContain('title="2026-01-05 코호트, 1: 50.0% (1명)"');
  });
});
