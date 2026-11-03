"use client";
// summary: 요약 문장(서버의 data.text) + 목표 대비 막대(옛 paintSummary). 값이 없으면 막대·"목표 대비 0%"를 그리지 않는다
import { clamp, darken, overTarget, showValue } from "@/lib/calc";
import { cardFormat, cardTarget, cardValue } from "./__pure__";
import { Box, type P } from "./common";

export function Summary({ card, colors, range, merge }: P) {
  const d = card.data ?? {}, v = cardValue(card, 0, range, merge), target = cardTarget(card), fmt = cardFormat(card), ratio = target && v != null ? v / target : null;
  const err = (d.target as { error?: string } | undefined)?.error;
  return (
    <Box cls="summary">
      <div className="sum-text">{d.text || "—"}</div>
      {err && <div className="err">{err}</div>}
      {ratio != null ? (
        <div className="sum-bar" title={`${showValue(v, fmt)} / ${showValue(target, fmt)}`}>
          <i style={{ width: `${clamp(ratio, 0, 1) * 100}%`, background: overTarget(v!, target!) ? darken(colors.series[0]) : colors.series[0] }} />
          <span className="hint">{`목표 대비 ${+(ratio * 100).toFixed(1)}%`}</span>
        </div>
      ) : target && v == null ? <span className="hint">목표 대비 —</span> : null}
    </Box>
  );
}
