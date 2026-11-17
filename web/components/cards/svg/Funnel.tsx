"use client";
// funnel: 흐름(퍼널) 생키 + 한 줄 설명 + 단계별 표(옛 paintFunnel·funnel.js sankeySvg). 색: 막대는 팔레트 첫 색, 다음 단계 띠는 초록, 이탈 띠는 주황(카드 색 "단계 막대"·"다음 단계"·"이탈"로 바꿈)
import { EACH_TEXT, FUNNEL_COLUMNS, fpct, funnelColors, funnelRows, funnelSentence, gapRows, ladderLayout, sankeyLayout, windowText, type Step } from "./__pure__";
import { useRootScale } from "../chart/scale";
import { Box, type P } from "./common";

// 배율 1의 값: 단계 이름 글자 수, 생키 위·아래 글자 자리, 사다리꼴 그림 높이·도형 몫, 단계마다 두 줄 글에 더 줄 높이(배율 1을 넘는 만큼)
const NAME_MAX = 14, TOP = 30, BOTTOM = 40, LADDER_H = 340, SHAPE_SHARE = 0.5, ROW_TEXT = 36;
const short = (s: string, max: number) => (s.length > max ? s.slice(0, max - 1) + "…" : s);

export function Funnel({ card, colors }: P) {
  const s = useRootScale();
  const d = card.data?.values as { steps?: Step[]; window?: { n?: number; unit?: string }; count?: "order" | "each"; gaps?: Parameters<typeof gapRows>[0]; trim?: number } | undefined, steps = d?.steps || [], f = funnelColors(card, colors.palette);
  const c = { bar: f["단계 막대"], next: f["다음 단계"], drop: f.이탈, text: colors.text };
  const ladder = card.view.funnel_shape === "ladder";
  // 글자가 커지는 만큼: 생키는 위·아래 글자 자리를, 사다리꼴은 글 쪽 폭(도형 몫을 줄임)과 줄 높이를 늘린다
  const g = ladder ? null : sankeyLayout(steps, { top: TOP * s, bottom: BOTTOM * s });
  const lad = ladder ? ladderLayout(steps, { scale: card.view.ladder_scale === true, shapeShare: SHAPE_SHARE / s, height: LADDER_H + steps.length * ROW_TEXT * (s - 1) }) : null;
  const gaps = gapRows(d?.gaps ?? [], card.view.gap_stats ?? [], d?.trim ?? 0.1);
  const label = (x: number, y: number, text: string, anchor: "start" | "middle" | "end", extra: React.SVGProps<SVGTextElement> = {}) => <text x={x} y={y} textAnchor={anchor} fill={c.text} fontSize={11 * s} {...extra}>{text}</text>;
  return (
    <Box cls="table">
      <div className="hint">{d?.count === "each" ? EACH_TEXT : `${funnelSentence(steps)} (${windowText(d?.window)})`}</div>
      <div style={{ flex: 1, minHeight: 140 }}>
        {lad ? (
          <svg viewBox={`0 0 ${lad.width} ${lad.height}`} preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%" }} role="img" aria-label={card.title}>
            {lad.rows.map((r, i) => (
              <g key={"l" + i}>
                <path d={r.path} fill={c.bar}><title>{r.lines[1]}</title></path>
                <text x={r.textX} y={r.textY} fill={c.text}>
                  <tspan x={r.textX} dy="-0.3em" fontSize={12 * s} fontWeight={600}>{r.lines[0]}</tspan>
                  <tspan x={r.textX} dy="1.3em" fontSize={11 * s} opacity={0.8}>{r.lines[1]}</tspan>
                </text>
              </g>
            ))}
          </svg>
        ) : g && (
        <svg viewBox={`0 0 ${g.width} ${g.height}`} preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%" }} role="img" aria-label={card.title}>
          {g.nexts.map((b, i) => <path key={"n" + i} d={b.path} fill={c.next} opacity={0.45}><title>{`${steps[b.from].name} → ${steps[b.to].name}: ${b.users.toLocaleString()}명`}</title></path>)}
          {g.drops.map((b, i) => <path key={"d" + i} d={b.path} fill={c.drop} opacity={0.55}><title>{`${steps[b.step - 1].name}에서 ${b.users.toLocaleString()}명 이탈`}</title></path>)}
          {g.bars.map((b, i) => <rect key={"b" + i} x={b.x} y={b.y} width={b.w} height={Math.max(b.h, 1)} fill={c.bar} rx={2}><title>{`${b.name}: ${b.users.toLocaleString()}명`}</title></rect>)}
          {g.bars.map((b, i) => {
            const anchor = i === 0 ? "start" : i === g.bars.length - 1 ? "end" : "middle", lx = anchor === "start" ? b.x : anchor === "end" ? b.x + b.w : b.x + b.w / 2;
            return (
              <g key={"t" + i}>
                {label(lx, 16 * s, short(b.name, Math.floor(NAME_MAX / s)), anchor, { fontWeight: 600 })}
                {label(lx, g.height - 22 * s, `${b.users.toLocaleString()}명`, anchor)}
                {label(lx, g.height - 8 * s, i ? fpct(steps[i].from_first) : "100%", anchor, { opacity: 0.7 })}
              </g>
            );
          })}
        </svg>
        )}
      </div>
      {!ladder && (
        <table>
          <thead><tr>{FUNNEL_COLUMNS.map(h => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>{funnelRows(steps).map((r, k) => (
            <tr key={k}>{FUNNEL_COLUMNS.map((h, i) => <td key={h} className={i ? "num" : ""}>{typeof r[h] === "number" ? r[h].toLocaleString() : r[h]}</td>)}</tr>
          ))}</tbody>
        </table>
      )}
      {gaps.length > 0 && (
        <table>
          <thead><tr><th>단계</th><th>걸린 시간</th></tr></thead>
          <tbody>{gaps.map((r, k) => <tr key={k}><td>{r.label}</td><td>{r.values}</td></tr>)}</tbody>
        </table>
      )}
    </Box>
  );
}
