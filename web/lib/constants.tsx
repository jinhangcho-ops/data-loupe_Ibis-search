"use client";
// 서버 상수(옛 화면의 SERVER): /api/dash/constants를 한 번 받아 Context로 나눠 준다. 받기 전·실패면 null.
import { createContext, useContext, useEffect, useState } from "react";
import { constants } from "./api";
import { setWords } from "./autoviz";
import { setCalendar } from "./calc";
import type { Constants } from "./types";

const Ctx = createContext<Constants | null>(null);

export function ConstantsProvider({ children }: { children: React.ReactNode }) {
  const [found, setFound] = useState<Constants | null>(null);
  useEffect(() => {
    constants().then(c => { setCalendar(c.week_start, c.fiscal_start_month); setWords(c.dash.viz.words); setFound(c); }).catch(() => setFound(null));  // 주·연 묶음(BUCKET)이 서버 설정을 따르게 먼저
  }, []);
  return <Ctx.Provider value={found}>{children}</Ctx.Provider>;
}

/** 서버 상수. 아직 못 받았으면 null(쓰는 쪽이 빈 상태를 그린다) */
export const useConstants = () => useContext(Ctx);
