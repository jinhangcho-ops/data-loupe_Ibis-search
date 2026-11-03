"use client";
// funnel: 흐름(퍼널) 생키 + 한 줄 설명 + 단계별 표(옛 paintFunnel·funnel.js sankeySvg). 색: 막대는 팔레트 첫 색, 다음 단계 띠는 초록, 이탈 띠는 주황(카드 색 "단계 막대"·"다음 단계"·"이탈"로 바꿈)
import { FUNNEL_COLORS, FUNNEL_COLUMNS, fpct, funnelRows, funnelSentence, sankeyLayout, windowText, type Step } from "./__pure__";
import { Box, type P } from "./common";

const short = (s: string) => (s.length > 14 ? s.slice(0, 13) + "…" : s);

export function Funnel({ card, colors }: P) {
  const d = card.data?.values as { steps?: Step[]; window?: { n?: number; unit?: string } } | undefined, steps = d?.steps || [], pick = (name: string, fallback: string) => card.colors?.series?.[name] ?? fallback;
  const c = { bar: pick("단계 막대", colors.palette?.[0] ?? FUNNEL_COLORS.bar), next: pick("다음 단계", FUNNEL_COLORS.next), drop: pick("이탈", FUNNEL_COLORS.drop), text: colors.text };
  const g = sankeyLayout(steps);
  const label = (x: number, y: number, text: string, anchor: "start" | "middle" | "end", extra: React.SVGProps<SVGTextElement> = {}) => <text x={x} y={y} textAnchor={anchor} fill={c.text} fontSize={11} {...extra}>{text}</text>;
  return (
    <Box cls="table">
      <div className="hint">{`${funnelSentence(steps)} (${windowText(d?.window)})`}</div>
      <div style={{ flex: 1, minHeight: 140 }}>
        <svg viewBox={`0 0 ${g.width} ${g.height}`} preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%" }} role="img" aria-label={card.title}>
          {g.nexts.map((b, i) => <path key={"n" + i} d={b.path} fill={c.next} opacity={0.45}><title>{`${steps[b.from].name} → ${steps[b.to].name}: ${b.users.toLocaleString()}명`}</title></path>)}
          {g.drops.map((b, i) => <path key={"d" + i} d={b.path} fill={c.drop} opacity={0.55}><title>{`${steps[b.step - 1].name}에서 ${b.users.toLocaleString()}명 이탈`}</title></path>)}
          {g.bars.map((b, i) => <rect key={"b" + i} x={b.x} y={b.y} width={b.w} height={Math.max(b.h, 1)} fill={c.bar} rx={2}><title>{`${b.name}: ${b.users.toLocaleString()}명`}</title></rect>)}
          {g.bars.map((b, i) => {
            const anchor = i === 0 ? "start" : i === g.bars.length - 1 ? "end" : "middle", lx = anchor === "start" ? b.x : anchor === "end" ? b.x + b.w : b.x + b.w / 2;
            return (
              <g key={"t" + i}>
                {label(lx, 16, short(b.name), anchor, { fontWeight: 600 })}
                {label(lx, g.height - 22, `${b.users.toLocaleString()}명`, anchor)}
                {label(lx, g.height - 8, i ? fpct(steps[i].from_first) : "100%", anchor, { opacity: 0.7 })}
              </g>
            );
          })}
        </svg>
      </div>
      <table>
        <thead><tr>{FUNNEL_COLUMNS.map(h => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{funnelRows(steps).map((r, k) => (
          <tr key={k}>{FUNNEL_COLUMNS.map((h, i) => <td key={h} className={i ? "num" : ""}>{typeof r[h] === "number" ? r[h].toLocaleString() : r[h]}</td>)}</tr>
        ))}</tbody>
      </table>
    </Box>
  );
}
