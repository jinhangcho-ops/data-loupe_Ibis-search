// 감사(화면): 지도 카드의 '보일 영역' 체크 목록에 영역 묶음 이름이 나오고, 그것을 고르면 묶음이 남는다. 값은 여기서 지어낸다.
import { describe, expect, it } from "vitest";
import { applyGroups, groupNames } from "@/lib/groups";
import type { GroupView } from "@/lib/groups";
import type { Card, CardData } from "@/lib/types";

const card = (groups: GroupView): Card => ({ kind: "geomap", view: { groups } }) as unknown as Card;
const data: CardData = { values: { regions: [{ name: "서울", value: 3, rings: [[1]] }, { name: "부산", value: 1, rings: [[3]] }, { name: "경기", value: 2, rings: [[2]] }], points: [], bounds: [0, 0, 1, 1] } };

describe("감사 4", () => {
  it("체크 목록은 묶인 영역 대신 묶음 이름을 첫 영역 자리에", () => {
    expect(groupNames(card({ sets: { "수도권": ["서울", "경기"] } }), data).names).toEqual(["수도권", "부산"]);
  });
  it("묶음 이름을 고르면 묶음이 남는다", () => {
    const got = applyGroups(card({ sets: { "수도권": ["서울", "경기"] }, keep: ["수도권"] }), data).values as { regions: { name: string; value: number }[] };
    expect(got.regions.map((r) => [r.name, r.value])).toEqual([["수도권", 5]]);
  });
});
