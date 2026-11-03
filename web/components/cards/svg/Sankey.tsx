"use client";
// sankey(P14): 단계 사이를 오간 인원(data.values = {nodes:[{name, users}], links:[{s, t, v}]}, 서버 dashboard/flow_pairs, "이탈" 노드는 있을 때만 마지막). 좌표는 __pure__.flowLayout, 띠 색은 출발 노드 색을 옅게
import { flowLayout } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const W = 720, H = 340, NODE = 14;

export function Sankey(p: P) {
  const { nodes, links } = more(p);
  const g = nodes && links ? flowLayout({ nodes, links }, { width: W, height: H, nodeW: NODE, pad: 12 }) : null;
  if (!g?.links.length) return <Say text={NOTHING} />;
  const color = g.nodes.map((n, i) => tone(p, n.name, i)), right = Math.max(...g.nodes.map(n => n.x));
  return (
    <Box cls="sankey">
      <Frame w={W} h={H} label={p.card.title}>
        {g.links.map((l, i) => <path key={i} d={l.path} fill={color[l.s]} fillOpacity={0.3}><title>{`${g.nodes[l.s].name} → ${g.nodes[l.t].name}: ${l.v.toLocaleString()}명`}</title></path>)}
        {g.nodes.map((n, i) => n.value > 0 && (
          <g key={i}>
            <rect x={n.x} y={n.y} width={NODE} height={Math.max(1, n.h)} fill={color[i]}><title>{`${n.name}: ${n.value.toLocaleString()}명`}</title></rect>
            <text x={n.x === right ? n.x - 6 : n.x + NODE + 6} y={n.y + n.h / 2} fontSize={12} textAnchor={n.x === right ? "end" : "start"} dominantBaseline="middle" fill="var(--text)" pointerEvents="none">{`${n.name} ${n.value.toLocaleString()}`}</text>
          </g>
        ))}
      </Frame>
    </Box>
  );
}
