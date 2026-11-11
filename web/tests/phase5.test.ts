// Phase 5(plans/phase-5.md): 해시·UUID 컬럼을 그룹·집계 목록에서 빼기(web/lib/hashed.ts). 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
import { describe, expect, it } from "vitest";
import { withoutHashed } from "@/lib/hashed";

describe("withoutHashed", () => {
  it("켜져 있으면 해시 컬럼을 빼고 뺀 것을 차례대로 알려 준다", () => {
    expect(withoutHashed(["id", "uid", "city", "token"], ["token", "uid"], true, [])).toEqual({ names: ["id", "city"], hidden: ["uid", "token"] });
  });
  it("다시 넣은 컬럼은 남긴다", () => {
    expect(withoutHashed(["id", "uid", "city"], ["uid"], true, ["uid"])).toEqual({ names: ["id", "uid", "city"], hidden: [] });
  });
  it("꺼져 있으면 그대로", () => {
    expect(withoutHashed(["id", "uid"], ["uid"], false, [])).toEqual({ names: ["id", "uid"], hidden: [] });
  });
  it("목록에 없는 해시 이름은 hidden에 넣지 않는다", () => {
    expect(withoutHashed(["a"], ["zz"], true, [])).toEqual({ names: ["a"], hidden: [] });
  });
});
