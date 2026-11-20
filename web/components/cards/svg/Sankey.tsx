"use client";
// sankey(P14): 단계 사이를 오간 인원(data.values = {nodes:[{name, users}], links:[{s, t, v}]}, 서버 dashboard/flow_pairs, "이탈" 노드는 있을 때만 마지막). 좌표는 __pure__.flowLayout, 띠 색은 출발 노드 색을 옅게
import { useRootScale } from "../chart/scale";
import { flowLayout } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const W = 720, H = 340, NODE = 12, GAP = 10;  // GAP: 한 열 안 노드 사이(화면 크기 배율 1, 이름 글자와 같이 커진다)
const NODE_RX = 4, LINK_OPACITY = 0.38;  // 72-sankey: 노드 폭 12에 모서리 4, 띠 진하기(띠는 솟지 않는다)

export function Sankey(p: P) {
  const s = useRootScale(), { nodes, links } = more(p);
  const g = nodes && links ? flowLayout({ nodes, links }, { width: W, height: H, nodeW: NODE, pad: 12, gap: GAP * s }) : null;
  if (!g?.links.length) return <Say text={NOTHING} />;
  const color = g.nodes.map((n, i) => tone(p, n.name, i)), right = Math.max(...g.nodes.map(n => n.x));
  return (
    <Box cls="sankey nm-well">
      <Frame w={W} h={H} label={p.card.title}>
        {g.links.map((l, i) => <path key={i} d={l.path} fill={color[l.s]} fillOpacity={LINK_OPACITY}><title>{`${g.nodes[l.s].name} → ${g.nodes[l.t].name}: ${l.v.toLocaleString()}명`}</title></path>)}
        {g.nodes.map((n, i) => n.value > 0 && (
          <g key={i}>
            <rect className="nm-raise" x={n.x} y={n.y} width={NODE} height={Math.max(1, n.h)} rx={NODE_RX} fill={color[i]}><title>{`${n.name}: ${n.value.toLocaleString()}명`}</title></rect>
            <text x={n.x === right ? n.x - 6 : n.x + NODE + 6} y={n.y + n.h / 2} fontSize={12 * s} textAnchor={n.x === right ? "end" : "start"} dominantBaseline="middle" fill="var(--text)" pointerEvents="none">{`${n.name} ${n.value.toLocaleString()}`}</text>
          </g>
        ))}
      </Frame>
    </Box>
  );
}
