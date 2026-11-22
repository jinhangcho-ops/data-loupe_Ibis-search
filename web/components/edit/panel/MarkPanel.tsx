"use client";
// B9 목록 카드의 [줄 표시] 칸(plans/dash-blocks.md "B9 세부 명세"): 열쇠 컬럼, 사용자가 만드는 표시 칸(고르기·글·날짜·숫자)과 이름·고를 값,
// 표시 바꾸는 곳, 가져올 때 같은 줄 규칙(view.row_marks). [표시 칸 적용]을 누르면 ListPanel의 다시 구하기(onApply)로 표시 컬럼을 받는다. ListPanel이 부른다.
import type { ReactNode } from "react";
import { useConstants } from "@/lib/constants";
import type { Card, CardView, MarkField, MarkView } from "@/lib/types";
import { Check, LineBox, Sel, Txt } from "../menu/Fields";

const KIND_NAMES: Record<MarkField["kind"], string> = { choice: "고르기", text: "글", date: "날짜", number: "숫자" };
const EDITS: [string, string][] = [["cell", "줄 끝 칸"], ["pop", "누르면 뜨는 칸"]];
const CLASHES: [string, string][] = [["keep", "있는 표시 두기"], ["replace", "가져온 표시로 바꾸기"], ["fill", "빈 칸만 채우기"]];
const ID_CHARS = 16;  // 서버 dash_store.ROW_MARK_ID
const NEED_GROUPS = "묶음 컬럼을 고르면 줄에 표시를 붙일 수 있어요.";
const NEED_NAME = "칸 이름을 적어 주세요.";
const NEED_KEYS = "열쇠 컬럼을 하나 이상 골라 주세요.";
const KEYS_NOTE = "열쇠 컬럼을 바꾸면 이미 붙인 표시는 새 열쇠와 맞지 않아 보이지 않아요(지우지는 않아요).";
const APPLY_NOTE = "칸을 더하거나 이름·고를 값을 바꾼 뒤 [표시 칸 적용]을 눌러야 목록에 보여요.";

/** card: 고른 목록 카드. groups: 묶음 컬럼(열쇠 후보). onChange: 고친 카드 조각(view 통째). onApply: 보기 조각을 넣고 다시 구하기(ListPanel의 resort) */
export type MarkPanelProps = { card: Card; groups: string[]; onChange: (patch: Partial<Card>) => void; onApply: (patch: Partial<CardView>) => void };

/** [SPECIFICATION] plans/dash-blocks.md#MarkPanel
 *  목록 카드의 줄 표시 설정 칸(열쇠 컬럼·표시 칸 더하기·이름·고를 값·빼기·바꾸는 곳·겹치는 줄 규칙·적용)을 그린다. */
export function MarkPanel(props: MarkPanelProps): ReactNode {
  const { card, groups, onChange, onApply } = props;
  const d = useConstants()?.dash.row_marks;
  if (!d) return null;
  const m = card.view.row_marks ?? d.default;
  const put = (next: MarkView) => onChange({ view: { ...card.view, row_marks: next } });
  const putField = (i: number, f: Partial<MarkField>) => put({ ...m, fields: m.fields.map((x, j) => (j === i ? { ...x, ...f } : x)) });
  return (
    <>
      <h5>줄 표시</h5>
      {groups.length === 0 && <div className="hint">{NEED_GROUPS}</div>}
      <span className="hint">줄을 가리는 열쇠 컬럼</span>
      {groups.map(c => (
        <Check key={c} label={c} checked={m.keys.includes(c)} onChange={on => put({ ...m, keys: on ? [...m.keys, c] : m.keys.filter(x => x !== c) })} />
      ))}
      {m.fields.length > 0 && m.keys.length === 0 && <div className="err" role="alert">{NEED_KEYS}</div>}
      <div className="hint">{KEYS_NOTE}</div>
      {m.fields.map((f, i) => (
        <div key={f.id} className="cm-block">
          <span className="hint">{`${KIND_NAMES[f.kind]} 칸`}</span>
          <Txt label="칸 이름" value={f.name} onChange={name => putField(i, { name: name.slice(0, d.max_name) })} />
          {!f.name && <div className="err" role="alert">{NEED_NAME}</div>}
          {f.kind === "choice" && (
            <LineBox label="고를 값" value={f.choices.join("\n")} placeholder="한 줄에 하나" parse={() => ({})}
              onChange={t => putField(i, { choices: [...new Set(t.split("\n").map(s => s.trim()).filter(Boolean))].slice(0, d.max_choices).map(s => s.slice(0, d.max_name)) })} />
          )}
          <button type="button" className="ghost" onClick={() => put({ ...m, fields: m.fields.filter((_, j) => j !== i) })}>빼기</button>
        </div>
      ))}
      {m.fields.length < d.max_fields && groups.length > 0 && d.kinds.map(k => (
        <button key={k} type="button" className="ghost" onClick={() => put({ ...m, keys: m.keys.length ? m.keys : [groups[0]], fields: [...m.fields, { id: crypto.randomUUID().replaceAll("-", "").slice(0, ID_CHARS), name: "", kind: k, choices: [] }] })}>{`${KIND_NAMES[k]} 칸 더하기`}</button>
      ))}
      <Sel label="표시 바꾸는 곳" value={m.edit} options={EDITS} onChange={edit => put({ ...m, edit: edit as MarkView["edit"] })} />
      <Sel label="가져올 때 같은 줄이 있으면" value={m.clash} options={CLASHES} onChange={clash => put({ ...m, clash: clash as MarkView["clash"] })} />
      <div className="hint">{APPLY_NOTE}</div>
      <button type="button" className="ghost" onClick={() => onApply({ row_marks: m })}>표시 칸 적용</button>
    </>
  );
}
