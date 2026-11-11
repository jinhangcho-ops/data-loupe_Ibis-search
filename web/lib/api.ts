// Flask API 클라이언트: 대시보드가 쓰는 API 전부를 타입 있는 함수로. 요청 body는 옛 화면(static/dashboard/*.js, static/core/dash*.js)이 보내던 것 그대로.
// 같은 출처(/api/...)라 열쇠는 Flask가 첫 페이지에서 둔 쿠키(key)로 간다. 경로를 고치면 tests_pytest/test_cr_n0.py가 Flask 라우트와 대조한다.
import type {
  Card, ChartPoints, Columns, CompareItem, CompareMode, ComputeResult, Constants, Ctx, DashList, DashSettings, Dashboard, DayParse, EveryParse,
  LiveMin, PeriodParse, QueryRows, RefreshStatus, Source, Targets, Template,
} from "./types";

/** 서버가 돌려준 오류(응답의 error 문장). driver는 드라이버가 없어 못 붙었을 때의 안내 */
export class ApiError extends Error {
  driver?: unknown;
}

/** 처리 용량 확인 팝업(옛 common.js confirmScan, X-Needs-Confirm): 내용과 [취소] [그대로 실행](차단이면 이유와 [닫기]만). 확인하면 true.
 *  요청 도중(React 밖)에 부르므로 DOM으로 직접 띄운다. 창(WebView2)에서는 window.confirm이 안 뜰 수 있어 쓰지 않는다 */
function confirmScan(d: { text: string; blocked?: boolean }): Promise<boolean> {
  return new Promise(done => {
    const make = (tag: string, cls: string, text = "") => Object.assign(document.createElement(tag), { className: cls, textContent: text });
    const box = make("div", "modal"), inner = make("div", "modal-box"), note = make("p", "", d.text), row = make("div", "row");
    const pick = (ok: boolean) => () => { box.remove(); done(ok); };
    note.style.cssText = "white-space:pre-wrap; margin:10px 0 14px";
    row.style.justifyContent = "flex-end";
    const buttons = d.blocked ? [make("button", "btn", "닫기")] : [make("button", "ghost", "취소"), make("button", "btn p", "그대로 실행")];
    buttons.forEach((b, i) => { b.onclick = pick(!d.blocked && i === 1); });
    box.onclick = e => { if (e.target === box) pick(false)(); };
    box.onkeydown = e => { if (e.key === "Escape") { e.preventDefault(); pick(false)(); } };
    inner.setAttribute("role", "dialog");
    inner.setAttribute("aria-modal", "true");
    row.append(...buttons);
    inner.append(make("h3", "", d.blocked ? "실행하지 않았어요" : "읽을 데이터가 많아요"), note, row);
    box.append(inner);
    document.body.append(box);
    buttons[buttons.length - 1].focus();
  });
}

// common.js send()와 같다: body가 없으면 GET, 있으면 JSON POST. 실패면 서버 문장으로 던지고, 용량 확인을 물으면 확인 뒤 confirmed를 붙여 다시 보낸다.
async function send(url: string, body?: unknown, method?: "DELETE"): Promise<Response> {
  const res = await fetch(url, method ? { method } : body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    const d = await res.json().catch(() => ({ error: `요청이 실패했어요(${res.status}).` }));
    const err = new ApiError(d.error);
    err.driver = d.driver;
    throw err;
  }
  if (res.headers.get("X-Needs-Confirm")) {
    const d = await res.json();
    if (!(await confirmScan(d))) throw new ApiError(d.blocked ? d.text : "취소했어요.");  // 차단이면 이유 팝업([닫기]만) 뒤 그 이유로
    return send(url, { ...(body as object), confirmed: true });
  }
  return res;
}
const api = async <T>(url: string, body?: unknown): Promise<T> => (await send(url, body)).json();
const q = encodeURIComponent;

// ---------- 대시보드 ----------
export const constants = () => api<Constants>("/api/dash/constants");
export const listDashboards = () => api<DashList>("/api/dash");
export const getDashboard = (id: string) => api<{ dashboard: Dashboard }>("/api/dash/open", { id });
/** 저장(id가 없으면 새 대시보드). 돌려주는 dashboard에는 카드 값(data)이 빠져 있다 */
// 카드에 data가 없으면 서버가 저장된 값을 둔다
export const saveDashboard = (d: Omit<Partial<Dashboard>, "cards"> & { name: string; cards: Partial<Card>[] }) => api<{ dashboard: Dashboard } & DashList>("/api/dash", d);
export const deleteDashboard = (id: string) => api<DashList>("/api/dash/delete", { id });
/** 저장한 대시보드(dashboard: id)에 카드 하나. id가 비면 name으로 새 대시보드 */
export const addCard = (dashboard: string, card: Partial<Card>, name?: string) =>
  api<{ dashboard: { id: string; name: string; cards: number } } & DashList>("/api/dash/card/add", { dashboard, name, card });
export const saveSettings = (id: string, fields: DashSettings) => api<{ dashboard: Dashboard }>("/api/dash/settings", { id, ...fields });
export const setLast = (id: string) => api<{ ok: true }>("/api/dash/last", { id });
/** 배치도 바탕 그림 주소(<img src>). 서버가 저장된 카드의 경로만 쓴다 */
export const imageUrl = (dash: string, card: string) => `/api/dash/image?dash=${q(dash)}&card=${q(card)}`;

// ---------- 카드 값 ----------
/** 카드 하나를 구한다(저장하지 않음). 옛 화면은 card.data를 {since}만 남겨 보낸다(cardRequest) */
export const computeCard = (card: Card, ctx?: Ctx | null) => api<{ data: Card["data"]; source: Source }>("/api/dash/card/compute", { card, ctx: ctx ?? undefined });
/** 카드 여러 개(실시간·기간 바꾸기). 카드마다 {id, data} 또는 {id, error} */
export const computeCards = (cards: Card[], ctx?: Ctx | null) => api<{ results: ComputeResult[]; seconds: number }>("/api/dash/cards/compute", { cards, ctx: ctx ?? undefined });
/** 옛 dash_ctl.cardRequest: 구할 때는 저장된 값 대신 since만 보낸다 */
export const cardRequest = (c: Card): Card => ({ ...c, data: c.data?.since ? { since: c.data.since } : {} });
/** 산점도·버블의 점. body는 카드의 source.body(옛 dash.js: {...body, y, size, source_key, date, ctx}) */
export const chartPoints = (body: Record<string, unknown> & { y?: string; size?: string; source_key?: string; date?: string; ctx?: Ctx | null }) =>
  api<{ points: ChartPoints }>("/api/chart/points", body);
export const compare = (items: CompareItem[]) => api<{ results: unknown[] }>("/api/compare", { items });

// ---------- 기간·주기 ----------
/** dashboard: true면 시각이 든 기간을 막는다(대시보드 기간 칸). compare_mode로 견줄 기간(previous·context.prev)을 정한다(비면 직전 기간) */
export const parsePeriod = (text: string, as_of?: string, dashboard = true, compare_mode?: CompareMode) => api<PeriodParse>("/api/period/parse", { text, as_of, dashboard, compare_mode });
export const parseDay = (text: string) => api<DayParse>("/api/period/day", { text });
export const parseEvery = (text: string, kind: "refresh" | "live") => api<EveryParse>("/api/period/every", { text, kind });
export const getLiveMin = () => api<LiveMin>("/api/dash/live_min");
export const setLiveMin = (seconds: number) => api<LiveMin>("/api/dash/live_min", { seconds });

// ---------- 최신화 ----------
export const refreshStatus = () => api<RefreshStatus>("/api/dash/refresh/status");
export const stopRefresh = () => api<RefreshStatus>("/api/dash/refresh/stop", {});

// ---------- 템플릿·보고서 ----------
export const listTemplates = () => api<{ templates: Template[] }>("/api/dash/templates");
export const saveTemplate = (t: { name: string; cards: Template["cards"] }) => api<{ templates: Template[] }>("/api/dash/templates", t);
export const deleteTemplate = (id: string) => api<{ templates: Template[] }>("/api/dash/templates/delete", { id });
/** 보고서 JPEG(data URL) → 1쪽 PDF. 창이 없으면 window:false와 pdf(data URL)를 돌려줘 화면이 내려받는다 */
export const savePdf = (image: string, name: string) =>
  api<{ path?: string; cancelled?: boolean; window?: false; name?: string; pdf?: string }>("/api/dash/pdf", { image, name });
/** 그림 여러 개(PNG·JPG data URL)를 고른 폴더에. 창이 없으면 window:false */
export const saveImages = (images: { name: string; data: string }[]) => api<{ window?: false; [more: string]: unknown }>("/api/images", { images });

// ---------- 화면 설정·초안 ----------
/** 앱 설정 몇 항목(theme·board_ratio·image·board_format …)을 바꾼다(/api/settings, 쿠키로 돌아온다) */
export const saveSetting = (changes: Record<string, string>) => api<Record<string, unknown>>("/api/settings", changes);
const DRAFT = "/api/draft?screen=dashboard";
/** 저장하지 못한 편집의 초안(draft.js): 읽기·쓰기·지우기. same이 false면 다른 연결에서 하던 것 */
export const draft = {
  get: <S>() => api<{ draft?: { saved: string; connection?: { name?: string }; state: S }; same?: boolean }>(DRAFT),
  put: (state: unknown) => api<{ saved: boolean }>(DRAFT, { state }),
  drop: () => send(DRAFT, undefined, "DELETE").then(() => undefined),
};

// ---------- 차트 낱말·쿼리 화면 자동 규칙 기본값(viz.json) ----------
/** 설정 폴더의 차트 설정을 읽는다(없으면 서버 기본 낱말) */
export const viz = () => api<Constants["dash"]["viz"]>("/api/viz");
/** 저장하고 검사한 사본을 돌려받는다(틀리면 서버 문장으로 던진다) */
export const saveViz = (prefs: Constants["dash"]["viz"]) => api<Constants["dash"]["viz"]>("/api/viz", prefs);

// ---------- 목표 파일 ----------
export const getTargets = (offset = 0) => api<Targets>(`/api/targets?offset=${offset}`);
export const setTargets = (path: string) => api<Targets>("/api/targets", { path });

// ---------- 테이블·컬럼·조회·지표(카드 추가 메뉴·선반·최근 행) ----------
export const tables = (schema = "", virtual = false) => api<{ tables: string[]; virtual?: string[] }>("/api/tables?schema=" + q(schema) + (virtual ? "&virtual=1" : ""));
export const columns = (base: string, schema = "") => api<Columns>("/api/columns", { schema, base });
export const hashed = (base: string, schema = "") => api<{ on: boolean; columns: string[] }>("/api/diag/hashed", { schema, base });
/** 테이블의 태그 켜기 정보(Phase 4 /api/labels): 켜졌으면 키·글 컬럼·사전 */
export const labels = (schema: string, base: string) => api<{ on: boolean; key?: string; key_type?: string; text?: string; vocab?: string[]; labeled?: number; quick: number }>(`/api/labels?schema=${encodeURIComponent(schema)}&base=${encodeURIComponent(base)}`);
export const values = (body: Record<string, unknown>, column: string) => api<{ values: string[]; more: boolean; partial: boolean | null }>("/api/values", { ...body, column });
/** 앞 N행(recent_rows·table). 옛 화면은 {...source.body, limit, count:false} */
export const query = (body: Record<string, unknown>) => api<QueryRows>("/api/query", body);
/** 내 지표(metrics: 테이블에 묶은 정의, text = "이름 · 테이블 (연결)")와 지표 파일의 공통 정의(legacy: 이름으로 KPI 카드의 body.names에 쓴다) */
export const myMetrics = () => api<{ metrics: (Record<string, unknown> & { id: string; name: string; text: string; scope?: { table?: string; flow_id?: string } })[];
  templates: unknown; legacy: { id: string; name: string; text: string; description: string; format: string }[]; source: Source; better: string[] }>("/api/my_metrics");

// ---------- 지표 사전 ----------
export type CatalogEntry = { id: string; area: string; name: string; abbr: string; help: string; formula: string; cross: boolean };
export type Availability = Record<string, { usable: boolean; reason: string; suggest: Record<string, string>; note?: string }>;
/** 지표 사전 전체(영역·항목). 크니 화면이 한 번만 받는다 */
export const catalog = () => api<{ areas: { id: string; name: string }[]; entries: CatalogEntry[] }>("/api/catalog");
/** 항목마다 이 테이블(columns = {컬럼: 종류 num·text·date…})에서 쓸 수 있는지와 빈칸 추천 컬럼 */
export const catalogAvailable = (columns: Record<string, string>) => api<{ availability: Availability }>("/api/catalog/available", { columns });
/** 항목을 테이블(scope)에 묶은 내 지표로 저장한다 */
export const catalogSave = (id: string, slots: Record<string, string>, table: string, columns: Record<string, string>) =>
  api<{ metric: { id: string; name: string; text: string } }>("/api/catalog/save", { id, slots, scope: { table, schema: "" }, columns });
