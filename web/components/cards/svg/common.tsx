"use client";
// SVG/HTML 카드가 함께 쓰는 것: 화면 모드 색(useTheme), 상자·그림 틀·단추 아이콘 모양, 상태 이름·색, 연결돼 있을 때만 앞 N행 읽기(useRows).
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { cardRequest, filteredBody, getLiveMin, query } from "@/lib/api";
import type { Card, CompareMode, Ctx, MoreValues } from "@/lib/types";
import { pickColor, sameSource } from "@/lib/series";
import { shades } from "@/lib/calc";
import type { Theme } from "./__pure__";
import { markColors } from "../ChartCard";
import { rem } from "../chart/scale";

/** 종류별 카드 컴포넌트가 받는 것: card.data는 이미 그릴 값(구한 값 또는 저장된 값), range는 보는 기간(ctx.range), merge는 상수 merge */
export type P = { card: Card; colors: Theme; range: Ctx["range"]; merge: string[]; filters?: Ctx["filters"]; prev?: Ctx["prev"]; mode?: CompareMode };  // filters: 보는 사람이 고른 필터(행을 직접 읽는 카드만 쓴다); prev·mode: 직전 기간·비교 기준(KPI 추이 나누기)

const LIGHT: Theme = { card: "#f1f3f7", head: "#e6e9ef", ink: "#1c2230", text: "#586174", line: "#d5dae3", err: "#b53124", ok: "#1c7448",
  series: shades("#6250d6"), hi: "#00968f" };  // common.css 밝게(그리기 전·prerender)
const themeKey = () => document.documentElement.dataset.theme ?? "";
const watchTheme = (fn: () => void) => { const mo = new MutationObserver(fn); mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] }); return () => mo.disconnect(); };
const cache = new Map<string, Theme>();
// 지금 화면 모드의 색(CSS 변수, 옛 board.js themeColors). data-theme가 바뀌면 다시 읽는다.
export function useTheme(): Theme {
  const key = useSyncExternalStore(watchTheme, themeKey, () => null);
  if (key == null) return LIGHT;
  if (!cache.has(key)) {
    const css = getComputedStyle(document.documentElement), c = (n: string) => css.getPropertyValue(n).trim();
    cache.set(key, { card: c("--card"), head: c("--bg"), ink: c("--text"), text: c("--muted"), line: c("--line"), err: c("--err"), ok: c("--ok"), series: shades(c("--mark")), hi: c("--hi") });
  }
  return cache.get(key)!;
}

/** 새 카드(P14) 색: 카드 지정 색 → 표시색의 진하기 단계(대시보드가 고른 표시색 → 화면 모드의 var(--mark)) */
export const tone = (p: P, name: string, i: number) => pickColor(p.card, "series", name, markColors(p.colors.palette), i);
/** 새 카드(P14)의 data.values(서버 dash_calc 모양) */
export const more = (p: P) => (p.card.data?.values ?? {}) as MoreValues;
/** 값 모양은 있는데 비었을 때(새 카드) */
export const NOTHING = "그릴 값이 없어요. 기간이나 조건을 바꿔 다시 구해 보세요.";

export const LEVEL_NAMES: Record<string, string> = { ok: "정상", warn: "주의", bad: "이상" };
export const levelColor = (level: string, colors: Theme) => level === "bad" ? colors.err : level === "warn" ? "#f08c00" : colors.ok;

export const Box = ({ cls, children }: { cls: string; children?: React.ReactNode }) => <div className={"cbox " + cls}>{children}</div>;
/** 안내 한 줄 상자(옛 box("summary", sum-text)) */
export const Say = ({ text, cls = "summary" }: { text: string; cls?: string }) => <Box cls={cls}><div className={cls === "summary" ? "sum-text" : "hint"}>{text}</div></Box>;
/** viewBox에 맞춰 늘어나는 SVG. 화면 읽기 프로그램에는 카드 이름으로. max(px, 배율 1)를 주면 그 너비까지만 커지고 가운데에 놓인다(카드마다 그림 크기가 같게) */
export const Frame = ({ w, h, label, max, children }: { w: number; h: number; label: string; max?: number; children?: React.ReactNode }) => (
  <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" className="w-full h-full" role="img" aria-label={label}
    style={max ? { maxWidth: rem(max), maxHeight: rem((max * h) / w), margin: "auto" } : undefined}>{children}</svg>
);

export const ICON_PATHS: Record<string, string> = { chart: "M3 20h18M6 16v-5M11 16V7M16 16v-8", up: "M4 16l6-6 4 4 6-8M15 6h5v5", user: "M12 12a4 4 0 100-8 4 4 0 000 8zM5 20c0-4 3-6 7-6s7 2 7 6", cart: "M3 4h3l2 11h10l2-8H7M10 20h.01M17 20h.01", coin: "M12 3a9 9 0 100 18 9 9 0 000-18zM9 12h6M12 8v8", clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2" };

type Rows = { hint?: string; error?: string; columns?: string[]; rows?: unknown[][] };
// 행 값 읽기: 연결돼 있을 때만 /api/query로 앞 limit행(저장하지 않는다). 연결 열쇠는 /api/dash/live_min의 source.key(옛 dash_ctl.loadMin).
// filters(보는 사람이 고른 필터)가 있으면 서버가 걸 수 있는 것만 조건으로 더한 조회(/api/dash/card/filtered)로 읽는다.
export function useRows(card: Card, limit: number, on = true, filters?: Ctx["filters"]): Rows {
  const [got, setGot] = useState<Rows>({ hint: "불러오는 중…" });
  const body = JSON.stringify(card.source?.body ?? {}), key = card.source?.key, picked = JSON.stringify(filters ?? []);
  const latest = useRef(card);
  useEffect(() => { latest.current = card; });
  useEffect(() => {
    if (!on) return;
    let live = true;
    (async () => {
      const now = (await getLiveMin()).source?.key ?? "";
      if (!now || (key && !sameSource(key, now))) return { hint: now ? "다른 연결에서 만든 카드라 지금은 볼 수 없어요" : "데이터에 연결하면 보여요" };
      const f = JSON.parse(picked) as NonNullable<Ctx["filters"]>;
      const cut = f.length ? (await filteredBody(cardRequest(latest.current), { filters: f })).body : JSON.parse(body);
      const d = await query({ ...cut, limit, count: false });
      return { columns: d.rows.columns, rows: d.rows.rows.map(r => d.rows.columns.map(c => r[c])) };
    })().then(r => { if (live) setGot(r); }, e => { if (live) setGot({ error: (e as Error).message }); });
    return () => { live = false; };
  }, [body, key, limit, on, picked]);
  return got;
}
