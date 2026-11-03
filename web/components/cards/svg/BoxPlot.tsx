"use client";
// boxplot(P14): 그룹마다 상자 그림(data.values.groups = [{name, n, min, q1, med, q3, max, outliers, outlier_count}]). 수염 min~max(1.5 IQR 끝), 상자 q1~q3, 굵은 중앙값, 이상치는 빈 원
import { shownum } from "@/lib/calc";
import { tickBy } from "../chart/Charts";
import { fitText } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const W = 480, H = 260, L = 48, B = 22, T = 8;
const fine = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function BoxPlot(p: P) {
  // 사분위가 없는 그룹(행이 없음)은 빼고, 수염 끝이 비면 상자 끝으로
  const shown = (more(p).groups ?? []).filter(b => fine(b.q1) && fine(b.med) && fine(b.q3)).map(b => ({ ...b, min: fine(b.min) ? b.min : b.q1, max: fine(b.max) ? b.max : b.q3, outliers: (b.outliers ?? []).filter(fine) }));
  if (!shown.length) return <Say text={NOTHING} />;
  const nums = shown.flatMap(b => [b.min, b.max, ...(b.outliers ?? [])]).filter(fine), lo = Math.min(...nums), hi = Math.max(...nums);
  const y = (v: number) => (hi === lo ? (T + H - B) / 2 : T + ((H - B - T) * (hi - v)) / (hi - lo)), band = (W - L) / shown.length, every = Math.ceil(shown.length / 6), w = Math.min(44, band * 0.5), c = tone(p, "주 색", 0);
  return (
    <Box cls="boxplot">
      <Frame w={W} h={H} label={p.card.title}>
        {[0, 0.5, 1].map(k => { const v = lo + (hi - lo) * k; return <g key={k}><line x1={L} x2={W} y1={y(v)} y2={y(v)} stroke="var(--line)" /><text x={L - 6} y={y(v)} fontSize={11} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{tickBy((hi - lo) / 2)(v)}</text></g>; })}
        {shown.map((b, i) => {
          const cx = L + band * (i + 0.5);
          return (
            <g key={i}>
              <title>{`${b.name}${b.n != null ? ` (${b.n.toLocaleString()}개)` : ""}: 수염 아래 ${shownum(b.min)} · Q1 ${shownum(b.q1)} · 중앙값 ${shownum(b.med)} · Q3 ${shownum(b.q3)} · 수염 위 ${shownum(b.max)}${b.outlier_count ? ` · 이상치 ${b.outlier_count.toLocaleString()}개` : ""}`}</title>
              <line x1={cx} x2={cx} y1={y(b.max)} y2={y(b.min)} stroke="var(--muted)" />
              <line x1={cx - w / 4} x2={cx + w / 4} y1={y(b.max)} y2={y(b.max)} stroke="var(--muted)" />
              <line x1={cx - w / 4} x2={cx + w / 4} y1={y(b.min)} y2={y(b.min)} stroke="var(--muted)" />
              <rect x={cx - w / 2} y={y(b.q3)} width={w} height={Math.max(1, y(b.q1) - y(b.q3))} fill={c} fillOpacity={0.35} stroke={c} />
              <line x1={cx - w / 2} x2={cx + w / 2} y1={y(b.med)} y2={y(b.med)} stroke={c} strokeWidth={3} />
              {(b.outliers ?? []).map((o, j) => <circle key={j} cx={cx} cy={y(o)} r={3} fill="none" stroke={c} />)}
              {i % every === 0 && <text x={cx} y={H - 6} fontSize={11} textAnchor="middle" fill="var(--muted)">{fitText(b.name, band * every - 4, 11)}</text>}{/* 그룹이 많으면 몇 칸마다 하나(이름 전부는 툴팁) */}
            </g>
          );
        })}
      </Frame>
    </Box>
  );
}
