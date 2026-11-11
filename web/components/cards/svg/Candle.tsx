"use client";
// candle(P14): 기간마다 시작·최고·최저·끝(data.values = {labels, candles:[{open, high, low, close}]}, 같은 순서). 심지는 최저~최고, 몸통은 시작~끝, 오르면 주 색·내리면 --err
import { shownum } from "@/lib/calc";
import { tickBy } from "../chart/Charts";
import { MIN_POINTS, sliceWindow } from "./__pure__";
import { useZoomX } from "../useZoomX";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const W = 600, H = 260, L = 48, B = 20, T = 8;

export function Candle(p: P) {
  const v = more(p), shown = (v.candles ?? []).map((c, i) => ({ ...c, label: v.labels?.[i] ?? "" })).filter(c => c.high != null && c.low != null);  // 상한은 서버(최근 기간을 남긴다)
  const z = useZoomX(shown.length > MIN_POINTS ? { kind: "index", count: shown.length } : null), vis = sliceWindow(shown, z.view);
  if (!shown.length) return <Say text={NOTHING} />;
  const up = tone(p, "주 색", 0);  // 오름 몸통 = [색]의 주 색(정하지 않으면 팔레트 첫 색)
  const lo = Math.min(...vis.map(c => c.low!)), hi = Math.max(...vis.map(c => c.high!)), band = (W - L) / vis.length, w = Math.max(1, Math.min(16, band * 0.6));
  const y = (v: number) => (hi === lo ? (T + H - B) / 2 : T + ((H - B - T) * (hi - v)) / (hi - lo)), every = Math.ceil(vis.length / 6);
  return (
    <Box cls="candle">
      <div {...z.bind}>
      <Frame w={W} h={H} label={p.card.title}>
        {[0, 0.5, 1].map(k => { const v = lo + (hi - lo) * k; return <g key={k}><line x1={L} x2={W} y1={y(v)} y2={y(v)} stroke="var(--line)" /><text x={L - 6} y={y(v)} fontSize={11} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{tickBy((hi - lo) / 2)(v)}</text></g>; })}
        {vis.map((c, i) => {
          const cx = L + band * (i + 0.5), open = c.open ?? c.close ?? c.low!, close = c.close ?? open, col = close >= open ? up : "var(--err)";
          return (
            <g key={i}>
              <title>{`${c.label}: 시작 ${shownum(c.open)} · 최고 ${shownum(c.high)} · 최저 ${shownum(c.low)} · 끝 ${shownum(c.close)}`}</title>
              <line x1={cx} x2={cx} y1={y(c.high!)} y2={y(c.low!)} stroke={col} />
              <rect x={cx - w / 2} y={y(Math.max(open, close))} width={w} height={Math.max(1, Math.abs(y(open) - y(close)))} fill={col} />
              {i % every === 0 && <text x={cx} y={H - 5} fontSize={10} textAnchor="middle" fill="var(--muted)">{c.label}</text>}
            </g>
          );
        })}
      </Frame>
      </div>
    </Box>
  );
}
