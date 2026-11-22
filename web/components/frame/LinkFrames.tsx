"use client";
// B2+B4 링크를 보내거나 받는 틀 요소의 몸통(plans/dash-blocks.md "B2+B4 세부 명세 (2차, 확정)"): 세그먼트·칩 줄·걸린 필터 줄. FrameCard의 DRAW가 부른다.
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Empty } from "@/components/cards/svg/common";
import { useShell } from "@/components/shell/ShellProvider";
import { filterValues } from "@/lib/api";
import { filtersFor, pressLabel, tileSmall } from "@/lib/links";
import { markBars, markTotal } from "@/lib/marks";
import { useDash } from "@/lib/store";
import type { Card } from "@/lib/types";
import { frameError, freshFrame } from "./registry";

/** 칩 줄이 받아 둔 값 목록의 처음 상태 */
const NO_CHIPS = { values: [] as string[], note: "", error: "", asked: "" };  // asked: 이 값을 받은 요청(컬럼·필터). 지금 요청과 다르면 받는 중

const MARK_NO_LIST = "셀 목록이 대시보드에 없어요.";
const MARK_NO_FIELD = "고른 표시 칸이 목록에 없어요.";
const PERCENT = 100;

/** card: 요소 카드. live: 눌렀을 때 할 일을 하는지(만드는 곳의 편집 중·미리 보기는 false) */
export type LinkFrameProps = { card: Card; live: boolean };

/** [SPECIFICATION] plans/dash-blocks.md#Segment
 *  세그먼트 요소: 칸을 눌러 상대 카드의 보기를 바꾸거나 값으로 거른다(늘 한 칸 눌림). */
export function Segment(props: LinkFrameProps): ReactNode {
  const { card, live } = props;
  const { dispatch } = useDash();
  const shell = useShell();
  const frame = card.view.frame ?? freshFrame("segment");
  const at = shell.elems[card.id]?.cell ?? 0;
  const why = frameError("segment", frame);
  if (why) return <span className="hint">{why}</span>;
  return (
    <div className="fr-seg" role="group" aria-label={card.title} data-ambient={frame.ambient || undefined} inert={!live || undefined}>
      {(frame.cells ?? []).map((c, i) => (
        <button key={i} type="button" className="fr-seg-b" aria-pressed={i === at}
          onClick={() => shell.setCell(card.id, i).catch(e => dispatch({ type: "error", message: (e as Error).message }))}>{c.label}</button>
      ))}
    </div>
  );
}

/** [SPECIFICATION] plans/dash-blocks.md#ChipRow
 *  칩 줄 요소: 받은 필터로 좁힌 컬럼 값 칩을 하나만 골라 거른다(다시 누르면 풂). */
export function ChipRow(props: LinkFrameProps): ReactNode {
  const { card, live } = props;
  const { state, dispatch } = useDash();
  const shell = useShell();
  const frame = card.view.frame ?? freshFrame("chip_row");
  const column = frame.column ?? "";
  const cards = state.dash?.cards ?? [];
  const dashId = state.dash?.id ?? "";
  const filters = filtersFor(card.id, shell.presses, cards);
  const key = JSON.stringify(filters);
  const [s, setS] = useState(NO_CHIPS);
  const asked = [dashId, column, key, shell.conn?.key].join("\n"), loading = !!column && s.asked !== asked;
  // filters는 key로 바뀜을 본다(매 그림마다 새 배열이라 의존값에 넣지 않는다)
  useEffect(() => {
    if (!column) return;
    let current = true;
    filterValues(dashId, column, filters).then(
      d => { if (current) setS({ values: d.values, note: d.note, error: "", asked }); },
      e => { if (current) setS(prev => ({ ...prev, error: (e as Error).message, asked })); });
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dashId, column, key, shell.conn?.key]);
  const why = frameError("chip_row", frame);
  if (why) return <span className="hint">{why}</span>;
  return (
    <div className={"fr-chips" + (loading ? " busy" : "")} role="group" aria-label={card.title} aria-busy={loading || undefined} data-ambient={frame.ambient || undefined} inert={!live || undefined}>
      {!s.values.length && !loading && !s.error && <Empty />}
      {s.values.map(v => {
        const on = shell.presses.some(p => p.from === card.id && p.value?.in[0] === v);
        return (
          <button key={v} type="button" className="chip fr-chip" aria-pressed={on}
            onClick={() => shell.press(card.id, on ? null : { value: { column, in: [v] } }).catch(e => dispatch({ type: "error", message: (e as Error).message }))}>{v}</button>
        );
      })}
      {s.note && <span className="hint">{s.note}</span>}
      {s.error && <span className="hint" role="status">{s.error}</span>}
      {loading && <i className="bspin" aria-hidden="true" />}
    </div>
  );
}

/** [SPECIFICATION] plans/dash-blocks.md#MarkCount
 *  표시 세기 요소: 고른 목록의 고르기 표시 칸을 숫자 타일(누르면 그 값으로 거름)이나 진척 칸(값별 몫)으로 그린다. */
export function MarkCount(props: LinkFrameProps): ReactNode {
  const { card, live } = props;
  const { state, dispatch } = useDash();
  const shell = useShell();
  const frame = card.view.frame ?? freshFrame("mark_count");
  const why = frameError("mark_count", frame);
  if (why) return <span className="hint">{why}</span>;
  const src = (state.dash?.cards ?? []).find(c => c.id === frame.list && c.kind === "list");
  if (!src) return <span className="hint">{MARK_NO_LIST}</span>;
  const field = src.view.row_marks?.fields.find(f => f.id === frame.field && f.kind === "choice");
  if (!field) return <span className="hint">{MARK_NO_FIELD}</span>;
  const data = state.values[src.id]?.data ?? src.data;
  if (frame.look === "bar") {
    const bars = markBars(data, field);
    if (!bars.length) return <Empty />;
    return (
      <div className="fr-mbar" role="group" aria-label={frame.label || card.title} data-ambient={frame.ambient || undefined}>
        {frame.label && <span className="fr-mcount-label">{frame.label}</span>}
        <div className="fr-mbar-track" aria-hidden="true">{bars.map(b => <i key={b.value} style={{ "--r": b.ratio, "--c": b.color } as CSSProperties} />)}</div>
        <div className="fr-mbar-legend">{bars.map(b => <span key={b.value}><b style={{ "--c": b.color } as CSSProperties} />{`${b.label} ${b.n.toLocaleString()} · ${Math.round(b.ratio * PERCENT)}%`}</span>)}</div>
      </div>
    );
  }
  const values = frame.values ?? [];
  const n = markTotal(data, field.id, values);
  if (n === null) return <Empty />;
  const on = shell.presses.some(p => p.from === card.id);
  return (
    <button type="button" className="fr-mcount" aria-pressed={values.length ? on : undefined} disabled={!live || !values.length} data-ambient={frame.ambient || undefined}
      onClick={() => shell.press(card.id, on ? null : { value: { column: field.name, in: values } }).catch(e => dispatch({ type: "error", message: (e as Error).message }))}>
      {frame.label && <span className="fr-mcount-label">{frame.label}</span>}
      <b className="fr-mcount-n">{n.toLocaleString()}</b>
    </button>
  );
}

/** [SPECIFICATION] plans/dash-blocks.md#FilterTags
 *  걸린 필터 줄 요소: 걸린 필터마다 꼬리표와 ×, 단추 글자가 있으면 줄 끝에 모두 풀기 단추. */
export function FilterTags(props: LinkFrameProps): ReactNode {
  const { card, live } = props;
  const { state, dispatch } = useDash();
  const shell = useShell();
  const cards = state.dash?.cards ?? [];
  const frame = card.view.frame ?? freshFrame("filter_bar");
  const fail = (e: unknown) => dispatch({ type: "error", message: (e as Error).message });
  const tags: { big: string; small: string; off: () => Promise<void> }[] = [
    ...shell.picks.filter(p => p.in.length).map(p => ({ big: `${p.column} = ${p.in.join(", ")}`, small: "", off: () => shell.setFilter(p.column, []) })),
    ...shell.presses.map(p => {
      const body = (cards.find(c => c.id === p.from)?.source?.body ?? {}) as { conditions?: unknown[]; periods?: string[] };
      return { big: pressLabel(p, cards), small: p.tile ? tileSmall(body.conditions ?? [], p.tile.periods ? body.periods ?? [] : []) : "", off: () => shell.press(p.from, null) };
    }),
  ];
  return (
    <div className="fr-tags" role="group" aria-label={card.title} data-ambient={frame.ambient || undefined} inert={!live || undefined}>
      {tags.map((t, i) => (
        <span key={i} className="chip fr-tag">
          <b className="fr-tag-big">{t.big}</b>
          {t.small && <small className="fr-tag-small">{t.small}</small>}
          <button type="button" className="ghost" aria-label={`${t.big} 풀기`} onClick={() => t.off().catch(fail)}>×</button>
        </span>
      ))}
      {frame.clear_label && <button type="button" className="fr-btn" onClick={() => shell.clearFilters().catch(fail)}>{frame.clear_label}</button>}
    </div>
  );
}
