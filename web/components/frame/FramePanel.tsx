"use client";
// B10 틀 요소의 설정 칸(plans/dash-blocks.md "B10 세부 명세"): 카드 설정 패널(CardPanel)이 요소 카드일 때 데이터·모양 칸 대신 이것을 그린다.
import { useEffect, useState, type ReactNode } from "react";
import type { Card } from "@/lib/types";
import { columns } from "@/lib/api";
import { linkTargets } from "@/lib/links";
import { useDash } from "@/lib/store";
import { Check, LineBox, Sel, Txt } from "@/components/edit/menu/Fields";
import { ICONS } from "@/components/edit/panel/Tools";
import { MARK_EMPTY } from "@/lib/marks";
import { FRAME_ACTIONS, FRAME_PARTS, TEXT_ALIGNS, TEXT_SIZES, frameError, freshFrame, isFrame, type FrameAction, type FrameItem, type FrameKind, type FramePart, type FrameSettings, type SegCell, type TextAlign, type TextSize } from "./registry";

const MAX_LABEL = 30;  // 단추 글자 상한(서버 dash_store.MAX_FRAME_LABEL)
const MAX_TEXT = 500;  // 글 요소 글자 상한(서버 dash_store.MAX_FRAME_TEXT)
const MAX_ITEMS = 8;  // 줄 안 항목 상한(서버 dash_store.MAX_FRAME_ITEMS)
const MAX_CELLS = 8;  // 세그먼트 칸 상한(서버 dash_store.MAX_SEG_CELLS)
const BLANK_ITEM: FrameItem = { label: "", icon: "", action: "", arg: "" };
const BLANK_CELL: SegCell = { label: "", series: "", share: false, value: "" };
const LINKED = "링크한 카드에만 적용돼요. 링크는 아래 '링크' 칸에서 골라요. 만드는 곳에서 요소 칸을 바꾸면 그 값이 저장돼요.";
const NOTES: Partial<Record<FrameKind, string>> = {
  period_pick: LINKED, asof_field: LINKED, unit_seg: LINKED,
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
  const all = state.dash?.cards ?? [];
  const cards = all.filter(c => !isFrame(c.kind)).map((c): [string, string] => [c.id, c.title]);
  const lists = all.filter(c => c.kind === "list").map((c): [string, string] => [c.id, c.title]);
  const marked = all.filter(c => c.kind === "list" && (c.view.row_marks?.fields.length ?? 0) > 0);
  const markLists = marked.map((c): [string, string] => [c.id, c.title]);
  const markNames = marked.flatMap(c => (c.view.row_marks?.fields ?? []).filter(f => f.kind === "choice").map(f => f.name));
  const [cols, setCols] = useState<string[]>([]);
  const [colsError, setColsError] = useState("");
  const filterCols = [...cols, ...markNames.filter(n => !cols.includes(n))];
  // 거를 컬럼 후보: 데이터 카드들이 읽는 표마다 한 번 받은 컬럼 이름의 합집합
  const tableKey = JSON.stringify([...new Set(all.filter(c => !isFrame(c.kind) && c.source?.table).map(c => JSON.stringify([c.source.schema ?? "", c.source.table])))].sort());
  const filterKind = kind === "segment" || kind === "chip_row";
  useEffect(() => {
    if (!filterKind) return;
    let live = true;
    const tables = (JSON.parse(tableKey) as string[]).map(t => JSON.parse(t) as [string, string]);
    Promise.allSettled(tables.map(([schema, table]) => columns(table, schema))).then(rs => {
      if (!live) return;
      setCols([...new Set(rs.flatMap(r => (r.status === "fulfilled" ? r.value.columns.map(([name]) => name) : [])))].sort());
      setColsError(rs.flatMap(r => (r.status === "rejected" ? [(r.reason as Error).message] : [])).join(" / "));
    });
    return () => { live = false; };
  }, [filterKind, tableKey]);
  const target = kind === "segment" ? all.find(c => c.id === linkTargets(card, all)[0]) : undefined;
  const seriesNames = [...new Set((target?.data.values?.series ?? target?.data.by_unit?.day?.series ?? []).map(s => s.name))];
  const items = frame.items ?? [];
  const parts = frame.parts ?? [];
  const cells = frame.cells ?? [];
  const src = marked.find(c => c.id === frame.list);
  const choice = (src?.view.row_marks?.fields ?? []).filter(f => f.kind === "choice");
  const fld = choice.find(f => f.id === frame.field);
  const vals = frame.values ?? [];
  const putCell = (i: number, p: Partial<SegCell>) => set({ cells: cells.map((x, j) => (j === i ? { ...x, ...p } : x)) });
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
        {need === "card" && <Sel label={item.action === "csv" ? "저장할 목록" : item.action === "marks_save" || item.action === "marks_load" ? "표시를 주고받을 목록" : "크게 볼 카드"} value={item.arg ?? ""} blank="" options={item.action === "csv" ? lists : item.action === "marks_save" || item.action === "marks_load" ? markLists : cards}onChange={v => put({ arg: v })} />}
      </>
    );
  };
  const error = frameError(kind, frame) || colsError;
  return (
    <div className="cm-block">
      {note && <p className="hint">{note}</p>}
      {kind === "button" && fields(frame, set)}
      {kind === "text" && (
        <>
          <LineBox label="글" value={frame.text ?? ""} placeholder="" parse={() => ({})} onChange={v => set({ text: v.slice(0, MAX_TEXT) })} />
          <Sel label="글자 크기" value={frame.size ?? "md"} options={Object.entries(TEXT_SIZES)} onChange={v => set({ size: v as TextSize })} />
          <Sel label="정렬" value={frame.align ?? "left"} options={Object.entries(TEXT_ALIGNS)} onChange={v => set({ align: v as TextAlign })} />
        </>
      )}
      {kind === "top_strip" && (Object.keys(FRAME_PARTS) as FramePart[]).map(key => (
        <Check key={key} label={FRAME_PARTS[key]} checked={parts.includes(key)}
          onChange={on => set({ parts: (Object.keys(FRAME_PARTS) as FramePart[]).filter(k => (k === key ? on : parts.includes(k))) })} />
      ))}
      {kind === "segment" && (
        <>
          <Sel label="방식" value={frame.mode ?? "view"} options={[["view", "보기 바꾸기"], ["filter", "값으로 거르기"]]} onChange={v => set({ mode: v as "view" | "filter" })} />
          {frame.mode === "filter" && <Sel label="거를 컬럼" value={frame.column ?? ""} blank="" options={filterCols} onChange={v => set({ column: v })} />}
          {cells.map((c, i) => (
            <div key={i} className="cm-block">
              <Txt label="칸 글자" value={c.label} onChange={v => putCell(i, { label: v.slice(0, MAX_LABEL) })} />
              {frame.mode === "filter"
                ? <Txt label="값(비우면 거르지 않음)" value={c.value} onChange={v => putCell(i, { value: v })} />
                : (
                  <>
                    <Sel label="계열" value={c.series} blank="모든 계열" options={seriesNames} onChange={v => putCell(i, { series: v })} />
                    <Check label="비율(%)로" checked={c.share} onChange={on => putCell(i, { share: on })} />
                  </>
                )}
              <button type="button" onClick={() => set({ cells: cells.filter((_, j) => j !== i) })}>빼기</button>
            </div>
          ))}
          <button type="button" disabled={cells.length >= MAX_CELLS} onClick={() => set({ cells: [...cells, { ...BLANK_CELL }] })}>칸 추가</button>
        </>
      )}
      {kind === "chip_row" && <Sel label="거를 컬럼" value={frame.column ?? ""} blank="" options={filterCols}onChange={v => set({ column: v })} />}
      {kind === "filter_bar" && <Txt label="모두 풀기 단추 글자(비우면 단추 없음)" value={frame.clear_label ?? ""} onChange={v => set({ clear_label: v.slice(0, MAX_LABEL) })} />}
      {kind === "mark_count" && (
        <>
          <Txt label="글자" value={frame.label ?? ""} onChange={v => set({ label: v.slice(0, MAX_LABEL) })} />
          <Sel label="셀 목록" value={frame.list ?? ""} blank="" options={markLists} onChange={v => set({ list: v, field: "", values: [] })} />
          <Sel label="표시 칸" value={frame.field ?? ""} blank="" options={choice.map((f): [string, string] => [f.id, f.name])} onChange={v => set({ field: v, values: [] })} />
          <Sel label="모양" value={frame.look ?? "number"} options={[["number", "숫자 타일"], ["bar", "진척 칸"]]} onChange={v => set({ look: v as "number" | "bar" })} />
          {(frame.look ?? "number") === "number" && fld && [...fld.choices, ""].map(x => (
            <Check key={x} label={x || MARK_EMPTY} checked={vals.includes(x)} onChange={on => set({ values: on ? [...vals, x] : vals.filter(y => y !== x) })} />
          ))}
          {!marked.length && <p className="hint">표시 칸이 있는 목록이 없어요. 목록 카드 설정의 [줄 표시]에서 칸을 더하세요.</p>}
        </>
      )}
      {(kind === "button" || kind === "nav_rail" || kind === "segment" || kind === "chip_row" || kind === "filter_bar" || kind === "mark_count") &&<Check label="고른 단추에 빛 효과" checked={frame.ambient ?? false} onChange={on => set({ ambient: on })} />}
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
