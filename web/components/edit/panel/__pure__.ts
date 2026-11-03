// 순수 함수 자가 점검: node --experimental-strip-types web/components/edit/panel/__pure__.ts (실패하면 assert가 던진다)
import assert from "node:assert/strict";
// @ts-expect-error node의 .ts 직접 실행용 확장자 import
import { SHELF_ROWS, applyDrop, dropCheck, setAgg, shelfChips, stateOf } from "./shelf.ts";

const s = stateOf({ x: "일자", series: [{ y: "금액", agg: "sum" }], conditions: [{ any: ["a = 1", { line: "b = 2" }] }] });
assert.equal(dropCheck("y", "지점", "문자", s).ok, false);
assert.equal(dropCheck("y", "일자", "날짜", s).ok, false);
assert.equal(dropCheck("y", "금액", "실수", s).ok, false);       // 이미 값에 있음
assert.equal(dropCheck("y", SHELF_ROWS, "", s).ok, true);
assert.equal(dropCheck("x", SHELF_ROWS, "", s).ok, false);
assert.equal(dropCheck("split", "수량", "정수", s).ok, false);
assert.equal(dropCheck("split", "일자", "날짜", s).ok, false);
assert.equal(dropCheck("split", "지점", "문자", s).ok, true);
const t = applyDrop("y", SHELF_ROWS, "", s);
assert.deepEqual(t.series.at(-1), { y: null, agg: "count" });
assert.equal(applyDrop("y", "수량", "정수", s).series.at(-1)?.agg, "sum");
assert.equal(applyDrop("split", "지점", "문자", { ...s, split: "" }).split, "지점");
assert.deepEqual(shelfChips(applyDrop("filter", "지점", "문자", s, "서울", "=")).filter, ["a = 1 or b = 2", "지점 = 서울"]);
assert.equal(setAgg(t.series, 0, "mean")[0].agg, "mean");
assert.equal(setAgg(t.series, 1, "mean")[1].agg, "count");        // 행 개수는 안 바뀐다
console.log("shelf ok");
