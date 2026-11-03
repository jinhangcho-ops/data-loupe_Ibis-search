"use client";
// status(정상·주의·이상 배지와 수 + 데이터 신선도)와 alerts(나쁜 엔티티 목록: 이상 먼저, 오래된 순)(옛 paintStatus·paintAlerts)
import { Box, LEVEL_NAMES, Say, levelColor, type P } from "./common";

export type Entity = { name: string; kind?: string; level: string; since?: string; since_text?: string; reasons: string[]; impact: string[] };

export function Status({ card, colors }: P) {
  const v = card.data?.values as { counts?: Record<string, number>; fresh?: string } | undefined, counts = v?.counts ?? {};
  return (
    <Box cls="status">
      <div className="badges">{["ok", "warn", "bad"].map(l => (
        <span key={l} className="badge" style={{ borderColor: levelColor(l, colors), color: levelColor(l, colors) }}><b>{String(counts[l] ?? 0)}</b>{" " + LEVEL_NAMES[l]}</span>
      ))}</div>
      {v?.fresh && <div className="hint">{v.fresh}</div>}
      {card.data?.note && <div className="hint">{card.data.note}</div>}
    </Box>
  );
}

export function Alerts({ card, colors }: P) {
  const bad = ((card.data?.values?.entities ?? []) as Entity[]).filter(e => e.level !== "ok")
    .sort((a, b) => Number(b.level === "bad") - Number(a.level === "bad") || String(a.since).localeCompare(String(b.since)));
  if (!bad.length) return <Say cls="alerts" text="지금은 경고가 없어요" />;
  return (
    <Box cls="alerts">
      {bad.map(e => (
        <div key={e.name} className={"alert lv-" + e.level}>
          <b>{e.name}</b>
          <span className="badge" style={{ borderColor: levelColor(e.level, colors), color: levelColor(e.level, colors) }}>{LEVEL_NAMES[e.level]}</span>
          <span>{e.since_text}</span>
          <span className="hint">{e.reasons.join(", ")}</span>
          {e.impact.length ? <span className="hint">{"영향: " + e.impact.join(", ")}</span> : null}
        </div>
      ))}
    </Box>
  );
}
