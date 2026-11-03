"use client";
// donut_kpi: 가운데 칸(0)과 네 꼭지점 칸(1~4), 고리 4방식(view.ring: stack·double·half·ticks). 목표를 넘은 부분은 진한 색(옛 paintDonut)
import { TAU, arcPath, clamp, darken, overTarget, polar, ringDash, showValue, underTarget } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { cardFormat, cardTarget, cardValue } from "./__pure__";
import { Frame, type P } from "./common";

type SlotLike = { label: string; format?: string; unit_text?: string; color?: string };

export function Donut({ card, colors, range, merge }: P) {
  const slots: SlotLike[] = card.view?.slots?.length ? card.view.slots : (card.data?.by_unit?.day?.series ?? []).map(s => ({ label: s.name })), ring = card.view?.ring || "stack";
  const vals = slots.map((_, i) => cardValue(card, i, range, merge)), target = cardTarget(card), main = vals[0] ?? 0, color = (i: number) => slots[i]?.color || pickColor(card, "series", slots[i]?.label ?? String(i), colors, i);
  const W = 260, H = 200, cx = W / 2, cy = 100, r = 62, sw = 16, track = colors.line, c0 = color(0), over = darken(c0), fmt = (i: number) => showValue(vals[i], slots[i]?.format || cardFormat(card), slots[i]?.unit_text);
  const kids: React.ReactNode[] = [];
  const circle = (key: string, rad: number, ratio: number, stroke: string, width = sw, off = 0) => (
    <circle key={key} cx={cx} cy={cy} r={rad} fill="none" stroke={stroke} strokeWidth={width} strokeDasharray={ringDash(rad, ratio).dash} strokeDashoffset={-ringDash(rad, off).len} transform={`rotate(-90 ${cx} ${cy})`} />
  );
  const base = (key: string, rad: number, width = sw) => <circle key={key} cx={cx} cy={cy} r={rad} fill="none" stroke={track} strokeWidth={width} />;
  if (ring === "half") {  // 반원 게이지 + 목표 점
    const ratio = target ? clamp(main / target, 0, 1) : clamp(main, 0, 1), a = -Math.PI / 2 + Math.PI * ratio;
    kids.push(<path key="t" d={arcPath(cx, cy + 20, r, -Math.PI / 2, Math.PI / 2)} fill="none" stroke={track} strokeWidth={sw} strokeLinecap="round" />,
      <path key="v" d={arcPath(cx, cy + 20, r, -Math.PI / 2, Math.max(a, -Math.PI / 2 + 0.001))} fill="none" stroke={target && overTarget(main, target) ? over : c0} strokeWidth={sw} strokeLinecap="round" />);
    if (target) { const [x, y] = polar(cx, cy + 20, r, Math.PI / 2); kids.push(<circle key="g" cx={x} cy={y} r={5} fill={colors.ink} />); }
  } else if (ring === "double") {  // 겹친 두 고리: 바깥 = 칸0, 안 = 칸1(목표 대비)
    const full = target || Math.max(main, vals[1] ?? 0, 1);
    kids.push(base("b0", r), circle("c0", r, main / full, overTarget(main, full) ? over : c0), base("b1", r - sw - 4, sw - 4), circle("c1", r - sw - 4, (vals[1] ?? 0) / full, color(1), sw - 4));
  } else {  // stack(실제·모자람·넘은 부분), ticks(고리 + 목표 눈금)
    const full = ring === "ticks" ? Math.max(target ?? 0, main) || 1 : target || Math.max(main, 1);
    kids.push(base("b", r));
    if (ring === "stack") {
      kids.push(circle("v", r, Math.min(main, full) / full, c0));
      if (target && underTarget(main, target)) kids.push(circle("lack", r, (target - main) / full, colors.text, sw, main / full));  // 모자람
      if (target && overTarget(main, target)) kids.push(circle("over", r, (main - target) / full, over, sw, 1 - (main - target) / full));  // 넘은 부분
    } else {
      kids.push(circle("v", r, main / full, target && overTarget(main, target) ? over : c0));
      if (target) { const a = TAU * clamp(target / full, 0, 1), [x0, y0] = polar(cx, cy, r - sw / 2 - 3, a), [x1, y1] = polar(cx, cy, r + sw / 2 + 3, a); kids.push(<line key="tick" x1={x0} y1={y0} x2={x1} y2={y1} stroke={colors.ink} strokeWidth={2.5} />); }
    }
  }
  const corners = [[8, 18, "start"], [W - 8, 18, "end"], [8, H - 22, "start"], [W - 8, H - 22, "end"]] as const;
  return (
    <Frame w={W} h={H} label={card.title}>
      {kids}
      <text x={cx} y={cy + 6} textAnchor="middle" fontSize={22} fontWeight={700} fill={colors.ink}>{fmt(0)}</text>
      <text x={cx} y={cy + 24} textAnchor="middle" fontSize={11} fill={colors.text}>{slots[0]?.label ?? ""}</text>
      {corners.map(([x, y, anchor], j) => slots[j + 1] && (  // 네 꼭지점 칸
        <g key={j}>
          <text x={x} y={y} textAnchor={anchor} fontSize={11} fill={colors.text}>{slots[j + 1].label}</text>
          <text x={x} y={y + 14} textAnchor={anchor} fontSize={14} fontWeight={600} fill={colors.ink}>{fmt(j + 1)}</text>
        </g>
      ))}
    </Frame>
  );
}
