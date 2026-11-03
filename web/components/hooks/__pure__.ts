// 되돌리기·자동 저장의 순수 자가 점검(useUndo record·move, store reducer, useAutosave draftState·slimDash, calc BUCKET):
// node --import <@/·확장자 없는 import를 풀고 .ts/.tsx를 typescript로 옮기는 로더> web/components/hooks/__pure__.ts (실패하면 assert가 던진다)
import assert from "node:assert/strict";
import { BUCKET, setCalendar } from "@/lib/calc";
import { initialState, reducer, type Action, type DashState } from "@/lib/store";
import type { Card, Dashboard } from "@/lib/types";
import { draftState, markSaved, slimDash } from "./useAutosave";
import { move, record, undoInternals } from "./useUndo";

const card = (id: string, data: Record<string, unknown> = { v: id }) => ({ id, kind: "kpi", title: id, layout: { x: 0, y: 0, w: 4, h: 4 }, colors: {}, view: {}, source: {}, data }) as unknown as Card;
const dash = { id: "d", name: "대시", palette: [], north_star: null, period: "", as_of: "", cards: [card("a"), card("b")] } as unknown as Dashboard;
let s: DashState = reducer(initialState, { type: "open", dash });
const { U, cur } = undoInternals;
cur.dispatch = (a: Action) => { record(a, s, t); s = reducer(s, a); cur.state = s; };
let t = 10_000;
const act = (a: Action) => { t += 1000; cur.dispatch(a); };
record({ type: "open", dash }, s);
cur.state = s;

// 사용자가 카드를 옮김 → 쌓임. 화면이 스스로 붙인 색 순서(auto)·설정 meta는 쌓지 않고 다시 하기도 지우지 않는다
act({ type: "cards", cards: [s.dash!.cards[1], s.dash!.cards[0]] });
assert.equal(U.back.length, 1);
act({ type: "card", id: "a", patch: { colors: { order: ["x"] } as unknown as Card["colors"] }, auto: true });
act({ type: "meta", patch: { period: "최근 7일", as_of: "" } });
assert.equal(U.back.length, 1);
move(U.back, U.fwd);  // 되돌리기
assert.deepEqual(s.dash!.cards.map(c => c.id), ["a", "b"]);
assert.equal(s.dash!.period, "최근 7일");  // 기간은 되돌리지 않는다
assert.equal(U.fwd.length, 1);
act({ type: "card", id: "a", patch: { colors: { order: ["y"] } as unknown as Card["colors"] }, auto: true });  // 되돌린 뒤 색 순서가 다시 붙어도
assert.equal(U.fwd.length, 1);  // 다시 하기가 남는다
move(U.fwd, U.back);  // 다시 하기
assert.deepEqual(s.dash!.cards.map(c => c.id), ["b", "a"]);
move(U.back, U.fwd);
move(U.fwd, U.back);  // Ctrl+Z·Y 연속
assert.deepEqual(s.dash!.cards.map(c => c.id), ["b", "a"]);
act({ type: "meta", patch: { name: "새 이름" } });  // 설정 아닌 meta는 쌓고 다시 하기를 지운다
assert.equal(U.back.length, 2);
assert.equal(U.fwd.length, 0);

// reducer: saved는 화면의 data를 두고 dirty를 끈다
assert.equal(s.dirty, true);
const saved = reducer(s, { type: "saved", dash: { ...s.dash!, cards: s.dash!.cards.map(c => { const { data, ...r } = c; void data; return r as Card; }) } });
assert.equal(saved.dirty, false);
assert.equal(saved.dash!.cards[0].data, s.dash!.cards[0].data);

// 초안·저장 요청: 초안에는 data가 없고, 저장 요청은 서버에 있는 그대로의 data만 뺀다
assert.ok(draftState(s.dash!).cards.every(c => !("data" in c)));
markSaved(s.dash!);
const changed = { ...s.dash!, cards: s.dash!.cards.map(c => c.id === "a" ? { ...c, data: { v: "새 값" } as Card["data"] } : c) };
const body = slimDash(changed);
assert.deepEqual(body.cards.map(c => "data" in c), changed.cards.map(c => c.id === "a"));

// BUCKET: 주 시작 요일·회계연도
assert.equal(BUCKET.week("2026-11-01"), "2026-10-26");  // 일요일 → 그 주 월요일(기본)
assert.equal(BUCKET.year("2026-03-05"), "2026-01-01");
setCalendar("sun", 4);
assert.equal(BUCKET.week("2026-11-01"), "2026-11-01");  // 일요일 시작
assert.equal(BUCKET.week("2026-11-07"), "2026-11-01");  // 토요일 → 앞 일요일
assert.equal(BUCKET.week("2026-11-02"), "2026-11-01");
assert.equal(BUCKET.year("2026-03-05"), "2025-04-01");  // 4월 시작 회계연도
assert.equal(BUCKET.year("2026-04-01"), "2026-04-01");
setCalendar(undefined, 13);
assert.equal(BUCKET.week("2026-11-01"), "2026-10-26");
assert.equal(BUCKET.year("2026-03-05"), "2026-01-01");  // 틀린 달은 1월
console.log("hooks/__pure__ ok");
