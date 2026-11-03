"use client";
// wordcloud(P14): 자주 나온 글자(data.values.words = [{w, n}], 상한은 서버)를 많이 나온 것부터 크게(12~48px), 가운데로 흘려 놓는다(겹치지 않게 줄바꿈 배치)
import { Box, NOTHING, Say, more, tone, type P } from "./common";

export function WordCloud(p: P) {
  const shown = (more(p).words ?? []).filter(x => x.w && x.n > 0).sort((a, b) => b.n - a.n);
  if (!shown.length) return <Say text={NOTHING} />;
  const lo = shown[shown.length - 1].n, hi = shown[0].n, size = (n: number) => (hi === lo ? 24 : 12 + (36 * (n - lo)) / (hi - lo));
  return (
    <Box cls="wordcloud">
      <div role="list" aria-label={p.card.title} style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", alignItems: "center", alignContent: "center", gap: "2px 10px", flex: 1, overflow: "hidden" }}>
        {shown.map((x, i) => <span key={x.w} role="listitem" title={`${x.w}: ${x.n.toLocaleString()}번`} style={{ fontSize: size(x.n), lineHeight: 1.1, fontWeight: 600, color: tone(p, x.w, i) }}>{x.w}</span>)}
      </div>
    </Box>
  );
}
