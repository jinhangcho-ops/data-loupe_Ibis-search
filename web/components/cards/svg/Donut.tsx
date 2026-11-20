"use client";
// donut_kpi: 가운데 칸(0)과 네 꼭지점 칸(1~4), 고리 4방식(view.ring: stack·double·half·ticks). 목표를 넘은 부분은 진한 색(옛 paintDonut)
import { TAU, arcPath, clamp, darken, overTarget, polar, showValue, underTarget } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { ROUND_PX } from "../chart/shapes";
import { useRootScale } from "../chart/scale";
import { cardFormat, cardTarget, cardValue, growFont, sectorPath } from "./__pure__";
import { Box, Frame, type P } from "./common";

const MAIN = 22, LABEL = 11, CORNER = 32, HOLE_FIT = 0.85, MAIN_MIN = 12;  // 가운데 값·이름의 기준 글자 크기, 꼭지점 칸 두 줄의 높이(화면 크기 배율 1)
const STACK_OUT = 74, STACK_IN = 48, RIM = 8, SLICE_GAP = 2;  // 49-donut: 조각 바깥·안쪽 반지름, 솟은 바탕 원·가운데 원과 고리 사이 틈(8/82라 그림자가 고리에 닿지 않는다), 조각 사이 틈(카드 색 선)
const RING_R = 80, RING_STEP = 22, RING_W = 16;  // 75-activity-rings: 바깥 고리 반지름, 안쪽 고리까지의 간격, 고리 굵기
const HALF_R = 104, HALF_W = 22, HALF_BASE = 150, HALF_LIFT = 38;  // 74-gauge: 반원 반지름·굵기, 반원 밑변 높이, 가운데 글을 밑변에서 올리는 만큼
const MARK_INSET = 3, MARK_W = 3;  // 26-bullet의 목표 선: 고리 안쪽으로 들어간 만큼, 굵기
const RING_FILL = 0.95;  // 도넛 차트(RoundChart)의 바깥 반지름 비율: 고리 지름을 그 도넛과 같게 맞춘다

type SlotLike = { label: string; format?: string; unit_text?: string; color?: string };

export function Donut({ card, colors, range, merge }: P) {
  const s = useRootScale();
  const slots: SlotLike[] = card.view?.slots?.length ? card.view.slots : (card.data?.by_unit?.day?.series ?? []).map(s => ({ label: s.name })), ring = card.view?.ring || "stack";
  const vals = slots.map((_, i) => cardValue(card, i, range, merge)), target = cardTarget(card), main = vals[0] ?? 0, color = (i: number) => slots[i]?.color || pickColor(card, "series", slots[i]?.label ?? String(i), colors, i);
  const grow = CORNER * (s - 1), W = 260, H = 200 + 2 * grow, cx = W / 2, cy = 100 + grow, hcy = HALF_BASE + grow, c0 = color(0), over = darken(c0), fmt = (i: number) => showValue(vals[i], slots[i]?.format || cardFormat(card), slots[i]?.unit_text);
  const kids: React.ReactNode[] = [];
  const ringTrack = (key: string, rad: number) => <circle key={key} cx={cx} cy={cy} r={rad} fill="none" stroke={colors.line} strokeWidth={RING_W} />;  // 고리 아래 트랙(그림자 없음)
  const arc = (key: string, y: number, rad: number, a0: number, a1: number, stroke: string, width: number) => a1 > a0 && <path key={key} className="nm-raise" d={arcPath(cx, y, rad, a0, a1)} fill="none" stroke={stroke} strokeWidth={width} strokeLinecap="round" />;  // 솟고 끝이 둥근 호
  const mark = (key: string, y: number, rad: number, width: number, a: number) => { const [x0, y0] = polar(cx, y, rad - width / 2 + MARK_INSET, a), [x1, y1] = polar(cx, y, rad + width / 2 - MARK_INSET, a); return <line key={key} x1={x0} y1={y0} x2={x1} y2={y1} stroke={colors.ink} strokeWidth={MARK_W} strokeLinecap="round" />; };  // 목표 표시
  const circleD = (rad: number) => `M${cx - rad},${cy} a${rad},${rad} 0 1 0 ${2 * rad},0 a${rad},${rad} 0 1 0 ${-2 * rad},0`;
  if (ring === "half") {  // 74-gauge: 트랙 + 솟은 호, 목표 표시
    const ratio = target ? clamp(main / target, 0, 1) : clamp(main, 0, 1);
    kids.push(<path key="t" d={arcPath(cx, hcy, HALF_R, -Math.PI / 2, Math.PI / 2)} fill="none" stroke={colors.line} strokeWidth={HALF_W} strokeLinecap="round" />,
      arc("v", hcy, HALF_R, -Math.PI / 2, -Math.PI / 2 + Math.PI * ratio, target && overTarget(main, target) ? over : c0, HALF_W));
    if (target) kids.push(mark("g", hcy, HALF_R, HALF_W, Math.PI / 2));
  } else if (ring === "double") {  // 75-activity-rings: 겹친 두 고리, 바깥 = 칸0, 안 = 칸1(목표 대비)
    const full = target || Math.max(main, vals[1] ?? 0, 1), inner = RING_R - RING_STEP;
    kids.push(ringTrack("b0", RING_R), arc("c0", cy, RING_R, 0, TAU * clamp(main / full, 0, 1), overTarget(main, full) ? over : c0, RING_W), ringTrack("b1", inner), arc("c1", cy, inner, 0, TAU * clamp((vals[1] ?? 0) / full, 0, 1), color(1), RING_W));
  } else if (ring === "stack") {  // 49-donut: 솟은 바탕 원 위에 조각(그림자 없음), 가운데 솟은 원. 조각은 모두 같은 반지름·굵기
    const full = target || Math.max(main, 1), got = clamp(main / full, 0, 1), ov = target && overTarget(main, target) ? clamp((main - target) / full, 0, 1) : 0;
    const rest = target && underTarget(main, target) ? { from: got, to: 1, fill: colors.text } : ov ? { from: got - ov, to: got, fill: over } : { from: got, to: 1, fill: colors.line };  // 모자람 · 넘은 부분 · 남은 트랙
    kids.push(<circle key="disc" className="nm-raise" cx={cx} cy={cy} r={STACK_OUT + RIM} fill={colors.card} />,
      ...[{ from: 0, to: got - ov, fill: c0 }, rest].filter(sl => sl.to > sl.from).map((sl, j) => <path key={"sl" + j} d={sl.to - sl.from >= 1 ? circleD(STACK_OUT) + circleD(STACK_IN) : sectorPath(cx, cy, STACK_IN, STACK_OUT, TAU * sl.from, TAU * sl.to)} fillRule="evenodd" fill={sl.fill} stroke={colors.card} strokeWidth={SLICE_GAP} />),
      <circle key="hub" className="nm-raise" cx={cx} cy={cy} r={STACK_IN - RIM} fill={colors.card} />);
  } else {  // ticks: 75-activity-rings의 고리 + 목표 눈금
    const full = Math.max(target ?? 0, main) || 1;
    kids.push(ringTrack("b", RING_R), arc("v", cy, RING_R, 0, TAU * clamp(main / full, 0, 1), target && overTarget(main, target) ? over : c0, RING_W));
    if (target) kids.push(mark("tick", cy, RING_R, RING_W, TAU * clamp(target / full, 0, 1)));
  }
  const corners = [[8, 18 * s, "start"], [W - 8, 18 * s, "end"], [8, H - 22 * s, "start"], [W - 8, H - 22 * s, "end"]] as const;
  const dia = 2 * (ring === "half" ? HALF_R + HALF_W / 2 : ring === "stack" ? STACK_OUT : RING_R + RING_W / 2), inner = ring === "half" ? HALF_R - HALF_W / 2 : ring === "stack" ? STACK_IN : RING_R - (ring === "double" ? RING_STEP : 0) - RING_W / 2, mid = ring === "half" ? hcy - HALF_LIFT : cy;  // 고리 바깥 지름, 안쪽 반지름, 가운데 글의 세로 중심
  const hole = 2 * inner * HOLE_FIT, big = fmt(0), name = slots[0]?.label ?? "", km = growFont(big, hole, MAIN, s, MAIN_MIN) / MAIN, kn = growFont(name, hole, LABEL, s) / LABEL;  // 가운데 글은 고리 안쪽 폭까지만 키운다
  return (
    <Box cls="nm-well"><Frame w={W} h={H} label={card.title} max={(W * ROUND_PX * RING_FILL) / dia}>
      {kids}
      <text x={cx} y={mid + 6 * km} textAnchor="middle" fontSize={MAIN * km} fontWeight={700} fill={colors.ink}>{big}</text>
      <text x={cx} y={mid + 6 * km + 18 * kn} textAnchor="middle" fontSize={LABEL * kn} fill={colors.text}>{name}</text>
      {corners.map(([x, y, anchor], j) => slots[j + 1] && (  // 네 꼭지점 칸
        <g key={j}>
          <text x={x} y={y} textAnchor={anchor} fontSize={LABEL * s} fill={colors.text}>{slots[j + 1].label}</text>
          <text x={x} y={y + 14 * s} textAnchor={anchor} fontSize={14 * s} fontWeight={600} fill={colors.ink}>{fmt(j + 1)}</text>
        </g>
      ))}
    </Frame></Box>
  );
}
