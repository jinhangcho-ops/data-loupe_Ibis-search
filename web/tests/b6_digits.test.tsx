// B6 소수 자릿수 칸(plans/dash-blocks.md "B6 세부 명세"): 값 글자(showValue·roundText·showGap), 비교 요청(compareItem), 목록 줄 글자(listRows),
// 설정 칸(PlacesField), 카드 추가 메뉴(buildCard·formFromCard). 데이터는 지어낸 것.
// it.fails: 기존 함수를 고쳐야 통과하는 시험(주석의 마지막 항목 절이 끝나면 그 strict-coder가 it로 바꾼다). PlacesField(새 함수) 시험은 표시 없음.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { listRows, type ListCol } from "@/components/cards/svg/List";
import { buildCard, emptyForm, formFromCard, FORM_KEYS, type Form } from "@/components/edit/menu/build";
import { PlacesField } from "@/components/edit/menu/Fields";
import { roundText, showGap, showValue } from "@/lib/calc";
import { compareItem } from "@/lib/series";
import type { Card, ListView, PrevSpan } from "@/lib/types";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const RANGE = { min: 0, max: 6 };  // 사용자 결정(B6): 자릿수 0~6(서버 dash.digits와 같은 값)
const MERGE = ["count", "sum", "mean", "min", "max"];
const DAYS = ["2026-01-01", "2026-01-02"];
const daily = (view: Record<string, unknown>) => ({
  id: "c1", kind: "kpi", title: "값", view: { compare: { mode: "span" }, better: "up", ...view },
  data: { by_unit: { day: { labels: DAYS, series: [{ name: "합계 (v)", agg: "sum", sum: [1, 2], count: [1, 1], min: [1, 2], max: [1, 2] }] } } },
}) as unknown as Card;
const NOW = { start: DAYS[1], end: DAYS[1] }, BEFORE = { start: DAYS[0], end: DAYS[0], text: "전일" } as unknown as PrevSpan;

describe("값 글자", () => {
  // 마지막 항목: B6 calc (수정)
  it("showValue·roundText·showGap 자릿수", () => {
    expect(showValue(12.5, "number", "", { digits: 2, zeros: true })).toBe("12.50");
    expect(showValue(12.5, "number", "", { digits: 2 })).toBe("12.5");
    expect(showValue(1234.5, "number", "", { digits: 0 })).toBe("1,235");
    expect(showValue(0.12345, "percent", "", { digits: 1 })).toBe("12.3%");
    expect(showValue(1234.5, "currency", "", { digits: 2, zeros: true }).endsWith("1,234.50")).toBe(true);
    expect(showValue(3, "number", "명", { digits: 1, zeros: true })).toBe("3.0명");
    expect(roundText(-0.004, 2, "", true)).toBe("0.00");
    expect(roundText(12.5, 2, "", true)).toBe("12.50");
    expect(showGap(0.0123, "percent", { digits: 2 })).toBe("1.23%p");
    expect(showGap(-2.5, "number", { digits: 1, zeros: true })).toBe("2.5");
  });
  it("비우면 지금 규칙 그대로", () => {
    expect(showValue(1.005)).toBe("1.01");
    expect(showValue(12.5, "number", "", {})).toBe("12.5");
    expect(showValue(0.125, "percent")).toBe("12.5%");
    expect(showValue(1234.5, "currency").endsWith("1,235")).toBe(true);
    expect(roundText(2.5, 0)).toBe("3");
    expect(showGap(0.0123, "percent")).toBe("1.2%p");
    expect(showGap(-2.25)).toBe("2.25");
  });
});

describe("비교 요청", () => {
  // 마지막 항목: B6 compareItem (수정)
  it("compareItem은 자릿수를 정한 카드만 fmt·digits를 보낸다", () => {
    const item = compareItem(daily({ format: "percent", digits: 1, zeros: true }), NOW, BEFORE, MERGE)!;
    expect(item).toMatchObject({ fmt: "percent", digits: 1, zeros: true });
    const noZeros = compareItem(daily({ digits: 0 }), NOW, BEFORE, MERGE)!;
    expect(noZeros).toMatchObject({ fmt: "number", digits: 0 });
    expect("zeros" in noZeros).toBe(false);
  });
  it("자릿수를 비운 카드는 지금 그대로 보낸다", () => {
    const item = compareItem(daily({ format: "percent" }), NOW, BEFORE, MERGE)!;
    expect(item).not.toBeNull();
    expect("fmt" in item || "digits" in item || "zeros" in item).toBe(false);
  });
});

describe("목록·카드 그림", () => {
  const cols: ListCol[] = [{ name: "g", kind: "group" }, { name: "v", kind: "value" }];
  const list = (part: Record<string, unknown>) => ({ parts: [{ kind: "number", column: "v", swap: false, format: "number", rules: [], basis: "max", side: "end", ...part }], pick: "none", key: "", swaps: [], report: "screen" }) as unknown as ListView;
  it("listRows가 줄 부분의 자릿수를 따른다", () => {
    const rows = listRows(cols, [["a", "b"], [12.5, 3]], list({ digits: 2, zeros: true }), 0);
    expect(rows.map(r => r.cells[0].text)).toEqual(["12.50", "3.00"]);
  });
  it("listRows: 자릿수가 없으면 지금 그대로", () => {
    const rows = listRows(cols, [["a", "b"], [12.5, 3]], list({}), 0);
    expect(rows.map(r => r.cells[0].text)).toEqual(["12.5", "3"]);
  });
  it("카드 파일이 places를 넘긴다", () => {
    expect(src("components/cards/svg/Tile.tsx")).toContain('showValue(v, fmt, "", card.view)');
    expect(src("components/cards/svg/Sentence.tsx")).toContain('showValue(value, b.format, "", view)');
    expect(src("components/cards/svg/List.tsx")).toContain('showValue(v, p.format, "", p)');
    expect(src("components/cards/svg/TargetTrend.tsx")).toContain("showGap(now - target, fmt, card.view)");
    expect(src("components/cards/svg/Kpi.tsx")).toContain('showValue(v, fmt, "", view)');
    expect(src("components/cards/svg/Kpi.tsx")).toContain("showValue(pair.num)");  // 짝 절대량은 그대로
  });
});

describe("설정 칸", () => {
  const draw = (value: { digits?: number; zeros?: boolean }) => renderToStaticMarkup(<PlacesField value={value} range={RANGE} onChange={() => undefined} />);
  it("PlacesField: 비우면 체크 칸이 꺼지고 흐리다", () => {
    const html = draw({});
    expect(html).toContain("소수 자릿수");
    expect(html).toContain("비움(지금 규칙)");
    for (let d = RANGE.min; d <= RANGE.max; d++) expect(html).toContain(`<option value="${d}">${d}</option>`);
    expect(html).toContain("끝자리 0 채우기");
    expect(html).toMatch(/type="checkbox"[^>]*disabled=""/);
    expect(html).not.toMatch(/type="checkbox"[^>]*checked=""/);
    expect(html).toContain("소수 자릿수를 고르면 쓸 수 있어요.");
  });
  it("PlacesField: 자릿수가 있으면 체크 칸을 쓴다", () => {
    const html = draw({ digits: 2, zeros: true });
    expect(html).toMatch(/type="checkbox"[^>]*checked=""/);
    expect(html).not.toMatch(/type="checkbox"[^>]*disabled=""/);
  });
  it("Tools·ListPanel·AddCardMenu가 PlacesField를 그린다", () => {
    for (const p of ["components/edit/panel/Tools.tsx", "components/edit/panel/ListPanel.tsx", "components/edit/AddCardMenu.tsx"]) {
      expect(src(p)).toContain("<PlacesField");
      expect(src(p)).toContain("consts.dash.digits");
    }
  });
});

describe("카드 추가 메뉴", () => {
  const form = (places: Form["places"]): Form => ({ ...emptyForm("kpi"), table: "t", rows: [{ label: "", y: "v", agg: "sum" }], places });
  it("buildCard·formFromCard가 자릿수를 오간다", () => {
    const view = buildCard(form({ digits: 1, zeros: true }), ["sum"]).view!;
    expect([view.digits, view.zeros]).toEqual([1, true]);
    const plain = buildCard(form({}), ["sum"]).view!;
    expect("digits" in plain || "zeros" in plain).toBe(false);
    expect(formFromCard({ view: { digits: 3 } } as unknown as Card).places).toEqual({ digits: 3, zeros: undefined });
    expect(FORM_KEYS).toContain("digits");
    expect(FORM_KEYS).toContain("zeros");
    expect(emptyForm().places).toEqual({});
  });
});
