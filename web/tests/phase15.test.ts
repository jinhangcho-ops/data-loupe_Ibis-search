// Phase 15(plans/phase-15.md) 순수 함수: 날짜 타입에 맞는 기본 단위, 히스토그램 구간 이름, 캔들 값 컬럼 필수.
// unitForDate·histLabel은 새 함수(스켈레톤이라 지금 실패). 캔들은 buildCard (수정)이 끝나야 통과하므로 it.fails로 표시했다(고친 뒤 it으로 바꾼다).
import { describe, expect, it } from "vitest";
import { histLabel } from "@/components/cards/chart/Charts";
import { buildCard, emptyForm, type Form } from "@/components/edit/menu/build";
import { unitForDate } from "@/components/edit/menu/kinds";

const COLS: [string, string][] = [["d", "날짜"], ["t", "날짜시간"], ["amt", "실수"]];

describe("unitForDate: 날짜만 있는 컬럼이면 일", () => {
  it("날짜 → day, 날짜시간·없는 이름·빈 글자 → 받은 기본값", () => {
    expect(unitForDate(COLS, "d", "hour")).toBe("day");
    expect(unitForDate(COLS, "d", "minute")).toBe("day");
    expect(unitForDate(COLS, "t", "hour")).toBe("hour");
    expect(unitForDate(COLS, "t", "minute")).toBe("minute");
    expect(unitForDate(COLS, "nope", "hour")).toBe("hour");
    expect(unitForDate(COLS, "", "minute")).toBe("minute");
  });
});

describe("histLabel: 구간 이름은 구간 너비에 맞춘 자릿수", () => {
  it("너비가 500쯤이면 정수로(소수 넷째 자리 꼬리 없음)", () => {
    const e = [1000.1234, 1500.5678, 2001.0122];
    expect(histLabel(e, 0)).toBe("1,000–1,501");
    expect(histLabel(e, 1)).toBe("1,501–2,001");
  });
  it("너비가 0.1이면 소수 한 자리, 1만 이상은 짧게", () => {
    expect(histLabel([0.1, 0.2, 0.3], 1)).toBe("0.2–0.3");
    expect(histLabel([10000, 20000], 0)).toBe("1만–2만");
  });
  it("범위 밖 구간 번호는 오류", () => {
    expect(() => histLabel([0, 1, 2], 2)).toThrow("히스토그램 구간 번호가 범위를 벗어났어요.");
    expect(() => histLabel([0, 1, 2], -1)).toThrow("히스토그램 구간 번호가 범위를 벗어났어요.");
  });
});

describe("캔들 카드는 값 컬럼이 있어야 만든다(15A2 화면 쪽)", () => {
  const form = (y: string): Form => ({ ...emptyForm("candle"), table: "t", date: "d", rows: [{ label: "", y, agg: "sum" }] });
  it("값 줄 컬럼이 비면 '값 줄의 숫자 컬럼을 고르세요.'", () => {
    expect(() => buildCard(form(""), ["sum", "count"])).toThrow("값 줄의 숫자 컬럼을 고르세요.");
  });
  it("값 컬럼을 고르면 그대로 만든다", () => {
    expect(buildCard(form("amt"), ["sum", "count"]).source?.body?.series).toEqual([{ y: "amt", agg: "sum" }]);
  });
});
