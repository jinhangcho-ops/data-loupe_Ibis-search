// kinds.ts의 순수 함수 자가 점검: node --experimental-strip-types web/components/edit/menu/__pure__.ts (실패하면 assert가 던진다)
import assert from "node:assert/strict";
// @ts-expect-error node의 .ts 직접 실행용 확장자 import
import { CARD_KINDS, WITH_DATE, galleryItems, parseBlankLine, shellOf } from "./kinds.ts";

const aggs = ["sum", "count", "mean"];
assert.deepEqual(parseBlankLine("매출 = 값 amt sum 금액", aggs).blank, { name: "매출", kind: "value", y: "amt", agg: "sum", format: "currency" });
assert.equal(parseBlankLine("비교 = 비교", aggs).blank?.agg, "count");
assert.match(parseBlankLine("x = 모름", aggs).error ?? "", /종류는/);
assert.match(parseBlankLine("x = 값 a bogus", aggs).error ?? "", /집계는/);
assert.match(parseBlankLine("엉터리", aggs).error ?? "", /모양으로/);
assert.equal(Object.keys(CARD_KINDS).length, 18);              // 옛 메뉴의 종류 수(퍼널·지도 포함)
assert.ok(!WITH_DATE.includes("funnel") && !WITH_DATE.includes("chart") && WITH_DATE.includes("table"));
assert.equal(galleryItems({ bar: "막대", line: "라인" }).filter((t: { kind: string }) => t.kind === "chart").length, 2);
assert.deepEqual(shellOf({ group: "차트", kind: "chart", type: "bar", name: "막대", help: "" }).source, {});
console.log("menu ok");
