"use client";
// N6 — 자동 저장·초안(옛 dashboard.html save·scheduleSave·saveIfDirty, draft.js Draft, Ctrl+S, 최신화 뒤 다시 열기).
// 카드·이름·설정이 바뀌면(state.dirty) 0.7초 조용할 때 저장한다(입력 중에는 계속 미뤄진다). 실시간 값(state.values)은 저장하지 않는다(카드 data는 저장된 값).
// 저장이 2초 넘게 안 되면(실패 등) 초안(/api/draft?screen=dashboard)을 남기고, 다음에 화면을 열면 "하던 작업을 이어서 할까요?"를 묻는다.
// ShellProvider가 한 번 부른다(open = 화면 틀의 열기: 저장값을 보이고 기간 규칙대로만 다시 구한다). 저장·단축키·초안은 모듈 공용 한 벌. flush()는 다른 대시보드로 옮기기 전에 부른다(실패면 던진다).
import { useEffect } from "react";
import { draft as drafts, getDashboard, saveDashboard } from "@/lib/api";
import { useDash, type Action, type DashState } from "@/lib/store";
import type { Card, Dashboard } from "@/lib/types";

export type Autosave = { flush: () => Promise<void>; saving: boolean };

const QUIET = 700;          // 마지막 편집 뒤 저장까지(옛 scheduleSave)
const DRAFT_QUIET = 2000;   // 저장되지 않은 편집이 이만큼 남으면 초안으로(옛 DRAFT_QUIET)
type DraftState = { id: string; palette: string[]; north_star: Dashboard["north_star"]; cards: Omit<Card, "data">[] };

// 모듈 공용: 지금 상태·dispatch(훅이 렌더마다 맞춘다), 저장 줄(차례로), 타이머, 초안이 서버에 있을 수 있나, 초안을 물었나
const cur: { state: DashState | null; dispatch: (a: Action) => void; open: (id: string) => Promise<void> } = { state: null, dispatch: () => {}, open: async () => {} };
let chain: Promise<void> = Promise.resolve(), timer = 0, draftTimer = 0, draftHas = false, asked = false;
const seenEvents = new WeakSet<Event>();  // 훅을 여러 번 불러도 최신화 이벤트는 한 번만
const onServer = new Map<string, unknown>();  // 카드 id → 서버에 저장돼 있는 값(data) 그대로의 참조. 같으면 저장 요청에서 data를 뺀다(서버가 저장된 값을 둔다)

/** 글자를 쓰는 칸인가(옛 draft.js isTyping). 단축키·다시 열기를 미루는 데 쓴다 */
export const isTyping = (t: EventTarget | null) => {
  const e = t as HTMLElement | null;
  return !!e && (e.isContentEditable || e.tagName === "TEXTAREA" || (e.tagName === "INPUT" && /^(|text|search|number|email|url|tel|password|date|datetime-local|month|week|time)$/.test((e as HTMLInputElement).type)));
};
/** 팝업·펼친 목록이 떠 있나(그때는 단축키를 무시한다) */
export const modalOpen = () => !!document.querySelector(".modal:not(.hidden), .menu:not(.hidden)");

// 초안에 담는 모양(옛 dashState와 같게: 카드 값 data는 뺀다)
export const draftState = (d: Dashboard): DraftState => ({ id: d.id, palette: d.palette, north_star: d.north_star, cards: d.cards.map(c => { const { data, ...rest } = c; void data; return rest; }) });

/** 서버가 이 카드들의 지금 값(data)을 갖고 있다고 적는다(연 직후·저장한 뒤) */
export const markSaved = (d: Dashboard) => d.cards.forEach(c => onServer.set(c.id, c.data));
/** 저장 요청 모양: 서버에 있는 그대로의 카드 값(data, 수 MB일 수 있다)은 뺀다 */
export const slimDash = (d: Dashboard) => ({ ...d, cards: d.cards.map(c => { if (onServer.get(c.id) !== c.data) return c; const { data, ...rest } = c; void data; return rest; }) });

async function saveNow() {
  clearTimeout(timer);
  const s = cur.state, dash = s?.dash;
  if (!s || !dash || !s.dirty) return;
  try {
    const { dashboard, ...list } = await saveDashboard(slimDash(dash));
    markSaved(dash);
    cur.dispatch({ type: "list", list });  // 목록의 카드 수·이름도 맞춘다
    if (cur.state?.dash !== dash) return;  // 요청 중에 또 고쳤으면 dirty를 그대로 두어 다음 저장이 그것을 쓴다
    cur.dispatch({ type: "saved", dash: dashboard });
    if (draftHas) { draftHas = false; drafts.drop().catch(() => {}); }
  } catch (e) {
    cur.dispatch({ type: "error", message: "저장하지 못했어요. " + (e as Error).message });
    throw e;
  }
}
/** 저장 줄 끝에 저장을 하나 더(앞 저장이 실패해도 이번 것은 한다). 실패하면 던진다 */
export const flush = () => (chain = chain.then(saveNow, saveNow));

// 초안 팝업(옛 Draft.popup): 이어서 하기 / 버리기 / 닫기. 화면 밖 DOM에 잠깐 붙인다(훅은 그릴 자리가 없다)
function askDraft() {
  drafts.get<DraftState>().then(d => {
    const draft = d.draft;
    if (!draft) return;
    draftHas = true;
    const at = new Date(draft.saved), conn = draft.connection?.name, same = d.same !== false;
    const make = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = "") => Object.assign(document.createElement(tag), { className: cls, textContent: text });
    const box = make("div", "modal"), inner = make("div", "modal-box"), row = make("div", "row"), note = make("p");
    row.style.justifyContent = "flex-end";
    note.style.cssText = "white-space:pre-wrap; margin:10px 0 14px";
    note.textContent = `${at.getMonth() + 1}/${at.getDate()} ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}에 하던 작업이 남아 있어요.${conn ? `\n연결: ${conn}` : ""}`
      + (same ? "" : "\n\n지금과 다른 연결에서 하던 작업이에요. 그 연결로 바꾸면 이어서 할 수 있어요.");
    const end = () => box.remove();
    const shut = make("button", "btn", "닫기"), drop = make("button", "ghost", "버리기"), go = make("button", "btn p", "이어서 하기");
    shut.onclick = end;
    drop.onclick = () => { draftHas = false; drafts.drop().catch(() => {}); end(); };
    go.onclick = async () => {
      try {
        if (!cur.state?.list.some(x => x.id === draft.state.id)) throw new Error("그 대시보드가 지금은 없어요.");
        let cards = cur.state.dash?.cards ?? [];
        if (cur.state.dash?.id !== draft.state.id) {  // 연 직후의 cur.state는 다음 렌더 전이라 옛 대시보드다: 저장된 카드 값은 서버에서 읽는다
          await cur.open(draft.state.id);
          cards = (await getDashboard(draft.state.id)).dashboard.cards;
        }
        const data = Object.fromEntries(cards.map(c => [c.id, c.data]));
        cur.dispatch({ type: "cards", cards: draft.state.cards.map(c => ({ ...c, data: data[c.id] ?? {} }) as Card) });  // dirty가 켜져 자동 저장이 이어서 쓴다
        cur.dispatch({ type: "meta", patch: { palette: draft.state.palette, north_star: draft.state.north_star } });
        end();
      } catch (e) { note.textContent = (e as Error).message; go.replaceWith(shut); shut.focus(); }  // 대시보드가 없어졌다 같은 이유를 그대로 보인다
    };
    row.append(drop, same ? go : shut);
    inner.append(make("h3", "", "하던 작업을 이어서 할까요?"), note, row);
    box.append(inner);
    document.body.append(box);
    (same ? go : shut).focus();
  }).catch(() => {});  // 초안을 못 읽어도 화면은 그대로 쓴다
}

export function useAutosave(open: (id: string) => Promise<void>): Autosave {
  const { state, dispatch } = useDash();
  useEffect(() => { cur.state = state; cur.dispatch = dispatch; cur.open = open; });

  // 바뀔 때마다 저장을 미룬다(디바운스). 그래도 저장이 안 되고 남으면 초안
  useEffect(() => {
    if (state.dash && !state.dirty) markSaved(state.dash);  // 연 직후·저장 뒤: 서버 값과 같다
    if (!state.dirty || !state.dash) return;
    clearTimeout(timer);
    timer = window.setTimeout(() => flush().catch(() => {}), QUIET);  // 실패 이유는 saveNow가 error로 보였다
    clearTimeout(draftTimer);
    draftTimer = window.setTimeout(() => {
      const s = cur.state;
      if (!s?.dirty || !s.dash) return;
      draftHas = true;
      drafts.put(draftState(s.dash)).catch(() => {});
    }, DRAFT_QUIET);
  }, [state.dash, state.dirty]);

  // 처음 대시보드가 열린 뒤 한 번: 남은 초안을 묻는다(그 전에는 초안을 쓰지 않으므로 덮어쓰지 않는다)
  const opened = !!state.dash;
  useEffect(() => { if (opened && !asked) { asked = true; askDraft(); } }, [opened]);

  useEffect(() => {
    // Ctrl+S 저장(입력 칸 안에서도: 브라우저의 "페이지 저장"이 뜨지 않게, 옛 Shortcuts와 같음)
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || e.altKey || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.key.toLowerCase() !== "s" || modalOpen()) return;
      e.preventDefault();
      flush().catch(() => {});
    };
    // 창을 닫거나 다른 화면으로 갈 때: 남은 편집을 카드 값 없는 초안으로 keepalive(본문 64KB 상한, 응답은 못 받는다). 다음에 열면 "이어서 할까요?"가 되살린다
    const onHide = () => {
      const s = cur.state;
      if (!s?.dirty || !s.dash) return;
      draftHas = true;
      fetch("/api/draft?screen=dashboard", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: draftState(s.dash) }) }).catch(() => {});
    };
    // 최신화가 끝나면 새 값으로 다시 연다(저장이 끝난 뒤. 입력 중이면 2초 뒤에 다시 본다)
    const onRefreshed = (e: Event) => {
      if (seenEvents.has(e)) return;
      seenEvents.add(e);
      const id = cur.state?.dash?.id, results = (e as CustomEvent<{ results?: { state: string; dashboard: string }[] }>).detail?.results ?? [];
      if (!id || !results.some(r => r.state === "ok" && r.dashboard === id)) return;
      const reopen = () => {
        if (cur.state?.dash?.id !== id) return;
        if (isTyping(document.activeElement)) return void setTimeout(reopen, 2000);  // 다시 열면 쓰던 글이 사라진다
        flush().then(() => { if (cur.state?.dash?.id === id) return cur.open(id); }).catch(() => {});
      };
      reopen();
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("pagehide", onHide);
    document.addEventListener("dash-refreshed", onRefreshed);
    return () => { document.removeEventListener("keydown", onKey); window.removeEventListener("pagehide", onHide); document.removeEventListener("dash-refreshed", onRefreshed); };
  }, []);

  return { flush, saving: state.dirty };
}
