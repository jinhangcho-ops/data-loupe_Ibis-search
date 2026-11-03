"use client";
// geomap(지도): 영역 경계를 값으로 칠하고(코로플레스) 점을 찍은 웹 메르카토르 SVG 한 장(옛 static/geo/geomap.js paintGeoMap).
// [지도 배경(OpenStreetMap)]을 켰을 때만(view.geo.tiles) 타일을 깔고 출처를 적는다. 못 받은 타일은 빼고, 하나도 못 받으면 출처도 뺀다.
// SvgCards가 React.lazy로 불러 지도 카드가 처음 그려질 때만 이 조각을 받는다(옛 need(GEO_JS)).
import { useState } from "react";
import { showValue } from "@/lib/calc";
import { OSM_CREDIT, cardFormat, fillLevels, mapFrame, ringPath, tileZoom, tilesFor } from "./__pure__";
import type { P } from "./common";

const GEO_W = 640;
type Geo = { bounds: number[]; regions: { name: string; value: number | null; rings: number[][] }[]; points: [number, number][]; label?: string };

export default function GeoMap({ card, colors }: P) {
  const [lost, setLost] = useState<Set<string>>(() => new Set());
  const v = card.data.values as unknown as Geo, geo = (card.view.geo ?? {}) as { tiles?: boolean; color?: string }, frame = mapFrame(v.bounds, GEO_W);
  const base = geo.color ?? colors.palette?.[0] ?? colors.series[0], fmt = cardFormat(card);
  const tiles = geo.tiles ? tilesFor(frame, tileZoom(frame)) : [], kept = tiles.filter(t => !lost.has(t.url));
  const levels = fillLevels(v.regions.map(r => r.value)), shade = geo.tiles ? 0.7 : 1;
  const nums = v.regions.map(r => r.value).filter((x): x is number => typeof x === "number"), id = "geo-" + card.id, y = frame.h - 22;
  const dot = card.colors?.series?.["점"] ?? colors.palette?.[1] ?? colors.series[1];
  return (
    <svg viewBox={`0 0 ${frame.w.toFixed(1)} ${frame.h.toFixed(1)}`} preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%", overflow: "hidden" }} role="img" aria-label={card.title}>
      {geo.tiles && <g>{kept.map(t => (
        <image key={t.url} href={t.url} x={t.px.toFixed(1)} y={t.py.toFixed(1)} width={t.size.toFixed(1)} height={t.size.toFixed(1)} preserveAspectRatio="none" onError={() => setLost(s => new Set(s).add(t.url))} />
      ))}</g>}
      {v.regions.map((r, i) => {
        const own = card.colors?.parts?.[r.name], color = own ?? base;
        return (
          <path key={i} d={r.rings.map(ring => ringPath(ring, frame.project)).join("")} fillRule="evenodd" fill={own || levels[i] != null ? color : "none"}
            fillOpacity={own ? 0.85 : levels[i] == null ? 0 : (levels[i]! * shade).toFixed(3)} stroke={colors.text} strokeOpacity={0.55} strokeWidth={0.8} vectorEffect="non-scaling-stroke">
            <title>{`${r.name}${r.value == null ? "" : " · " + showValue(r.value, fmt)}`}</title>
          </path>
        );
      })}
      {v.points.length > 0 && (
        <g fill={dot} fillOpacity={0.75} stroke={colors.card} strokeWidth={0.5}>
          {v.points.map(([lon, lat], i) => { const [x, yy] = frame.project(lon, lat); return <circle key={i} cx={x.toFixed(1)} cy={yy.toFixed(1)} r={2.6} />; })}
        </g>
      )}
      {nums.length > 0 && (  // 범례: 옅은 색 → 진한 색과 가장 작은·큰 값
        <>
          <defs><linearGradient id={id}><stop offset="0%" stopColor={base} stopOpacity={0.18} /><stop offset="100%" stopColor={base} stopOpacity={0.9} /></linearGradient></defs>
          <rect x={8} y={y} width={90} height={8} fill={`url(#${id})`} stroke={colors.line} />
          <text x={8} y={y - 3} fontSize={10} fill={colors.text}>{v.label}</text>
          <text x={8} y={y + 19} fontSize={10} fill={colors.text}>{showValue(Math.min(...nums), fmt)}</text>
          <text x={98} y={y + 19} fontSize={10} fill={colors.text} textAnchor="end">{showValue(Math.max(...nums), fmt)}</text>
        </>
      )}
      {kept.length > 0 && <text x={frame.w - 4} y={frame.h - 4} fontSize={9} fill="#333" textAnchor="end" stroke="#fff" strokeWidth={2} paintOrder="stroke">{OSM_CREDIT}</text>}
    </svg>
  );
}
