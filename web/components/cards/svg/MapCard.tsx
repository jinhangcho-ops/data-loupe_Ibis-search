"use client";
// map(배치도): 배경(서버실 칸·네트워크·그림) 위에 엔티티를 상태 색 점으로. 점을 끌어 옮기면 위치를 0~1 비율로 view.positions에 둔다(옛 paintMap).
// 끌기는 포인터 캡처로, 끄는 동안은 지역 상태만 바꾸고 놓을 때 dispatch(card) 한 번. 그림 바탕은 /api/dash/image를 blob 주소로(CSP img-src에 'self'가 없다).
import { useEffect, useRef, useState } from "react";
import { autoGrid, clamp } from "@/lib/calc";
import { imageUrl } from "@/lib/api";
import { useDash } from "@/lib/store";
import { LEVEL_NAMES, Say, levelColor, type P } from "./common";
import type { Entity } from "./Status";

export function MapCard({ card, colors }: P) {
  const { state, dispatch } = useDash();
  const dashId = state.dash?.id ?? "", view = card.view, bg = view.bg || "rack", W = 400, H = 260;
  const entities = (card.data?.values?.entities ?? []) as Entity[];
  const [drag, setDrag] = useState<Record<string, [number, number]> | null>(null);
  const [img, setImg] = useState<{ url?: string; error?: string }>({});
  const root = useRef<SVGSVGElement>(null), grab = useRef<string | null>(null);
  useEffect(() => {  // 그림은 저장된 카드의 경로로 서버에서 받는다
    if (bg !== "image" || !dashId) return;
    let live = true, url = "";
    fetch(imageUrl(dashId, card.id)).then(async r => {
      if (!r.ok) throw new Error((await r.json()).error);
      url = URL.createObjectURL(await r.blob());
      if (live) setImg({ url }); else URL.revokeObjectURL(url);
    }).catch(e => { if (live) setImg({ error: (e as Error).message }); });
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [bg, dashId, card.id, view.image_path]);
  if (!entities.length) return <Say cls="alerts" text="보여 줄 대상이 없어요" />;
  const pos: Record<string, [number, number]> = { ...autoGrid(entities), ...(view.positions ?? {}), ...drag };
  const links = [...(view.links ?? []), ...entities.flatMap(e => (e.impact ?? []).map(t => [e.name, t] as [string, string]))];
  const at = (e: React.PointerEvent): [number, number] => {  // 화면 좌표 → SVG 안 좌표 → 0~1
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(root.current!.getScreenCTM()!.inverse());
    return [clamp(p.x / W, 0, 1), clamp(p.y / H, 0, 1)];
  };
  const down = (name: string) => (e: React.PointerEvent<SVGGElement>) => {
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* 포인터를 못 잡아도 끌기는 된다 */ }
    grab.current = name;
    setDrag({ ...pos });
  };
  const move = (e: React.PointerEvent<SVGGElement>) => { const name = grab.current; if (name) { const p = at(e); setDrag(d => ({ ...d, [name]: p })); } };
  const up = () => {
    if (!grab.current || !drag) return;
    grab.current = null;
    const positions = Object.fromEntries(Object.entries(drag).map(([k, p]) => [k, p.map(n => +n.toFixed(3)) as [number, number]]));
    setDrag(null);
    dispatch({ type: "card", id: card.id, patch: { view: { ...view, positions } } });
  };
  const middle = (text: string, fill: string, size = 12) => <text x={W / 2} y={H / 2} textAnchor="middle" fontSize={size} fill={fill}>{text}</text>;
  return (
    <svg ref={root} viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "100%", touchAction: "none" }} role="img" aria-label={card.title}>
      <g>
        {bg === "rack" ? Array.from({ length: 6 }, (_, i) => <rect key={i} x={20 + i * 62} y={14} width={52} height={H - 28} rx={4} fill="none" stroke={colors.line} strokeWidth={1.5} />)
          : bg === "network" ? Array.from({ length: 9 }, (_, k) => k + 1).flatMap(i => [
            <line key={"v" + i} x1={i * 40} y1={0} x2={i * 40} y2={H} stroke={colors.line} strokeDasharray="2 6" />,
            <line key={"h" + i} x1={0} y1={i * 26} x2={W} y2={i * 26} stroke={colors.line} strokeDasharray="2 6" />])
          : img.url ? <image href={img.url} x={0} y={0} width={W} height={H} preserveAspectRatio="xMidYMid meet" />
          : img.error ? middle(img.error, levelColor("bad", colors), 11)
          : middle(dashId ? "그림을 불러오는 중…" : "카드를 저장하면 그림이 보여요", colors.text)}
      </g>
      <g>{links.filter(([a, b]) => pos[a] && pos[b]).map(([a, b], i) => (
        <line key={i} x1={pos[a][0] * W} y1={pos[a][1] * H} x2={pos[b][0] * W} y2={pos[b][1] * H} stroke={colors.text} strokeWidth={1.2} opacity={0.6} />
      ))}</g>
      <g>{entities.map(e => {
        const [x, y] = [pos[e.name][0] * W, pos[e.name][1] * H];
        return (
          <g key={e.name} style={{ cursor: "grab" }} onPointerDown={down(e.name)} onPointerMove={move} onPointerUp={up}>
            <circle cx={x} cy={y} r={e.level === "ok" ? 8 : 10} fill={levelColor(e.level, colors)} stroke={colors.card} strokeWidth={2}>
              <title>{`${e.name}: ${LEVEL_NAMES[e.level]}${e.since_text ? ", " + e.since_text : ""}${e.reasons.length ? ", " + e.reasons.join(", ") : ""}`}</title>
            </circle>
            <text x={x} y={y + 22} textAnchor="middle" fontSize={10} fill={colors.ink}>{e.name}</text>
          </g>
        );
      })}</g>
    </svg>
  );
}
