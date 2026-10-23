# 함수 목록

[← README](../README.md) · [쿼리](query.md) · [분석](analysis.md) · [대시보드](dashboard.md) · [설정과 참고](settings.md)

집계·계산을 고르는 모든 칸(탐색의 차트·피벗·계산, 분석, 전처리의 계산 컬럼·중복 기준, 대시보드 카드)은 **같은 함수 목록**에서 고릅니다. 코드로는 `core/sql_funcs.py`이고, 화면은 `GET /api/funcs`로 받아 칸을 그립니다. 함수 하나는 이름·한 줄 설명·예시·넣을 값(구분자, 정렬 기준, N, 분위수 P …)이 정해져 있어, 고르면 필요한 칸이 저절로 나타납니다.

## 집계 (묶음마다 값 하나)

차트의 Y, 피벗의 값, 카드의 값, 지표 파일의 `agg`, 분석의 그룹 비교·추이에서 고릅니다. 값 컬럼이 숫자여야 하는 것(합계·평균·분위수·표준편차 …)은 숫자 컬럼만 보입니다.

| 종류 | 함수 |
|---|---|
| 세기 | 개수 `count` · 고유 개수 `count_distinct`(정확) · 고유 개수(근사) `approx_count_distinct`(큰 표에서 빠름) · 조건 개수 `count_if` |
| 합·평균 | 합계 `sum` · 평균 `mean` · 조건부 합계/평균 `sum_if` `mean_if` · 최소 `min` · 최대 `max` · 범위 `range` |
| 가운데·위치 | 중앙값 `median` · 분위수 `quantile`(P를 0~1로) · `p50` `p90` `p95` `p99` · 최빈값 `mode` |
| 퍼짐·관계 | 표준편차·분산(표본 `std` `var`, 모집단 `std_pop` `var_pop`) · 상관계수 `corr` · 공분산(`cov` `cov_pop`) |
| 모으기 | 이어 붙이기 `string_agg`(구분자·정렬 기준·중복 빼기) · 목록 모으기 `collect` · 모두 참 `all` · 하나라도 참 `any` |
| 기준 행의 값 | 가장 작은/큰 쪽의 값 `arg_min` `arg_max` · 첫 값 `first` · 마지막 값 `last` (정렬 기준 필요) |

**겹치는 값 중 하나 고르기**: 같은 묶음에 값이 여럿일 때 하나로 정하는 방법입니다. 집계 칸의 "겹치는 값 중 하나 고르기" 묶음, 전처리 ⑤ 중복 기준의 **[남길 것]**에서 같은 선택지가 나옵니다: 아무 값 `any_value` · 정렬 기준의 첫 값·마지막 값 · 최소 · 최대 · 최빈값 · 모두 이어 붙이기(구분자) · **여러 개면 알리기** `unique_or_null`(값이 하나뿐이면 그 값, 둘 이상이면 빈 값. 중복 세기에서 값이 여러 개인 묶음 수도 보여 줍니다).

## 창 함수 (행마다 값 하나, 정렬 기준·묶음 기준)

탐색 ⑥ 계산, 전처리 계산 컬럼에서 씁니다. 행 번호 `row_number` · 순위 `rank` · 밀집 순위 `dense_rank` · 백분위 순위 `percent_rank` · 누적 분포 `cume_dist` · N등분 `ntile` · 이전/다음 값 `lag` `lead`(N행, 없을 때 값) · 묶음의 첫/마지막/N번째 값 `first_value` `last_value` `nth_value` · 누적 합·평균·최소·최대 `cum_sum` `cum_mean` `cum_min` `cum_max` · 이동 합·평균·최소·최대 `moving_sum` `moving_mean` `moving_min` `moving_max`(최근 N행 또는 N일) · 묶음 안 비율 `share_of_group` · 이전 대비 차이·변화율 `diff` `pct_change`.

## 행마다 계산하는 함수

계산 줄의 `식`, 전처리의 계산 컬럼에서 `함수(컬럼, 값…)` 모양으로 씁니다(예: `substr(이름, 1, 2)`, `case(금액 >= 100, '고액', 금액 >= 10, '중간', '소액')`).

| 종류 | 함수 |
|---|---|
| 글자 | `length` `upper` `lower` `capitalize` `trim` `ltrim` `rtrim` `substr`(시작·길이) `left` `right` `replace` `regex_contains` `regex_extract`(그룹) `regex_replace` `split`(목록) `split_part`(N번째) `concat` `lpad` `rpad` `contains` `startswith` `endswith` `find` `reverse` `repeat` |
| 숫자 | `round`(자릿수) `ceil` `floor` `abs` `mod` `power` `sqrt` `exp` `ln` `log10` `log`(밑) `sign` `clip`(하한·상한) `bucket`(경계 목록 → 라벨) `safe_divide`(0으로 나누면 빈 값) |
| 날짜 | `truncate`(연·분기·월·주·일·시·분·초) `year` `quarter` `month` `week` `day` `weekday` `hour` `minute` `second` `epoch` `date_add` `date_sub` `date_diff` `strftime` `parse_date` `to_date` `to_time` `diff_now` `diff_today` |
| 조건 | `case` `coalesce` `nullif` `ifelse` `greatest` `least` `isin` `between` `isnull` `notnull` |
| 형 변환 | `cast`(안 되면 오류) · `try_cast`(안 되면 빈 값) |
| 구조 | `json_get`(경로 `a.b[0]`) · `struct_field` · `array_length` · `array_get` · `unnest`(목록 펼치기) |

## 묶음

`having`(집계한 뒤 거르기: 피벗의 **[집계 뒤 거르기]**) · `qualify`(창 결과로 거르기: 계산 줄 `창거르기`) · `rollup` `cube` `grouping_sets`(소계·총계 줄: 피벗의 **[소계·총계]**, 소계 줄의 칸은 `(소계)`, 총계는 `(전체)`) · `distinct`(중복 빼기).

## 계산 줄 만들기 (탐색 ⑥ 계산)

[계산] 단계의 폼에서 종류를 고르면 필요한 칸만 나타나고, **[줄 만들어 넣기]**가 줄을 서버에 검사받아 목록에 넣습니다. 목록의 줄을 직접 써도 됩니다.

| 종류 | 줄 모양 |
|---|---|
| 창 함수 | `lag 금액 1 기준 주문일 그룹 매장 이름 전날금액` · `dense_rank 기준 금액 desc` |
| 식 | `식 금액 * 수량 이름 합계금액` · `식 median(금액) 그룹 매장 이름 매장중앙값` |
| SQL식 | `SQL식 DATE_TRUNC('month', 주문일) 이름 월` (아래 참고) |
| 창 결과로 거르기 | `창거르기 rank <= 3 기준 금액 desc 그룹 매장` |

**SQL 식 직접 쓰기**는 목록에 없는 DB 고유 함수가 필요할 때 씁니다. 식 한 줄이 새 컬럼이 되고, 읽기만 하는 식이어야 합니다(파일·환경 읽기, 여러 문장은 막힙니다). 파일 연결은 이 PC 사본에서 계산합니다.

## 이 연결이 못 하는 함수

테이블을 고르면 지금 연결이 각 함수를 하는지 미리 확인합니다(실행하지 않고 SQL만 만들어 봅니다). 고르는 칸에서 이렇게 보입니다.

| 표시 | 뜻 |
|---|---|
| 그대로 | 이 연결이 바로 계산합니다 |
| `· 대체 식` | 같은 결과가 나오는 다른 식으로 계산합니다(안내 글에 방법이 나옵니다: 예 상관계수 = 공분산 ÷ 표준편차 두 개) |
| `· 이 PC에서 계산` | 이 연결이 못 해서 기간·조건까지의 결과를 이 PC에 받아 계산합니다(큰 표는 처리 용량을 먼저 묻고, 너무 크면 막습니다) |
| 흐리게(고를 수 없음) | 지금 표에 맞는 종류의 컬럼이 없습니다(마우스를 올리면 이유) |

**함수 찾기**: 계산·계산 컬럼의 **[함수 찾기]**에서 분류·이름·설명으로 찾고, 예시를 누르면 식 칸에 들어갑니다.
