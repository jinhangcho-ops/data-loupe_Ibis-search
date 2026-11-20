// 그림 색 3개: 표시색 하나의 진하기 단계 + 강조색. 고르는 색은 표시색·강조색 둘뿐(dash.palette = [표시색, 강조색]).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { HEX, shades } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { THEME_SERIES, markColors } from "@/components/cards/ChartCard";
import type { Card } from "@/lib/types";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const card = { id: "c", kind: "chart", view: { auto: { semantic: false } } } as unknown as Card;

describe("표시색의 진하기 단계", () => {
  it("hex 표시색은 서로 다른 hex 여섯 단계가 되고 첫 단계는 그 색 그대로", () => {
    const s = shades("#6250d6");
    expect(s[0]).toBe("#6250d6");
    expect(new Set(s).size).toBe(6);
    expect(s.every(c => HEX.test(c))).toBe(true);
    expect(s[1]).toBe("#a99fe8");  // 흰색과 55:45
    expect(s[2]).toBe("#453896");  // 검정과 70:30
  });
  it("화면 모드의 단계는 var(--mark)를 섞은 것이라 화면 모드를 따라간다", () => {
    expect(THEME_SERIES[0]).toBe("var(--mark)");
    expect(THEME_SERIES[1]).toBe("color-mix(in srgb, var(--mark) 55%, #ffffff)");
  });
});

describe("대시보드가 고른 두 색", () => {
  it("둘째 계열은 강조색이 아니라 표시색의 다음 단계", () => {
    const colors = markColors(["#6250d6", "#00968f"]);
    expect(pickColor(card, "series", "가", colors, 0)).toBe("#6250d6");
    expect(pickColor(card, "series", "나", colors, 1)).toBe("#a99fe8");
    expect(pickColor(card, "series", "사", colors, 6)).toBe("#6250d6");  // 단계를 넘으면 되풀이
  });
  it("고르지 않았으면 화면 모드의 단계", () => {
    expect(markColors([]).series).toBe(THEME_SERIES);
    expect(markColors(undefined).palette).toBeUndefined();
  });
  it("설정 창은 표시색·강조색 두 칸만 받는다", () => {
    const dialog = src("components/shell/SettingsDialog.tsx");
    expect(dialog).toContain('aria-label="표시색"');
    expect(dialog).toContain('aria-label="강조색"');
    expect(dialog).not.toContain("기본 색 넣기");
    expect(dialog).toContain("dispatch({ type: \"meta\", patch: { palette: found } });");
  });
  it("색 변수는 밝은·어두운 화면에 표시색·강조색이 하나씩", () => {
    const css = src("styles/common.css");
    expect(css.match(/--mark:#[0-9a-f]{6}; --hi:#[0-9a-f]{6};/g)).toHaveLength(2);
  });
});
