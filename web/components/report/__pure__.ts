// 순수 함수 자가 점검(layout.ts): node --experimental-strip-types --import <@/·확장자 없는 import를 푸는 로더> web/components/report/__pure__.ts (실패하면 assert가 던진다)
import assert from "node:assert/strict";
import { fitRect, REPORT, reportLayout } from "./layout";

const lay = reportLayout([{ id: "a", layout: { x: 0, y: 0, w: 4, h: 4 } }, { id: "b", layout: { x: 4, y: 2, w: 8, h: 6 } }]);
assert.deepEqual(lay.rects.a, { x: REPORT.pad, y: REPORT.head, w: 4 * 100 + 3 * 10, h: 4 * 40 + 3 * 10 });
assert.equal(lay.rects.b.x, REPORT.pad + 4 * 110);
assert.equal(lay.width, 24 * 2 + 12 * 100 + 11 * 10);
assert.equal(lay.height, REPORT.head + 8 * 50 - 10 + REPORT.pad);  // 가장 아래 줄 y+h = 8
assert.equal(reportLayout([]).height, REPORT.head + 50 - 10 + REPORT.pad);  // 카드가 없어도 한 줄
assert.deepEqual(fitRect(200, 100, 100, 100), { x: 0, y: 25, w: 100, h: 50 });  // 넓은 그림은 가로에 맞춰 위아래 여백
assert.deepEqual(fitRect(50, 100, 100, 100), { x: 25, y: 0, w: 50, h: 100 });   // 작은 그림은 키운다
console.log("report/__pure__ ok");
