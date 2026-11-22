"use client";
// B2+B4 링크 설정 칸(plans/dash-blocks.md "B2+B4 세부 명세 (2차, 확정)"): 보내는 카드·요소의 상대 고르기(view.link_to), 없는 상대, 다른 테이블의 맞출 컬럼(view.link_map). CardPanel이 부른다.
import { useEffect, useState, type ReactNode } from "react";
import { isFrame } from "@/components/frame/registry";
import { columns as fetchColumns } from "@/lib/api";
import { isSender, linkTargets, missingTargets } from "@/lib/links";
import { useDash } from "@/lib/store";
import type { Card } from "@/lib/types";
import { Check, Sel } from "../menu/Fields";

/** 칩 줄 요소 종류(값 필터를 보내면서 다른 보내는 쪽의 상대도 되는 요소) */
const CHIP_ROW = "chip_row";
/** 타일이 읽은 컬럼(구한 값의 data.reads) */
type TileReads = { conditions: string[]; periods: string[] };

/** card: 고른 카드·요소. onChange: 고친 카드 조각(view 통째)을 받는다 */
export type LinkPanelProps = { card: Card; onChange: (patch: Partial<Card>) => void };

/** [SPECIFICATION] plans/dash-blocks.md#LinkPanel
 *  보내는 카드·요소의 링크 상대와 맞출 컬럼을 고르는 칸을 그린다(보내는 종류가 아니면 아무것도 안 그림). */
export function LinkPanel({ card, onChange }: LinkPanelProps): ReactNode {
  const { state } = useDash();
  const [colsOf, setColsOf] = useState<Record<string, string[] | string>>({});  // 상대 카드 id → 컬럼 이름들, 못 받으면 이유 글
  const cards = state.dash?.cards ?? [];
  const to = card.view.link_to ?? linkTargets(card, cards);
  const map = card.view.link_map ?? {};
  const frame = card.view.frame;
  const sends = card.kind === "chart" || card.kind === "tile" || card.kind === CHIP_ROW || (card.kind === "segment" && frame?.mode === "filter");
  const viewSeg = card.kind === "segment" && !sends;
  const found = (id: string): Card | undefined => cards.find(c => c.id === id);
  // 맞출 컬럼을 고를 상대(데이터 카드): 보내는 쪽이 틀 요소이거나 다른 테이블일 때
  const same = (t: Card): boolean => t.source.table === card.source.table && (t.source.schema ?? "") === (card.source.schema ?? "");
  const mapTo = sends ? to.map(found).filter((t): t is Card => !!t && !isFrame(t.kind) && (isFrame(card.kind) || !same(t))) : [];
  const want = JSON.stringify(mapTo.map(t => [t.id, t.source.table ?? "", t.source.schema ?? ""]));
  useEffect(() => {
    let live = true;
    (JSON.parse(want) as [string, string, string][]).forEach(([id, table, schema]) => {
      fetchColumns(table, schema).then(r => live && setColsOf(o => ({ ...o, [id]: r.columns.map(c => c[0]) })))
        .catch((e: Error) => live && setColsOf(o => ({ ...o, [id]: e.message })));
    });
    return () => { live = false; };
  }, [want]);
  if (!isSender(card.kind)) return null;

  const change = (next: string[]) => onChange({ view: { ...card.view, link_to: next, link_map: Object.fromEntries(Object.entries(map).filter(([id]) => next.includes(id))) } });
  const setMap = (id: string, col: string, v: string) => {
    const row = { ...(map[id] ?? {}) };
    if (v) row[col] = v; else delete row[col];
    const rest = Object.fromEntries(Object.entries(map).filter(([k]) => k !== id));
    onChange({ view: { ...card.view, link_map: Object.keys(row).length ? { ...rest, [id]: row } : rest } });
  };
  const choices: { id: string; title: string }[] = [
    ...cards.filter(c => c.id !== card.id && (sends ? !isFrame(c.kind) || c.kind === CHIP_ROW : !isFrame(c.kind))),
    ...(card.kind === "chart" ? [{ id: card.id, title: `${card.title} (이 카드)` }] : []),
  ];
  const reads = (): string[] | null => {
    if (card.kind === "chart") return [card.source.body?.x as string];
    if (card.kind === "tile") {
      const r = (card.data as { reads?: TileReads }).reads;
      return r ? [...r.conditions, ...(card.view.press === "period" ? r.periods : [])] : null;
    }
    return [frame?.column ?? ""].filter(c => c !== "");
  };
  const read = reads();

  return (
    <div>
      <h5>링크</h5>
      <p className="hint">고른 카드만 따라와요. 나중에 놓은 카드는 여기서 더해야 따라와요.</p>
      {viewSeg
        ? <Sel label="상대 카드" value={to[0] ?? ""} blank="고르지 않음" options={choices.map(c => [c.id, c.title])} onChange={v => change(v ? [v] : [])} />
        : choices.map(c => <Check key={c.id} label={c.title} checked={to.includes(c.id)} onChange={on => change(on ? [...to, c.id] : to.filter(x => x !== c.id))} />)}
      {missingTargets(card, cards).map(id => (
        <div key={id} className="hint">{id}: 상대가 없어요 <button type="button" onClick={() => change(to.filter(x => x !== id))}>빼기</button></div>
      ))}
      {mapTo.length > 0 && read === null && <div className="hint">이 카드를 다시 구하면 맞출 컬럼을 고를 수 있어요.</div>}
      {read !== null && mapTo.map(t => {
        const cols = colsOf[t.id];
        if (typeof cols === "string") return <div key={t.id} className="err" role="alert">{t.title}: {cols}</div>;
        return cols && read.map(col => (
          <Sel key={`${t.id}/${col}`} label={`${t.title}: ${col} →`} value={map[t.id]?.[col] ?? ""} blank="같은 이름" options={cols} onChange={v => setMap(t.id, col, v)} />
        ));
      })}
    </div>
  );
}
