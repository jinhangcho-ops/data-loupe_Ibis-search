// Phase 16(plans/phase-16.md) 순수 함수: 카드 추가 서랍의 종류 단추 묶음(kindGroups). 새 함수라 스켈레톤 상태에서는 실패한다.
import { describe, expect, it } from "vitest";
import { CARD_KINDS, kindGroups } from "@/components/edit/menu/kinds";

describe("kindGroups: 카드 모음의 묶음 차례, 맨 앞은 차트", () => {
  it("묶음 이름과 차례", () => {
    expect(kindGroups().map(g => g[0])).toEqual(["차트", "값·목표", "표·글", "모니터링", "분포·구성", "변화·비교", "흐름", "지도"]);
  });
  it("차트 묶음은 chart 하나, 값·목표는 카드 모음 차례 그대로", () => {
    const g = Object.fromEntries(kindGroups());
    expect(g["차트"]).toEqual(["chart"]);
    expect(g["값·목표"]).toEqual(["kpi", "donut_kpi", "gauge", "summary", "stripes", "blocks", "target_trend", "dual_trend"]);
    expect(g["지도"]).toEqual(["geomap"]);
  });
  it("모든 카드 종류가 한 번씩만", () => {
    const all = kindGroups().flatMap(g => g[1]);
    expect(all.length).toBe(new Set(all).size);
    expect([...all].sort()).toEqual(Object.keys(CARD_KINDS).sort());
  });
});
