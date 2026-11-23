"use client";
// tile(B2 숫자 타일): 이름(카드 머리 줄)과 큰 숫자 하나, 그 아래 적은 설명 글과 전기 대비 한 줄. 값은 KPI와 같은 길(cardValue 첫 계열), 추이선·목표 없음.
import type { CSSProperties, ReactNode } from "react";
import { showValue } from "@/lib/calc";
import { compareItem } from "@/lib/series";
import { useCompare } from "@/components/shell/ShellProvider";
import { cardFormat, cardValue, textUnits } from "./__pure__";
import { Box, Empty, type P } from "./common";
import { kpiDelta } from "./kpi_trend";

/** [SPECIFICATION] plans/dash-blocks.md#B2 Tile (2차)
 *  숫자 타일 카드의 몸통: 큰 숫자(와 작은 단위)·설명 글·전기 대비를 그리고, 값이 비면 0건 한 줄을 그린다. */
export function Tile(p: P): ReactNode {
  const { card, range, merge, prev, mode = "prev" } = p;
  const v = cardValue(card, 0, range, merge), fmt = cardFormat(card), unit = card.view?.unit_text ?? "";
  const num = showValue(v, fmt, "", card.view), said = useCompare(card.id), note = card.view?.note ?? "";
  const label = compareItem(card, range, prev, merge, mode)?.label ?? "";
  if (v == null) return <Box cls="kpi tile"><Empty /></Box>;
  return (
    <Box cls="kpi tile">
      <div className="kpi-text">
        <div className="kpi-val" style={{ "--n": textUnits(num + unit).toFixed(2) } as CSSProperties}>{num}{unit && <span className="kpi-unit">{unit}</span>}</div>
        {note && <div className="hint tile-note">{note}</div>}
        {said && <div className={"bcmp kpi-cmp " + said.tone} title={said.text}>{kpiDelta(said, label, fmt)}</div>}
      </div>
    </Box>
  );
}
