import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Data Loupe 대시보드",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body style={{ fontFamily: '"Segoe UI", "Malgun Gothic", sans-serif' }}>{children}</body>
    </html>
  );
}
