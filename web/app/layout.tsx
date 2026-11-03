import type { Metadata } from "next";
import "../styles/dashboard.css";

export const metadata: Metadata = {
  title: "Data Loupe · 대시보드",
};

// 화면 모드(옛 head.html과 같다): 저장된 설정(쿠키 theme)이 있으면 그것, 없으면 Windows 설정. 그리기 전에 정해 깜빡임을 막는다(Flask가 nonce를 붙인다).
const THEME = `(() => { const m = document.cookie.match(/(?:^|; )theme=([^;]*)/), v = m && m[1].replace(/^"(.*)"$/, "$1");
  document.documentElement.dataset.theme = v || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); })();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        {/* 색 토큰·단추·카드 규칙은 옛 화면과 같은 파일을 Flask(/static)에서 그대로 쓴다(복사 없음, 색이 한 곳) */}
        {/* eslint-disable @next/next/no-css-tags */}
        <link rel="stylesheet" href="/static/core/common.css" />
        <link rel="stylesheet" href="/static/core/dash_edit.css" />
        {/* eslint-enable @next/next/no-css-tags */}
        <script dangerouslySetInnerHTML={{ __html: THEME }} />
      </head>
      <body className="app">{children}</body>
    </html>
  );
}
