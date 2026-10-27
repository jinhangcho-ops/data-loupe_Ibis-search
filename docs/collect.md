# 수집

[← README](../README.md) · [쿼리](query.md) · [분석](analysis.md) · [수집](collect.md) · [연동](integrations.md) · [대시보드](dashboard.md)

인터넷의 데이터(웹 API, 파일 주소, 웹페이지)를 받아 내 PC에 표 파일(Parquet·CSV)로 저장하는 화면입니다. 받은 결과는 DB 테이블이나 Kafka 토픽에 넣을(적재) 수 있고, 정한 시각에 저절로 돌게 예약할 수 있습니다. 오른쪽 **수집 설정**의 ①~⑦을 채우고 **[수집 시작]**을 누릅니다.

![수집 화면: 요청 예시로 채우기와 오른쪽 수집 설정](photo/collect-api-paste.png)

## 기능 설명

| 기능 | 한 줄 설명 | 자세히 |
|---|---|---|
| API 수집 | curl 예시를 붙여 넣어 요청을 만들고, 시험 보내기로 기록 위치를 골라 쪽마다 받기. 파일 주소로 받기도 여기에 | [functions/collect_api.md](functions/collect_api.md) |
| 웹페이지 수집 | 수집용 창에서 받을 값을 눌러 컬럼을 만들고 여러 쪽을 받기 | [functions/collect_web.md](functions/collect_web.md) |
| 적재·업로드 기록 | 결과를 DB 테이블·Kafka 토픽에 넣고, 넣은 것을 장부로 남겨 그 적재분만 지울 명령 보기 | [functions/load.md](functions/load.md) |
| 예약 | 저장한 구성을 매일·매주·N분마다 프로그램 안에서나 Windows 예약 작업으로 돌리고, 결과를 알림으로 받기 | [functions/schedule.md](functions/schedule.md) |
| 보낼 곳(알림) | 예약 결과를 받을 Slack·Notion·웹훅 등록 | [functions/integrations_send.md](functions/integrations_send.md) |
