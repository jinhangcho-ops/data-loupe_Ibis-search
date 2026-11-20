// Phase 16(plans/phase-16.md) 순수 함수: 카드 추가 서랍의 종류 단추 묶음(kindGroups). 새 함수라 스켈레톤 상태에서는 실패한다.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Gallery } from "@/components/edit/Shelf";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { CARD_KINDS, TILE_PICS, kindGroups } from "@/components/edit/menu/kinds";

vi.mock("@/lib/constants", () => ({ useConstants: () => ({ dash: { types: { bar: "막대", polyline: "꺾인 선" } } }) }));

describe("Gallery: 그림으로 고르기(pics)와 카드 모음", () => {
  const html = (pics: boolean) => renderToStaticMarkup(createElement(Gallery, { pics, onAdd: () => {} }));
  it("그림으로 고르기는 예시가 있는 종류에 밝은·어두운 그림을, 없는 종류는 이름만", () => {
    const out = html(true);
    expect(out).toContain('src="/static/core/charts/bar.svg"');
    expect(out).toContain('src="/static/core/charts/bar-dark.svg"');
    expect(out).not.toContain("polyline.svg");
    expect(out).not.toContain("draggable");
  });
  it("카드 모음은 그림 없이 끌 수 있는 칸 그대로", () => {
    expect(html(false)).not.toContain("<img");
    expect(html(false)).toContain('draggable="true"');
  });
});

describe("TILE_PICS: 카드 모음 타일의 미리보기 그림", () => {
  it("이름마다 밝은·어두운 그림 파일이 있다", () => {
    const dir = join(__dirname, "../../static/core/charts");
    expect(TILE_PICS.flatMap(n => [`${n}.svg`, `${n}-dark.svg`]).filter(f => !existsSync(join(dir, f)))).toEqual([]);
  });
});

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
