"use client";
// B10 틀 요소의 몸통(plans/dash-blocks.md "B10 세부 명세"): 단추·화면 이동 줄·위쪽 화면 줄·기간 고르기·기준일·기간 단위.
import type { ReactNode } from "react";
import { ICON_PATHS } from "@/components/cards/svg/common";
import { PageTabs } from "@/components/shell/PageTabs";
import { AsOfField, PeriodField } from "@/components/shell/PeriodBar";
import { useShell } from "@/components/shell/ShellProvider";
import { ThemeButton, flipEasy, flipTheme } from "@/components/shell/TopBar";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { Card, Constants, Unit } from "@/lib/types";
import { ChipRow, FilterTags, Segment } from "./LinkFrames";
import { FRAME_ACTIONS, frameError, freshFrame, runAction, type ActionHost, type FrameItem, type FrameKind, type FrameSettings } from "./registry";

/** 그리는 함수가 받는 것: 요소 카드·설정과 앱의 지금 상태 */
type Draw = {
  card: FrameCardProps["card"]; frame: FrameSettings; live: boolean; host: ActionHost; page: string; zoom: string | null;
  name: string; title: string | undefined; consts: Constants | null; unit: Unit | null; setUnit: (unit: Unit | null) => void; alert: (message: string) => void;
};

/** 단추 하나(단추 요소 자신, 줄 안 항목). 설정이 틀리면 단추 대신 그 이유 */
function FrameButton({ item, d }: { item: Partial<FrameItem>; d: Draw }) {
  const { label = "", icon = "", action = "", arg = "" } = item;
  const why = frameError("button", item);
  if (why) return <span className="hint">{why}</span>;
  const pressed = action === "page" ? arg === d.page : action === "dash_only" ? d.host.dashOnly : action === "zoom" ? d.zoom === arg : undefined;
  const run = () => { try { runAction({ action, arg }, d.host); } catch (e) { d.alert((e as Error).message); } };
  return (
    <button type="button" className="fr-btn" disabled={!d.live || action === ""} aria-pressed={pressed} aria-label={label ? undefined : action ? FRAME_ACTIONS[action].name : undefined} onClick={run}>
      {icon && <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON_PATHS[icon]} /></svg>}
      {label && <span>{label}</span>}
    </button>
  );
}

/** 요소 종류 → 그리는 함수(한 곳) */
const DRAW: Record<FrameKind, (d: Draw) => ReactNode> = {
  button: d => <div data-ambient={d.frame.ambient || undefined} inert={!d.live || undefined}><FrameButton item={d.frame} d={d} /></div>,
  nav_rail: d => (
    <nav className="fr-nav" aria-label={d.title} data-ambient={d.frame.ambient || undefined} inert={!d.live || undefined}>
      {(d.frame.items ?? []).map((item, i) => <FrameButton key={i} item={item} d={d} />)}
    </nav>
  ),
  top_strip: d => (
    <div className="fr-top">
      {(d.frame.parts ?? []).map(part =>
        part === "name" ? <b key={part} className="fr-name">{d.name}</b>
        : part === "period" ? <PeriodField key={part} id={`fr-per-${d.card.id}`} elem={d.card.id} />
        : part === "asof" ? <AsOfField key={part} id={`fr-asof-${d.card.id}`} elem={d.card.id} />
        : <span key={part} className="fr-act" inert={!d.live || undefined}><ThemeButton /></span>)}
      <span className="fr-act" inert={!d.live || undefined}>{(d.frame.items ?? []).map((item, i) => <FrameButton key={i} item={item} d={d} />)}</span>
    </div>
  ),
  period_pick: d => <PeriodField id={`fr-per-${d.card.id}`} elem={d.card.id} />,
  asof_field: d => <AsOfField id={`fr-asof-${d.card.id}`} elem={d.card.id} />,
  unit_seg: d => d.consts && (
    <div className="fr-seg" role="group" aria-label="기간 단위">
      {(Object.entries(d.consts.dash.units) as [Unit, string][]).map(([key, name]) => (
        <button key={key} type="button" className="fr-seg-b" aria-pressed={d.unit === key} onClick={() => d.setUnit(d.unit === key ? null : key)}>{name}</button>
      ))}
    </div>
  ),
  page_tabs: () => <PageTabs frame />,
  text: d => <div className="fr-text" data-size={d.frame.size ?? "md"} style={{ textAlign: d.frame.align ?? "left" }}>{d.frame.text ?? ""}</div>,
  segment: d => <Segment card={d.card} live={d.live} />,
  chip_row: d => <ChipRow card={d.card} live={d.live} />,
  filter_bar: d => <FilterTags card={d.card} live={d.live} />,
};

/** card: 요소 카드(kind가 FRAME_KINDS). live: 눌렀을 때 할 일을 하는지(만드는 곳의 편집 중·추가 메뉴의 미리 보기는 false) */
export type FrameCardProps = { card: Card; live: boolean };

/** [SPECIFICATION] plans/dash-blocks.md#FrameCard
 *  요소 카드 하나의 몸통을 종류에 맞게 그린다. */
export function FrameCard(props: FrameCardProps): ReactNode {
  const { card, live } = props;
  const { state, dispatch } = useDash();
  const shell = useShell();
  const consts = useConstants();
  const kind = card.kind as FrameKind;
  const pages = state.dash?.pages ?? [];
  const host: ActionHost = {
    recompute: shell.recompute, setPage: shell.setPage, dashOnly: shell.dashOnly, setDashOnly: shell.setDashOnly, setReport: shell.setReport, setZoom: shell.setZoom, setSettings: shell.setSettings,
    flipTheme, flipEasy, clearFilters: shell.clearFilters, pages, cardIds: (state.dash?.cards ?? []).map(c => c.id),
  };
  return DRAW[kind]({
    card, frame: card.view.frame ?? freshFrame(kind), live, host, page: pages.includes(shell.page) ? shell.page : pages[0] ?? "", zoom: shell.zoom,
    name: state.dash?.name ?? "", title: state.dash?.cards.find(c => c.id === card.id)?.title, consts, unit: shell.elems[card.id]?.unit ?? null, setUnit: u => shell.setElemUnit(card.id, u), alert: message => dispatch({ type: "error", message }),
  });
}
