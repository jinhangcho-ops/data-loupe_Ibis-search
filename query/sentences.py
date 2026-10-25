"""문장 틀 데이터(6-4, 10-4): 탐색 틀 1~50번(48~50은 좌표 틀)과 지표 틀 1~15번. 틀은 데이터로만 두고(계산은 하지 않는다) GET /api/sentences(flow_app.py)로 화면에 보낸다(모든 화면에 싣지 않으려고 CONSTANTS에 넣지 않는다).
- 틀 하나 = {id, kind(explore·metric), no, name, text(빈칸을 {이름}으로 쓴 문장), slots: [빈칸], uses: [함수 사전 id], extends(변형이면 원래 틀 번호), preset(변형이 정해 둔 빈칸 값)}.
  어느 틀이든 앞에 {기간}을 붙일 수 있다(PERIOD, 틀마다 period: True).
- 빈칸 = {name, type, label, kinds(컬럼 종류 제한), options(고르는 값), default, optional}. 빈칸 타입은 SLOT_TYPES.
- 함수 사전과의 이음: funcs_for(틀)가 그 틀이 쓸 수 있는 sql_funcs id를 준다. {집계} 칸은 집계 함수 전부, {가공} 칸은 행 함수(글자·숫자·날짜·조건·형 변환·구조) 전부,
  {창} 칸은 창 함수 전부를 받고, 그 밖의 함수(QUALIFY·소계·펼치기 …)는 틀이 uses에 적는다. 사전의 모든 함수가 적어도 한 틀에서 쓰이는지는 unused가 알려 준다."""
from core import sql_funcs

SLOT_TYPES = ("column", "columns", "value", "values", "text_match", "number", "period", "agg", "unit", "metric", "order", "n",
              "cond", "choice", "table", "text", "transform", "window")  # 빈칸 타입
PERIOD = {"name": "기간", "type": "period", "label": "기간(선택)", "optional": True}
NUM, TXT, DAT, ANY = "num", "text", "date", "any"
SPECIAL = {"agg": "집계", "transform": "가공", "window": "창"}  # 함수 사전 전체를 받는 빈칸 타입 → 보이는 이름


def slot(name, type, label=None, kinds=ANY, options=None, default=None, optional=False, when=None):
    """빈칸 하나. when = {다른 빈칸 이름: [그 값들]}이면 그 빈칸이 그 값일 때만 보인다(문장 글에 {이름}이 없는 곁가지 칸)."""
    return {"name": name, "type": type, "label": label or name, "kinds": kinds, "options": options or [], "default": default, "optional": optional, "when": when or {}}


def column(name, label=None, kinds=ANY, optional=False):
    return slot(name, "column", label, kinds, optional=optional)


def choice(name, options, default=None, label=None):
    return slot(name, "choice", label, options=options, default=default or options[0])


def temp(no, name, text, slots=(), uses=(), kind="explore", **extra):
    """틀 하나. no는 종류 안의 번호(탐색 1~50, 지표 1~15)."""
    return {"id": f"{'e' if kind == 'explore' else 'm'}{no:02d}", "kind": kind, "no": no, "name": name, "text": text, "slots": list(slots), "uses": list(uses),
            "period": True, **extra}


G, V, N_, T_ = column("무엇", "나눌 기준 컬럼"), column("값", "값 컬럼", NUM), slot("N", "n", "N", default=10), column("대상", "대상(사람·물건) 컬럼")
AGG = slot("집계", "agg", "집계")
COND = slot("조건", "cond", "조건")
ORDER = slot("정렬 기준", "order", "정렬 기준 컬럼")
UNIT = slot("단위", "unit", "시간 단위", options=["day", "week", "month", "year"], default="day")
METRIC_A, METRIC_B = slot("지표 A", "metric", "지표 A"), slot("지표 B", "metric", "지표 B")

EXPLORE = [
    temp(1, "모든 데이터 보기", "모든 데이터를 봐요"),
    temp(2, "컬럼들만 보기", "{컬럼들}만 봐요(중복 {중복})", [slot("컬럼들", "columns"), choice("중복", ["빼고", "두고"], "두고")], ["distinct"]),
    temp(3, "컬럼=값인 행의 컬럼들", "{컬럼}이(가) {값}인 행의 {컬럼들}을 봐요", [column("컬럼"), slot("값", "value"), slot("컬럼들", "columns")]),
    temp(4, "값 목록 중 하나인 행", "{컬럼}이(가) {값 목록} 중 하나인 행을 봐요", [column("컬럼"), slot("값 목록", "values")], ["isin"]),
    temp(5, "글자 포함·시작·끝", "{컬럼}이(가) {글자}하는 행을 봐요",
         [column("컬럼", kinds=TXT), slot("글자", "text_match", options=["포함", "으로 시작", "으로 끝"], default="포함")], ["contains", "startswith", "endswith"]),
    temp(6, "행 수", "행이 몇 개인지 세요", [], ["count"]),
    temp(7, "조건인 대상 수", "{조건}인 {대상}이(가) 몇 개인지 세요", [COND, T_], ["count_distinct"]),
    temp(8, "무엇별 대상 수", "{무엇}별 {대상} 수를 세요", [G, T_], ["count_distinct"]),
    temp(9, "무엇별 수 중 N 이상 많은 순", "{무엇}별 건수가 {N} 이상인 것을 많은 순으로 봐요", [G, slot("N", "n", "N 이상", default=2)], ["count", "having"]),
    temp(10, "조건인 행에서 무엇별 수 많은 순", "{조건}인 행에서 {무엇}별 건수를 많은 순으로 봐요", [COND, G], ["count", "having"]),
    temp(11, "두 번 이상 나오는 값", "두 번 이상 나오는 {컬럼} 값을 찾아요", [column("컬럼")], ["count", "having"]),
    temp(12, "값 목록 각각의 건수", "{컬럼}이(가) {값 목록} 각각인 행이 몇 건인지 봐요", [column("컬럼"), slot("값 목록", "values")], ["count", "isin"]),
    temp(13, "무엇별 값 집계 상위 N", "{무엇}별 {값}의 {집계} 상위 {N}개를 봐요", [G, slot("값", "column", "값 컬럼"), AGG, slot("N", "n", "N", default=10)]),
    temp(14, "무엇별 고유 대상 수", "{무엇}별 고유 {대상} 수를 {정확도}로 구해요", [G, T_, choice("정확도", ["정확", "빠르게(근사)"], "정확")],
         ["count_distinct", "approx_count_distinct"]),
    temp(15, "단계 값마다 고유 대상 수", "{단계}의 값마다 고유 {대상} 수를 봐요", [column("단계", "단계 컬럼"), T_], ["count_distinct"]),
    temp(16, "테이블A 중 테이블B에 이어진 행이 가장 많은 것", "{테이블 A} 중 {테이블 B}에 {연결 컬럼}으로 이어진 행이 가장 많은 것을 봐요",
         [slot("테이블 A", "table"), slot("테이블 B", "table"), column("연결 컬럼", "이어 주는 컬럼")], ["count", "rank"]),
    temp(17, "전체 중 조건 비율", "전체 중 {조건}인 비율을 구해요", [COND], ["count", "count_if"]),
    temp(18, "무엇별 수와 전체 대비 비중", "{무엇}별 수와 전체 대비 비중을 봐요", [G], ["count", "share_of_group"]),
    temp(19, "무엇별 조건을 한 대상 비율", "{무엇}별로 {조건}을 한 {대상}의 비율을 구해요", [G, COND, T_], ["count_distinct"]),
    temp(20, "무엇별 대상 1명당 평균", "{무엇}별 {대상} 1명당 평균 {값}을 구해요", [G, T_, slot("값", "column", "값 컬럼", NUM)], ["sum", "count_distinct"]),
    temp(21, "지표 A ÷ 지표 B", "{지표 A} ÷ {지표 B}를 구해요", [METRIC_A, METRIC_B]),
    temp(22, "기간 동안 단위마다 고유 대상 수", "{날짜}의 {단위}마다 고유 {대상} 수를 봐요", [UNIT, T_, column("날짜", "날짜 컬럼", DAT)], ["count_distinct", "truncate"]),
    temp(23, "단위마다 대상 수와 대상당 평균", "{날짜}의 {단위}마다 {대상} 수와 {대상}당 평균 {값}을 봐요", [UNIT, T_, slot("값", "column", "값 컬럼", NUM), column("날짜", "날짜 컬럼", DAT)],
         ["count_distinct", "sum", "truncate"]),
    temp(24, "대상마다 처음 컬럼 값 기준", "{대상}마다 {정렬 기준} 순으로 처음 나온 {컬럼} 값을 기준으로 {집계}를 봐요", [T_, column("컬럼"), ORDER, AGG], ["first", "arg_min"]),
    temp(25, "목록 컬럼을 펼쳐 무엇별 집계 상위 N", "{목록 컬럼}을 펼쳐 {무엇}별 {집계} 상위 {N}개를 봐요",
         [column("목록 컬럼", kinds="array"), G, AGG, slot("N", "n", "N", default=10)], ["unnest"]),
    # 10단계에서 더한 틀 26~47: 함수 사전의 모든 함수가 적어도 한 틀에서 쓰이게 한다
    temp(26, "중앙값·분위수", "{무엇}별 {값}의 {종류}를 구해요",
         [G, V, choice("종류", ["중앙값", "P50", "P90", "P95", "P99", "분위수 N"], "중앙값"), slot("P", "number", "분위수(0~1)", default=0.5, optional=True, when={"종류": ["분위수 N"]})],
         ["median", "p50", "p90", "p95", "p99", "quantile"]),
    temp(27, "퍼짐", "{무엇}별 {값}의 {종류}를 구해요", [G, V, choice("종류", ["표준편차", "분산", "범위"]), slot("기준", "choice", "기준", options=["표본", "모집단"], default="표본", when={"종류": ["표준편차", "분산"]})],
         ["std", "std_pop", "var", "var_pop", "range"]),
    temp(28, "최빈값", "{무엇}별로 가장 많이 나온 {컬럼} 값을 찾아요", [G, column("컬럼")], ["mode"]),
    temp(29, "대표 값", "{무엇}마다 {컬럼} 값을 하나로 정해요: {대표 값 고르기}",
         [G, column("컬럼"), choice("대표 값 고르기", list(sql_funcs.REPRESENTATIVE), "any_value"), slot("정렬 기준", "order", optional=True, when={"대표 값 고르기": ["first", "last", "string_agg"]}), slot("구분자", "text", default=",", optional=True, when={"대표 값 고르기": ["string_agg"]})],
         [v[1] for v in sql_funcs.REPRESENTATIVE.values()]),
    temp(30, "이어 붙이기", "{무엇}별 {컬럼} 값을 {구분자}로 이어 붙여요({정렬 기준} 순, 중복 {중복})",
         [G, column("컬럼"), slot("구분자", "text", default=","), slot("정렬 기준", "order", optional=True), choice("중복", ["빼고", "두고"], "두고")], ["string_agg"]),
    temp(31, "목록 모으기", "{무엇}별 {컬럼} 값을 목록으로 모아요", [G, column("컬럼")], ["collect"]),
    temp(32, "참거짓 집계", "{무엇}별로 {조건}이 {범위} 맞는지 봐요", [G, COND, choice("범위", ["모두", "하나라도"])], ["all", "any"]),
    temp(33, "첫·마지막 행", "{무엇}마다 {정렬 기준}이 {방향} 행을 봐요", [G, ORDER, choice("방향", ["가장 이른", "가장 늦은"])],
         ["first", "last", "arg_min", "arg_max", "row_number"]),
    temp(34, "순위 안", "{무엇}마다 {값} 순위가 {N}위 안인 행을 봐요", [G, slot("값", "column", "값 컬럼", NUM), slot("N", "n", "N", default=3)],
         ["row_number", "rank", "dense_rank", "qualify"]),
    temp(35, "이전 대비", "{무엇}마다 {정렬 기준} 순으로 {값}이 이전 행보다 얼마나 변했는지({보기}) 봐요", [G, ORDER, slot("값", "column", "값 컬럼", NUM), choice("보기", ["차이", "변화율", "이전 값", "다음 값"])],
         ["lag", "lead", "diff", "pct_change"]),
    temp(36, "누적·이동", "{정렬 기준} 순으로 {값}의 {종류}을 봐요",
         [ORDER, slot("값", "column", "값 컬럼", NUM), choice("종류", ["누적 합", "누적 평균", "누적 최소", "누적 최대", "이동 합", "이동 평균", "이동 최소", "이동 최대"]), slot("N", "n", "이동 크기", default=3, optional=True, when={"종류": ["이동 합", "이동 평균", "이동 최소", "이동 최대"]})],
         ["cum_sum", "cum_mean", "cum_min", "cum_max", "moving_sum", "moving_mean", "moving_min", "moving_max"]),
    temp(37, "묶음 안 비율", "{무엇} 안에서 {나눌 것}별 {값}이 차지하는 비율을 봐요", [G, column("나눌 것"), slot("값", "column", "값 컬럼", NUM)], ["share_of_group", "sum"]),
    temp(38, "백분위", "{값}이 상위 몇 %인지 행마다 {방식}으로 봐요", [slot("값", "column", "값 컬럼", NUM), choice("방식", ["백분위 순위", "누적 분포", "N등분"]), slot("N", "n", "구간 수", default=4, optional=True, when={"방식": ["N등분"]})],
         ["percent_rank", "cume_dist", "ntile", "first_value", "last_value", "nth_value"]),
    temp(39, "소계·총계", "{무엇}·{나눌 것}별 {값}의 {집계}와 {방식}을 봐요", [G, column("나눌 것"), slot("값", "column", "값 컬럼", NUM), AGG, choice("방식", ["소계·총계", "모든 조합(CUBE)", "고른 조합(GROUPING SETS)"])],
         ["rollup", "cube", "grouping_sets", "having"]),
    temp(40, "새 컬럼(가공)", "{새 이름}은 {컬럼}을 {가공}한 값이에요", [slot("새 이름", "text"), column("컬럼"), slot("가공", "transform", "가공")]),
    temp(41, "조건 분류", "{조건1}이면 {값1}, {조건2}이면 {값2}, 아니면 {값3}으로 {새 이름}을 만들어요",
         [slot("조건1", "cond"), slot("값1", "value"), slot("조건2", "cond", optional=True), slot("값2", "value", optional=True), slot("값3", "value"), slot("새 이름", "text")],
         ["case", "ifelse", "bucket", "between", "isnull", "notnull", "nullif", "coalesce", "greatest", "least"]),
    temp(42, "날짜 차이", "{날짜 A}와 {날짜 B} 사이가 {단위}로 얼마인지 봐요", [column("날짜 A", kinds=DAT), column("날짜 B", kinds=DAT), slot("단위", "unit", "시간 단위", options=["day", "hour", "minute"], default="day")],
         ["date_diff", "diff_now", "diff_today"]),
    temp(43, "JSON 꺼내기", "{JSON 컬럼}에서 {경로} 값을 꺼내 봐요", [column("JSON 컬럼", kinds="json"), slot("경로", "text")], ["json_get", "struct_field", "array_length", "array_get"]),
    temp(44, "형 변환", "{컬럼}을 {바꿀 형}로 바꿔요(안 되는 값은 빈 값으로)", [column("컬럼"), choice("바꿀 형", ["숫자", "날짜", "글자"])], ["cast", "try_cast"]),
    temp(45, "함께 움직임", "{값 A}와 {값 B}가 함께 움직이는 정도를 {종류}으로 구해요",
         [slot("값 A", "column", "값 A", NUM), slot("값 B", "column", "값 B", NUM), choice("종류", ["상관계수", "공분산"]), slot("기준", "choice", "기준", options=["표본", "모집단"], default="표본", when={"종류": ["공분산"]})], ["corr", "cov", "cov_pop"]),
    temp(46, "근사 고유 개수", "{무엇}별 고유 {대상} 수를 빠르게(근사) 어림해요", [G, T_], ["approx_count_distinct"], extends=14, preset={"정확도": "빠르게(근사)"}),
    temp(47, "SQL 식 직접", "{새 이름}은 SQL 식 {식}의 값이에요", [slot("새 이름", "text"), slot("식", "text", "SQL 식")], [sql_funcs.RAW]),
    # 좌표 틀 48~50: 값 칸은 개수면 비워 둔다
    temp(48, "영역별 값", "{영역}별 {값}의 {집계}를 봐요", [column("영역", "영역 이름 컬럼"), slot("값", "column", "값 컬럼", optional=True), AGG]),
    temp(49, "기준점 반경 안", "{위도}·{경도}로 기준점 {기준 위도}, {기준 경도}에서 {반경}km 안의 {값}의 {집계}를 봐요",
         [column("위도", "위도 컬럼", NUM), column("경도", "경도 컬럼", NUM), slot("기준 위도", "number", "기준 위도", default=37.5665), slot("기준 경도", "number", "기준 경도", default=126.978),
          slot("반경", "number", "반경(km)", default=5), slot("값", "column", "값 컬럼", optional=True), AGG], ["within_km"]),
    temp(50, "격자별 값", "{위도}·{경도}를 {칸 크기}도 칸으로 나눈 격자별 {값}의 {집계}를 봐요",
         [column("위도", "위도 컬럼", NUM), column("경도", "경도 컬럼", NUM), slot("칸 크기", "number", "칸 크기(도)", default=0.01), slot("값", "column", "값 컬럼", optional=True), AGG], ["grid_id", "grid_lat", "grid_lon"]),
]

METRICS = [
    temp(1, "행 수", "행 수", [], ["count"], "metric"),
    temp(2, "고유 대상 수", "고유 {대상} 수", [T_], ["count_distinct"], "metric"),
    temp(3, "값의 집계", "{값}의 {집계}", [slot("값", "column", "값 컬럼"), AGG], [], "metric"),
    temp(4, "처음 값 기준 대상 수", "처음({정렬 기준} 순) {컬럼} 값이 {값}인 {대상} 수", [column("컬럼"), slot("값", "value"), T_, ORDER], ["first", "count_distinct"], "metric"),
    temp(5, "A÷B", "{지표 A} ÷ {지표 B}", [METRIC_A, METRIC_B], [], "metric"),
    temp(6, "A가 B에서 차지하는 %", "{지표 A}가 {지표 B}에서 차지하는 %", [METRIC_A, METRIC_B], [], "metric"),
    temp(7, "A−B", "{지표 A} − {지표 B}", [METRIC_A, METRIC_B], [], "metric"),
    temp(8, "A+B", "{지표 A} + {지표 B}", [METRIC_A, METRIC_B], [], "metric"),
    temp(9, "대상 하나당", "{값} 전체 합 ÷ {대상} 수", [slot("값", "column", "값 컬럼", NUM), T_], ["sum", "count_distinct"], "metric"),
    temp(10, "대상마다 구한 값의 평균", "{대상}마다 구한 {값}의 {집계}의 평균", [T_, slot("값", "column", "값 컬럼"), AGG], ["mean"], "metric"),
    temp(11, "N번 이상 한 대상 비율", "{조건}을 {N}번 이상 한 {대상}의 비율", [COND, slot("N", "n", "N", default=2), T_], ["count_if", "count_distinct", "having"], "metric"),
    temp(12, "단계 A 중 단계 B까지 간 비율", "{단계 A}를 한 {대상} 중 {단계 B}까지 간 비율", [slot("단계 A", "cond"), slot("단계 B", "cond"), T_], ["count_distinct"], "metric"),
    temp(13, "단위마다 구한 지표의 평균", "{단위}마다 구한 {지표}의 평균", [UNIT, slot("지표", "metric")], ["mean"], "metric"),
    temp(14, "직전 같은 기간 대비 변화율", "{지표}의 직전 같은 기간 대비 변화율", [slot("지표", "metric")], ["pct_change"], "metric"),
    temp(15, "누적", "{지표}의 누적({정렬 기준} 순)", [slot("지표", "metric"), ORDER], ["cum_sum"], "metric"),
]
TEMPLATES = EXPLORE + METRICS


def get(id):
    """id("e05", "m03")의 틀. 없으면 ValueError."""
    for t in TEMPLATES:
        if t["id"] == id:
            return t
    raise ValueError(f"'{id}' 문장 틀이 없습니다.")


def row_ids():
    """{가공} 칸이 받는 함수: 행마다 계산하는 함수 전부(글자·숫자·날짜·조건·형 변환·구조)."""
    return [i for i, f in sql_funcs.FUNCS.items() if not (f["aggregate"] or f["window"] or f["table"] or f.get("raw"))]


def funcs_for(template):
    """틀(또는 id)이 쓸 수 있는 함수 사전 id들. uses에 적은 것 + {집계} 칸이면 집계 함수 전부 + {가공} 칸이면 행 함수 전부 + {창} 칸이면 창 함수 전부."""
    t = get(template) if isinstance(template, str) else template
    found = list(t["uses"])
    for s in t["slots"]:
        if s["type"] == "agg":
            found += sql_funcs.aggregate_ids()
        elif s["type"] == "transform":
            found += row_ids()
        elif s["type"] == "window":
            found += [i for i, f in sql_funcs.FUNCS.items() if f["window"]]
    return list(dict.fromkeys(found))


def uses_map():
    """{틀 id: [쓸 수 있는 함수 id]} 전체."""
    return {t["id"]: funcs_for(t) for t in TEMPLATES}


def catalog():
    """화면에 보내는 문장 틀 {period, slot_types, special, templates, uses}."""
    return {"period": PERIOD, "slot_types": list(SLOT_TYPES), "special": SPECIAL, "templates": TEMPLATES, "uses": uses_map()}
