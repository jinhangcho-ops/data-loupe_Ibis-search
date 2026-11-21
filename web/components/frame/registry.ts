// B10 틀 요소(plans/dash-blocks.md "B10 세부 명세"): 격자에 놓는 조작·틀 요소의 종류와 할 일을 등록하는 화면 쪽의 한 곳(DOM 없음).
// 요소는 카드와 같은 꼴로 저장한다(kind = 요소 종류, 설정은 view.frame). 서버의 한 벌은 core/dash_store.py의 FRAME_KINDS·FRAME_ACTIONS(web/tests/b10_frames.test.ts가 둘을 견준다).
import type { PickedFilter } from "@/lib/types";

/** 추가 메뉴의 묶음 차례. "보기"는 지금의 카드 종류(edit/menu/kinds.ts)이고 요소는 조작·틀에만 있다 */
export const FRAME_GROUPS = ["조작", "보기", "틀"] as const;
export type FrameGroup = (typeof FRAME_GROUPS)[number];

/** 요소 종류 → 이름, 묶음, 풀이, 처음 크기(칸). B2·B3의 요소도 여기에 한 줄씩 더한다 */
export const FRAME_KINDS = {
  button: { name: "단추", group: "조작", help: "누르면 고른 일을 해요", w: 2, h: 1 },
  period_pick: { name: "기간 고르기", group: "조작", help: "달력으로 기간을 골라요", w: 3, h: 1 },
  asof_field: { name: "기준일", group: "조작", help: "기준일을 정해요", w: 2, h: 1 },
  unit_seg: { name: "기간 단위", group: "조작", help: "차트를 묶는 단위를 바꿔요", w: 3, h: 1 },
  nav_rail: { name: "화면 이동 줄", group: "틀", help: "세로로 놓는 이동 단추 줄", w: 1, h: 8 },
  top_strip: { name: "위쪽 화면 줄", group: "틀", help: "이름·기간·기준일·화면 모드", w: 12, h: 1 },
  page_tabs: { name: "쪽 탭", group: "틀", help: "쪽을 오가는 가로 탭", w: 12, h: 1 },
} as const satisfies Record<string, { name: string; group: FrameGroup; help: string; w: number; h: number }>;
export type FrameKind = keyof typeof FRAME_KINDS;

/** 할 일 → 이름, 받는 값(arg)의 종류("" 없음, page 쪽 이름, card 카드 id). 지금 앱에 이미 있는 일만 */
export const FRAME_ACTIONS = {
  export: { name: "내보내기", arg: "" },
  refresh: { name: "최신화", arg: "" },
  page: { name: "쪽 이동", arg: "page" },
  theme: { name: "화면 모드 바꾸기", arg: "" },
  dash_only: { name: "대시보드만 보기", arg: "" },
  zoom: { name: "카드 크게 보기", arg: "card" },
  easy: { name: "쉬운 보기 켜고 끄기", arg: "" },
  filters_clear: { name: "필터 모두 풀기", arg: "" },
  settings: { name: "대시보드 설정 창 열기", arg: "" },
} as const satisfies Record<string, { name: string; arg: "" | "page" | "card" }>;
export type FrameAction = keyof typeof FRAME_ACTIONS;

/** 위쪽 화면 줄에 보일 칸(서버 FRAME_PARTS와 같은 차례) */
export const FRAME_PARTS = { name: "이름", period: "기간", asof: "기준일", theme: "화면 모드" } as const;
export type FramePart = keyof typeof FRAME_PARTS;

/** 단추 하나(단추 요소 자신, 줄 안 항목): 글자·아이콘(둘 중 하나는 꼭)·할 일·받는 값 */
export type FrameItem = { label: string; icon: string; action: FrameAction | ""; arg: string };
/** view.frame: 종류마다 쓰는 항목만 있다(단추는 FrameItem + ambient, 이동 줄은 items + ambient, 위쪽 줄은 parts + items, 나머지는 빈 객체) */
export type FrameSettings = Partial<FrameItem> & { ambient?: boolean; items?: FrameItem[]; parts?: FramePart[] };

/** 할 일이 부르는 앱의 기능. ShellProvider의 값과 TopBar의 flipTheme·flipEasy로 채운다(FrameCard) */
export type ActionHost = {
  recompute: () => unknown; setPage: (page: string) => void; dashOnly: boolean; setDashOnly: (on: boolean) => void;
  setReport: (open: boolean) => void; setZoom: (id: string | null) => void; flipTheme: () => void; pages: string[]; cardIds: string[];
  flipEasy: () => void; clearFilters: () => unknown; setSettings: (open: boolean) => void;
};

/** [SPECIFICATION] plans/dash-blocks.md#isFrame
 *  kind가 틀 요소 종류(FRAME_KINDS의 키)인지. */
export function isFrame(kind: string): kind is FrameKind {
  return Object.hasOwn(FRAME_KINDS, kind);
}

/** [SPECIFICATION] plans/dash-blocks.md#freshFrame
 *  그 종류의 요소를 막 놓았을 때의 설정(view.frame). */
export function freshFrame(kind: FrameKind): FrameSettings {
  if (kind === "button") return { label: "", icon: "", action: "", arg: "", ambient: false };
  if (kind === "nav_rail") return { items: [], ambient: false };
  if (kind === "top_strip") return { parts: Object.keys(FRAME_PARTS) as FramePart[], items: [] };
  return {};
}

/** [SPECIFICATION] plans/dash-blocks.md#frameError
 *  요소 설정이 저장될 수 없는 이유 한 문장(없으면 ""). */
export function frameError(kind: FrameKind, frame: FrameSettings): string {
  const items: Partial<FrameItem>[] = kind === "button" ? [frame] : kind === "nav_rail" || kind === "top_strip" ? (frame.items ?? []) : [];
  for (const { label, icon, action, arg } of items) {
    if ((label ?? "").trim() === "" && (icon ?? "") === "") return "단추에는 글자나 아이콘 중 하나가 있어야 해요.";
    const need = action ? FRAME_ACTIONS[action].arg : "";
    if (need === "page" && (arg ?? "") === "") return "이동할 쪽을 고르세요.";
    if (need === "card" && (arg ?? "") === "") return "크게 볼 카드를 고르세요.";
  }
  return "";
}

/** [SPECIFICATION] plans/dash-blocks.md#findSpecs
 *  추가 메뉴의 찾기: 이름이나 풀이에 글자가 든 항목만(대소문자 무시, 차례 그대로). */
export function findSpecs<T extends { name: string; help: string }>(specs: T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  if (q === "") return specs;
  return specs.filter(s => s.name.toLowerCase().includes(q) || s.help.toLowerCase().includes(q));
}

/** [SPECIFICATION] plans/dash-blocks.md#runAction
 *  단추 하나의 할 일을 host의 기능으로 한다. 할 수 없으면 던진다. */
export function runAction(item: Pick<FrameItem, "action" | "arg">, host: ActionHost): void {
  const { action, arg } = item;
  if (action === "") throw new Error("이 단추가 할 일을 아직 정하지 않았어요.");
  if (!Object.hasOwn(FRAME_ACTIONS, action)) throw new Error(`모르는 할 일이에요: ${action}`);
  if (action === "page" && !host.pages.includes(arg)) throw new Error(`'${arg}' 쪽이 없어요.`);
  if (action === "zoom" && !host.cardIds.includes(arg)) throw new Error("크게 볼 카드가 없어요.");
  if (action === "export") host.setReport(true);
  else if (action === "refresh") host.recompute();
  else if (action === "theme") host.flipTheme();
  else if (action === "dash_only") host.setDashOnly(!host.dashOnly);
  else if (action === "easy") host.flipEasy();
  else if (action === "filters_clear") host.clearFilters();
  else if (action === "settings") host.setSettings(true);
  else if (action === "page") host.setPage(arg);
  else host.setZoom(arg);
}

/** [SPECIFICATION] plans/dash-blocks.md#filterNote
 *  보고서 그림에 적는 한 줄: 걸린 기간·기준일·필터(없으면 ""). */
export function filterNote(period: string, asOf: string, picks: PickedFilter[]): string {
  const parts: string[] = [];
  const p = period.trim();
  if (p !== "") parts.push(`기간 ${p}`);
  const a = asOf.trim();
  if (a !== "") parts.push(`기준일 ${a}`);
  for (const pick of picks) {
    if (pick.in.length > 0) parts.push(`${pick.column}: ${pick.in.join(", ")}`);
  }
  return parts.join(" · ");
}
