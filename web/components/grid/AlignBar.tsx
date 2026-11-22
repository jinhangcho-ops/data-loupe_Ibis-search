"use client";
// (B5) 섹션 머리 줄(편집 중)의 맞춤 단추 줄: 한 섹션에서 고른 조각(state.picked)이 둘 이상일 때만 보인다. 계산은 grid/sections의 alignPieces·distributePieces·sameSize.
import type { GridItem } from "@/lib/calc";
import type { Layout } from "@/lib/types";
import { alignPieces, distributePieces, sameSize } from "./sections";
import type { Room } from "./sections";

const DISTRIBUTE_MIN = 3; // 같은 간격은 조각 3개부터

export type AlignBarProps = {
  ids: string[];                                         // 이 섹션에서 고른 조각 id(고른 차례)
  items: GridItem[];                                     // 이 섹션의 모든 조각(sections.pieceItems)
  room: Room;                                            // 이 섹션의 눈금 수
  onApply: (layouts: Record<string, Layout>) => void;    // 맞춘 자리를 넣는다
  onError: (message: string) => void;                    // 맞출 수 없는 이유를 알린다
};

/** [SPECIFICATION] plans/dash-blocks.md#AlignBar
 *  고른 조각을 맞추는 단추 줄(맞춤 6, 같은 간격 2, 같은 크기 2). 고른 조각이 둘보다 적으면 그리지 않는다. */
export function AlignBar(props: AlignBarProps): React.JSX.Element | null {
  const { ids, items, room, onApply, onError } = props;
  if (ids.length < 2) return null;
  const buttons: { name: string; glyph: string; disabled?: boolean; run: () => Record<string, Layout> }[] = [
    { name: "왼쪽 맞춤", glyph: "⇤", run: () => alignPieces(items, ids, "left") },
    { name: "가로 가운데 맞춤", glyph: "↔", run: () => alignPieces(items, ids, "hcenter") },
    { name: "오른쪽 맞춤", glyph: "⇥", run: () => alignPieces(items, ids, "right") },
    { name: "위 맞춤", glyph: "⤒", run: () => alignPieces(items, ids, "top") },
    { name: "세로 가운데 맞춤", glyph: "↕", run: () => alignPieces(items, ids, "vcenter") },
    { name: "아래 맞춤", glyph: "⤓", run: () => alignPieces(items, ids, "bottom") },
    { name: "가로 같은 간격", glyph: "⋯", disabled: ids.length < DISTRIBUTE_MIN, run: () => distributePieces(items, ids, "x") },
    { name: "세로 같은 간격", glyph: "⋮", disabled: ids.length < DISTRIBUTE_MIN, run: () => distributePieces(items, ids, "y") },
    { name: "같은 너비", glyph: "▭", run: () => sameSize(items, ids, "w", room) },
    { name: "같은 높이", glyph: "▯", run: () => sameSize(items, ids, "h", room) },
  ];
  return (
    <div className="sec-align" role="group" aria-label="조각 맞추기">
      {buttons.map((b) => (
        <button
          key={b.name}
          type="button"
          className="ib"
          title={b.name}
          aria-label={b.name}
          disabled={b.disabled}
          onClick={() => {
            try {
              onApply(b.run());
            } catch (e) {
              onError((e as Error).message);
            }
          }}
        >
          {b.glyph}
        </button>
      ))}
    </div>
  );
}
