"use client";
// N4 — 대시보드 고르기·새 대시보드(옛 #d-pick·showList·#d-new·#d-new-go).
// 고르면 useShell().open(지금 편집을 먼저 저장하고, 못 하면 옮기지 않는다). 새 대시보드는 이름 + 시작 모양(기본 4종 edit/Templates.TEMPLATES·내 템플릿 api.listTemplates)으로 api.saveDashboard → 목록 → 열기.
import { useEffect, useRef, useState } from "react";
import { TEMPLATES, templateCards } from "@/components/edit/Templates";
import { listTemplates, saveDashboard } from "@/lib/api";
import { useDash } from "@/lib/store";
import type { Template } from "@/lib/types";
import { useShell } from "./ShellProvider";

const LABEL = { margin: 0, display: "inline-flex", alignItems: "center", gap: 6 } as const;

export function PickDashboard() {
  const { state, dispatch } = useDash();
  const { open, flush } = useShell();
  const [making, setMaking] = useState(false);
  const [name, setName] = useState("");
  const [tpl, setTpl] = useState("overview");
  const [mine, setMine] = useState<Template[]>([]);
  const [busy, setBusy] = useState(false);
  const items = state.list.length ? state.list : [{ id: "", name: "저장한 대시보드가 없어요", cards: 0 }];
  const all = [...TEMPLATES, ...mine.map(t => ({ ...t, id: "my:" + t.id, name: `${t.name} (내 템플릿)`, help: "저장해 둔 카드 배치" }))];
  const wrap = useRef<HTMLSpanElement>(null), opener = useRef<HTMLButtonElement>(null);
  // 바깥 클릭·Esc로 닫고 [새 대시보드] 단추로 초점을 돌려준다
  useEffect(() => {
    if (!making) return;
    const off = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setMaking(false); };
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, [making]);
  const close = () => { setMaking(false); opener.current?.focus(); };
  const toggle = () => {
    setMaking(!making);
    if (!making) listTemplates().then(d => setMine(d.templates)).catch(() => setMine([]));  // 내 템플릿을 못 읽어도 기본 4종으로 만든다
  };
  const make = async () => {
    setBusy(true);
    try {
      await flush();  // 새 대시보드로 옮기기 전에 지금 편집을 저장한다(못 하면 옮기지 않는다)
      const t = all.find(x => x.id === tpl) ?? TEMPLATES[TEMPLATES.length - 1];
      const made = await saveDashboard({ name, cards: templateCards(t), template: t.id === "blank" ? "" : t.name });
      setName("");
      setMaking(false);
      dispatch({ type: "list", list: made });
      await open(made.dashboard.id);
    } catch (e) { dispatch({ type: "error", message: (e as Error).message }); }
    finally { setBusy(false); }
  };
  return (
    <>
      <label className="hint" style={LABEL}>대시보드
        <select value={state.dash?.id ?? ""} onChange={e => open(e.target.value)} title="열어 볼 대시보드. 마지막에 연 대시보드가 먼저 열려요" style={{ width: "auto", minWidth: 200 }}>
          {items.map(d => <option key={d.id} value={d.id}>{d.id ? `${d.name} (카드 ${d.cards}개)` : d.name}</option>)}
        </select>
      </label>
      <span className="drop" ref={wrap} onKeyDown={e => { if (e.key === "Escape" && making) { e.stopPropagation(); close(); } }}>
        <button ref={opener} className="btn" onClick={toggle} aria-expanded={making} aria-haspopup="dialog" title="이름과 시작 모양을 정해 새 대시보드를 만들어요">새 대시보드</button>
        {making && (
          <div className="pop" role="dialog" aria-label="새 대시보드" style={{ left: 0, right: "auto", width: "min(320px, 92vw)", gap: 8, padding: 12 }}>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="대시보드 이름" aria-label="새 대시보드 이름" autoFocus
              onKeyDown={e => { if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !busy) make(); }} />
            <label className="hint" style={LABEL}>시작 모양
              <select value={tpl} onChange={e => setTpl(e.target.value)} title="시작 모양. 카드 자리만 놓이고, 데이터는 카드마다 정해요" style={{ width: "auto", flex: 1 }}>
                {all.map(t => <option key={t.id} value={t.id} title={t.help ?? ""}>{t.name}</option>)}
              </select>
            </label>
            <button className="btn p" onClick={make} disabled={busy}>만들기</button>
          </div>
        )}
      </span>
    </>
  );
}
