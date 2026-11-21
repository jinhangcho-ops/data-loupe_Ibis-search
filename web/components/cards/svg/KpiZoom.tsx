"use client";
// (B10b) KPI 큰 창(그림 2): 왼쪽 위 이름·값·비교 한 줄, 오른쪽 위 눈금 단추 2개와 ×, 아래 축이 있는 큰 추이(직전 기간 회색 선 + 이번 기간 색 선·바탕).
// 눈금은 기간 단위 요소가 고른 단위를 따른다(kpi_trend.ZOOM_TICKS). 시간 눈금은 창을 열 때 한 번만 서버에 묻는다(/api/dash/card/hours).
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Card, ComputeResult, Ctx, Unit } from "@/lib/types";
import { AXIS, MARGIN, TIP } from "../chart/Charts";
import { seriesKit } from "../chart/series";
import { markColors } from "../ChartCard";
import { Pop } from "../Pop";
import { cardFormat, cardValue } from "./__pure__";
import { GRID_DASH, NOW_FILL, TICK_NAMES, ZOOM_TICKS, dayTrend, hourTrend, kpiColor, kpiDelta, trendSpans, unitTrend, zoomRows, type HourValues, type Tick } from "./kpi_trend";
import { showValue } from "@/lib/calc";
import { compareItem } from "@/lib/series";
import { useConstants } from "@/lib/constants";
import { cardHours } from "@/lib/api";
import { useCompare } from "@/components/shell/ShellProvider";
import { useDash } from "@/lib/store";

export const HOUR_LOADING = "시간 눈금 값을 읽는 중이에요. 그동안 일 눈금으로 보여요.";
export const HOUR_OFFLINE = "연결하면 시간 눈금을 볼 수 있어요. 지금은 일 눈금으로 보여요.";
export const HOUR_EMPTY = "이 기간에는 시간 값이 없어 일 눈금으로 보여요.";
export const NO_TREND ="기간 기준 날짜 컬럼이 없는 카드라 추이를 그릴 수 없어요.";
export const NOW_NAME = "이번 기간", PREV_NAME = "직전 기간";  // 그림 풍선의 선 이름(직전 기간 이름이 있으면 그것)
export const ZOOM_LINE = 3;  // 큰 창 선 굵기(Charts.tsx 주 선 W_LINE과 같다)

export type KpiZoomProps = { card: Card; result?: ComputeResult; palette: string[]; ctx: Ctx | null; unit: Unit | null; live: boolean; onClose: () => void };

/** [SPECIFICATION] plans/dash-blocks.md#KpiZoom
 *  KPI 카드의 큰 창(Pop head 자리에 이름·값·비교·눈금 단추·×, 몸통에 Recharts 추이)을 그린다. */
export function KpiZoom({ card, result, palette, ctx, unit, live, onClose }: KpiZoomProps): ReactNode {
  const consts = useConstants();
  const merge = consts?.merge ?? [];
  const kit = useMemo(() => consts && seriesKit(consts), [consts]);
  const shown: Card = { ...card, data: result?.data ?? card.data };
  const fmt = cardFormat(shown), unitText = shown.view?.unit_text ?? "";
  const num = showValue(cardValue(shown, 0, ctx?.range, merge), fmt);
  const mode = ctx?.compare_mode ?? "prev";
  const said = useCompare(card.id), dash = useDash().state.dash?.id ?? "";
  const label = compareItem(shown, ctx?.range, ctx?.prev, merge, mode)?.label ?? "";
  const spans = trendSpans(shown, ctx?.range, ctx?.prev, mode), ticks = ZOOM_TICKS[unit ?? "none"], timed = !!shown.data?.by_unit?.day;
  const [tick, setTick] = useState<Tick>(ticks[0]);
  const [hours, setHours] = useState<HourValues | null>(null);
  const hourly = ticks[0] === "hour" && timed;
  const [note, setNote] = useState(hourly ? (live ? HOUR_LOADING : HOUR_OFFLINE) : "");
  useEffect(() => {
    if (!hourly || !live) return;
    let on = true;
    cardHours(shown, ctx, dash).then(h => { if (!on) return; setHours(h); setNote(h.reason || (h.labels.length ? "" : HOUR_EMPTY)); }, e => { if (on) setNote((e as Error).message); });
    return () => { on = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const hourOk = !!hours && !hours.reason && hours.labels.length > 0;
  const draw: Tick = tick === "hour" && !hourOk ? "day" : tick;
  const trend = draw === "hour" ? hourTrend(hours!, spans) : draw === "day" ? dayTrend(shown, spans, merge) : kit ? unitTrend(shown, spans, draw, kit.byUnit) : { prev: [], now: [] };
  const rows = zoomRows(trend, draw), color = kpiColor(shown, markColors(palette));
  const head = (
    <>
      <div className="zoom-head">
        <h3>{card.title}</h3>
        <div className="zoom-val">{num}{unitText && <span className="kpi-unit">{unitText}</span>}</div>
        {said && <div className={"bcmp kpi-cmp " + said.tone} title={said.text}>{kpiDelta(said, label, fmt)}</div>}
      </div>
      <div className="zoom-tools">
        {timed && <div className="zoom-ticks" role="group" aria-label="눈금">{ticks.map(k => <button key={k} type="button" className="btn" aria-pressed={draw === k} disabled={k === "hour" && !hourOk} title={k === "hour" && !hourOk ? note || undefined : undefined} onClick={() => setTick(k)}>{TICK_NAMES[k]}</button>)}</div>}
        <button type="button" className="ib" aria-label="닫기" title="닫기" onClick={onClose} autoFocus>×</button>
      </div>
    </>
  );
  return (
    <Pop title={card.title} cls="zoom-box kpi-zoom" onClose={onClose} head={head}>
      {ticks[0] === "hour" && !hourOk && note && <div className="hint" role="status">{note}</div>}
      {!timed ? <div className="hint">{NO_TREND}</div> : (
        <div className="zoom-chart">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={MARGIN}>
              <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray={GRID_DASH} />
              <XAxis dataKey="label" {...AXIS} />
              <YAxis {...AXIS} tickFormatter={(x: number) => showValue(x, fmt)} />
              <Tooltip {...TIP} formatter={x => showValue(x as number, fmt, unitText)} />
              {spans?.prev && <Line dataKey="prev" name={spans.prev.text || PREV_NAME} stroke="var(--muted)" strokeWidth={ZOOM_LINE} dot={false} isAnimationActive={false} />}
              <Area dataKey="now" name={spans ? NOW_NAME : card.title} stroke={color} strokeWidth={ZOOM_LINE} fill={color} fillOpacity={NOW_FILL} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Pop>
  );
}
