"use client";
// N6 — 그림·보고서(옛 dashboard.html d-down·d-report-png·d-report-pdf, board.js saveBoard, dash_report.js saveReportPng·saveReportPdf).
// [그림으로 저장]: 고른 카드들(state.picked)을 그림 파일로(/api/images, 창이 없으면 window:false → 내려받기). [PNG 보고서]·[PDF 보고서]: 모든 카드를 화면 배치대로 한 장(/api/images, /api/dash/pdf).
// 비율·색·형식은 설정(board_ratio·image·board_format, /api/settings)에 남고 쿠키로 돌아온다.
// [CSV로 저장]: 고른 차트 카드의 표를 CSV로. [모든 데이터 포함](기억하지 않음)을 켜면 그림·CSV가 자동 규칙을 푼 모든 데이터로(보고서는 보이는 그대로).
import { useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { saveImages, savePdf, saveSetting } from "@/lib/api";
import { isShell } from "@/components/edit/menu/kinds";
import { useDash } from "@/lib/store";
import { CardBody } from "@/components/cards/CardBody";
import { CHART_KINDS, autoDraw } from "@/components/cards/ChartCard";
import { seriesKit } from "@/components/cards/chart/series";
import { useConstants } from "@/lib/constants";
import { applyGroups } from "@/lib/groups";
import { csvText } from "@/lib/autoviz";
import type { Card } from "@/lib/types";
import { BOARD_RATIOS } from "./layout";
import { cardBox, cardPicture, inMode, reportCanvas } from "./picture";

export type ReportProps = { open: boolean; onClose: () => void };

type Picks = { board_ratio: string; image: string; board_format: string };
const DEFAULTS: Picks = { board_ratio: "screen", image: "light", board_format: "png" };
const ALL_KINDS = [...CHART_KINDS, "boxplot"];  // 모든 데이터로 다시 그리면 그림이 달라지는 카드(차트 규칙·상자 그림 꼬리 자르기). 히스토그램·분석 hist의 꼬리는 서버가 다시 구해야 해서 화면 밖 그리기로 달라지지 않는다
const OFFSCREEN_LEFT = -10000;  // 화면 밖 상자 위치(px)
const FRAMES = 2;  // 화면 밖 상자를 그린 뒤 기다리는 프레임 수(ResizeObserver·Recharts가 크기를 잰다)
const CSV_BOM = "﻿";  // Excel이 한글을 바로 읽게(서버 export·쿼리 화면과 같다)
// 서버가 내려준 설정 쿠키(head.html saved와 같은 규칙: 따옴표·퍼센트 인코딩 풀기)
const cookie = (name: string) => {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  if (!m) return undefined;
  try { return decodeURIComponent(m[1].replace(/^"(.*)"$/, "$1")); } catch { return m[1]; }
};
const download = (href: string, name: string) => Object.assign(document.createElement("a"), { href, download: name }).click();

export function Report({ open, onClose }: ReportProps) {
  const { state } = useDash();
  const [chosen, setPicks] = useState<Picks | null>(null);  // 이 창에서 고른 것. 아직 없으면 설정 쿠키
  const [msg, setMsg] = useState({ text: "", ok: false });
  const [busy, setBusy] = useState("");
  const [all, setAll] = useState(false);
  const [shot, setShot] = useState<{ card: Card; w: number; h: number } | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const consts = useConstants();
  if (!open) return null;  // 아래는 브라우저에서만(열렸을 때) 돈다
  const picks = chosen ?? { board_ratio: cookie("board_ratio") || DEFAULTS.board_ratio, image: cookie("image") || DEFAULTS.image, board_format: cookie("board_format") || DEFAULTS.board_format };
  const cards = state.dash?.cards ?? [], title = state.dash?.name || "대시보드";
  const pick = (key: keyof Picks, value: string) => {
    setPicks({ ...picks, [key]: value });
    saveSetting({ [key]: value }).catch(() => {});
  };
  const run = (name: string, fn: () => Promise<{ text: string; ok: boolean } | void>) => async () => {
    if (busy) return;
    setBusy(name);
    setMsg({ text: "", ok: false });
    try { const said = await fn(); if (said) setMsg(said); } catch (e) { setMsg({ text: (e as Error).message, ok: false }); } finally { setBusy(""); }
  };
  // 고른 카드들의 그림(옛 saveBoard): 카드 순서대로 하나씩. 빈 틀·아직 그려지지 않은 카드는 빼고 그 수를 알린다
  const pictures = run("down", async () => {
    const picked = cards.filter(c => state.picked.includes(c.id)), boxes = picked.map(c => [c, !isShell(c) && cardBox(cards, c.id)] as const).filter(([, box]) => box);
    const skipped = picked.length - boxes.length;
    if (!boxes.length) return { text: skipped ? "고른 카드가 아직 그려지지 않았어요." : "저장할 카드를 먼저 고르세요. 카드를 누르면 선택돼요.", ok: false };
    const type = picks.board_format === "jpeg" ? "jpeg" : "png", ext = type === "jpeg" ? "jpg" : "png";
    const shots = await inMode(picks.image, async () => {
      try {
        const out: { name: string; data: string }[] = [];
        for (const [card, box] of boxes) {
          const body = (box as HTMLElement).querySelector<HTMLElement>(".bbody");
          if (all && body && ALL_KINDS.includes(card.kind)) {
            flushSync(() => setShot({ card: { ...card, view: { ...card.view, all_data: true } }, w: body.clientWidth, h: body.clientHeight }));
            for (let i = 0; i < FRAMES; i++) await new Promise(r => requestAnimationFrame(r));
            out.push({ name: card.title, data: await cardPicture(host.current!, card.title, picks.image, type, BOARD_RATIOS[picks.board_ratio]) });
          } else out.push({ name: card.title, data: await cardPicture(box as HTMLElement, card.title, picks.image, type, BOARD_RATIOS[picks.board_ratio]) });
        }
        return out;
      } finally { setShot(null); }
    });
    const d = await saveImages(shots) as { cancelled?: boolean; window?: false; folder?: string; saved?: string[] };
    const more = skipped ? ` 아직 그려지지 않은 ${skipped}개는 뺐어요.` : "";
    if (d.cancelled) return;
    if (d.window === false) { shots.forEach(p => download(p.data, `${p.name}.${ext}`)); return { text: `${shots.length}개를 내려받았어요.${more}`, ok: true }; }
    return { text: `${d.saved?.length ?? 0}개를 저장했어요. 위치: ${d.folder}${more}`, ok: true };
  });
  const reportPng = run("png", async () => {
    const data = (await inMode(picks.image, () => reportCanvas(cards, title, picks.image))).toDataURL("image/png");
    const d = await saveImages([{ name: title, data }]) as { cancelled?: boolean; window?: false; folder?: string; saved?: string[] };
    if (d.cancelled) return;
    if (d.window === false) { download(data, `${title}.png`); return { text: "보고서를 PNG로 내려받았어요.", ok: true }; }
    return { text: `보고서를 저장했어요. 위치: ${d.folder}/${d.saved?.[0]}`, ok: true };
  });
  const reportPdf = run("pdf", async () => {
    const data = (await inMode(picks.image, () => reportCanvas(cards, title, picks.image, 1.5))).toDataURL("image/jpeg", 0.92);  // 서버(dash_pdf.py)가 JPEG 한 장을 1쪽 PDF로
    const d = await savePdf(data, title);
    if (d.cancelled) return;
    if (d.window === false) { download(d.pdf!, d.name!); return { text: "보고서를 PDF로 내려받았어요.", ok: true }; }
    return { text: `보고서를 저장했어요. 위치: ${d.path}`, ok: true };
  });
  const csv = run("csv", async () => {
    const kit = seriesKit(consts!);
    const picked = cards.filter(c => state.picked.includes(c.id) && CHART_KINDS.includes(c.kind) && !isShell(c));
    if (!picked.length) return { text: "CSV로 저장할 차트 카드를 먼저 고르세요. 카드를 누르면 선택돼요.", ok: false };
    let n = 0, k = 0;
    for (const card of picked) {
      const box = cardBox(cards, card.id);
      if (!box) { k++; continue; }
      const raw = state.values[card.id]?.data ?? card.data ?? {};
      const c = { ...card, data: applyGroups(card, raw), range: state.ctx?.range };
      const t = autoDraw(c, kit, consts!, box.querySelector<HTMLElement>(".bbody")!.clientWidth, all).table;
      if (!t) { k++; continue; }
      download(URL.createObjectURL(new Blob([CSV_BOM + csvText(t)], { type: "text/csv" })), `${card.title}.csv`);
      n++;
    }
    if (!n) return { text: "고른 카드는 표로 만들 수 없어요.", ok: false };
    return { text: `${n}개를 CSV로 내려받았어요.${k ? ` 표로 만들 수 없거나 아직 그려지지 않은 ${k}개는 뺐어요.` : ""}`, ok: true };
  });
  const label = (name: string, text: string) => (busy === name ? "…" : text);
  return (
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) onClose(); }} onKeyDown={e => { if (e.key === "Escape") onClose(); }}>
      <div className="modal-box" role="dialog" aria-label="그림·보고서">
        <div className="head row" style={{ gap: 8 }}>
          <h3>그림·보고서</h3>
          <button className="ib" title="닫기" style={{ marginLeft: "auto" }} onClick={onClose}>×</button>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <select title="그림 비율" value={picks.board_ratio} onChange={e => pick("board_ratio", e.target.value)} style={{ width: "auto" }}>
            <option value="screen">화면대로</option><option value="square">1:1</option><option value="4:3">4:3</option><option value="wide">16:9</option>
          </select>
          <select title="그림 색. 쿼리 화면의 그림 저장과 같은 설정이에요" value={picks.image} onChange={e => pick("image", e.target.value)} style={{ width: "auto" }}>
            <option value="light">밝게</option><option value="dark">어둡게</option>
          </select>
          <select title="그림 형식" value={picks.board_format} onChange={e => pick("board_format", e.target.value)} style={{ width: "auto" }}>
            <option value="png">PNG</option><option value="jpeg">JPG</option>
          </select>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 10 }}>
          <label className="row" style={{ gap: 4 }} title="켜면 자동 규칙(Top-N·꼬리 자르기·점 줄이기 등)이 덜어 낸 것까지 담아요. 보고서에는 쓰지 않아요"><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} />모든 데이터 포함</label>
          <button className="btn" disabled={!!busy} onClick={pictures} title="선택한 카드를 그림 파일로 저장해요. 프로그램 창에서는 저장할 폴더를 한 번 물어봐요">{label("down", "그림으로 저장")}</button>
          <button className="btn" disabled={!!busy || !consts} onClick={csv} title="선택한 차트 카드의 값을 CSV 파일로 내려받아요">{label("csv", "CSV로 저장")}</button>
          <button className="btn" disabled={!!busy || !cards.length} onClick={reportPng} title="모든 카드를 화면 배치대로 PNG 한 장에 담아요">{label("png", "PNG 보고서")}</button>
          <button className="btn" disabled={!!busy || !cards.length} onClick={reportPdf} title="모든 카드를 화면 배치대로 PDF 한 쪽에 담아요">{label("pdf", "PDF 보고서")}</button>
        </div>
        <div className={msg.ok ? "ok" : "err"} id="err-report">{msg.text}</div>
        {shot ? createPortal(<div ref={host} aria-hidden="true" style={{ position: "fixed", left: OFFSCREEN_LEFT, top: 0, width: shot.w }}><div className="bbody" style={{ width: shot.w, height: shot.h, aspectRatio: "auto", maxHeight: "none" }}><CardBody card={shot.card} result={state.values[shot.card.id]} palette={state.dash!.palette} ctx={state.ctx} /></div></div>, document.body) : null}
      </div>
    </div>
  );
}
