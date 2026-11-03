"use client";
// treemap·sunburst(P14): data.values.items = [{name, value, children?}](1~2단계, 항목 상한은 서버가 기타로 묶음). 아이들 합이 값보다 작으면 "기타" 아이(withRest).
// 트리맵은 Recharts Treemap, 선버스트는 손수 SVG(Recharts SunburstChart는 조각 글자가 값이라 이름을 쓸 수 없다): 뿌리 값 = 첫 단계 합(treeRoot), 조각은 sunburstArcs·sectorPath.
// 첫 단계 항목마다 색(토큰), 둘째 단계는 같은 색을 옅게. 글자는 본문 색 + 카드 색 테두리(어느 바탕에서도 읽히게)
import { Tooltip, Treemap as Tree, type TreemapNode } from "recharts";
import { polar, shownum } from "@/lib/calc";
import type { TreeItem } from "@/lib/types";
import { Frame, TIP } from "../chart/Charts";
import { fitText, sectorPath, sunburstArcs, treeRoot, withRest } from "./__pure__";
import { Box, Frame as SvgFrame, NOTHING, Say, more, tone, type P } from "./common";

type Painted = TreeItem & { fill: string; fillOpacity?: number; children?: Painted[] };
// 첫 단계 항목마다 색을 칠해 둘째 단계에 물려준다
const paint = (p: P, items: TreeItem[]): Painted[] => items.map((it, i) => {
  const fill = tone(p, it.name, i);
  return { ...it, fill, children: it.children?.map(ch => ({ ...ch, fill, fillOpacity: 0.7, children: undefined })) };
});
/** 어느 바탕에서도 읽히는 글자: 본문 색, 카드 색 테두리를 먼저 그린다 */
const LABEL = { fill: "var(--text)", stroke: "var(--card)", strokeWidth: 3, paintOrder: "stroke", pointerEvents: "none" } as const;

/** 사각형 하나(뿌리는 그리지 않는다). 잎 칸이 넉넉하면 이름. 둘째 단계는 툴팁이 "부모 › 이름"이고, 부모 칸의 왼쪽 위에 놓인 칸이 부모 이름을 굵게 먼저 쓴다
 *  (Recharts는 부모 칸을 아이 칸이 다 덮어서 부모 이름이 안 보인다). n.root는 부모 칸 */
export function TreeCell(n: TreemapNode) {
  if (!n.depth) return <g />;
  const fill = n.fill as string, name = String(n.name ?? ""), up = n.depth > 1 ? n.root : undefined, head = !!up && n.x === up.x && n.y === up.y, room = n.width - 8;
  return (
    <g>
      <rect x={n.x} y={n.y} width={n.width} height={n.height} fill={fill} fillOpacity={(n.fillOpacity as number | undefined) ?? 1} stroke="var(--card)" strokeWidth={2}><title>{`${up ? `${up.name} › ` : ""}${name}: ${shownum(n.value)}`}</title></rect>
      {head && n.height > 18 && room > 12 && <text x={n.x + 6} y={n.y + 13} fontSize={10} fontWeight={700} {...LABEL}>{fitText(String(up!.name), room, 10)}</text>}
      {n.height > (head ? 32 : 18) && room > 12 && !(n.children?.length) && <text x={n.x + 6} y={n.y + (head ? 27 : 14)} fontSize={11} {...LABEL}>{fitText(name, room, 11)}</text>}
    </g>
  );
}

export function Treemap(p: P) {
  const items = paint(p, withRest(more(p).items));
  if (!items.length) return <Say text={NOTHING} />;
  return (
    <Box cls="treemap">
      <div style={{ flex: 1, minHeight: 0 }}>
        <Frame><Tree data={items} dataKey="value" nameKey="name" isAnimationActive={false} content={TreeCell}><Tooltip {...TIP} /></Tree></Frame>
      </div>
    </Box>
  );
}

const S = 320, C = S / 2;
export function Sunburst(p: P) {
  const root = treeRoot(p.card.title, withRest(more(p).items)), arcs = sunburstArcs(root);
  if (!arcs.length) return <Say text={NOTHING} />;
  const two = arcs.some(a => a.depth === 2), ring = (d: 1 | 2): [number, number] => (d === 1 ? [44, two ? 104 : 156] : [106, 156]);
  return (
    <Box cls="sunburst">
      <SvgFrame w={S} h={S} label={p.card.title}>
        {arcs.map((a, i) => {
          const [r0, r1] = ring(a.depth), mid = (a.a0 + a.a1) / 2, rm = (r0 + r1) / 2, [x, y] = polar(C, C, rm, mid), room = (a.a1 - a.a0) * rm;
          return (
            <g key={i}>
              <path d={sectorPath(C, C, r0, r1, a.a0, a.a1)} fill={tone(p, arcs.find(b => b.depth === 1 && b.top === a.top)!.name, a.top)} fillOpacity={a.depth === 2 ? 0.7 : 1} stroke="var(--card)" strokeWidth={1.5}>
                <title>{`${a.parent ? `${a.parent} › ` : ""}${a.name}: ${shownum(a.value)} (${((a.value / root.value) * 100).toFixed(1)}%)`}</title>
              </path>
              {room > 20 && fitText(a.name, room - 6, 11) !== "…" && <text x={x} y={y} fontSize={11} textAnchor="middle" dominantBaseline="middle" {...LABEL}>{fitText(a.name, room - 6, 11)}</text>}
            </g>
          );
        })}
      </SvgFrame>
    </Box>
  );
}
