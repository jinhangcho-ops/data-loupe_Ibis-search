"use client";
// 카드 위에 뜨는 창(카드 크게 보기, 표 줄 상세). 카드 상자가 그림자·변형을 써도 화면 전체를 덮도록 body에 붙인다.
// 창 안을 눌러도 카드 고르기(Grid의 section onClick)로 번지지 않게 누르기를 여기서 멈춘다. 바깥·Esc·[닫기]로 닫는다. head를 주면 제목 줄을 그것으로 바꾼다(KPI 큰 창).
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export function Pop({ title, cls, onClose, head, children }: { title: string; cls?: string; onClose: () => void; head?: ReactNode; children: ReactNode }) {
  return createPortal(
    <div className="modal" onClick={e => { e.stopPropagation(); if (e.target === e.currentTarget) onClose(); }}
      onKeyDown={e => { e.stopPropagation(); if (e.key === "Escape") { e.preventDefault(); onClose(); } }}>
      <div className={"modal-box" + (cls ? " " + cls : "")} role="dialog" aria-modal="true" aria-label={title}>
        <div className="head">{head ?? <><h3>{title}</h3><button className="btn" onClick={onClose} autoFocus>닫기</button></>}</div>
        {children}
      </div>
    </div>, document.body);
}
