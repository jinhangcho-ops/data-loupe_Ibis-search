// SVG/HTML 카드의 순수 계산(화면 요소 없음): 옛 dash_cards.js의 cardValue·cardDays·cardTarget·cardFormat,
// funnel.js의 생키 좌표·표·문장, static/geo/geomap.js의 메르카토르·타일 계산을 본문 그대로 옮겼다(SERVER.merge는 인자 merge로 받는다).
import { TAU, anaView, clamp, isPoints, overTarget, polar, shiftDay, showValue, underTarget, zeroWhenEmpty } from "@/lib/calc";
import { loadedRange, windowValue, type Range } from "@/lib/series";
import type { Card, NorthStar, TreeItem } from "@/lib/types";
import type { SCard, Series } from "../chart/series";
import type { Ana } from "../chart/Charts";
import { HBARS, ROUND_TYPES } from "../chart/shapes";
const NO_ZOOM_TYPES: string[] = [...ROUND_TYPES, ...HBARS, "radar", "slope"];  // 확대하지 않는 계열 종류(둥근 차트·가로 막대·레이더·슬로프)

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
/** 퍼널의 세 색(그림과 [색] 칸 견본이 함께 쓴다): 카드 지정 색 → 단계 막대는 팔레트 첫 색, 다음 단계는 초록, 이탈은 주황 */
export const funnelColors = (card: Card, palette?: string[]): Record<string, string> => {
  const pick = (name: string, fallback: string) => card.colors?.series?.[name] ?? fallback;
  return { "단계 막대": pick("단계 막대", palette?.[0] ?? FUNNEL_COLORS.bar), "다음 단계": pick("다음 단계", FUNNEL_COLORS.next), 이탈: pick("이탈", FUNNEL_COLORS.drop) };
};
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
export const windowText = (w?: { n?: number; unit?: string } | null) => (w && w.n ? `앞 단계부터 ${w.n}${WINDOW_UNIT_NAMES[w.unit ?? ""] ?? ""} 안에 도달한 사람만` : "기간 제한 없음");

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

// ======================= 새 카드(P14) =======================
/** 달력 히트맵 칸: 첫 날~끝 날의 날마다(값이 없는 날은 v null) 열 = 첫 날의 주부터 센 주, 줄 = 요일(월요일 시작, sunday면 일요일 시작). 달이 바뀌는 주에 달 이름 */
export function calendarCells(given: { day: string; v: number | null }[], sunday = false) {
  if (!given.length) return { cells: [], weeks: 0, months: [] as { col: number; label: string }[] };
  const sorted = [...given].sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0)), byDay = new Map(sorted.map(d => [d.day, d.v]));
  const days: { day: string; v: number | null }[] = [];
  for (let d = sorted[0].day; d <= sorted[sorted.length - 1].day; d = shiftDay(d, 1)) days.push({ day: d, v: byDay.get(d) ?? null });  // 빠진 날도 칸으로(구멍·달 이름이 어긋나지 않게)
  const row = (d: string) => (new Date(d + "T00:00:00Z").getUTCDay() + (sunday ? 0 : 6)) % 7, lead = row(days[0].day);
  const cells = days.map((d, i) => { const n = i + lead; return { ...d, col: Math.floor(n / 7), row: n % 7 }; });
  const months: { col: number; label: string }[] = [];
  cells.forEach((c, i) => {
    if (i && !c.day.endsWith("-01")) return;
    const label = `${+c.day.slice(5, 7)}월`;
    if (months.at(-1)?.col === c.col) months[months.length - 1].label = label; else months.push({ col: c.col, label });
  });
  return { cells, weeks: cells[cells.length - 1].col + 1, months };
}

/** 생키 흐름(서버 flow_pairs): 노드는 이름(또는 {name}), 링크의 s·t는 노드 번호나 이름, v는 인원 */
export type Flow = { nodes: (string | { name: string })[]; links: { s: number | string; t: number | string; v: number }[] };
/** 생키 좌표: 노드 열 = 들어오는 링크를 따라간 가장 긴 거리, 노드 높이 ∝ max(들어온 합, 나간 합), 열마다 위에서 쌓고 띠는 노드 안에서 차례로 쌓는다 */
export function flowLayout(flow: Flow, { width = 720, height = 340, pad = 8, nodeW = 14, gap = 10 } = {}) {
  const names = flow.nodes.map(n => (typeof n === "string" ? n : n.name)), at = (k: number | string) => (typeof k === "number" ? k : names.indexOf(k));
  const links = flow.links.map(l => ({ s: at(l.s), t: at(l.t), v: Math.max(0, Number(l.v) || 0) }))
    .filter(l => l.s >= 0 && l.t >= 0 && l.s < names.length && l.t < names.length && l.s !== l.t && l.v > 0);
  const col = names.map(() => 0);
  for (let pass = 0; pass < names.length; pass++) for (const l of links) col[l.t] = Math.min(names.length, Math.max(col[l.t], col[l.s] + 1));  // 고리가 있어도 노드 수만큼만
  const sum = (i: number, key: "s" | "t") => links.filter(l => l[key] === i).reduce((a, l) => a + l.v, 0), value = names.map((_, i) => Math.max(sum(i, "s"), sum(i, "t")));
  const groups = [...new Set(col)].sort((a, b) => a - b).map(c => names.map((_, i) => i).filter(i => col[i] === c && value[i] > 0)).filter(g => g.length);
  const per = groups.length ? Math.min(...groups.map(g => (height - 2 * pad - gap * (g.length - 1)) / g.reduce((a, i) => a + value[i], 0))) : 0;
  const k = groups.length, x = (g: number) => (k > 1 ? pad + (g * (width - 2 * pad - nodeW)) / (k - 1) : (width - nodeW) / 2);
  const nodes = names.map((name, i) => ({ name, x: 0, y: 0, h: value[i] * per, value: value[i] }));
  groups.forEach((g, gi) => { let y = pad; g.forEach(i => { Object.assign(nodes[i], { x: x(gi), y }); y += nodes[i].h + gap; }); });
  const outY = nodes.map(n => n.y), inY = nodes.map(n => n.y);
  const bands = links.map(l => {
    const h = l.v * per, y0 = outY[l.s], y1 = inY[l.t], x0 = nodes[l.s].x + nodeW, x1 = nodes[l.t].x, mid = (x0 + x1) / 2;
    outY[l.s] += h;
    inY[l.t] += h;
    return { ...l, y0, y1, h, path: `M${x0},${y0} C${mid},${y0} ${mid},${y1} ${x1},${y1} L${x1},${y1 + h} C${mid},${y1 + h} ${mid},${y0 + h} ${x0},${y0 + h} Z` };
  });
  return { width, height, nodeW, nodes, links: bands };
}

/** 스파크라인(calc.sparkPoints와 같은 눈금)에서 값 t의 y. 그린 값 범위 밖이면 위·아래 끝에 붙인다 */
export function sparkY(values: (number | null)[], t: number, h: number, pad = 2) {
  const real = values.filter((v): v is number => v != null), lo = Math.min(...real), hi = Math.max(...real);
  if (!real.length) return h / 2;
  if (hi === lo) return t > hi ? 1 : t < lo ? h - 1 : h / 2;  // 값이 한결같으면 선은 가운데: 목표가 위면 위 끝, 아래면 아래 끝
  return Math.max(1, Math.min(h - 1, h - pad - ((h - 2 * pad) * (t - lo)) / (hi - lo)));
}

/** 트리 항목(값 > 0만): 아이들 합이 값보다 작으면 나머지를 "기타" 아이로(넓이·각도가 부모와 맞게), 값이 0 이하인 아이는 뺀다.
 *  아이 합이 부모보다 크면(평균·최대처럼 더해지지 않는 집계) 아이를 버리고 부모만 그린다 */
export function withRest(items: TreeItem[] | undefined): TreeItem[] {
  return (items ?? []).filter(it => Number(it.value) > 0).map(it => {
    const kids = (it.children ?? []).filter(c => Number(c.value) > 0), sum = kids.reduce((a, c) => a + Number(c.value), 0);
    if (!kids.length || sum > it.value + 1e-9 * Math.abs(it.value)) return { name: it.name, value: Number(it.value) };
    return { name: it.name, value: Number(it.value), children: sum < it.value - 1e-9 * Math.abs(it.value) ? [...kids, { name: "기타", value: it.value - sum }] : kids };
  });
}
/** 선버스트 뿌리: 값 = 첫 단계 합(각도는 이것으로 나눈다. 없으면 각도가 NaN이 되어 빈 그림) */
export const treeRoot = (name: string, items: TreeItem[]): TreeItem & { children: TreeItem[] } => ({ name, value: items.reduce((a, it) => a + (Number(it.value) || 0), 0), children: items });
/** 선버스트 조각: 뿌리 값으로 각도를 나눠 첫 단계(depth 1, 안쪽 고리)와 둘째 단계(depth 2, 바깥 고리)의 [a0, a1](라디안, 12시가 0, 시계 방향). top은 첫 단계 번호(색).
 *  아이 조각은 부모 각도 안에서 자른다(합이 안 맞는 입력이어도 한 바퀴를 넘지 않게) */
export function sunburstArcs(root: TreeItem & { children: TreeItem[] }) {
  const out: { name: string; value: number; depth: 1 | 2; top: number; a0: number; a1: number; parent?: string }[] = [];
  if (!(root.value > 0)) return out;
  let a = 0;
  root.children.forEach((it, i) => {
    const a1 = a + (TAU * it.value) / root.value;
    out.push({ name: it.name, value: it.value, depth: 1, top: i, a0: a, a1 });
    let b = a;
    for (const ch of it.children ?? []) {
      const b1 = Math.min(a1, b + (TAU * ch.value) / root.value);
      if (b1 > b) out.push({ name: ch.name, value: ch.value, depth: 2, top: i, a0: b, a1: b1, parent: it.name });
      b = b1;
    }
    a = a1;
  });
  return out;
}
/** 고리 조각(반지름 r0~r1, 각도 a0~a1) 경로. 한 바퀴면 반씩 둘로 */
export function sectorPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  if (a1 - a0 >= TAU - 1e-6) return sectorPath(cx, cy, r0, r1, a0, a0 + Math.PI) + sectorPath(cx, cy, r0, r1, a0 + Math.PI, a1);
  const big = a1 - a0 > Math.PI ? 1 : 0, f = (n: number) => +n.toFixed(2);
  const [x0, y0] = polar(cx, cy, r1, a0).map(f), [x1, y1] = polar(cx, cy, r1, a1).map(f), [x2, y2] = polar(cx, cy, r0, a1).map(f), [x3, y3] = polar(cx, cy, r0, a0).map(f);
  return `M${x0},${y0} A${r1},${r1} 0 ${big} 1 ${x1},${y1} L${x2},${y2} A${r0},${r0} 0 ${big} 0 ${x3},${y3} Z`;
}

/** KPI 글자 폭 어림(글자 수 --n 대신, dashboard.css가 0.62em × n으로 나눈다): 한글·한자·전각은 약 1em이라 1/0.62, 나머지는 1 */
export const textUnits = (text: string) => [...text].reduce((a, c) => a + (/[\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]/.test(c) ? 1 / 0.62 : 1), 0);
/** 글자를 px 폭(글자 크기 font)에 맞춰 자른다(넘치면 끝에 …). 폭은 textUnits × 0.62em(한글 약 1em) */
export function fitText(s: string, px: number, font: number) {
  const w = (t: string) => textUnits(t) * 0.62 * font, out = [...s];
  if (w(s) <= px) return s;
  while (out.length && w(out.join("") + "…") > px) out.pop();
  return out.join("") + "…";
}

// ======================= Phase 3: 사다리꼴 퍼널 보기·가로축 확대·이동 =======================
/** 단계마다 따로 센 퍼널(count = each)의 한 줄 설명(순서대로가 아니라 funnelSentence를 쓰지 않는다) */
export const EACH_TEXT = "단계마다 따로 센 사람 수예요. 앞 단계를 거치지 않은 사람도 들어가요.";
/** 사다리꼴 한 칸: 번호 i(0부터), 경로, 옆 글 두 줄의 자리 */
export type LadderRow = { i: number; path: string; top: number; bottom: number; x0: number; x1: number; textX: number; textY: number; lines: [string, string] };
// [SPECIFICATION] plans/phase-3.md#ladderLines
export function ladderLines(step: Step, i: number): [string, string] {
  const users = `${step.users.toLocaleString()}명`;
  return [`${i + 1}. ${step.name}`, i === 0 ? users : `${users} · 전 단계 대비 ${fpct(step.conv)} · 첫 단계 대비 ${fpct(step.from_first)}`];
}
export const LADDER_MIN = 0.06;  // 인원 비례 사다리꼴에서 가장 좁은 폭 = 최대 폭 × 이 몫(0명이어도 보이게)
// [SPECIFICATION] plans/phase-3.md#ladderLayout
export function ladderLayout(steps: Step[], { width = 720, height = 340, gap = 6, shapeShare = 0.5, minRatio = 0.3, pad = 8, textGap = 12, scale = false } = {}): { width: number; height: number; rows: LadderRow[] } {
  const k = steps.length;
  if (k === 0) return { width, height, rows: [] };
  const shapeW = width * shapeShare, maxW = shapeW - 2 * pad, minW = maxW * minRatio, cx = shapeW / 2, rowH = (height - 2 * pad - gap * (k - 1)) / k;
  const most = Math.max(...steps.map(s => s.users)), w = (n: number) => maxW * Math.max(LADDER_MIN, most > 0 ? n / most : 0), f = (n: number) => n.toFixed(1);
  const rows = steps.map((step, i): LadderRow => {
    const top = pad + i * (rowH + gap), bottom = top + rowH;
    const wTop = scale ? w(step.users) : maxW - ((maxW - minW) * i) / k;
    const wBot = scale ? (i + 1 < k ? w(steps[i + 1].users) : wTop) : maxW - ((maxW - minW) * (i + 1)) / k;
    const x0 = cx - wTop / 2, x1 = cx + wTop / 2;
    return { i, path: `M${f(x0)},${f(top)} L${f(x1)},${f(top)} L${f(cx + wBot / 2)},${f(bottom)} L${f(cx - wBot / 2)},${f(bottom)} Z`, top, bottom, x0, x1, textX: shapeW + textGap, textY: top + rowH / 2, lines: ladderLines(step, i) };
  });
  return { width, height, rows };
}

/** 걸린 시간 표의 값 종류(view.gap_stats에 고른 것만, 차례 고정)와 머리글 */
export const GAP_STATS = ["mean", "median", "trimmed"] as const;
export type GapStat = (typeof GAP_STATS)[number];
/** 서버 funnel.gap_stats 한 줄(초, 사람이 없으면 값 null) */
export type Gap = { from: string; to: string; n: number; mean: number | null; median: number | null; trimmed: number | null };
const SEC_PER_MIN = 60, SEC_PER_HOUR = 3600, SEC_PER_DAY = 86400;  // 걸린 시간 글자의 단위 경계(초)
// [SPECIFICATION] plans/phase-3.md#durationText
export function durationText(sec: number | null | undefined): string {
  if (sec == null) return "-";
  if (sec < SEC_PER_MIN) return `${+sec.toFixed(1)}초`;
  if (sec < SEC_PER_HOUR) return `${+(sec / SEC_PER_MIN).toFixed(1)}분`;
  if (sec < SEC_PER_DAY) return `${+(sec / SEC_PER_HOUR).toFixed(1)}시간`;
  return `${+(sec / SEC_PER_DAY).toFixed(1)}일`;
}
const GAP_NAMES: Record<Exclude<GapStat, "trimmed">, string> = { mean: "평균", median: "중앙값" };  // 걸린 시간 표의 값 이름(절사평균은 몫을 붙여 따로)
const PERCENT = 100;  // 자르는 몫(0~0.5)을 글자 %로
// [SPECIFICATION] plans/phase-3.md#gapRows
export function gapRows(gaps: Gap[], stats: GapStat[], trim: number): { label: string; values: string }[] {
  if (!stats.length) return [];
  const picked = GAP_STATS.filter(s => stats.includes(s));
  const name = (s: GapStat) => (s === "trimmed" ? `절사평균(${Math.round(trim * PERCENT)}%)` : GAP_NAMES[s]);
  return gaps.map(g => ({
    label: `${g.from} → ${g.to}`,
    values: g.n === 0 ? "지나간 사람이 없어요" : `${picked.map(s => `${name(s)} ${durationText(g[s])}`).join(" · ")} (${g.n.toLocaleString()}명)`,
  }));
}

/** 가로축 범위 [시작, 끝]: 순서 축은 번호(0 ~ n-1), 값 축은 x 값 */
export type Span = [number, number];
/** 확대할 수 있는 가로축: 순서(라벨 n개) 또는 값(최소~최대). 확대할 수 없는 그림은 null */
export type ZoomAxis = { kind: "index"; count: number } | { kind: "value"; lo: number; hi: number };
export const ZOOM_FACTOR = 1.25;  // 휠 한 칸에 보이는 폭이 바뀌는 배수(확대는 1/1.25)
export const MIN_POINTS = 5;  // 순서 축에서 가장 크게 확대했을 때 남는 라벨 수
export const MIN_SHARE = 0.001;  // 값 축에서 가장 크게 확대했을 때 폭 = 전체 폭 × 이 몫
export const DRAG_PX = 4;  // 이만큼 넘게 끌어야 이동으로 본다(덜 움직이면 카드 누르기 그대로)
// [SPECIFICATION] plans/phase-3.md#flowDropout
export function flowDropout(card: Card): boolean {
  const drop = (card.view as { flow_dropout?: unknown } | undefined)?.flow_dropout;
  if (typeof drop === "boolean") return drop;
  return (((card.source?.analysis as { ends?: string[] } | undefined)?.ends) ?? []).length > 0;
}
// [SPECIFICATION] plans/phase-3.md#fullSpan
export function fullSpan(axis: ZoomAxis): Span {
  return axis.kind === "index" ? [0, axis.count - 1] : [axis.lo, axis.hi];
}
// [SPECIFICATION] plans/phase-3.md#zoomRange
export function zoomRange(view: Span, axis: ZoomAxis, anchor: number, notches: number): Span {
  const [f0, f1] = fullSpan(axis), full = f1 - f0, w = view[1] - view[0];
  const minW = axis.kind === "index" ? Math.min(full, MIN_POINTS - 1) : full * MIN_SHARE;
  const nw = clamp(w * ZOOM_FACTOR ** (-notches), minW, full);
  const s = clamp(view[0] + anchor * w - anchor * nw, f0, f1 - nw);
  return [s, s + nw];
}
// [SPECIFICATION] plans/phase-3.md#panRange
export function panRange(view: Span, axis: ZoomAxis, share: number): Span {
  const [f0, f1] = fullSpan(axis), w = view[1] - view[0];
  const s = clamp(view[0] - share * w, f0, f1 - w);
  return [s, s + w];
}
// [SPECIFICATION] plans/phase-3.md#sliceWindow
export function sliceWindow<T>(rows: T[], view: Span | null): T[] {
  if (view === null) return rows;
  const i0 = Math.max(0, Math.floor(view[0])), i1 = Math.min(rows.length - 1, Math.ceil(view[1]));
  return rows.slice(i0, i1 + 1);
}
// [SPECIFICATION] plans/phase-3.md#zoomAxis
export function zoomAxis(card: SCard, s: Series | null, ana: boolean): ZoomAxis | null {
  const index = (n: number): ZoomAxis | null => (n > MIN_POINTS ? { kind: "index", count: n } : null);
  const value = (xs: number[]): ZoomAxis | null => {
    const real = xs.filter(Number.isFinite), lo = Math.min(...real), hi = Math.max(...real);
    return lo < hi ? { kind: "value", lo, hi } : null;
  };
  if (ana) {
    const d = card.data.values as Ana;
    switch (anaView(card)) {
      case "hist": return index(d.histogram.counts.length);
      case "values": return index((d.labels ?? []).length + (d.nulls ? 1 : 0) + (d.other ? 1 : 0));
      case "scatter": return value(d.points.map(r => r[0]));
      case "box":
      case "heat": return null;
      default: return index((d.labels ?? []).length);
    }
  }
  if (isPoints(card)) return value((card.data.points as { points: number[][] }).points.map(r => r[0]));
  if (!s || NO_ZOOM_TYPES.includes(card.view.type)) return null;
  return index(s.labels.length);
}
