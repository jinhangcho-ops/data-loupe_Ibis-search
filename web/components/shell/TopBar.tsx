"use client";
// 왼쪽 메뉴(static/core/status.html의 nav.rail과 같은 마크업): 앱 이름, 화면(설치한 기능만, 상수 features.on), 아래쪽 [설정](홈 /?panel=feat로 가서 static 설정 창을 연다)·쉬운 보기(#easy)·화면 모드.
// 위쪽 줄: 연결 이름(crumb), 최신화 알약·팝업(옛 dash_refresh.js). 사용률·작업·업데이트·크기 조절은 static 화면(common.js)에만 있다(결정 D2).
// 최신화는 연결에 성공한 화면(쿼리·홈)이 시작한다. 여기서는 열 때 돌고 있으면 0.8초마다 상태를 읽어 알약에 보이고, 끝나면 document에 dash-refreshed(결과)를 보낸다.
import { useCallback, useEffect, useRef, useState } from "react";
import { refreshStatus, saveSetting, stopRefresh } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import type { RefreshStatus } from "@/lib/types";
import { useShell } from "./ShellProvider";

const REFRESH_STATE: Record<string, string> = { ok: "완료", failed: "실패", skipped: "건너뜀", waiting: "대기" };
const MOON = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>;
const SUN = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
const icon = (body: React.ReactNode) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{body}</svg>;  // status.html 메뉴 아이콘과 같은 모양
const I_LOGO = icon(<><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3M8 11h6M11 8v6" /></>);
const I_HOME = icon(<><path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /></>);
const I_QUERY = icon(<><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>);
const I_PREP = icon(<><path d="M3 3v18h18" /><path d="M7 15l4-4 3 3 5-6" /></>);
const I_DASH = icon(<><rect x="3" y="3" width="7" height="9" /><rect x="14" y="3" width="7" height="5" /><rect x="14" y="12" width="7" height="9" /><rect x="3" y="16" width="7" height="5" /></>);
const I_COLLECT = icon(<><path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M5 21h14" /></>);
const I_LINK = icon(<><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" /><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></>);
const I_SET = icon(<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />);
const I_EASY = icon(<><circle cx="12" cy="12" r="9" /><path d="M9 10h.01M15 10h.01M8.5 14.5a4.5 4.5 0 0 0 7 0" /></>);
const TABS: [string, string, string, string, React.ReactNode][] = [  // [주소, 기능 id(features), 이름, 설명, 아이콘]
  ["/", "", "홈", "처음 화면: 데이터 연결과 최근 연결", I_HOME],
  ["/search", "query", "쿼리", "테이블을 찾아보고 SQL, 지표, 차트를 만들어요", I_QUERY],
  ["/prep", "prep", "분석", "데이터를 다듬고 그래프로 살펴봐요", I_PREP],
  ["/dashboard", "dashboard", "대시보드", "모아 둔 차트와 지표를 보고 고쳐요", I_DASH],
  ["/collect", "collect", "수집", "웹 API나 웹페이지에서 데이터를 받아 저장해요", I_COLLECT],
  ["/integrations", "integrations", "연동", "Slack, Notion, GA4, LLM 같은 서비스와 연결해요", I_LINK],
];
const PANEL_PARAM = "panel";


export function TopBar() {
  const on = useConstants()?.features.on;
  const { conn, loadConn } = useShell();
  const [st, setSt] = useState<RefreshStatus | null>(null);
  const [pop, setPop] = useState(false);
  const [theme, setTheme] = useState("");
  const [easy, setEasy] = useState(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const poll = useCallback(async function step() {
    clearTimeout(timer.current);
    try {
      const s = await refreshStatus();
      setSt(s);
      if (s.running) { timer.current = setTimeout(step, 800); return; }
      loadConn();
      document.dispatchEvent(new CustomEvent("dash-refreshed", { detail: s }));  // 다시 열기는 자동 저장(N6 useAutosave)이 저장을 끝낸 뒤 한다(옛 dash-refreshed)
    } catch { /* 상태를 못 읽어도 화면은 그대로 쓴다 */ }
  }, [loadConn]);
  useEffect(() => {
    refreshStatus().then(s => { if (s.running) { setSt(s); poll(); } }).catch(() => {});  // 다른 화면에서 시작해 아직 돌고 있으면 이어서
    setTimeout(() => { setTheme(document.documentElement.dataset.theme ?? "light"); setEasy(document.documentElement.classList.contains("easy")); });
    return () => clearTimeout(timer.current);
  }, [poll]);
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    saveSetting({ theme: next }).catch(() => {});
  };
  // 쉬운 보기(status.html의 #easy, common.js showEasy와 같다): html.easy를 바꾸고 설정 easy로 저장한다
  const flipEasy = () => {
    const next = !easy;
    document.documentElement.classList.toggle("easy", next);
    setEasy(next);
    saveSetting({ easy: next ? "on" : "off" }).catch(() => {});
  };
  const count = (s: string) => st?.results.filter(r => r.state === s).length ?? 0;
  const title = !st ? "" : st.running ? `대시보드를 최신 값으로 바꾸고 있어요 (${st.done}/${st.total})`
    : `대시보드를 최신 값으로 바꿨어요. 완료 ${count("ok")}개, 실패 ${count("failed")}개, 건너뜀 ${count("skipped")}개`;
  return (
    <>
      <nav className="rail" aria-label="화면">
        <div className="rail-name" title="Data Loupe">{I_LOGO}<span>Data Loupe</span></div>
        {TABS.filter(([, id]) => !id || !on || on.includes(id)).map(([href, , name, tip, ic]) => (
          <a key={href} href={href} title={tip} className={href === "/dashboard" ? "on" : undefined} aria-current={href === "/dashboard" ? "page" : undefined}>{ic}<span>{name}</span></a>
        ))}
        <div className="rail-foot">
          <a className="rail-btn" href={`/?${PANEL_PARAM}=feat`} title="쓸 기능, 업데이트, 계산·표본·날짜, 차트 낱말, 보낼 곳을 정해요">{I_SET}<span>설정</span></a>
          <button id="easy" className="rail-btn" aria-pressed={easy} onClick={flipEasy} title="켜면 처음 쓰는 사람에게 필요 없는 칸을 [더 많은 설정] 뒤에 접어요">{I_EASY}<span id="easy-text">{easy ? "쉬운 보기 켜짐" : "쉬운 보기 꺼짐"}</span></button>
          <button id="theme" className="ib" onClick={flip} title={theme === "dark" ? "밝은 화면으로" : "어두운 화면으로"} aria-label={theme === "dark" ? "밝은 화면으로" : "어두운 화면으로"}>{theme === "dark" ? SUN : MOON}</button>
        </div>
      </nav>
      <header className="topbar">
        <div id="crumb" className={"crumb" + (conn ? " on" : "")} title="지금 연결된 데이터"><i></i><span id="crumb-text">{conn?.label || "연결 안 됨"}</span></div>
        {st?.running && (
          <button className="jobx" id="rf-pill" style={{ cursor: "pointer", border: 0 }} onClick={() => setPop(true)} title="대시보드를 최신 값으로 바꾸는 중이에요. 누르면 진행 상황을 볼 수 있어요.">
            <i></i><span>{`대시보드 최신화 ${st.done}/${st.total}`}</span></button>
        )}
      </header>
      {pop && st && (
        <div className="modal" onClick={e => { if (e.target === e.currentTarget) setPop(false); }}>
          <div className="modal-box" role="dialog" aria-modal="true" aria-labelledby="rf-title">
            <div className="head"><h3 id="rf-title">{title}</h3></div>
            <ul className="rf-list">
              {st.results.map((r, i) => (
                <li key={i} className={r.state + (st.running && r.title === st.current && (r.state as string) === "waiting" ? " now" : "")}>
                  <b>{REFRESH_STATE[r.state] ?? r.state}</b><span>{r.title + (r.message ? `: ${r.message}` : "")}</span></li>
              ))}
            </ul>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="ghost" disabled={!st.running} onClick={() => stopRefresh().then(setSt).catch(() => {})}>멈추기</button>
              <button className="btn" onClick={() => setPop(false)} autoFocus>{st.running ? "닫고 계속하기" : "닫기"}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
