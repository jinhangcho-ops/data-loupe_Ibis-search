"use client";
// 위쪽 줄(옛 static/core/status.html): 연결 이름(crumb, 연결되면 .crumb.on), 화면 탭(설치한 기능만, 상수 features.on), 최신화 알약·팝업(옛 dash_refresh.js), 화면 모드 단추.
// [보낼 곳 설정]·[설정]은 홈(/?panel=…)으로 가서 static 창을 연다.
// 최신화는 연결에 성공한 화면(쿼리·홈)이 시작한다. 여기서는 열 때 돌고 있으면 0.8초마다 상태를 읽어 알약에 보이고, 끝나면 document에 dash-refreshed(결과)를 보낸다.
import { useCallback, useEffect, useRef, useState } from "react";
import { refreshStatus, saveSetting, stopRefresh } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import type { RefreshStatus } from "@/lib/types";
import { useShell } from "./ShellProvider";

const TABS: [string, string, string, string][] = [  // [주소, 기능 id(features), 이름, 설명]
  ["/", "", "홈", "처음 화면"],
  ["/search", "query", "쿼리", "테이블을 찾아보고 SQL, 지표, 차트를 만들어요"],
  ["/prep", "prep", "분석", "데이터를 다듬고 그래프로 살펴봐요"],
  ["/dashboard", "dashboard", "대시보드", "모아 둔 차트와 지표를 보고 고쳐요"],
  ["/collect", "collect", "수집", "웹 API나 웹페이지에서 데이터를 받아 저장해요"],
  ["/integrations", "integrations", "연동", "Slack, Notion, GA4, LLM 같은 서비스와 연결해요"],
];
const PANEL_PARAM = "panel";
const PANELS: [string, string, string][] = [  // [창 id, 이름, 설명]
  ["dest", "보낼 곳 설정", "알림과 결과를 받을 Slack, Notion, 웹훅을 정해요"],
  ["feat", "설정", "쓸 기능, 업데이트, 메모리·표본·날짜 같은 앱 설정을 정해요"],
];
const REFRESH_STATE: Record<string, string> = { ok: "완료", failed: "실패", skipped: "건너뜀", waiting: "대기" };
const MOON = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>;
const SUN = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;


export function TopBar() {
  const on = useConstants()?.features.on;
  const { conn, loadConn } = useShell();
  const [st, setSt] = useState<RefreshStatus | null>(null);
  const [pop, setPop] = useState(false);
  const [theme, setTheme] = useState("");
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
    setTimeout(() => setTheme(document.documentElement.dataset.theme ?? "light"));
    return () => clearTimeout(timer.current);
  }, [poll]);
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    saveSetting({ theme: next }).catch(() => {});
  };
  const count = (s: string) => st?.results.filter(r => r.state === s).length ?? 0;
  const title = !st ? "" : st.running ? `대시보드를 최신 값으로 바꾸고 있어요 (${st.done}/${st.total})`
    : `대시보드를 최신 값으로 바꿨어요. 완료 ${count("ok")}개, 실패 ${count("failed")}개, 건너뜀 ${count("skipped")}개`;
  return (
    <>
      <header className="topbar">
        <div className={"crumb" + (conn ? " on" : "")} title="지금 연결된 데이터"><i></i><span>{conn?.label || "연결 안 됨"}</span></div>
        <nav className="pages">
          {TABS.filter(([, id]) => !id || !on || on.includes(id)).map(([href, , name, tip]) => (
            <a key={href} href={href} title={tip} className={href === "/dashboard" ? "on" : undefined} aria-current={href === "/dashboard" ? "page" : undefined}>{name}</a>
          ))}
        </nav>
        {st?.running && (
          <button className="jobx" id="rf-pill" style={{ cursor: "pointer", border: 0 }} onClick={() => setPop(true)} title="대시보드를 최신 값으로 바꾸는 중이에요. 누르면 진행 상황을 볼 수 있어요.">
            <i></i><span>{`대시보드 최신화 ${st.done}/${st.total}`}</span></button>
        )}
        <div className="top-tools">
          <button className="ib" onClick={flip} title={theme === "dark" ? "밝은 화면으로" : "어두운 화면으로"} aria-label={theme === "dark" ? "밝은 화면으로" : "어두운 화면으로"}>{theme === "dark" ? SUN : MOON}</button>
          {PANELS.map(([id, name, tip]) => (
            <a key={id} className="btn" href={`/?${PANEL_PARAM}=${id}`} title={tip} style={{ textDecoration: "none" }}>{name}</a>
          ))}
        </div>
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
