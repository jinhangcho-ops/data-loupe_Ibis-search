import type { Metadata } from "next";

export const metadata: Metadata = { title: "Data Loupe · 차트" };

/** 차트 섬(iframe): 위쪽 줄·탭 없이 그림만 */
export default function ChartLayout({ children }: { children: React.ReactNode }) {
  return children;
}
