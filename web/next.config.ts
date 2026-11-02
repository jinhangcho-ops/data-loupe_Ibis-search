import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  // 빌드 결과 web/out/ 을 Flask가 /next/... 로 그대로 내준다(static/ 안에 두면 화면 파일을 훑는 시험·검사에 걸린다).
  assetPrefix: "/next",
  trailingSlash: false,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
