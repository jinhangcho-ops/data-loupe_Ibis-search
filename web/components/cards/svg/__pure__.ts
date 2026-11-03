// SVG/HTML 카드의 순수 계산(화면 요소 없음): 옛 dash_cards.js의 cardValue·cardDays·cardTarget·cardFormat,
// funnel.js의 생키 좌표·표·문장, static/geo/geomap.js의 메르카토르·타일 계산을 본문 그대로 옮겼다(SERVER.merge는 인자 merge로 받는다).
import { overTarget, shiftDay, showValue, underTarget, zeroWhenEmpty } from "@/lib/calc";
import { loadedRange, windowValue, type Range } from "@/lib/series";
import type { Card, NorthStar } from "@/lib/types";

/** 그리는 색(옛 board.js themeColors + 대시보드 팔레트). err·ok는 상태 색(옛 cssVar("--err")·("--ok")) */
export type Theme = { card: string; head: string; ink: string; text: string; line: string; err: string; ok: string; series: string[]; palette?: string[] };
// 기간(range)의 계열 i 값: 일 값이 있으면 기간으로 묶고, 없으면 날짜 컬럼 없이 구한 값
export function cardValue(card: Card, i: number, range: Range, merge: string[]): number | null {
  const by = card.data?.by_unit?.day;
  if (by && by.series[i] && !merge.includes(by.series[i].agg) && range && by.labels.some(d => (!range.start || d >= range.start) && (!range.end || d <= range.end)) && windowValue(by.series[i], by.labels, range.start, range.end, merge) == null) {
    const w = card.data.values?.window_nunique as { start?: string | null; end?: string | null; values: Record<string, number | null> } | undefined;  // 고유 개수는 일 값을 묶을 수 없어, 서버가 이 기간으로 따로 구한 값만 쓴다(없으면 —)
    return w && (w.start ?? null) === (range.start ?? null) && (w.end ?? null) === (range.end ?? null) ? w.values[i] ?? null : null;
  }
  if (by) return by.series[i] ? windowValue(by.series[i], by.labels, range?.start, range?.end, merge, loadedRange(card, merge)) : null;
  return (card.data?.values?.scalar as (number | null)[] | undefined)?.[i] ?? null;
}
// 기간 안의 날마다 값 [{ day, v }]. view.window(보는 구간)가 있으면 그 구간만. 개수·합계는 읽어 온 범위 안의 빈 날을 0으로 채운다.
export function cardDays(card: Card, i: number, range: Range, merge: string[]): { day: string; v: number | null }[] {
  const by = card.data?.by_unit?.day, s = by?.series[i], win = card.view?.window, from = win?.from ?? range?.start, to = win?.to ?? range?.end;
  if (!by || !s) return [];
  let days = by.labels.filter(d => (!from || d >= from) && (!to || d <= to));
  const loaded = loadedRange(card, merge);
  if (zeroWhenEmpty(s.agg) && days.length) {  // 보는 기간(없으면 있는 날)의 처음~끝 중 읽어 온 범위 안의 날 모두
    const lo = [from ?? days[0], loaded?.start].filter(Boolean).sort().pop()!, hi = [to ?? days[days.length - 1], loaded?.end].filter(Boolean).sort()[0]!;
    days = [];
    for (let d = lo; d <= hi; d = shiftDay(d, 1)) days.push(d);
  }
  return days.map(day => ({ day, v: windowValue(s, by.labels, day, day, merge, loaded) }));
}
export const cardTarget = (card: Card): number | null => (card.data?.target as { value?: number | null } | undefined)?.value ?? (card.view?.target as { value?: number } | undefined)?.value ?? null;
export const cardFormat = (card: Card): string => card.view?.format || (card.data?.values?.format as string | undefined) || "number";
// 카운터 지표(북극성을 올리다 나빠지면 안 되는 지표)가 한계를 벗어났는지(옛 dash_edit.js counterAlerts). cards는 지금 보이는 값을 단 카드.
// 값이 없거나 한계를 정하지 않은 카운터는 뺀다. 합계의 꼬리(1.1 + 2.2)로 거짓 경보를 내지 않게 overTarget·underTarget으로 본다.
export function counterAlerts(cards: Card[], star: NorthStar | undefined, range: Range, merge: string[]) {
  const out: { id: string; title: string; value: number; op: ">=" | "<="; limit: number; text: string }[] = [];
  for (const id of star?.counters ?? []) {
    const card = cards.find(c => c.id === id), lim = star?.limits?.[id];
    if (!card || !lim) continue;
    const v = cardValue(card, 0, range, merge);
    if (v == null || !Number.isFinite(v)) continue;
    const bad = lim.op === ">=" ? underTarget(v, lim.value) : overTarget(v, lim.value), fmt = cardFormat(card);
    if (bad) out.push({ id, title: card.title, value: v, op: lim.op, limit: lim.value,
      text: `${card.title} 값이 한계를 벗어났어요. 지금 ${showValue(v, fmt)}, 한계 ${showValue(lim.value, fmt)} ${lim.op === ">=" ? "이상" : "이하"}` });
  }
  return out;
}

// ======================= 흐름(퍼널): static/core/funnel.js =======================
export const FUNNEL_COLORS = { bar: "#00968f", next: "#008300", drop: "#eb6834" };
export type Step = { name: string; users: number; conv?: number | null; from_first?: number | null; drop?: number };
// 생키 좌표: 단계마다 세로 막대, 다음 단계 띠, 이탈 띠(3차 베지어 둘 + 직선 둘로 닫은 경로)
export function sankeyLayout(steps: Step[], { width = 720, height = 340, pad = 12, barW = 22, top = 30, bottom = 40, lane = 14 } = {}) {
  const k = steps.length, users = steps.map(s => s.users), first = users[0] || 0;
  const drops = users.map((n, i) => (i ? Math.max(0, users[i - 1] - n) : 0)), worst = Math.max(0, ...drops);
  const avail = height - top - bottom - lane, per = first + worst ? avail / (first + worst) : 0;  // 사람 한 명의 높이: 첫 막대와 가장 큰 이탈 띠가 세로에 다 들어가게
  const h = (n: number) => (n > 0 ? Math.max(1, n * per) : 0);
  const gap = k > 1 ? (width - 2 * pad - barW) / (k - 1) : 0, x = (i: number) => (k > 1 ? pad + i * gap : (width - barW) / 2);
  const bars = steps.map((s, i) => ({ x: x(i), y: top, w: barW, h: h(s.users), name: s.name, users: s.users }));
  const band = (x0: number, y0a: number, y0b: number, x1: number, y1a: number, y1b: number) => {
    const mid = (x0 + x1) / 2;
    return `M${x0},${y0a} C${mid},${y0a} ${mid},${y1a} ${x1},${y1a} L${x1},${y1b} C${mid},${y1b} ${mid},${y0b} ${x0},${y0b} Z`;
  };
  const nexts: { path: string; from: number; to: number; users: number }[] = [], lost: { path: string; step: number; users: number }[] = [], laneTop = top + h(first) + lane;
  for (let i = 1; i < k; i++) {
    const prev = bars[i - 1], cur = bars[i], hn = h(users[i]), hd = h(drops[i]), x0 = prev.x + barW;
    if (users[i] > 0) nexts.push({ path: band(x0, top, top + hn, cur.x, top, top + hn), from: i - 1, to: i, users: users[i] });
    if (drops[i] > 0) {  // 이탈: 이전 막대의 남는 아랫부분에서 두 막대 사이의 아래 칸으로
      const xe = x0 + (cur.x - x0) * 0.72, y0 = top + hn;
      lost.push({ path: band(x0, y0, y0 + hd, xe, laneTop, laneTop + hd), step: i, users: drops[i] });
    }
  }
  return { width, height, bars, nexts, drops: lost, per };
}
export const fpct = (v: number | null | undefined) => (v == null ? "-" : `${(v * 100).toFixed(1)}%`);
export const FUNNEL_COLUMNS = ["단계", "사용자 수", "다음 단계 전환율", "처음 대비", "이탈 수"];
// 표: 단계별 수, 다음 단계 전환율, 처음 대비, 이탈 수(마지막 단계는 다음이 없다)
export function funnelRows(steps: Step[]): Record<string, string | number | undefined>[] {
  return steps.map((s, i) => {
    const next = steps[i + 1];
    return { 단계: s.name, "사용자 수": s.users, "다음 단계 전환율": next ? fpct(next.conv) : "-", "처음 대비": fpct(s.from_first), "이탈 수": next ? next.drop : "-" };
  });
}
// 한 줄 설명: 처음부터 끝까지 남은 비율과 가장 많이 빠진 곳
export function funnelSentence(steps: Step[]) {
  if (!steps.length || !steps[0].users) return "첫 단계에 도달한 사용자가 없어요.";
  const first = steps[0], last = steps[steps.length - 1];
  if (steps.length === 1) return `${first.name}에 ${first.users.toLocaleString()}명이 있어요.`;
  const worst = steps.slice(1).reduce<(Step & { from: string }) | null>((a, s, i) => ((s.drop ?? 0) > (a?.drop ?? -1) ? { ...s, from: steps[i].name } : a), null);
  return `${first.name} ${first.users.toLocaleString()}명 중 ${last.users.toLocaleString()}명(${fpct(last.from_first)})이 ${last.name}까지 갔어요.` +
    (worst && (worst.drop ?? 0) > 0 ? ` 가장 많이 빠진 곳은 ${worst.from} → ${worst.name}로, ${worst.drop!.toLocaleString()}명(${fpct(worst.drop! / steps.find(s => s.name === worst.from)!.users)})이 다음 단계로 가지 못했어요.` : " 중간에 빠진 사람은 없어요.");
}
const WINDOW_UNIT_NAMES: Record<string, string> = { minute: "분", hour: "시간", day: "일" };
export const windowText = (w?: { n?: number; unit?: string } | null) => (w && w.n ? `첫 단계부터 ${w.n}${WINDOW_UNIT_NAMES[w.unit ?? ""] ?? ""} 안에 도달한 사람만` : "기간 제한 없음");

// ======================= 지도: static/geo/geomap.js =======================
export const OSM_TILES = "https://tile.openstreetmap.org", OSM_CREDIT = "© OpenStreetMap contributors", MAX_MERC_LAT = 85.0511287798;
export const MAX_TILES = 16;  // 한 번에 받는 타일 수의 상한(보는 범위가 넓으면 줌을 낮춘다)
export const mercX = (lon: number) => (lon + 180) / 360;
export const mercY = (lat: number) => { const s = Math.sin(Math.max(-MAX_MERC_LAT, Math.min(MAX_MERC_LAT, lat)) * Math.PI / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };
export type MapFrame = { w: number; h: number; scale: number; x0: number; y0: number; x1: number; y1: number; project: (lon: number, lat: number) => [number, number] };
// 범위 [최소 경도, 최소 위도, 최대 경도, 최대 위도]에 맞춘 그림 틀(너비 width, 높이가 maxH를 넘으면 높이에 맞춤, pad는 여백 비율)
export function mapFrame(bounds: number[], width: number, maxH = 520, pad = 0.06): MapFrame {
  const [lo, la0, hi, la1] = bounds, tiny = 1e-6;
  let x0 = mercX(lo), x1 = mercX(hi), y0 = mercY(la1), y1 = mercY(la0);
  if (x1 - x0 < tiny) [x0, x1] = [x0 - 5e-5, x1 + 5e-5];
  if (y1 - y0 < tiny) [y0, y1] = [y0 - 5e-5, y1 + 5e-5];
  const px = (x1 - x0) * pad, py = (y1 - y0) * pad;
  [x0, x1, y0, y1] = [x0 - px, x1 + px, y0 - py, y1 + py];
  let scale = width / (x1 - x0);
  if ((y1 - y0) * scale > maxH) scale = maxH / (y1 - y0);
  const w = (x1 - x0) * scale, h = (y1 - y0) * scale;
  return { w, h, scale, x0, y0, x1, y1, project: (lon, lat) => [(mercX(lon) - x0) * scale, (mercY(lat) - y0) * scale] };
}
export function tileRange(frame: MapFrame, z: number) {
  const n = 2 ** z, clampT = (v: number) => Math.max(0, Math.min(n - 1, v));
  const wide = frame.x0 < 0 && frame.x1 > 1;  // 세계 전체를 양쪽으로 넘으면 한 바퀴만, 한쪽만 넘으면 감싸 이어 붙인다
  const edge = (v: number) => wide ? clampT(v) : v;
  return { x0: edge(Math.floor(frame.x0 * n)), x1: edge(Math.floor(frame.x1 * n - 1e-9)), y0: clampT(Math.floor(frame.y0 * n)), y1: clampT(Math.floor(frame.y1 * n - 1e-9)), n };
}
export function tileCount(frame: MapFrame, z: number) { const r = tileRange(frame, z); return (r.x1 - r.x0 + 1) * (r.y1 - r.y0 + 1); }
// 타일 줌: 타일 한 장이 256~512쯤, 타일 수가 MAX_TILES 이하가 되도록 낮춘다(0~18)
export function tileZoom(frame: MapFrame) {
  let z = Math.max(0, Math.min(18, Math.round(Math.log2(frame.scale / 256))));
  while (z > 0 && tileCount(frame, z) > MAX_TILES) z--;
  return z;
}
export function tilesFor(frame: MapFrame, z: number) {
  const r = tileRange(frame, z), size = frame.scale / r.n, out: { z: number; x: number; y: number; url: string; px: number; py: number; size: number }[] = [];
  for (let y = r.y0; y <= r.y1; y++) for (let x = r.x0; x <= r.x1; x++) out.push({ z, x, y, url: `${OSM_TILES}/${z}/${((x % r.n) + r.n) % r.n}/${y}.png`, px: (x / r.n - frame.x0) * frame.scale, py: (y / r.n - frame.y0) * frame.scale, size });
  return out;
}
// 영역 값 → 칠 투명도(0.18~0.9). 값이 모두 같으면 가운데, 빈 값은 null
export function fillLevels(values: unknown[], floor = 0.18, top = 0.9) {
  const real = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v)), lo = Math.min(...real), hi = Math.max(...real);
  return values.map(v => typeof v !== "number" || !Number.isFinite(v) ? null : hi === lo ? (floor + top) / 2 : floor + (top - floor) * (v - lo) / (hi - lo));
}
// 고리 하나(경도, 위도 번갈아)를 SVG 경로 글자로
export function ringPath(ring: number[], project: MapFrame["project"]) {
  let d = "";
  for (let i = 0; i + 1 < ring.length; i += 2) { const [x, y] = project(ring[i], ring[i + 1]); d += (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1); }
  return d + "Z";
}
