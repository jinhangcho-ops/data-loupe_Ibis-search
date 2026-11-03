"use client";
// 카드 설정 패널의 [모양] 줄(옛 dash.js tools·svgTools·loadPoints·colorEditor): 차트 종류(바꿀 수 없는 종류는 흐리게, 누르면 이유 = seriesKit.typeReason)·시간 단위,
// 산점도·버블 점 받기(/api/chart/points)와 [크기]·추세선·r, 목표 칸(값 모양이 있는 SVG 카드), 값 모양(형식·단위·좋은 방향), 비교 방식, 규칙 편집(N6 RulesEditor), 색 편집기(이름마다 HEX).
// 바꾸면 onChange({view}) 한 번. 점은 카드 값(data.points)에 두고, 기간·실시간으로 구한 값이 보이는 중이면 그 값에도 붙인다(dispatch values).
import { useMemo, useState } from "react";
import { CHART_KINDS } from "@/components/cards/ChartCard";
import { seriesKit } from "@/components/cards/chart/series";
import { useTheme } from "@/components/cards/svg/common";
import { cardRequest, chartPoints, computeCard } from "@/lib/api";
import { HEX } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { pickColor } from "@/lib/series";
import { useDash } from "@/lib/store";
import type { Card, CardData, CardView, ChartType } from "@/lib/types";
import { RulesEditor } from "../RulesEditor";
import { WATCH } from "../menu/kinds";

const FORMATS: [string, string][] = [["number", "숫자"], ["percent", "%"], ["currency", "₩"]];
// 목표·값 모양이 없는 SVG 카드(옛 svgTools plain)
const RINGS: [string, string][] = [["stack", "한 고리 누적"], ["double", "겹친 두 고리"], ["half", "반원 게이지"], ["ticks", "고리와 목표 눈금"]];
const PLAIN = ["recent_rows", "gauge", "status", "alerts", "map", "deviation", "table", "sentence", "funnel", "geomap"];

export function Tools({ card, columns, onChange }: { card: Card; columns: string[]; onChange: (patch: Partial<Card>) => void }) {
  const consts = useConstants();
  const { state, dispatch } = useDash();
  const theme = useTheme();
  const kit = useMemo(() => consts && seriesKit(consts), [consts]);
  const [msg, setMsg] = useState("");
  const [paint, setPaint] = useState(false);
  if (!consts || !kit) return null;
  const live = state.values[card.id]?.data, shown: Card = { ...card, data: live ?? card.data };
  const view = card.view, setView = (p: Partial<CardView>, extra: Partial<Card> = {}) => onChange({ view: { ...view, ...p }, ...extra });
  const byUnit = shown.data?.by_unit, timed = !!byUnit?.day && card.kind !== "dual_trend";
  const pointy = ["scatter", "bubble"].includes(view.type);
  // 점을 카드 값에 넣거나 뺀다(보이는 구한 값에도)
  const withPoints = (data: CardData | undefined, points: CardData["points"]) => { const d = { ...(data ?? {}) }; if (points) d.points = points; else delete d.points; return d; };
  const putPoints = (next: Partial<CardView>, points: CardData["points"]) => {
    setView(next, { data: withPoints(card.data, points) });
    if (live) dispatch({ type: "values", results: [{ id: card.id, data: withPoints(live, points) }] });
  };
  // 산점도·버블이면 서버에서 점을 받는다(연결 필요, 다시 구하기와 같은 기간). 다른 종류면 점은 버린다(카드 크기)
  const loadPoints = async (next: Partial<CardView>) => {
    const v = { ...view, ...next };
    if (!["scatter", "bubble"].includes(v.type)) return putPoints(next, undefined);
    const body = (card.source.body ?? {}) as Record<string, unknown> & { series?: { y?: string }[] };
    const { points } = await chartPoints({ ...body, y: body.series?.[0]?.y, size: v.type === "bubble" ? v.points?.size ?? "" : "", source_key: card.source.key, date: card.source.date, ctx: state.ctx });
    putPoints(next, points);
  };
  const tryPoints = (next: Partial<CardView>) => loadPoints(next).then(() => setMsg(""), e => setMsg((e as Error).message));
  const typeBtn = ([id, name]: [string, string]) => {
    const why = kit.typeReason(shown, id as Exclude<ChartType, "">);
    return (
      <button key={id} type="button" className={`btype${view.type === id ? " on" : ""}${why ? " off" : ""}`} aria-pressed={view.type === id} onClick={() => {
        if (why) return setMsg(why);
        tryPoints({ type: view.type === id ? "" : (id as CardView["type"]) });  // 한 번 더 누르면 계열마다 정한 대로
      }}>{name}</button>
    );
  };
  const unitText = card.kind === "kpi" || card.kind === "donut_kpi" || card.kind === "stripes";
  const hasRules = card.kind === "table" || card.kind === "sentence" || WATCH.includes(card.kind);
  const withTarget = !CHART_KINDS.includes(card.kind) && !PLAIN.includes(card.kind);
  const target = view.target as { value?: number; name?: string } | undefined, targetErr = (card.data?.target as { error?: string } | undefined)?.error;
  const setTarget = (raw: string) => {
    if (raw === "" ? target?.value == null : +raw === target?.value) return;
    if (raw === "") { const rest = { ...view }; delete rest.target; return onChange({ view: rest, data: { ...card.data, target: null } }); }
    onChange({ view: { ...view, target: { value: +raw } }, data: { ...card.data, target: { value: +raw, label: "", error: "" } } });
  };
  // 종류별 칸(옛 svgTools): 숫자 칸은 비우면 지우고, 칸 수·행 수는 서버 범위(1~100)로 맞춘다
  const num = (key: "cells" | "full" | "size" | "rows", label: string, width: number, min?: number, max?: number) => (
    <label className="hint bset">{label}
      <input type="number" aria-label={label} min={min} max={max} step={min === 1 ? 1 : undefined} style={{ width }} key={String(view[key] ?? "")} defaultValue={view[key] ?? ""}
        onBlur={e => {
          if (e.target.value === "") { if (view[key] == null) return; const rest = { ...view }; delete rest[key]; return onChange({ view: rest }); }
          const v = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, +e.target.value));
          if (v !== view[key]) setView({ [key]: v });
        }} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
    </label>
  );
  const tiles = !!(view.geo as { tiles?: boolean } | undefined)?.tiles;
  const kindFields = {
    geomap: (<>
      <label className="hint bset">지도 배경(OpenStreetMap)
        <input type="checkbox" aria-label="지도 배경" checked={tiles} title="켜면 지금 보는 지도 범위가 OpenStreetMap 서버(tile.openstreetmap.org)로 전송돼요. 끄면 경계만 그리고 아무것도 보내지 않아요."
          onChange={e => setView({ geo: { ...view.geo, tiles: e.target.checked } })} />
      </label>
      <span className="hint">{tiles ? "보는 범위가 tile.openstreetmap.org로 전송돼요." : ""}</span>
    </>),
    donut_kpi: (
      <label className="hint bset">고리
        <select className="bunit" style={{ flexBasis: "auto" }} aria-label="고리" value={view.ring || "stack"} onChange={e => setView({ ring: e.target.value as CardView["ring"] })}>
          {RINGS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
        </select>
      </label>
    ),
    stripes: <>{num("cells", "칸 수", 50, 1, 100)}{num("full", "가득 찬 값", 80)}</>,
    blocks: num("size", "한 칸 크기", 80),
    recent_rows: num("rows", "행 수", 50, 1, 100),
  }[card.kind as string] ?? null;
  // 날짜 X축이 아닌 카드에 대시보드의 기간·기준일을 걸 날짜 컬럼(옛 dateSelect). 고르면 그 기간으로 다시 구한다
  const dateCols = shown.data?.date_cols ?? [];
  const setDate = async (value: string) => {
    const source = { ...card.source }, was = card.source.date;
    if (value) source.date = value; else delete source.date;
    if (!value) return onChange({ source });
    try {
      const r = await computeCard(cardRequest({ ...card, source, data: {} }), state.ctx);
      setMsg("");
      onChange({ source: { ...source, ...r.source }, data: r.data });
    } catch (e) { setMsg(`${(e as Error).message}${was ? "" : " 기간 기준 날짜는 그대로예요."}`); }
  };
  const dateSel = dateCols.length > 0 && (
    <select className="bunit" style={{ flexBasis: "auto" }} aria-label="기간 기준 날짜" title="대시보드 기간과 기준일을 이 날짜 컬럼에 걸어 다시 구해요" value={card.source.date ?? ""} onChange={e => setDate(e.target.value)}>
      <option value="">기간 기준: 없음</option>
      {dateCols.map(c => <option key={c} value={c}>기간 기준: {c}</option>)}
    </select>
  );
  return (
    <div className="bextra">
      {card.kind === "chart" && (
        <div className="btools" role="group" aria-label="차트 종류">
          {Object.entries(consts.dash.types).map(typeBtn)}
          {(pointy || shown.data?.x_kind === "num") && (
            <select className="bunit" aria-label="버블 크기" title="버블 크기로 쓸 숫자 컬럼" value={view.points?.size ?? ""} onChange={e => tryPoints({ points: { size: e.target.value } })}>
              <option value="">크기 없음</option>
              {(shown.data?.num_cols ?? []).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          {pointy && ([["trend", "추세선"], ["show_r", "r"]] as const).map(([k, name]) => (
            <button key={k} type="button" className={`btype${view[k] ? " on" : ""}`} aria-pressed={!!view[k]} title={k === "show_r" ? "상관계수 r 보이기" : "추세선 보이기"} onClick={() => setView({ [k]: !view[k] })}>{name}</button>
          ))}
          {!byUnit && dateSel}
          {byUnit && (
            <select className="bunit" aria-label="시간 단위" title="시간 단위. 일 값을 묶어서 다시 그려요" value={view.unit || "day"} onChange={e => setView({ unit: e.target.value as CardView["unit"] })}>
              {Object.entries(consts.dash.units).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          )}
        </div>
      )}
      {card.kind !== "chart" && card.kind !== "analysis" && (
        <div className="btools">
          {withTarget && (target?.name ? <span className="hint">목표: {target.name}{targetErr ? ` (${targetErr})` : ""}</span> : (
            <label className="hint bset">목표
              <input type="number" aria-label="목표" style={{ width: 90 }} key={String(target?.value ?? "")} defaultValue={target?.value ?? ""}
                onBlur={e => setTarget(e.target.value)} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
            </label>
          ))}
          {dateSel}
          {(!PLAIN.includes(card.kind) || card.kind === "geomap") && (
            <select className="bunit" aria-label="표시 형식" value={view.format ?? "number"} onChange={e => setView({ format: e.target.value as CardView["format"] })}>
              {FORMATS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
            </select>
          )}
          {kindFields}
          {unitText && <input aria-label="단위" placeholder="단위" maxLength={10} style={{ width: 80, height: 24, fontSize: 12 }} value={view.unit_text ?? ""} onChange={e => setView({ unit_text: e.target.value })} />}
        </div>
      )}
      {timed && (
        <div className="btools">
          {byUnit.day && (
            <select className="bunit" style={{ flexBasis: "auto" }} aria-label="비교 방식" value={view.compare?.mode ?? "span"} onChange={e => setView({ compare: { mode: e.target.value as "span" | "point" } })}>
              <option value="span">기간 비교</option><option value="point">시점 비교</option>
            </select>
          )}
          <select className="bunit" style={{ flexBasis: "auto" }} aria-label="좋은 방향" value={view.better ?? "up"} onChange={e => setView({ better: e.target.value as "up" | "down" })}>
            <option value="up">늘수록 좋음</option><option value="down">줄수록 좋음</option>
          </select>
        </div>
      )}
      {hasRules && <RulesEditor view={view} columns={columns} onChange={p => setView(p)} />}
      <div className="btools">
        <button type="button" className={`btype${paint ? " on" : ""}`} aria-expanded={paint} onClick={() => setPaint(!paint)}>색</button>
      </div>
      {paint && <ColorEditor card={card} shown={shown} theme={theme.series} palette={state.dash?.palette ?? []} kit={kit} onChange={onChange} />}
      <div className="hint btype-msg" role="status">{msg}</div>
    </div>
  );
}

// 이름마다 HEX 칸(옛 colorEditor). 틀린 값은 칸 옆에 이유를 보이고 이전 색을 그대로 둔다
function ColorEditor({ card, shown, theme, palette, kit, onChange }: { card: Card; shown: Card; theme: string[]; palette: string[]; kit: ReturnType<typeof seriesKit>; onChange: (patch: Partial<Card>) => void }) {
  const [errs, setErrs] = useState<Record<string, string>>({});
  const colors = { palette: palette.length ? palette : undefined, series: theme }, { group, names } = kit.colorNames(shown);
  return (
    <div className="bcolors">
      {names.map((name, i) => {
        // 지도 카드: 정하지 않은 영역은 모두 바탕 색(값에 따라 진하기만 다름)
        const now = card.kind === "geomap" && !card.colors?.[group]?.[name] ? (card.view.geo?.color as string | undefined) ?? colors.palette?.[0] ?? colors.series[0] : pickColor(shown, group, name, colors, i);
        return (
          <div className="brow" key={name + now}>
            <i className="swatch" style={{ background: now }} />
            <span className="bname">{name}</span>
            <input defaultValue={now} title="색 코드. 예: #11706f" aria-label={`${name} 색`} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} onBlur={e => {
              const v = e.target.value.trim();
              if (v === now) return;
              if (!HEX.test(v)) { e.target.value = now; return setErrs({ ...errs, [name]: `‘${v}’은 색 코드가 아니에요. #11706f처럼 적어 주세요.` }); }
              setErrs({ ...errs, [name]: "" });
              onChange({ colors: { ...card.colors, [group]: { ...card.colors?.[group], [name]: v } } });
            }} />
            <span className="err">{errs[name]}</span>
          </div>
        );
      })}
      <button type="button" className="ghost" title="이 카드에서 따로 정한 색을 지우고 대시보드 색으로 돌아가요" onClick={() => onChange({ colors: { ...card.colors, series: {}, parts: {} } })}>지정 색 지우기</button>
    </div>
  );
}
