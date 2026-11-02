# web/ (Data Loupe 대시보드, Next.js 정적 내보내기)

- 빌드: `cd web && npm install && npm run build` (`next build`). 결과는 `web/out/`(예: `web/out/dashboard.html`, `web/out/_next/...`)에 그대로 둔다.
- Flask가 `web/out/`을 `/next/...`로 내준다(core/server.py `next_asset`, `assetPrefix: "/next"`). `static/` 안에 두지 않는 이유: 화면 파일을 훑는 시험·검사가 빌드 결과까지 읽는다.
- exe: DataLoupe.spec이 `web/out`이 있으면 함께 넣는다(없으면 옛 대시보드 화면으로 돈다).
- 개발 서버(`next dev`)는 쓰지 않는다. 앱은 오프라인에서 돌아 외부 글꼴을 받지 않는다.
- Next 16은 관례(App Router 파일 구조·API)가 이전 판과 다르다. 고치기 전에 `node_modules/next/dist/docs/01-app/`를 먼저 본다.
- Flask 쪽: `/dashboard`는 `web/out/dashboard.html`이 있으면 그것을 내주고(`?legacy=1`이면 옛 화면), 모든 `<script`와 `<link rel="preload" as="script">`에 nonce를 붙여 CSP(`'strict-dynamic'`)를 통과시킨다(core/server.py `html_response`). 상수는 `GET /api/dash/constants`.
