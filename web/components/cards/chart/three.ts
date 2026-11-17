// 3차원 그래프(막대·곡면·산점도)의 순수 계산(화면 요소 없음): 투영, 뒤에서 앞으로 그릴 차례, 높이 색, 축 눈금, 그릴 모양 만들기, 그릴 수 없는 이유. web/tests/chart3d.test.ts가 확인한다.
// 좌표는 한 변이 1인 정육면체 안(x 오른쪽, y 안쪽, z 위). 새 의존성 없이 SVG에 직접 그린다(그리는 쪽은 Chart3D.tsx).
import { clamp, shownum } from "@/lib/calc";
import { ZOOM_FACTOR, fitText } from "../svg/__pure__";

export type P3 = [number, number, number];
/** 보는 각도: yaw(세로축 둘레, 라디안), pitch(위에서 내려다보는 각, 라디안), zoom(배율) */
export type View3 = { yaw: number; pitch: number; zoom: number };
export const START_VIEW: View3 = { yaw: -0.6, pitch: 0.5, zoom: 1 };  // 처음 각도(두 번 누르면 돌아온다)
export const ROTATE_PER_PX = 0.01;  // 1px 끌 때 도는 각(라디안)
export const PITCH_RANGE: [number, number] = [0.05, Math.PI / 2 - 0.05];  // 바닥 아래·바로 위로는 넘어가지 않는다
export const ZOOM_RANGE: [number, number] = [0.5, 4];  // 휠로 줄이고 키우는 배율 범위
export const FILL = 0.5;  // 배율 1일 때 정육면체 한 변 = 그림 짧은 변 × 이 몫(대각선과 축 글자가 들어가게)
export const MAX_CELLS_3D = 2000;  // 3차원 막대·곡면의 칸 수(X 값 × 계열) 상한
export const AXIS_TICKS = 5;  // 숫자 축 눈금 수(어림)
export const MAX_AXIS_LABELS = 4;  // 이름 축(X·계열)에 적는 글자 수 상한(넘으면 건너뛰며 적는다. 비스듬한 모서리에서 글자가 겹치지 않을 만큼)
export const LABEL_PX = 72, LABEL_FONT = 11;  // 축 글자 한 칸의 폭·크기(px)
const BAR_FILL = 0.7;  // 막대 굵기 = 칸 × 이 몫
const TICK_GAP = 0.05, NAME_GAP = 0.4;  // 눈금 글자·축 이름이 바닥 모서리에서 떨어진 거리(이름은 눈금 글자 한 칸 너머)
const SHADE = { x: 82, y: 66 };  // 막대 옆면의 밝기(윗면 색 %)
const LINE_W = { over: 2.5, link: 1.5, grid: 0.5 }, DOT_R = 3;
export const SMOOTH_STEPS = [1, 2, 4, 8];  // 곡면 매끈함 단계(0단계 = 받은 그대로)마다 칸 한 변을 나누는 배수
export const MAX_FACES_3D = 8000;  // 곡면을 잘게 나눠 그리는 면 수 상한(넘는 매끈함 단계는 고를 수 없다)
export const SPREAD_RANGE: [number, number] = [0, 10], SPREAD_START = 2, SPREAD_STEP = 0.5;  // 손잡이를 끌 때 주변이 따라오는 넓이(칸 수, 0이면 그 점만)
const GRIP_R = 3.5, GRIP_HELP = "위아래로 끌면 이 점의 높이가 바뀌어요.";  // 모양 조절 손잡이

/** 정육면체 안의 점 → [화면 x, 화면 y, 깊이(클수록 보는 쪽에 가깝다)]. 원근 없이(평행 투영) 정육면체 가운데가 그림 가운데 */
export function project([x, y, z]: P3, v: View3, w: number, h: number): P3 {
  const cx = x - 0.5, cy = y - 0.5, cz = z - 0.5, cos = Math.cos(v.yaw), sin = Math.sin(v.yaw);
  const rx = cx * cos - cy * sin, ry = cx * sin + cy * cos, k = Math.min(w, h) * FILL * v.zoom;
  return [w / 2 + rx * k, h / 2 - (ry * Math.sin(v.pitch) + cz * Math.cos(v.pitch)) * k, cz * Math.sin(v.pitch) - ry * Math.cos(v.pitch)];
}
/** 끌어서 돌리기: 오른쪽으로 끌면 앞쪽이 오른쪽으로, 아래로 끌면 더 위에서 본다 */
export const orbit = (v: View3, dx: number, dy: number): View3 => ({ ...v, yaw: v.yaw + dx * ROTATE_PER_PX, pitch: clamp(v.pitch + dy * ROTATE_PER_PX, ...PITCH_RANGE) });
/** 휠 notches칸(양수 = 크게) */
export const zoomed = (v: View3, notches: number): View3 => ({ ...v, zoom: clamp(v.zoom * ZOOM_FACTOR ** notches, ...ZOOM_RANGE) });

/** 왼쪽 단추로 끌기 시작할 때 할 일: Ctrl을 눌렀으면 언제나 돌리기(켜지 않았어도, 손잡이 위여도), 아니면 켜진 그림에서만 손잡이 위는 높이 조절("lift")·그 밖은 돌리기("turn"). 아무것도 안 하면 null */
export const dragMode = (ctrl: boolean, active: boolean, onGrip: boolean): "turn" | "lift" | null => (ctrl ? "turn" : !active ? null : onGrip ? "lift" : "turn");
/** 화면에서 1px 위로 끌 때 정육면체 높이(z)가 바뀌는 양(project의 세로 식을 뒤집은 것) */
export const zPerPx = (v: View3, w: number, h: number): number => 1 / (Math.min(w, h) * FILL * v.zoom * Math.cos(v.pitch));

/** 높이 t(0 낮음 ~ 1 높음)의 색: low와 high 사이를 섞는다(CSS 변수 색도 된다) */
export const heightColor = (t: number, low: string, high: string) => `color-mix(in srgb, ${high} ${Math.round(clamp(t, 0, 1) * 100)}%, ${low})`;
const shade = (color: string, pct: number) => `color-mix(in srgb, ${color} ${pct}%, var(--text))`;

/** lo~hi를 덮는 깔끔한 눈금(간격은 1·2·5 × 10^k). 값이 하나뿐이면(lo = hi) 그 값의 크기만큼(0이면 1) 위아래로 넓힌다 */
export function niceTicks(lo: number, hi: number, n = AXIS_TICKS): number[] {
  const pad = hi === lo ? Math.abs(lo) || 1 : 0, a = lo - pad, b = hi + pad;
  const raw = (b - a) / n, pow = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 5, 10].find(m => m * pow >= raw)! * pow;
  const from = Math.floor(a / step), to = Math.ceil(b / step);
  return Array.from({ length: to - from + 1 }, (_, i) => +((from + i) * step).toPrecision(12));
}

/** 그릴 모양 하나. anchor: 차례를 정할 자리(없으면 꼭짓점 평균), bias: 그 깊이에 더할 값, layer: -1 맨 뒤·1 맨 앞, normal: 면이 보는 쪽(뒤를 보면 안 그린다) */
type Order = { anchor?: P3; bias?: number; layer?: -1 | 1 };
export type Shape = Order & (
  | { kind: "poly"; pts: P3[]; fill: string; stroke?: string; normal?: P3; title?: string }
  | { kind: "line"; pts: P3[]; stroke: string; width: number }
  | { kind: "dot"; pts: P3[]; fill: string; r: number; title?: string; grip?: [number, number] }  // grip: 모양 조절 손잡이의 격자 자리 [X 번호, 계열 번호]
  | { kind: "text"; pts: P3[]; text: string; align: "start" | "middle" | "end"; dx?: number; dy?: number; bold?: boolean });
export type Painted = { shape: Shape; xy: [number, number][]; key: number; depth: number };

/** 모양들을 투영해 뒤에서 앞으로 그릴 차례로(화가 알고리즘). 뒤를 보는 면은 뺀다 */
export function paint(shapes: Shape[], v: View3, w: number, h: number): Painted[] {
  const out: Painted[] = [];
  for (const shape of shapes) {
    if (shape.kind === "poly" && shape.normal && project(shape.normal.map(n => n + 0.5) as P3, v, w, h)[2] <= 0) continue;
    const p = shape.pts.map(q => project(q, v, w, h)), depth = p.reduce((a, q) => a + q[2], 0) / p.length;
    const key = shape.layer ? shape.layer * Infinity : (shape.anchor ? project(shape.anchor, v, w, h)[2] : depth) + (shape.bias ?? 0);
    out.push({ shape, xy: p.map(q => [q[0], q[1]]), key, depth });
  }
  return out.sort((a, b) => a.key - b.key || a.depth - b.depth);
}

export type Axis3 = { name: string; ticks: { at: number; text: string }[] };
export type Key3 = { name: string; color: string };
export type Scene = { shapes: Shape[]; axes: { x: Axis3; y: Axis3; z: Axis3 }; keys: Key3[] };

/** 보는 각도에 맞춘 축: 바닥 테두리, 먼 두 벽의 높이 눈금선, 가까운 바닥 모서리의 X·계열 글자, 화면 왼쪽 끝 모서리의 높이 글자, 축 이름 */
export function axisShapes(axes: Scene["axes"], v: View3): Shape[] {
  const at = (p: P3) => project(p, v, 1, 1);
  const xn = at([1, 0.5, 0.5])[2] > at([0, 0.5, 0.5])[2] ? 1 : 0, yn = at([0.5, 1, 0.5])[2] > at([0.5, 0, 0.5])[2] ? 1 : 0, xf = 1 - xn, yf = 1 - yn;  // 가까운 쪽·먼 쪽
  const [zx, zy] = at([xf, yn, 0])[0] < at([xn, yf, 0])[0] ? [xf, yn] : [xn, yf];  // 두 옆 모서리 중 화면 왼쪽
  const out = (near: number, gap: number) => (near ? 1 + gap : -gap);
  const rule = (pts: P3[]): Shape => ({ kind: "line", pts, stroke: "var(--line)", width: 1, layer: -1 });
  const text = (p: P3, t: string, align: "start" | "end", more: { dx?: number; dy?: number; bold?: boolean } = {}): Shape => ({ kind: "text", pts: [p], text: t, align, layer: 1, ...more });
  const away = (edge: P3, p: P3) => (at(p)[0] < at(edge)[0] ? "end" : "start");  // 글자는 모서리에서 바깥쪽으로 뻗는다(두 축의 글자가 가까운 꼭짓점에서 겹치지 않게)
  const floor = (edge: P3, p: P3, t: string, bold = false) => text(p, t, away(edge, p), { dy: 4, bold });
  const all: Shape[] = [
    rule([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 0]]), rule([[xf, yf, 0], [xf, yf, 1]]), rule([[xf, yn, 0], [xf, yn, 1]]), rule([[xn, yf, 0], [xn, yf, 1]]),
    ...axes.z.ticks.flatMap(t => [rule([[xf, 0, t.at], [xf, 1, t.at]]), rule([[0, yf, t.at], [1, yf, t.at]]), text([zx, zy, t.at], t.text, "end", { dx: -6, dy: 4 })]),
    ...axes.x.ticks.map(t => floor([t.at, yn, 0], [t.at, out(yn, TICK_GAP), 0], t.text)),
    ...axes.y.ticks.map(t => floor([xn, t.at, 0], [out(xn, TICK_GAP), t.at, 0], t.text)),
    floor([0.5, yn, 0], [0.5, out(yn, NAME_GAP), 0], axes.x.name, true), floor([xn, 0.5, 0], [out(xn, NAME_GAP), 0.5, 0], axes.y.name, true),
    text([zx, zy, 1], axes.z.name, "end", { dx: -6, dy: -12, bold: true }),
  ];
  return all.filter(s => s.kind !== "text" || s.text !== "");
}

const num = (v: number | string | null | undefined) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
/** 이름 축의 눈금: 글자가 많으면 건너뛰며, 긴 이름은 줄여서 */
const nameTicks = (names: string[], at: (i: number) => number): Axis3["ticks"] => {
  const step = Math.ceil(names.length / MAX_AXIS_LABELS);
  return names.flatMap((name, i) => (i % step ? [] : [{ at: at(i), text: fitText(name, LABEL_PX, LABEL_FONT) }]));
};
const valueTicks = (ticks: number[], fmt: (v: number) => string): Axis3["ticks"] => ticks.map(t => ({ at: (t - ticks[0]) / (ticks[ticks.length - 1] - ticks[0]), text: fmt(t) }));

export type GridType = "bar3d" | "surface3d";
export type GridSet = { label: string; values: (number | string | null)[]; line: boolean; color: string };
export const NEED_THREE = "3차원 그래프는 축이 세 개 필요해요. [나누기]에 컬럼을 넣거나 계열을 둘 이상 넣어 주세요.";
/** 3차원 막대·곡면(X 값 × 계열 × 값)을 그릴 수 없는 이유(그릴 수 있으면 빈 글자) */
export function gridReason(type: GridType, labels: number, sets: { values: (number | string | null)[] }[]): string {
  if (sets.length < 2) return NEED_THREE;
  if (type === "surface3d" && labels < 2) return "3차원 곡면은 X축 값이 둘 이상 있어야 해요. X축에 값이 여러 개인 컬럼을 골라 주세요.";
  if (labels * sets.length > MAX_CELLS_3D) return `칸이 ${(labels * sets.length).toLocaleString()}개라 3차원으로 그리기에 너무 많아요(상한 ${MAX_CELLS_3D.toLocaleString()}개). X축 값이나 계열 수를 줄여 주세요.`;
  if (!sets.some(d => d.values.some(v => num(v) !== null))) return "3차원으로 그릴 숫자 값이 없어요.";
  return "";
}

type Cell = number | null;
/** Catmull-Rom 곡선: p1~p2 사이 t(0~1)의 값(t = 0이면 p1, 1이면 p2). 바깥 이웃(p0·p3)이 없으면(줄의 끝이거나 빈 값) 직선으로 이어 간 자리로 본다 */
function catmull(p0: Cell | undefined, p1: number, p2: number, p3: Cell | undefined, t: number): number {
  const a = p0 ?? 2 * p1 - p2, d = p3 ?? 2 * p2 - p1;
  return 0.5 * (2 * p1 + (p2 - a) * t + (2 * a - 5 * p1 + 4 * p2 - d) * t * t + (3 * p1 - a - 3 * p2 + d) * t * t * t);
}
/** 한 줄을 k배로 잘게: 원래 점은 그대로, 사이는 곡선. 빈 값에 닿는 구간의 사이 점은 빈 값 */
const refine = (row: Cell[], k: number): Cell[] => Array.from({ length: (row.length - 1) * k + 1 }, (_, n) => {
  const i = Math.floor(n / k), t = (n % k) / k, p1 = row[i], p2 = row[i + 1];
  return t === 0 || p1 === null ? p1 : p2 === null ? null : catmull(row[i - 1], p1, p2, row[i + 2], t);
});
/** 격자 vals[계열][X]를 가로·세로 k배로 잘게(Catmull-Rom). 원래 격자점의 값은 그대로 지나고, 네 꼭짓점 중 빈 값이 있는 칸의 안쪽 점은 빈 값(채우지 않는다) */
export function smoothGrid(vals: Cell[][], k: number): Cell[][] {
  const wide = vals.map(row => refine(row, k)), cols = wide[0].map((_, n) => refine(wide.map(row => row[n]), k));
  return cols[0].map((_, m) => cols.map(col => col[m]));
}
/** 매끈함 단계 step(SMOOTH_STEPS의 번호)으로 그릴 면 수가 상한을 넘으면 그 이유(고를 수 있으면 빈 글자) */
export function smoothReason(nx: number, ny: number, step: number): string {
  const faces = (nx - 1) * (ny - 1) * SMOOTH_STEPS[step] ** 2;
  return faces > MAX_FACES_3D ? `면이 ${faces.toLocaleString()}개가 되어 이 단계는 그릴 수 없어요(상한 ${MAX_FACES_3D.toLocaleString()}개). X축 값이나 계열 수를 줄이면 고를 수 있어요.` : "";
}
/** 고른 단계 want 이하에서 그릴 수 있는 가장 높은 단계(0단계는 칸 수 상한 MAX_CELLS_3D 안이라 늘 된다) */
export function smoothStep(nx: number, ny: number, want: number): number {
  let step = want;
  while (smoothReason(nx, ny, step)) step--;
  return step;
}
/** 손잡이 (i, j)를 값 dv만큼 끌어 올린 뒤의 더할 높이 격자 [계열][X](값 단위). 그 점은 dv, 주변은 칸 거리 d에 따라 dv × exp(-d² / 2·spread²)(가우스, spread 0이면 그 점만). lift는 끌기 전의 격자(없으면 0) */
export function liftGrid(lift: number[][] | null, nx: number, ny: number, i: number, j: number, dv: number, spread: number): number[][] {
  return Array.from({ length: ny }, (_, y) => Array.from({ length: nx }, (_, x) => {
    const d2 = (x - i) ** 2 + (y - j) ** 2;
    return (lift?.[y][x] ?? 0) + dv * (d2 === 0 ? 1 : Math.exp(-d2 / (2 * spread * spread)));
  }));
}

/** 곡면 모양: smooth = 칸을 나누는 배수(SMOOTH_STEPS의 값), lift = 격자점마다 더할 높이(liftGrid, 없으면 받은 값 그대로), grips = 격자점 손잡이를 그릴지 */
export type Shaping = { smooth: number; lift: number[][] | null; grips: boolean };
export const PLAIN: Shaping = { smooth: 1, lift: null, grips: false };
/** 3차원 막대·곡면의 그림: span은 값 축의 길이(값 단위, 끈 거리를 값으로 바꿀 때 쓴다) */
export type GridScene = Scene & { span: number };

/** 3차원 막대·곡면의 그릴 모양. 막대는 칸마다 상자(계열 색, 0에서 값까지), 곡면은 이웃한 네 값의 면(높이 색 low→high)과 격자선, 선 계열(line)은 곡면 위의 선.
 *  names = [X축 이름, 계열 축 이름, 값 축 이름], fmt는 값 눈금 글자. shape(곡면만): 잘게 나눈 면은 테두리를 면 색으로 칠하고 원래 격자선만 따로 긋는다. 높이 축은 조절·보간한 값까지 덮는다 */
export function gridScene(type: GridType, labels: string[], sets: GridSet[], low: string, high: string, names: [string, string, string], fmt: (v: number) => string, shape: Shaping = PLAIN): GridScene {
  const nx = labels.length, ny = sets.length, bars = type === "bar3d", k = shape.smooth;
  const vals = sets.map((d, j) => labels.map((_, i) => { const v = num(d.values[i]); return v === null ? null : v + (shape.lift?.[j][i] ?? 0); }));
  const fine = bars ? vals : smoothGrid(vals, k), some = (v: Cell): v is number => v !== null, flat = vals.flat().filter(some), drawn = fine.flat().filter(some);
  const ticks = niceTicks(Math.min(...drawn, ...(bars ? [0] : [])), Math.max(...drawn, ...(bars ? [0] : [])));
  const z0 = ticks[0], z1 = ticks[ticks.length - 1], zn = (v: number) => (v - z0) / (z1 - z0);
  const xa = (i: number) => (bars ? (i + 0.5) / nx : i / (nx - 1)), ya = (j: number) => (bars ? (j + 0.5) / ny : j / (ny - 1));
  const shapes: Shape[] = [];
  if (bars) {
    const hx = BAR_FILL / 2 / nx, hy = BAR_FILL / 2 / ny, base = zn(0);
    vals.forEach((row, j) => row.forEach((v, i) => {
      if (v === null) return;
      const x = xa(i), y = ya(j), lo = Math.min(base, zn(v)), hi = Math.max(base, zn(v)), anchor: P3 = [x, y, 0.5], c = sets[j].color, title = `${labels[i]} · ${sets[j].label}: ${shownum(v)}`;
      const face = (pts: P3[], normal: P3, fill: string): Shape => ({ kind: "poly", pts, fill, stroke: "var(--card)", normal, anchor, title });
      const side = (a: [number, number], b: [number, number]): P3[] => [[a[0], a[1], lo], [b[0], b[1], lo], [b[0], b[1], hi], [a[0], a[1], hi]];
      shapes.push(
        face([[x - hx, y - hy, hi], [x + hx, y - hy, hi], [x + hx, y + hy, hi], [x - hx, y + hy, hi]], [0, 0, 1], c),
        face(side([x - hx, y - hy], [x - hx, y + hy]), [-1, 0, 0], shade(c, SHADE.x)), face(side([x + hx, y - hy], [x + hx, y + hy]), [1, 0, 0], shade(c, SHADE.x)),
        face(side([x - hx, y - hy], [x + hx, y - hy]), [0, -1, 0], shade(c, SHADE.y)), face(side([x - hx, y + hy], [x + hx, y + hy]), [0, 1, 0], shade(c, SHADE.y)));
    }));
  } else {
    const fx = (n: number) => n / ((nx - 1) * k), fy = (m: number) => m / ((ny - 1) * k);  // 잘게 나눈 격자의 자리(k = 1이면 xa·ya와 같다)
    const over = (fx(1) + fy(1)) / 2;  // 선은 이웃한 면보다 나중에(면 가운데까지의 거리보다 크게)
    const face = (m: number, n: number) => [fine[m]?.[n], fine[m]?.[n + 1], fine[m + 1]?.[n + 1], fine[m + 1]?.[n]].every(v => v != null);
    const rule = (pts: P3[], stroke: string, width: number): Shape => ({ kind: "line", pts, stroke, width, anchor: [(pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2, 0.5], bias: over });
    for (let m = 0; m < fine.length; m++) {
      for (let n = 0; n < fine[m].length; n++) {
        const a = fine[m][n], b = fine[m][n + 1], c = fine[m + 1]?.[n + 1], d = fine[m + 1]?.[n];
        if (a === null) continue;
        if (b != null && c != null && d != null) {
          const fill = heightColor((zn(a) + zn(b) + zn(c) + zn(d)) / 4, low, high);
          shapes.push({ kind: "poly", pts: [[fx(n), fy(m), zn(a)], [fx(n + 1), fy(m), zn(b)], [fx(n + 1), fy(m + 1), zn(c)], [fx(n), fy(m + 1), zn(d)]],
            fill, stroke: k > 1 ? fill : "var(--card)", anchor: [(fx(n) + fx(n + 1)) / 2, (fy(m) + fy(m + 1)) / 2, 0.5] });
        }
        if (b != null && m % k === 0) {  // 원래 격자의 계열 줄: 선 계열이면 곡면 위의 선, 잘게 나눴으면 격자선(면에 닿은 것만)
          const across: P3[] = [[fx(n), fy(m), zn(a)], [fx(n + 1), fy(m), zn(b)]];
          if (sets[m / k].line) shapes.push(rule(across, sets[m / k].color, LINE_W.over));
          else if (k > 1 && (face(m, n) || face(m - 1, n))) shapes.push(rule(across, "var(--card)", LINE_W.grid));
        }
        if (k > 1 && d != null && n % k === 0 && (face(m, n) || face(m, n - 1))) shapes.push(rule([[fx(n), fy(m), zn(a)], [fx(n), fy(m + 1), zn(d)]], "var(--card)", LINE_W.grid));  // 원래 격자의 X 줄
      }
    }
    if (shape.grips) {
      vals.forEach((row, j) => row.forEach((v, i) => {
        if (v !== null) shapes.push({ kind: "dot", pts: [[xa(i), ya(j), zn(v)]], fill: "var(--text)", r: GRIP_R, grip: [i, j], title: GRIP_HELP, anchor: [xa(i), ya(j), 0.5], bias: over * 2 });
      }));
    }
  }
  const lines = sets.filter(d => d.line).map(d => ({ name: d.label, color: d.color }));
  return {
    span: z1 - z0, shapes, axes: { x: { name: names[0], ticks: nameTicks(labels, xa) }, y: { name: names[1], ticks: nameTicks(sets.map(d => d.label), ya) }, z: { name: names[2], ticks: valueTicks(ticks, fmt) } },
    keys: bars ? sets.map(d => ({ name: d.label, color: d.color })) : [{ name: `낮음 ${fmt(Math.min(...flat))}`, color: low }, { name: `높음 ${fmt(Math.max(...flat))}`, color: high }, ...lines],
  };
}

export const NEED_Z = "3차원 산점도는 숫자 컬럼이 셋 필요해요. [Z축]에 숫자 컬럼을 골라 주세요.";
export type Points3 = { x: string; y: string; points: (number | string)[][]; groups: string[] };
/** 3차원 산점도(점 [x, y, z, (그룹)])를 그릴 수 없는 이유(그릴 수 있으면 빈 글자) */
export function pointsReason(p: Points3): string {
  if (!p.points.length) return "3차원 산점도로 그릴 점이 없어요. X축·Y축·Z축 값이 모두 숫자인 행이 있어야 해요.";
  if (p.points[0].length < (p.groups.length ? 4 : 3)) return NEED_Z;
  if (p.points.some(r => r.slice(0, 3).some(v => typeof v !== "number" || !Number.isFinite(v)))) return "3차원 산점도의 X축·Y축·Z축 값은 숫자여야 해요. 숫자가 아닌 값이 들어 있어요.";
  return "";
}

/** 3차원 산점도의 그릴 모양: 점마다 원(그룹마다 colors의 색), connect면 그룹마다 받은 차례대로 선으로 잇는다. zName은 Z축 이름 */
export function pointsScene(p: Points3, colors: string[], connect: boolean, zName: string, fmt: (v: number) => string): Scene {
  const names = [p.x, p.y, zName], groups = p.groups.length ? p.groups : [p.y];
  const ticks = [0, 1, 2].map(k => { const vs = p.points.map(r => r[k] as number); return niceTicks(Math.min(...vs), Math.max(...vs)); });
  const at = (r: (number | string)[]): P3 => [0, 1, 2].map(k => ((r[k] as number) - ticks[k][0]) / (ticks[k][ticks[k].length - 1] - ticks[k][0])) as P3;
  const shapes: Shape[] = [];
  groups.forEach((g, gi) => {
    const rows = p.points.filter(r => !p.groups.length || r[r.length - 1] === g);
    rows.forEach((r, i) => {
      shapes.push({ kind: "dot", pts: [at(r)], fill: colors[gi], r: DOT_R, title: `${p.groups.length ? `${g} · ` : ""}${names.map((n, k) => `${n} ${shownum(r[k])}`).join(", ")}` });
      if (connect && i > 0) shapes.push({ kind: "line", pts: [at(rows[i - 1]), at(r)], stroke: colors[gi], width: LINE_W.link });
    });
  });
  const axis = (k: number): Axis3 => ({ name: names[k], ticks: valueTicks(ticks[k], fmt) });
  return { shapes, axes: { x: axis(0), y: axis(1), z: axis(2) }, keys: p.groups.length ? groups.map((g, gi) => ({ name: g, color: colors[gi] })) : [] };
}
