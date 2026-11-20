"use client";
// boxplot(P14): 그룹마다 상자 그림(data.values.groups = [{name, n, min, q1, med, q3, max, outliers, outlier_count}]). 수염 min~max(1.5 IQR 끝), 상자 q1~q3, 굵은 중앙값, 이상치는 빈 원
import { shownum } from "@/lib/calc";
import { autoParams, clipBox } from "@/lib/autoviz";
import { useConstants } from "@/lib/constants";
import { tickBy } from "../chart/Charts";
import { useRootScale } from "../chart/scale";
import { fitText } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const W = 480, H = 260, L0 = 48, B0 = 22, T0 = 8, FONT = 11;  // 왼쪽·아래·위 글자 자리와 글자 크기(화면 크기 배율 1)
const BOX_RX = 6, WHISKER_W = 1.5, MEDIAN_W = 3, OUTLIER_W = 1.5, OUTLIER_R = 3, CAP_RATIO = 16 / 28;  // 상자 모서리 둥글기, 수염·이상치 선 굵기, 중앙값 선 굵기, 이상치 반지름, 수염 끝 가로선 길이 / 상자 폭(예시 37-boxplot: 16 / 28)
const fine =(v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function BoxPlot(p: P) {
  const consts = useConstants(), s = useRootScale(), L = L0 * s, B = B0 * s, T = T0 * s, font = FONT * s;  // 글자 자리는 글자와 같이 커진다
  // 사분위가 없는 그룹(행이 없음)은 빼고, 수염 끝이 비면 상자 끝으로
  const all = (more(p).groups ?? []).filter(b => fine(b.q1) && fine(b.med) && fine(b.q3)).map(b => ({ ...b, min: fine(b.min) ? b.min : b.q1, max: fine(b.max) ? b.max : b.q3, outliers: (b.outliers ?? []).filter(fine) }));
  if (!all.length) return <Say text={NOTHING} />;
  // 꼬리 자르기: 보이는 범위(P1~P99와 상자)만 그리고, 잘린 건수는 끝에 표시한다
  const tail = p.card.view.all_data === true || !consts?.dash?.auto ? null : autoParams(p.card.view.auto, consts.dash.auto).tail, box = tail ? clipBox(all) : null;
  const shown = box ? all.map(b => ({ ...b, min: Math.max(box.lo, b.min), max: Math.min(box.hi, b.max), outliers: b.outliers.filter(o => o >= box.lo && o <= box.hi) })) : all;
  const nums = shown.flatMap(b => [b.min, b.max, ...(b.outliers ?? [])]).filter(fine), lo = box ? box.lo : Math.min(...nums), hi = box ? box.hi : Math.max(...nums);
  const pct = (v: number) => +(v * 100).toFixed(2);
  const y = (v: number) => (hi === lo ? (T + H - B) / 2 : T + ((H - B - T) * (hi - v)) / (hi - lo)), band = (W - L) / shown.length, every = Math.ceil(shown.length / 6), w = Math.min(44, band * 0.5), c = tone(p, "주 색", 0);
  return (
    <Box cls="boxplot nm-well">
      <Frame w={W} h={H} label={p.card.title}>
        {[0, 0.5, 1].map(k => { const v = lo + (hi - lo) * k; return <g key={k}><line x1={L} x2={W} y1={y(v)} y2={y(v)} stroke="var(--line)" /><text x={L - 6} y={y(v)} fontSize={font} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{tickBy((hi - lo) / 2)(v)}</text></g>; })}
        {shown.map((b, i) => {
          const cx = L + band * (i + 0.5);
          return (
            <g key={i}>
              <title>{`${b.name}${b.n != null ? ` (${b.n.toLocaleString()}개)` : ""}: 수염 아래 ${shownum(b.min)} · Q1 ${shownum(b.q1)} · 중앙값 ${shownum(b.med)} · Q3 ${shownum(b.q3)} · 수염 위 ${shownum(b.max)}${b.outlier_count ? ` · 이상치 ${b.outlier_count.toLocaleString()}개` : ""}`}</title>
              <line x1={cx} x2={cx} y1={y(b.max)} y2={y(b.min)} stroke="var(--muted)" strokeWidth={WHISKER_W} />
              <line x1={cx - w * CAP_RATIO / 2} x2={cx + w * CAP_RATIO / 2} y1={y(b.max)} y2={y(b.max)} stroke="var(--muted)" strokeWidth={WHISKER_W} strokeLinecap="round" />
              <line x1={cx - w * CAP_RATIO / 2} x2={cx + w * CAP_RATIO / 2} y1={y(b.min)} y2={y(b.min)} stroke="var(--muted)" strokeWidth={WHISKER_W} strokeLinecap="round" />
              <rect x={cx - w / 2} y={y(b.q3)} width={w} height={Math.max(1, y(b.q1) - y(b.q3))} rx={BOX_RX} className="nm-raise" fill={c} />
              <line x1={cx - w / 2} x2={cx + w / 2} y1={y(b.med)} y2={y(b.med)} stroke={p.colors.ink} strokeWidth={MEDIAN_W} />
              {(b.outliers ?? []).map((o, j) => <circle key={j} cx={cx} cy={y(o)} r={OUTLIER_R} fill="none" stroke={c} strokeWidth={OUTLIER_W} />)}
              {tail && !!b.above && <text x={cx} y={y(hi) - 2} fontSize={font} textAnchor="middle" fill="var(--muted)">{fitText(`▲ P${pct(Number(tail.hi))} 초과 ${b.above.toLocaleString()}건`, band - 4, font)}</text>}
              {tail && !!b.below && <text x={cx} y={y(lo) + font} fontSize={font} textAnchor="middle" fill="var(--muted)">{fitText(`▼ P${pct(Number(tail.lo))} 미만 ${b.below.toLocaleString()}건`, band - 4, font)}</text>}
              {i % every === 0 && <text x={cx} y={H - 6 * s} fontSize={font} textAnchor="middle" fill="var(--muted)">{fitText(b.name, band * every - 4, font)}</text>}{/* 그룹이 많으면 몇 칸마다 하나(이름 전부는 툴팁) */}
            </g>
          );
        })}
      </Frame>
    </Box>
  );
}
