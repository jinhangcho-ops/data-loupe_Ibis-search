# 분석

[← README](../README.md) · [쿼리](query.md) · [분석](analysis.md) · [수집](collect.md) · [연동](integrations.md) · [대시보드](dashboard.md)

지저분한 데이터를 다듬고(전처리), 다듬은 데이터가 어떻게 생겼는지 그래프로 살펴보는(분석) 화면입니다. 원본 테이블은 바꾸지 않고, 정리한 결과는 새 파일이나 같은 DB의 새 테이블로 따로 저장합니다. 위쪽 **[전처리]**·**[분석]** 탭으로 오갑니다.

![전처리 화면: 오른쪽 전처리 조건과 가운데 미리보기 표](photo/prep-columns.png)

## 기능 설명

| 기능 | 한 줄 설명 | 자세히 |
|---|---|---|
| 전처리 | 컬럼 정리·새 컬럼·중복 빼기·정렬을 고르고, 미리 본 뒤 파일이나 새 테이블로 저장 | [functions/prep.md](functions/prep.md) |
| 분석 탭 | 분포·상관관계·그룹 비교·시계열 추이를 그래프와 표로 보고 대시보드에 넣기 | [functions/analysis_tab.md](functions/analysis_tab.md) |
| 흐름(퍼널) | 정해 둔 단계를 순서대로 거쳐 간 사람 수와 빠진 곳을 생키 그림과 표로 | [functions/funnel.md](functions/funnel.md) |
| 기억되는 것·안전 | 표본 행 수, 지난번 전처리, 저장한 구성이 어디에 남는지 | [functions/settings.md](functions/settings.md) |
