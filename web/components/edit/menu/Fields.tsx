"use client";
// 카드 추가 메뉴의 칸 부품(라벨이 입력을 감싸 이름이 붙는다). 줄 글 칸(LineBox)은 옛 dash_rules.js lineEditor: 줄마다 읽어 틀린 줄과 이유를 아래에 보인다.
import { readLines } from "@/lib/calc";
import type { ReactNode } from "react";

export type Opt = string | [string, string];  // 값 또는 [값, 글자]
const pair = (o: Opt): [string, string] => (typeof o === "string" ? [o, o] : o);

export function Sel({ label, value, onChange, options, blank, title, wide }: { label: string; value: string; onChange: (v: string) => void; options: Opt[]; blank?: string; title?: string; wide?: boolean }) {
  return (
    <label className="hint cm-field" title={title}>
      {label}
      <select value={value} onChange={e => onChange(e.target.value)} style={wide ? { flex: "1 1 220px" } : undefined}>
        {blank !== undefined && <option value="">{blank}</option>}
        {options.map(o => { const [v, n] = pair(o); return <option key={v} value={v}>{n}</option>; })}
      </select>
    </label>
  );
}

export function Txt({ label, value, onChange, type = "text", placeholder, title, width, min, max }: { label: string; value: string; onChange: (v: string) => void; type?: "text" | "number"; placeholder?: string; title?: string; width?: number; min?: number; max?: number }) {
  return (
    <label className="hint cm-field" title={title}>
      {label}
      <input type={type} value={value} placeholder={placeholder} min={min} max={max} step={type === "number" ? "any" : undefined} style={width ? { width, flex: "0 0 auto" } : undefined} onChange={e => onChange(e.target.value)} />
    </label>
  );
}

export function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return <label className="hint cm-field">{label}<input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} style={{ width: 24, height: 24, flex: "0 0 auto" }} /></label>;
}

/** 여러 줄 글 칸. parse는 한 줄 읽기 함수({error} 또는 결과) */
export function LineBox({ label, value, onChange, parse, placeholder }: { label: string; value: string; onChange: (v: string) => void; parse: (line: string) => { error?: string }; placeholder: string }) {
  const bad = readLines(value, parse).filter(f => f.error);
  return (
    <div className="cm-block">
      <label className="hint">{label}
        <textarea rows={3} value={value} placeholder={placeholder} style={{ width: "100%", fontSize: 12 }} onChange={e => onChange(e.target.value)} />
      </label>
      {bad.map((f, i) => <div key={i} className="err" role="alert">{f.line} → {f.error}</div>)}
    </div>
  );
}

export const Block = ({ children }: { children: ReactNode }) => <div className="cm-block">{children}</div>;
