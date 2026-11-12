"use client";
// cohort(P14): 처음 온 기간(코호트)별로 경과 기간마다 남은 비율 표(data.values = {periods, cohorts:[{start, size, retained[0~1]}]}, 상한은 서버).
// 칸 바탕은 주 색을 비율만큼(최대 60%) 섞고 글자는 본문 색, 빈 칸은 "-". values.partial이면 각 코호트의 마지막 칸(진행 중인 기간)을 흐리게(안내 글은 data.note로 카드 아래에)
import { Box, NOTHING, Say, more, tone, type P } from "./common";

const OPEN_OPACITY = 0.45;  // 진행 중인 기간의 칸을 흐리게

export function Cohort(p: P) {
  const d = more(p), shown = d.cohorts ?? [];
  if (!shown.length) return <Say text={NOTHING} />;
  const n = Math.max(...shown.map(c => c.retained?.length ?? 0)), c = tone(p, "주 색", 0);
  return (
    <Box cls="table cohort">
      <table>
        <thead>
          <tr><th>코호트</th><th className="num">크기</th>{Array.from({ length: n }, (_, k) => <th key={k} className="num">{d?.periods?.[k] ?? `+${k}`}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map(row => (
            <tr key={row.start}>
              <td>{row.start}</td>
              <td className="num">{row.size.toLocaleString()}</td>
              {Array.from({ length: n }, (_, k) => {
                const r = row.retained?.[k], open = d.partial === true && k === row.retained.length - 1;  // 진행 중인 마지막 기간의 칸(며칠 치뿐이라 낮다)
                return r == null ? <td key={k} className="num">-</td>
                  : <td key={k} className="num" title={`${row.start} 코호트, ${d?.periods?.[k] ?? `+${k}`}: ${(r * 100).toFixed(1)}% (${Math.round(r * row.size).toLocaleString()}명)${open ? " · 진행 중인 기간" : ""}`}
                    style={{ background: `color-mix(in srgb, ${c} ${Math.round(Math.max(0, Math.min(1, r)) * 60)}%, transparent)`, opacity: open ? OPEN_OPACITY : undefined }}>{(r * 100).toFixed(1)}%</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Box>
  );
}
