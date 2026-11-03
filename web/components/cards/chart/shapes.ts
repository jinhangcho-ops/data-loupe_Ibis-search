// 새 계열 차트(P14)의 순수 계산(화면 요소 없음): 겹친 도넛 링 반지름, 폭포 받침, 파레토 누적 %, 추세선. web/tests의 vitest가 확인한다.

/** 둥근 차트(겹친 고리를 켤 수 있다) */
export const ROUND_TYPES = ["doughnut", "pie", "radial", "polar"];
/** 첫 계열 하나로 그리는 차트(Charts.OneSeries) */
export const FIRST_ONLY = ["waterfall", "pareto", "trendline", "lollipop"];
/** 앞 두 계열로 그리는 차트(나비형: 왼쪽·오른쪽, 범위 막대: 아래·위) */
export const PAIR_ONLY = ["butterfly", "range"];

/** 링 n개가 inner~outer(%)를 고르게 나눠 갖는 반지름(안쪽 링이 먼저). gap은 링 사이 틈(%) */
export function ringRadii(n: number, inner: number, outer: number, gap = 2) {
  const w = n > 0 ? (outer - inner - gap * (n - 1)) / n : 0;
  return Array.from({ length: n }, (_, i) => ({ inner: inner + i * (w + gap), outer: inner + i * (w + gap) + w }));
}

/** 폭포: 값마다 받침(막대 아래 끝)·길이·오른 값인가·누적. 막대는 [base, base + size] 범위로 그린다(누적이 음수여도 같은 식) */
export function waterfallRows(values: (number | string | null)[]) {
  let sum = 0;
  return values.map(v => {
    const d = Number(v) || 0, from = sum;
    sum += d;
    return { base: Math.min(from, sum), size: Math.abs(d), up: d >= 0, end: sum };
  });
}

/** 파레토: 큰 값부터 줄 세우고 누적 %(0~100). 음수는 0으로(누적이 100을 넘지 않게), 합이 0이면 모두 0 */
export function paretoRows(labels: string[], values: (number | string | null)[]) {
  const rows = labels.map((label, i) => ({ label, v: Math.max(0, Number(values[i]) || 0) })).sort((a, b) => b.v - a.v);
  const total = rows.reduce((a, r) => a + r.v, 0);
  let run = 0;
  return rows.map(r => ({ ...r, pct: total ? ((run += r.v) / total) * 100 : 0 }));
}

/** 최소제곱 직선 y = a + b·i(i는 칸 순서). 빈 값은 빼고, 점이 2개 미만이면 null */
export function fitLine(values: (number | string | null)[]) {
  const pts = values.map((v, i) => [i, v == null || v === "" ? NaN : Number(v)]).filter(([, y]) => Number.isFinite(y));
  const n = pts.length;
  if (n < 2) return null;
  const mx = pts.reduce((a, p) => a + p[0], 0) / n, my = pts.reduce((a, p) => a + p[1], 0) / n;
  const sxx = pts.reduce((a, p) => a + (p[0] - mx) ** 2, 0), b = sxx ? pts.reduce((a, p) => a + (p[0] - mx) * (p[1] - my), 0) / sxx : 0;
  return { a: my - b * mx, b };
}
