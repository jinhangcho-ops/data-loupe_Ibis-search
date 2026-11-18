"use client";
// 차트 그림 안의 px(글자·여백·점·막대)를 바탕 글자 크기(html font-size, 설정의 화면 크기)에 맞춘다.
// 숫자를 하나씩 바꾸지 않고 그림 뿌리를 1/배율 크기로 배치한 뒤 배율만큼 키운다(16px이면 배율 1, 그대로).
import { useSyncExternalStore, type CSSProperties } from "react";

/** 차트의 px 숫자를 정한 기준 바탕 글자 크기 */
export const BASE_FONT_PX = 16;
/** 화면 없이 그릴 때(서버·vitest)의 배율 */
const NO_SCALE = 1;

/** 바탕 글자 크기("20px") → 배율(1.25) */
export const rootScale = (fontSize: string): number => parseFloat(fontSize) / BASE_FONT_PX;
/** 기준 크기의 px → rem 글자(바탕 글자 크기를 따라간다) */
export const rem = (px: number): string => `${px / BASE_FONT_PX}rem`;
/** 배치에 쓰는 너비: 화면 너비 w를 배율로 나눈 값(그림은 이 너비로 배치된 뒤 배율만큼 커진다) */
export const layoutWidth = (w: number, s: number): number => w / s;
/** 그림 뿌리의 모양: 1/배율 크기로 배치하고 왼쪽 위를 기준으로 배율만큼 키운다(화면에서는 부모 크기 그대로) */
export const scaledBox = (s: number): CSSProperties => ({ width: `${100 / s}%`, height: `${100 / s}%`, transform: `scale(${s})`, transformOrigin: "0 0" });

const read = () => rootScale(getComputedStyle(document.documentElement).fontSize);
const watch = (fn: () => void) => {
  const mo = new MutationObserver(fn);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-scale"] });
  addEventListener("resize", fn);
  return () => { mo.disconnect(); removeEventListener("resize", fn); };
};
/** 지금 배율 = 바탕 글자 px / 16. 창 크기와 html[data-scale]이 바뀌면 다시 읽는다 */
export const useRootScale = (): number => useSyncExternalStore(watch, read, () => NO_SCALE);
