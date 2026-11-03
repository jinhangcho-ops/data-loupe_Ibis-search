"use client";
// target_trend: 일 값 선 + 목표 가로 점선 + 목표 아래 구간 채움 + "목표보다 1.7%p 낮아요. 목표 아래로 N번 내려갔어요"(옛 paintTargetTrend)
import { belowPolygons, crossDown, overTarget, showGap, underTarget, zeroWhenEmpty } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { cardDays, cardFormat, cardTarget, cardValue } from "./__pure__";
import { Frame, Say, type P } from "./common";

export function TargetTrend({ card, colors, range, merge }: P) {
  const vs = cardDays(card, 0, range, merge).map(d => d.v), target = cardTarget(card), fmt = cardFormat(card), W = 300, H = 160, pad = 12, color = pickColor(card, "series", card.title, colors, 0);
  const real = vs.filter((x): x is number => x != null);
  if (!real.length) return <Say text="이 기간의 값이 없어요" />;
  const lo = Math.min(...real, target ?? Infinity), hi = Math.max(...real, target ?? -Infinity), yOf = (v: number) => H - 24 - (H - 24 - pad) * (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const xs = vs.map((_, i) => pad + (W - 2 * pad) * (vs.length < 2 ? 0.5 : i / (vs.length - 1)));
  const daily = zeroWhenEmpty(card.data?.by_unit?.day?.series?.[0]?.agg ?? ""), total = cardValue(card, 0, range, merge);  // 합계·개수의 기간 값은 날들의 합이라 하루 목표와 견줄 수 없다: 선에 그린 날들의 평균과 견준다
  const now = total != null && daily ? real.reduce((a, b) => a + b, 0) / real.length : total;
  const says = target == null ? ((card.data?.target as { error?: string } | undefined)?.error || "목표를 아직 정하지 않았어요") : now == null ? "—"
    : `${daily ? "일 평균이 " : ""}목표보다 ${showGap(now - target, fmt)} ${underTarget(now, target) ? "낮아요" : overTarget(now, target) ? "높아요" : "같아요"}. 목표 아래로 ${crossDown(vs, target)}번 내려갔어요`;
  return (
    <Frame w={W} h={H} label={card.title}>
      {target != null && belowPolygons(xs, vs, target, yOf).map((p, k) => <polygon key={k} points={p.map(q => q.map(n => n.toFixed(1)).join(",")).join(" ")} fill={colors.series[1]} opacity={0.3} />)}
      <polyline points={vs.map((v, i) => v == null ? null : `${xs[i].toFixed(1)},${yOf(v).toFixed(1)}`).filter(Boolean).join(" ")} fill="none" stroke={color} strokeWidth={2} />
      {target != null && <line x1={pad} x2={W - pad} y1={yOf(target)} y2={yOf(target)} stroke={colors.ink} strokeDasharray="6 4" />}
      <text x={pad} y={H - 6} fontSize={11} fill={colors.text}>{says}</text>
    </Frame>
  );
}
