// 순수 함수 자가 점검(layout.ts): node --experimental-strip-types --import <@/·확장자 없는 import를 푸는 로더> web/components/report/__pure__.ts (실패하면 assert가 던진다)
import assert from "node:assert/strict";
import { fitRect, reportLayout } from "./layout";

const BOX = { col: 100, gap: 12, row: 40, pad: 20, head: 56 };
const lay = reportLayout([{ id: "a", layout: { x: 0, y: 0, w: 4, h: 4 } }, { id: "b", layout: { x: 4, y: 2, w: 8, h: 6 } }], BOX);
assert.deepEqual(lay.rects.a, { x: 20, y: 56, w: 4 * 100 + 3 * 12, h: 4 * 40 + 3 * 12 });
assert.equal(lay.rects.b.x, 20 + 4 * 112);
assert.equal(lay.width, 20 * 2 + 12 * 100 + 11 * 12);
assert.equal(lay.height, 56 + 8 * 52 - 12 + 20);  // 가장 아래 줄 y+h = 8
assert.equal(reportLayout([], BOX).height, 56 + 52 - 12 + 20);  // 카드가 없어도 한 줄
assert.deepEqual(fitRect(200, 100, 100, 100), { x: 0, y: 25, w: 100, h: 50 });  // 넓은 그림은 가로에 맞춰 위아래 여백
assert.deepEqual(fitRect(50, 100, 100, 100), { x: 25, y: 0, w: 50, h: 100 });   // 작은 그림은 키운다
console.log("report/__pure__ ok");
