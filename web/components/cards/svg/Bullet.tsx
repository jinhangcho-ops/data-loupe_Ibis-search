"use client";
// bullet(P14): 값(첫 계열, 보는 기간) 굵은 막대 + 목표(cardTarget) 세로 선 + 구간(view.bands, 경계 값들)을 옅은 바탕 띠로
import { showValue } from "@/lib/calc";
import { tickBy } from "../chart/Charts";
import { useRootScale } from "../chart/scale";
import { cardFormat, cardTarget, cardValue } from "./__pure__";
import { Box, Frame, NOTHING, Say, tone, type P } from "./common";

const W = 420, H = 76, L = 8, R = 12, BAR = 24, FONT = 10, TICK_TOP = 46;  // 눈금 글자: 기준 크기, 글자 윗자리(막대·목표 선 아래)
const TOP = 8, BAND_RX = 6, MARK_INSET = 3;  // 26-bullet: 띠 윗자리, 띠 모서리, 목표 선이 띠 위·아래에서 들어간 만큼(값 막대는 띠 높이의 1/3, 끝이 둥근 알약)

export function Bullet(p: P) {
  const s = useRootScale(), v = cardValue(p.card, 0, p.range, p.merge), t = cardTarget(p.card), fmt = cardFormat(p.card), unit = p.card.view?.unit_text;
  if (v == null) return <Say text={NOTHING} />;
  const bands = [...(p.card.view?.bands ?? [])].filter(Number.isFinite).sort((a, b) => a - b);
  const max = Math.max(v, t ?? 0, ...bands, 0) * 1.05 || 1, x = (n: number) => L + ((W - L - R) * Math.max(0, n)) / max, c = tone(p, "주 색", 0);
  const tick = (n: number) => (fmt === "number" ? tickBy(max / 2)(n) : showValue(n, fmt));  // 퍼센트·금액이면 머리글과 같은 모양
  return (
    <Box cls="bullet">
      <div className="hint">{`${showValue(v, fmt, unit)}${t == null ? "" : ` · 목표 ${showValue(t, fmt, unit)}`}`}</div>
      <div className="nm-well flex-1 min-h-0"><Frame w={W} h={H} label={p.card.title}>
        <rect x={L} y={TOP} width={W - L - R} height={BAR} rx={BAND_RX} fill={p.colors.line} />
        {bands.map((b, i) => <rect key={i} x={L} y={TOP} width={x(b) - L} height={BAR} rx={BAND_RX} fill="var(--muted)" fillOpacity={0.12}><title>{`구간 ${showValue(b, fmt, unit)}`}</title></rect>)}
        <rect className="nm-raise" x={L} y={TOP + BAR / 3} width={x(v) - L} height={BAR / 3} rx={BAR / 6} fill={c}><title>{`값 ${showValue(v, fmt, unit)}`}</title></rect>
        {t != null && <line x1={x(t)} x2={x(t)} y1={TOP + MARK_INSET} y2={TOP + BAR - MARK_INSET} stroke="var(--text)" strokeWidth={3} strokeLinecap="round"><title>{`목표 ${showValue(t, fmt, unit)}`}</title></line>}
        {[0, 0.5, 1].map(k => <text key={k} x={x(max * k)} y={TICK_TOP + FONT * s} fontSize={FONT * s} textAnchor={k ? (k === 1 ? "end" : "middle") : "start"} fill="var(--muted)">{tick(max * k)}</text>)}
      </Frame></div>
    </Box>
  );
}
