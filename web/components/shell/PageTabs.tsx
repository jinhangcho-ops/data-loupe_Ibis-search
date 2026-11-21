"use client";
// 쪽(탭) 줄(5단계): 대시보드의 쪽 이름(dash.pages)마다 단추. 누르면 그 쪽의 카드만 격자에 보인다(지금 쪽은 화면 틀의 page, 저장하지 않는다).
// 쪽을 정하지 않은 카드와 지운 쪽의 카드는 첫 쪽에 보인다. 쪽이 하나도 없으면 줄이 없고 카드는 전부 보인다.
// 만드는 곳(build)에서는 쪽을 더하고 ×로 뺀다(목록은 대시보드와 함께 저장). 카드의 쪽은 카드 설정에서 고른다.
import { useState } from "react";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import { useShell } from "./ShellProvider";

export function PageTabs() {
  const { state, dispatch } = useDash();
  const { build, page, setPage } = useShell();
  const max = useConstants()?.dash.max_pages ?? Infinity;
  const [name, setName] = useState("");
  const pages = state.dash?.pages ?? [];
  if (!state.dash || (!pages.length && !build)) return null;
  const now = pages.includes(page) ? page : pages[0];
  const save = (next: string[]) => dispatch({ type: "meta", patch: { pages: next } });
  const fresh = name.trim(), taken = pages.includes(fresh);
  const add = () => { save([...pages, fresh]); setPage(fresh); setName(""); };
  return (
    <div className="ctl board-ctl" id="d-pages">
      <div role="tablist" aria-label="쪽" className="[display:flex] flex-wrap gap-1.5">
        {pages.map(p => (
          <span key={p} className="[display:inline-flex] items-center">
            <button type="button" role="tab" aria-selected={p === now} className={p === now ? "btn p" : "btn"} onClick={() => setPage(p)}>{p}</button>
            {build && <button type="button" className="ghost" title="이 쪽 빼기. 이 쪽의 카드는 첫 쪽으로 가요" aria-label={`${p} 쪽 빼기`} onClick={() => save(pages.filter(x => x !== p))}>×</button>}
          </span>
        ))}
      </div>
      {build && pages.length < max && (
        <>
          <input className="[flex:0_0_9rem] w-auto" value={name} placeholder="새 쪽 이름" aria-label="새 쪽 이름"
            onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && fresh && !taken) add(); }} />
          <button type="button" className="ghost" id="d-page-add" disabled={!fresh || taken} title="대시보드를 쪽(탭)으로 나눠요. 카드가 놓일 쪽은 카드 설정에서 골라요" onClick={add}>쪽 추가</button>
        </>
      )}
    </div>
  );
}
