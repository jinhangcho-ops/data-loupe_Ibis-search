"use client";
// bullet(P14): 값(첫 계열, 보는 기간) 굵은 막대 + 목표(cardTarget) 세로 선 + 구간(view.bands, 경계 값들)을 옅은 바탕 띠로
import { showValue } from "@/lib/calc";
import { tickBy } from "../chart/Charts";
import { cardFormat, cardTarget, cardValue } from "./__pure__";
import { Box, Frame, NOTHING, Say, tone, type P } from "./common";

const W = 420, H = 84, L = 8, R = 12, BAR = 34;

export function Bullet(p: P) {
  const v = cardValue(p.card, 0, p.range, p.merge), t = cardTarget(p.card), fmt = cardFormat(p.card), unit = p.card.view?.unit_text;
  if (v == null) return <Say text={NOTHING} />;
  const bands = [...(p.card.view?.bands ?? [])].filter(Number.isFinite).sort((a, b) => a - b);
  const max = Math.max(v, t ?? 0, ...bands, 0) * 1.05 || 1, x = (n: number) => L + ((W - L - R) * Math.max(0, n)) / max, c = tone(p, "주 색", 0);
  const tick = (n: number) => (fmt === "number" ? tickBy(max / 2)(n) : showValue(n, fmt));  // 퍼센트·금액이면 머리글과 같은 모양
  return (
    <Box cls="bullet">
      <div className="hint">{`${showValue(v, fmt, unit)}${t == null ? "" : ` · 목표 ${showValue(t, fmt, unit)}`}`}</div>
      <Frame w={W} h={H} label={p.card.title}>
        <rect x={L} y={8} width={W - L - R} height={BAR} fill="var(--muted)" fillOpacity={0.12} />
        {bands.map((b, i) => <rect key={i} x={L} y={8} width={x(b) - L} height={BAR} fill="var(--muted)" fillOpacity={0.12}><title>{`구간 ${showValue(b, fmt, unit)}`}</title></rect>)}
        <rect x={L} y={8 + BAR / 3} width={x(v) - L} height={BAR / 3} fill={c}><title>{`값 ${showValue(v, fmt, unit)}`}</title></rect>
        {t != null && <line x1={x(t)} x2={x(t)} y1={4} y2={12 + BAR} stroke="var(--text)" strokeWidth={3}><title>{`목표 ${showValue(t, fmt, unit)}`}</title></line>}
        {[0, 0.5, 1].map(k => <text key={k} x={x(max * k)} y={H - 18} fontSize={10} textAnchor={k ? (k === 1 ? "end" : "middle") : "start"} fill="var(--muted)">{tick(max * k)}</text>)}
      </Frame>
    </Box>
  );
}
