"use client";
// B10 틀 요소의 설정 칸(plans/dash-blocks.md "B10 세부 명세"): 카드 설정 패널(CardPanel)이 요소 카드일 때 데이터·모양 칸 대신 이것을 그린다.
import type { ReactNode } from "react";
import type { Card } from "@/lib/types";
import { useDash } from "@/lib/store";
import { Check, Sel, Txt } from "@/components/edit/menu/Fields";
import { ICONS } from "@/components/edit/panel/Tools";
import { FRAME_ACTIONS, FRAME_PARTS, frameError, freshFrame, isFrame, type FrameAction, type FrameItem, type FrameKind, type FramePart, type FrameSettings } from "./registry";

const MAX_LABEL = 30;  // 단추 글자 상한(서버 dash_store.MAX_FRAME_LABEL)
const MAX_ITEMS = 8;  // 줄 안 항목 상한(서버 dash_store.MAX_FRAME_ITEMS)
const BLANK_ITEM: FrameItem = { label: "", icon: "", action: "", arg: "" };
const ALL_CARDS = "지금은 이 대시보드의 모든 카드에 적용돼요.";
const NOTES: Partial<Record<FrameKind, string>> = {
  period_pick: ALL_CARDS, asof_field: ALL_CARDS,
  unit_seg: "같은 쪽의 날짜 차트에 적용돼요. 저장되지 않아요.",
  page_tabs: "쪽은 위의 쪽 줄에서 더하고 빼요. 보는 곳에서는 이 탭으로 쪽을 오가요.",
};
const ACTION_OPTS = Object.entries(FRAME_ACTIONS).map(([k, a]): [string, string] => [k, a.name]);

/** card: 고른 요소 카드. onChange: 고친 view.frame(통째)을 받는다 */
export type FramePanelProps = { card: Card; onChange: (frame: Record<string, unknown>) => void };

/** [SPECIFICATION] plans/dash-blocks.md#FramePanel
 *  요소 종류에 맞는 설정 칸(글자·아이콘·할 일·받는 값·엠비언트·줄 안 항목·보일 칸)을 그린다. */
export function FramePanel(props: FramePanelProps): ReactNode {
  const { card, onChange } = props;
  const { state } = useDash();
  const kind = card.kind as FrameKind;
  const frame: FrameSettings = card.view.frame ?? freshFrame(kind);
  const set = (patch: FrameSettings) => onChange({ ...frame, ...patch });
  const pages = state.dash?.pages ?? [];
  const cards = (state.dash?.cards ?? []).filter(c => !isFrame(c.kind)).map((c): [string, string] => [c.id, c.title]);
  const items = frame.items ?? [];
  const parts = frame.parts ?? [];
  const note = NOTES[kind];
  // 단추 하나의 칸 한 벌(단추 요소 자신, 줄 안 항목마다)
  const fields = (item: Partial<FrameItem>, put: (p: Partial<FrameItem>) => void) => {
    const need = item.action ? FRAME_ACTIONS[item.action].arg : "";
    return (
      <>
        <Txt label="글자" value={item.label ?? ""} onChange={v => put({ label: v.slice(0, MAX_LABEL) })} />
        <Sel label="아이콘" value={item.icon ?? ""} blank="없음" options={ICONS} onChange={v => put({ icon: v })} />
        <Sel label="할 일" value={item.action ?? ""} blank="정하지 않음" options={ACTION_OPTS} onChange={v => put({ action: v as FrameAction | "", arg: "" })} />
        {need === "page" && <Sel label="이동할 쪽" value={item.arg ?? ""} blank="" options={pages} onChange={v => put({ arg: v })} />}
        {need === "card" && <Sel label="크게 볼 카드" value={item.arg ?? ""} blank="" options={cards} onChange={v => put({ arg: v })} />}
      </>
    );
  };
  const error = frameError(kind, frame);
  return (
    <div className="cm-block">
      {note && <p className="hint">{note}</p>}
      {kind === "button" && fields(frame, set)}
      {kind === "top_strip" && (Object.keys(FRAME_PARTS) as FramePart[]).map(key => (
        <Check key={key} label={FRAME_PARTS[key]} checked={parts.includes(key)}
          onChange={on => set({ parts: (Object.keys(FRAME_PARTS) as FramePart[]).filter(k => (k === key ? on : parts.includes(k))) })} />
      ))}
      {(kind === "button" || kind === "nav_rail") && <Check label="고른 단추에 빛 효과" checked={frame.ambient ?? false} onChange={on => set({ ambient: on })} />}
      {(kind === "nav_rail" || kind === "top_strip") && (
        <>
          {items.map((item, i) => (
            <div key={i} className="cm-block">
              {fields(item, p => set({ items: items.map((x, j) => (j === i ? { ...x, ...p } : x)) }))}
              <button type="button" onClick={() => set({ items: items.filter((_, j) => j !== i) })}>빼기</button>
            </div>
          ))}
          <button type="button" disabled={items.length >= MAX_ITEMS} onClick={() => set({ items: [...items, { ...BLANK_ITEM }] })}>항목 추가</button>
        </>
      )}
      {error && <p className="hint" role="alert">{error}</p>}
    </div>
  );
}
