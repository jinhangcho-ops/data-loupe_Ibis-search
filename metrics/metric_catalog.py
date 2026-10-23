"""지표 사전(7-2): 8개 영역의 지표 항목을 데이터로 두고, 테이블에서 쓸 수 있는지(available) 알려 주고, 현재 연결에서 구한다(compute). 화면은 catalog()를 받아 보여 준다.
- 항목 = {id, area, name, abbr, help(실무 해석 한 줄), formula(식), sentence(빈칸이 든 문장), slots(빈칸, 문장 틀 빈칸과 같은 모양 + role), calc(metric_kernels의 계산 이름), params(그 계산의 고정 값),
  pair(짝 절대량 id들 — 비율 지표는 분자·분모를 함께 보인다), spread(평균형이면 중앙값·P90·P99를 함께), needs(필요한 컬럼), constants(사용자가 적는 상수 빈칸), format(number·percent), unit, better, hint(north_star·counter·guardrail 후보),
  window(창 함수 일이라 원격·파일 연결은 이 PC 사본에서), cross(광고비 같은 다른 테이블 값을 써서 맞춤 구절이 있어야 하는 지표)}.
- 3원칙은 자료로만 준다: 비율 지표는 pair로 짝 절대량, 평균형은 spread로 중앙값·P90·P99, 북극성·카운터·가드레일은 hint(대시보드의 north_star·counters·limits를 그대로 쓰고 여기서 따로 저장하지 않는다. 한계 확인은 check_limit).
- compute는 sentence_exec와 같은 규칙으로 현재 연결에서 구한다: 내 지표로 저장한 항목은 부르는 쪽이 scope를 먼저 검사하고(sentence_exec.check_scope), 원격 DB는 값을 읽는 조회만 보낸다(처리 용량 확인 scan_gate와
  이 PC 사본 만들기는 라우트 catalog_app이 한다). 다른 테이블이 필요한 지표는 맞춤 구절(기간 단위 또는 공통 키)이 없으면 이유와 함께 거부한다(6-5)."""
from query import flow
from core import parse
from query import sentence_run
from core import sql_funcs
from query.sentences import slot

UNITS = ("day", "week", "month")  # 기간 묶음 단위(metric_calc.UNITS와 같다. 계산 모듈은 numpy를 올리므로 시작할 때 불러오지 않는다)
MAX_N = 3650  # 정수 칸(N·일수·K 등)의 상한. 크게 적으면 코호트·곡선 표가 터무니없이 길어진다
PREFIX = "catalog:"  # 내 지표의 template 값 접두사("catalog:uv")
AREAS = [{"id": "traffic", "name": "트래픽·활성도"}, {"id": "funnel", "name": "퍼널·온보딩"}, {"id": "retention", "name": "리텐션·코호트"}, {"id": "revenue", "name": "수익화·LTV"},
         {"id": "marketing", "name": "마케팅·UA"}, {"id": "economy", "name": "게임 경제"}, {"id": "commerce", "name": "이커머스"}, {"id": "model", "name": "실험·모델"}]
SPREAD = (("median", 0.5), ("p90", 0.9), ("p99", 0.99))
HINTS = ("north_star", "counter", "guardrail")

# 항목에 없는 짝 절대량(비율 지표의 분자·분모) 이름. 항목 id와 같은 이름은 항목 이름을 쓴다.
PARTS = {"users_first": "첫 단계(A)를 한 사용자 수", "users_reached": "다음 단계(B)까지 간 사용자 수", "sessions": "세션 수", "user_days": "접속한 사용자·날 수(일별 DAU의 합)",
         "stay_seconds": "총 체류 시간(초)", "views": "조회 수", "exits": "마지막 페이지로 끝난 세션 수", "first_step_users": "첫 단계 사용자 수", "last_step_users": "마지막 단계 사용자 수",
         "cohort_size": "코호트 크기(신규 사용자 수)", "churned": "이탈 사용자 수", "resurrected": "복귀 사용자 수", "new": "신규 사용자 수", "refund": "환불 금액", "repeat_buyers": "재구매자 수",
         "revenue_n": "N일 안 매출", "spend": "광고비", "new_users": "신규 사용자 수", "ltv": "LTV", "cac": "CAC", "arpu_month": "월 ARPU", "churn_rate": "월 이탈률", "clicks": "클릭 수",
         "installs": "설치 수", "conversions": "전환 수", "invites": "보낸 초대 수", "joined": "초대로 가입한 수", "paid_installs": "유료 신규 수", "organic_installs": "오가닉 신규 수",
         "faucet": "들어온 재화", "sink": "나간 재화", "active_money_supply": "활성 통화량", "active_users": "활성 사용자 수", "tries": "도전 수", "clears": "클리어 수", "picks": "선택 수", "wins": "승리 수",
         "pulled_users": "뽑은 사용자 수", "reached_users": "천장에 닿은 사용자 수", "fee_revenue": "수수료 수익", "orders": "주문 수", "buyers": "구매자 수", "searches": "검색 수",
         "zero_searches": "결과 0건 검색 수", "search_sessions": "검색한 세션 수", "exit_sessions": "검색에서 끝난 세션 수", "tp": "맞게 찾은 수(TP)", "fp": "잘못 찾은 수(FP)", "fn": "놓친 수(FN)",
         "control_mean": "대조군 평균", "treat_mean": "실험군 평균", "control_n": "대조군 표본 수", "treat_n": "실험군 표본 수", "queries": "질의 수", "items": "항목 수", "holders": "보유자 수",
         "prev_revenue": "지난달 매출", "kept_revenue": "그 고객의 이번 달 매출", "users": "사용자 수", "mrr": "MRR"}

# 계산(calc)마다 표를 읽는 조회 수의 어림(없으면 1). 사본 없이 원격에서 바로 구할 때 확인창에 "표를 약 N번 읽어요"로 알린다.
PASSES = {"uv": 2, "pv_per_uv": 2, "stickiness": 2, "time_between": 2, "conversion": 2, "ftue": 3, "retention": 4, "flattening": 4, "states": 4, "power_curve": 3, "survival": 2, "new_payers": 2,
          "pur": 4, "arpu": 2, "arppu": 2, "arpdau": 2, "ltv": 4, "nrr": 3, "cac": 4, "roas": 5, "payback": 5, "frequency": 2, "tiers": 2, "gini": 2, "aov": 2, "purchase_frequency": 2,
          "pareto": 2, "pity": 2, "stage_clear": 3, "organic_uplift": 2, "velocity": 5, "money_supply": 4}

# ---------- 빈칸 도서관: 역할마다 한 번만 적고 항목은 역할 이름으로 고른다 ----------
# (이름, 타입, 안내, 컬럼 종류, 이름 단서(없으면 종류만 본다), 단서가 꼭 있어야 하는지)
COLS = {
    "user": ("사용자", "사용자(사람) 컬럼", "any", ()),
    "time": ("시각", "시각(날짜) 컬럼", "date", ()),
    "amount": ("금액", "금액 컬럼", "num", ("amount", "revenue", "price", "pay", "sales", "금액", "매출", "결제", "가격")),
    "start": ("시작", "시작 시각 컬럼", "date", ("start", "begin", "login", "시작", "접속")),
    "end": ("끝", "끝 시각 컬럼", "date", ("end", "stop", "logout", "finish", "끝", "종료")),
    "event": ("이벤트", "이벤트 컬럼", "any", ("event", "action", "type", "이벤트", "행동")),
    "page": ("페이지", "페이지 컬럼", "any", ("page", "screen", "url", "path", "페이지", "화면")),
    "refund": ("환불 금액", "환불 금액 컬럼", "num", ("refund", "환불")),
    "orders": ("주문", "주문(결제) 번호 컬럼", "any", ("order", "txn", "transaction", "주문", "결제번호")),
    "spend": ("광고비", "광고비 컬럼", "num", ("spend", "cost", "광고비", "비용")),
    "impressions": ("노출 수", "노출 수 컬럼", "num", ("impression", "impr", "노출")),
    "clicks": ("클릭 수", "클릭 수 컬럼", "num", ("click", "클릭")),
    "installs": ("설치 수", "설치 수 컬럼", "num", ("install", "설치")),
    "conversions": ("전환 수", "전환 수 컬럼", "num", ("conversion", "action", "purchase", "전환")),
    "source": ("유입 경로", "유입 경로 컬럼", "any", ("source", "channel", "campaign", "media", "유입", "채널", "매체")),
    "invites": ("보낸 초대 수", "보낸 초대 수 컬럼", "num", ("invite", "referral", "초대")),
    "joined": ("초대로 가입한 수", "초대로 가입한 수 컬럼", "num", ("joined", "signup", "convert", "invite", "초대", "가입")),
    "delta": ("재화 변화량", "재화 변화량 컬럼(얻으면 +, 쓰면 −)", "num", ("delta", "change", "amount", "gold", "coin", "변화", "재화")),
    "balance": ("잔액", "재화 잔액 컬럼", "num", ("balance", "wallet", "잔액", "보유")),
    "value": ("값", "값 컬럼", "num", ()),
    "stage": ("스테이지", "스테이지(단계) 컬럼", "any", ("stage", "level", "스테이지", "단계", "레벨")),
    "result": ("결과", "결과 컬럼", "any", ("result", "outcome", "win", "clear", "결과", "승패")),
    "item": ("항목", "항목(캐릭터·상품) 컬럼", "any", ("item", "sku", "product", "character", "hero", "deck", "항목", "상품", "캐릭터")),
    "status": ("상태", "상태 컬럼", "any", ("status", "state", "상태")),
    "fee_col": ("수수료 수익", "수수료 수익 컬럼", "num", ("fee", "commission", "수수료")),
    "results": ("검색 결과 수", "검색 결과 수 컬럼", "num", ("result", "hits", "count", "결과")),
    "y_true": ("정답", "정답 컬럼", ["bool", "num"], ("label", "actual", "true", "target", "truth", "정답", "실제")),
    "y_pred": ("예측", "예측 컬럼", ["bool", "num"], ("pred", "predict", "예측")),
    "score": ("점수", "예측 점수(확률) 컬럼", "num", ("score", "prob", "점수", "확률")),
    "qid": ("질의", "질의(사용자·검색어) 컬럼", "any", ("query", "qid", "user", "search", "질의", "검색")),
    "rank": ("순위", "추천 순위 컬럼(작을수록 위)", "num", ("rank", "position", "순위")),
    "rel": ("관련도", "정답 관련도 컬럼(0이면 관련 없음)", "num", ("rel", "relevance", "label", "grade", "관련")),
    "group": ("실험군", "실험군(그룹) 컬럼", "any", ("group", "variant", "arm", "bucket", "experiment", "test", "실험", "그룹")),
    "metric": ("지표 값", "지표 값 컬럼(숫자 또는 참거짓)", ["num", "bool"], ()),
}
STRICT = {"spend", "impressions", "clicks", "installs", "conversions", "invites", "joined", "delta", "balance", "fee_col", "results", "y_true", "y_pred", "score", "group", "rank", "rel"}  # 이름 단서가 있어야 쓸 수 있다고 보는 역할
EXTRA = {  # 컬럼이 아닌 빈칸: 역할 → (이름, 타입, 안내, 기본값/옵션 등)
    "unit": slot("단위", "unit", "시간 단위", options=list(UNITS), default="day"),
    "gap": slot("세션 간격(분)", "number", "이 시간(분)을 넘게 쉬면 새 세션", default=30),
    "sess_cols": slot("세션 컬럼들", "columns", "세션을 가리키는 컬럼들(있으면 간격 대신 이것으로)", optional=True),
    "event_value": slot("이벤트 값", "value", "이 이벤트 값인 행만(비우면 모두)", optional=True),
    "step_a": slot("단계 A", "value", "시작 단계의 이벤트 값"),
    "step_b": slot("단계 B", "value", "도착 단계의 이벤트 값"),
    "steps": slot("단계들", "values", "단계 이벤트 값들(순서대로, 2개 이상)"),
    "limit_sec": slot("제한 시간(초)", "number", "첫 단계 뒤 이 시간 안에 끝낸 것만(비우면 제한 없음)", optional=True),
    "n": slot("N", "n", "경과 기간 N", default=7),
    "from_n": slot("범위 시작 N", "number", "범위의 시작 N", default=1),
    "to_n": slot("범위 끝 N", "number", "범위의 끝 N", default=7),
    "eps": slot("허용 오차(%p)", "number", "이어진 두 N의 차이가 이보다 작아야 수평", default=0.5),
    "run": slot("연속 횟수", "n", "수평이 이만큼 이어져야 해요", default=3),
    "inactive_days": slot("미접속 기준(일)", "n", "이만큼 안 오면 떠났다고 봐요(비우면 기간만 봐요)", optional=True),
    "churn_days": slot("미접속 기준(일)", "n", "마지막 활동이 자료의 끝보다 이만큼 앞서면 떠난 것", default=30),
    "span": slot("최근 일수", "n", "최근 며칠(보통 7 또는 28)", default=7),
    "fee": slot("수수료(%)", "number", "스토어·결제 수수료율", default=0),
    "vat": slot("부가세(%)", "number", "부가세율", default=0),
    "n_days": slot("N일", "n", "첫 활동일로부터 며칠 안", default=30),
    "method": slot("계산 방식", "choice", "계산 방식", options=["코호트 누적", "ARPU÷이탈률"], default="코호트 누적"),
    "mode": slot("기준 방식", "choice", "등급을 나누는 방식", options=["분위수", "금액"], default="분위수"),
    "whale": slot("Whale 기준", "number", "분위수면 상위 몇 %, 금액이면 이 금액 이상", default=1),
    "dolphin": slot("Dolphin 기준", "number", "분위수면 상위 몇 %, 금액이면 이 금액 이상", default=10),
    "paid_value": slot("유료 값", "value", "유료로 온 것을 뜻하는 유입 경로 값"),
    "ad_table": slot("광고 테이블", "table", "광고비가 든 테이블"),
    "ad_time": {**slot("광고 날짜", "text", "광고비 테이블의 날짜 컬럼(기간 단위로 맞출 때)", optional=True), "other": True},
    "max_days": slot("최대 일수", "n", "이 일수까지 본다", default=180),
    "active_days": slot("활성 기준(일)", "n", "자료의 마지막 날 기준 최근 이 일수 안에 활동한 사람", default=30),
    "basis": slot("기준", "choice", "사용자별로 모으는 방식", options=["사용자별 합계", "사용자별 마지막 값"], default="사용자별 합계"),
    "win_value": slot("성공 값", "value", "이기거나 깬 것을 뜻하는 결과 값"),
    "pity": slot("천장 횟수", "n", "이 횟수만큼 뽑으면 천장", default=90),
    "exclude": slot("제외할 값들", "values", "이 상태 값인 행은 뺀다(취소·반품 등)", optional=True),
    "top": slot("상위 %", "number", "상위 몇 %의 항목", default=20),
    "threshold": slot("임계값", "number", "점수가 이 값 이상이면 예측이 참", default=0.5),
    "k": slot("K", "n", "상위 K개", default=10),
    "control": slot("대조군 값", "value", "대조군을 뜻하는 실험군 컬럼 값"),
    "treat": slot("실험군 값", "value", "실험군을 뜻하는 실험군 컬럼 값"),
    "alpha": slot("유의수준", "number", "유의수준 α", default=0.05),
    "power": slot("검정력", "number", "원하는 검정력", default=0.8),
    "op": slot("방향", "choice", "Lift가 한계를 어느 쪽으로 지켜야 하는지", options=[">=", "<="], default=">="),
    "limit": slot("한계(%)", "number", "Lift 한계(%)", default=-5),
}
CONSTANTS = {"gap", "eps", "run", "inactive_days", "churn_days", "span", "fee", "vat", "n_days", "n", "from_n", "to_n", "method", "mode", "whale", "dolphin", "max_days", "active_days", "pity",
             "top", "threshold", "k", "alpha", "power", "op", "limit", "basis", "limit_sec"}  # 사용자가 적는 상수 빈칸(컬럼을 고르는 칸이 아니다)
RANGES = {"gap": (0, None), "eps": (0, 100), "fee": (0, 100), "vat": (0, 100), "whale": (0, None), "dolphin": (0, None), "top": (0, 100), "alpha": (0, 1), "power": (0, 1),
          "threshold": (None, None), "limit": (None, None), "limit_sec": (0, None), "from_n": (0, None), "to_n": (0, None)}  # 숫자 칸의 범위(열린 쪽은 None)


def col_slot(role, **over):
    """컬럼 빈칸 하나(도서관의 역할로)."""
    name, label, kinds, hints = COLS[role]
    out = slot(name, "column", label, kinds)
    out.update(over)
    return out


def make_slot(role, over):
    """역할의 빈칸에 바꿀 값(over)을 얹고 역할·이름 단서·상수 여부·범위를 붙인다."""
    base = col_slot(role) if role in COLS else dict(EXTRA[role])
    base.update(over)
    over_name = over.get("name")
    if over_name:
        base["label"] = over.get("label", over_name)
    return {**base, "role": role, "hints": list(COLS[role][3]) if role in COLS else [], "strict": role in STRICT, "constant": role in CONSTANTS,
            "min": RANGES.get(role, (None, None))[0], "max": MAX_N if base["type"] == "n" else RANGES.get(role, (None, None))[1]}


def other(**over):
    """다른 테이블(광고 테이블)의 컬럼 빈칸: 이름만 받고 컬럼 검사는 계산 때 그 테이블에서 한다."""
    return {"type": "text", "other": True, **over}


def entry(id, area, name, abbr, help, formula, sentence, roles, calc, params=None, pair=(), spread=False, fmt="number", unit="", better="", hint="", window=False, cross=False, any_of=()):
    """항목 하나. roles = 빈칸 역할 이름들 또는 (역할, 바꿀 값) 쌍."""
    slots = [make_slot(r, {}) if isinstance(r, str) else make_slot(r[0], r[1]) for r in roles]
    needs = [{"role": s["role"], "name": s["name"], "label": s["label"], "kinds": s["kinds"], "hints": s["hints"], "strict": s["strict"], "other": bool(s.get("other"))}
             for s in slots if s["type"] in ("column", "text") and not s["optional"] and not s["constant"] and (s["type"] == "column" or s.get("other"))]
    return {"id": id, "area": area, "name": name, "abbr": abbr, "help": help, "formula": formula, "sentence": sentence, "slots": slots, "calc": calc, "params": params or {}, "pair": list(pair),
            "spread": spread, "needs": needs, "constants": [s["name"] for s in slots if s["constant"]], "format": fmt, "unit": unit, "better": better, "hint": hint, "window": window,
            "cross": cross, "any_of": [list(g) for g in any_of]}


OPT = {"optional": True}
SES = ["user", "time", "gap", "sess_cols"]  # 세션을 쓰는 항목의 공통 빈칸
AD_OTHER = [("ad_table", {}), ("spend", other(label="광고비 컬럼(광고 테이블)")), ("ad_time", {})]  # 광고비가 다른 테이블에 있는 항목의 빈칸
P = lambda unit, name: {"unit": unit, "unit_name": name}  # 활성 사용자 수의 단위

T, F, R, M, K, E, C = "traffic", "funnel", "retention", "revenue", "marketing", "economy", "commerce"
ENTRIES = [
    # ---------- 1 트래픽·활성도 ----------
    entry("uv", T, "고유 사용자 수", "UV", "기간 안에 한 번이라도 온 서로 다른 사람 수예요. 규모의 가장 기본 숫자입니다.", "고유 사용자 수", "{시각}의 {단위}마다 고유 {사용자} 수",
          [("user", {}), ("time", OPT), ("unit", {"default": None, "optional": True})], "uv", unit="명", hint="north_star"),
    entry("pv", T, "조회 수", "PV", "화면(페이지)이 열린 횟수예요. 사용자 수와 같이 봐야 몇 번씩 보는지 알 수 있어요.", "이벤트(조회) 행 수", "{이벤트}가 {이벤트 값}인 행의 수",
          [("event", OPT), "event_value"], "pv", unit="회"),
    entry("pv_per_uv", T, "사용자 1명당 조회 수", "PV/UV", "한 사람이 평균 몇 번 보는지예요. 오르면 더 깊이 쓰는 것이고, 소수의 헤비 유저가 끌어올린 것일 수도 있어요.", "PV ÷ UV",
          "{사용자} 1명당 {이벤트}가 {이벤트 값}인 행의 수", ["user", ("event", OPT), "event_value"], "pv_per_uv", pair=["pv", "uv"], spread=True, unit="회"),
    entry("dau", T, "일간 활성 사용자", "DAU", "하루에 한 번이라도 쓴 사람 수의 평균이에요. 서비스가 매일 쓰이는지 보는 대표 숫자입니다.", "일별 고유 사용자 수의 평균", "{시각}의 일마다 고유 {사용자} 수의 평균",
          ["user", "time"], "active", P("day", "일"), spread=True, unit="명", hint="north_star"),
    entry("wau", T, "주간 활성 사용자", "WAU", "한 주(월요일 시작)에 한 번이라도 쓴 사람 수의 평균이에요. 매일은 아니어도 주마다 오는 사람을 잡아요.", "주별 고유 사용자 수의 평균", "{시각}의 주마다 고유 {사용자} 수의 평균",
          ["user", "time"], "active", P("week", "주"), spread=True, unit="명"),
    entry("mau", T, "월간 활성 사용자", "MAU", "한 달에 한 번이라도 쓴 사람 수의 평균이에요. 서비스 전체 규모를 보는 기준입니다.", "월별 고유 사용자 수의 평균", "{시각}의 월마다 고유 {사용자} 수의 평균",
          ["user", "time"], "active", P("month", "월"), spread=True, unit="명"),
    entry("stickiness", T, "고착도", "Stickiness", "MAU 중 매일 오는 비율이에요. 높을수록 습관이 된 서비스예요.", "DAU ÷ MAU (×100은 %로 보일 때)", "{시각}의 월마다 DAU ÷ MAU를 {사용자} 기준으로",
          ["user", "time"], "stickiness", pair=["dau", "mau"], fmt="percent", better="up", hint="north_star"),
    entry("pcu", T, "최고 동시 접속", "PCU", "같은 시각에 가장 많이 접속한 사람 수예요. 서버 용량과 이벤트 피크를 잡을 때 써요.", "동시 접속 수의 최댓값", "{시작}~{끝} 구간의 동시 접속 최댓값",
          ["start", "end"], "concurrency", {"pick": "pcu"}, unit="명"),
    entry("acu", T, "평균 동시 접속", "ACU", "시간으로 가중한 평균 동시 접속이에요. 하루 종일 얼마나 북적이는지 보여요.", "Σ 동시 접속 × 유지 시간 ÷ 기간 길이", "{시작}~{끝} 구간의 평균 동시 접속",
          ["start", "end"], "concurrency", {"pick": "acu"}, unit="명"),
    entry("mcu", T, "월 최고 동시 접속", "MCU", "달마다의 최고 동시 접속이에요. 달별 규모 변화를 볼 수 있어요.", "달별 동시 접속의 최댓값", "{시작}~{끝} 구간의 달별 최고 동시 접속",
          ["start", "end"], "concurrency", {"pick": "mcu"}, unit="명"),
    entry("session_count", T, "사용자당 세션 수", "Session Count", "접속한 사람이 하루에 몇 번 들어오는지예요(세션 수 ÷ DAU). 오르면 자주 들르는 것이에요.", "세션 수 ÷ DAU", "{사용자}가 {세션 간격(분)}분 넘게 쉬면 새 세션일 때, 접속한 사용자·날 1개당 세션 수",
          SES, "session_count", pair=["sessions", "user_days"], spread=True, unit="회", window=True),
    entry("asd", T, "평균 세션 길이", "ASD", "한 번 들어와서 머무는 평균 시간이에요(총 체류 ÷ 세션 수). 평균은 긴 세션에 끌려가니 중앙값도 함께 봐요.", "총 체류 시간 ÷ 세션 수",
          "{사용자}의 {시각} 기준 세션(간격 {세션 간격(분)}분) 1개당 길이", SES, "session_length", pair=["stay_seconds", "sessions"], spread=True, unit="초", window=True),
    entry("tsd", T, "일 체류 시간", "TSD", "접속한 사람이 하루에 머무는 총 시간이에요(일 체류 합 ÷ DAU).", "일 체류 시간 합 ÷ DAU", "{사용자}가 접속한 날 1개당 체류 시간(세션 간격 {세션 간격(분)}분)",
          SES, "daily_stay", pair=["stay_seconds", "user_days"], spread=True, unit="초", window=True),
    entry("dau_split", T, "DAU 분해", "신규·기존·복귀", "오늘의 활성 사용자가 신규·기존·복귀 중 어디서 왔는지 나눠요. 늘어난 이유가 신규 때문인지 복귀 때문인지 알 수 있어요.", "활성 = 신규 + 기존 + 복귀",
          "{시각}의 일마다 {사용자}를 신규·기존·복귀로 나눠요(미접속 {미접속 기준(일)}일)", ["user", "time", "inactive_days"], "dau_split", unit="명", window=True),
    entry("quick_ratio", T, "퀵 레시오", "Quick Ratio", "들어오는 사람(신규+복귀)이 나가는 사람(이탈)의 몇 배인지예요. 1보다 크면 커지고 4 가까이면 건강하다고 봐요.", "(신규 + 복귀) ÷ 이탈",
          "{시각}의 {단위}마다 (신규 + 복귀) ÷ 이탈을 {사용자} 기준으로(미접속 {미접속 기준(일)}일)", ["user", "time", ("unit", {"default": "month"}), "inactive_days"], "states", {"pick": "quick"}, pair=["new", "resurrected", "churned"],
          unit="배", better="up", window=True),
    entry("power_curve", T, "파워 유저 곡선", "Power User Curve", "최근 7일(또는 28일) 중 며칠 왔는지의 분포예요. 오른쪽에 몰릴수록 매일 쓰는 핵심 사용자가 많아요.", "사용자별 최근 L일 중 접속 일수의 분포",
          "최근 {최근 일수}일 중 {사용자}가 {시각}에 며칠 접속했는지의 분포", ["user", "time", "span"], "power_curve", unit="일"),
    # ---------- 2 퍼널·온보딩 ----------
    entry("cvr", F, "단계 전환율", "CVR", "A를 한 사람 중 그 뒤에 B까지 간 비율이에요. 단계마다 구하면 어디서 막히는지 보여요.", "B까지 간 사용자 ÷ A를 한 사용자",
          "{이벤트}가 {단계 A}인 {사용자} 중 이후 {단계 B}까지 간 비율", ["user", "time", "event", "step_a", "step_b"], "conversion", pair=["users_first", "users_reached"], fmt="percent", better="up", hint="north_star"),
    entry("drop_off", F, "단계 이탈률", "Drop-off", "A를 한 사람 중 B로 못 간 비율이에요(1 − 전환율). 가장 큰 이탈 단계를 먼저 고쳐요.", "1 − 전환율",
          "{이벤트}가 {단계 A}인 {사용자} 중 이후 {단계 B}에 못 간 비율", ["user", "time", "event", "step_a", "step_b"], "conversion", {"invert": True}, pair=["users_first", "users_reached"], fmt="percent", better="down", hint="counter"),
    entry("bounce", F, "이탈 세션 비율", "Bounce Rate", "한 번만 보고 나간 세션의 비율이에요. 유입의 질이나 첫 화면의 문제를 알려 줘요.", "이벤트 1개짜리 세션 ÷ 세션 수",
          "{사용자}의 세션(간격 {세션 간격(분)}분) 중 {이벤트}가 {이벤트 값}인 행이 하나뿐인 세션의 비율", [*SES, ("event", OPT), "event_value"], "bounce", pair=["sessions"], fmt="percent", better="down", hint="counter", window=True),
    entry("exit_rate", F, "페이지 이탈률", "Exit Rate", "그 페이지가 세션의 마지막이었던 비율이에요. 사용자가 어느 화면에서 떠나는지 보여요.", "마지막 페이지가 p인 세션 ÷ p의 조회 수",
          "{사용자}의 세션(간격 {세션 간격(분)}분)이 {페이지}에서 끝난 비율", [*SES, "page"], "exit_rate", pair=["exits", "views"], fmt="percent", better="down", window=True),
    entry("ttv", F, "첫 가치까지 걸린 시간", "TTV/TTFV", "처음 가치를 느끼는 행동(A→B)까지 걸린 시간이에요. 짧을수록 온보딩이 잘 돼요.", "사용자별 A 이후 첫 B까지의 시간",
          "{이벤트}가 {단계 A}인 때부터 {단계 B}까지 {사용자}별 걸린 시간", ["user", "time", "event", "step_a", "step_b"], "time_between", pair=["users_first", "users_reached"], spread=True, unit="초", better="down"),
    entry("tutorial", F, "튜토리얼 완료율", "Tutorial Completion", "튜토리얼을 시작한 사람 중 끝까지 한 비율이에요. 낮으면 튜토리얼이 길거나 어려운 거예요.", "튜토리얼 완료 사용자 ÷ 시작 사용자",
          "{이벤트}가 {튜토리얼 시작 값}인 {사용자} 중 이후 {튜토리얼 완료 값}까지 한 비율", ["user", "time", "event", ("step_a", {"name": "튜토리얼 시작 값", "label": "튜토리얼 시작 이벤트 값"}), ("step_b", {"name": "튜토리얼 완료 값", "label": "튜토리얼 완료 이벤트 값"})],
          "conversion", pair=["users_first", "users_reached"], fmt="percent", better="up"),
    entry("ftue", F, "첫 경험 퍼널", "FTUE Funnel", "첫 접속부터 핵심 행동까지 단계별로 몇 명이 남는지예요. 단계 사이 가장 큰 낙폭이 고칠 곳이에요.", "단계별 도달 사용자와 전환",
          "{이벤트}의 {단계들}을 순서대로 거친 {사용자} 수(제한 {제한 시간(초)}초)", ["user", "time", "event", "steps", "limit_sec"], "ftue", pair=["first_step_users", "last_step_users"], fmt="percent", better="up"),
    entry("ttfp", F, "첫 결제까지 걸린 시간", "TTFP", "가입(첫 접속)부터 첫 결제까지 걸린 시간이에요. 길면 첫 구매를 유도할 계기가 필요해요.", "사용자별 가입 이후 첫 결제까지의 시간",
          "{이벤트}가 {가입 값}인 때부터 {첫 결제 값}까지 {사용자}별 걸린 시간", ["user", "time", "event", ("step_a", {"name": "가입 값", "label": "가입(첫 접속) 이벤트 값"}), ("step_b", {"name": "첫 결제 값", "label": "결제 이벤트 값"})],
          "time_between", pair=["users_first", "users_reached"], spread=True, unit="초", better="down"),
    # ---------- 3 리텐션·코호트 ----------
    entry("retention_classic", R, "클래식 리텐션", "Day-N Retention", "가입(첫 활동) 후 정확히 N일째에 돌아온 비율이에요. 가장 널리 쓰는 유지율이에요.", "N일째 활동 사용자 ÷ 코호트 크기",
          "{시각}의 {단위} 코호트가 정확히 {N}째에 {사용자} 기준으로 돌아온 비율", ["user", "time", "unit", "n"], "retention", {"mode": "classic"}, pair=["cohort_size"], fmt="percent", better="up", hint="north_star"),
    entry("retention_rolling", R, "롤링 리텐션", "Rolling Retention", "N일째 이후 언제든 돌아온 사람의 비율이에요. 클래식보다 너그럽고, 잠깐 쉬었다 오는 서비스에 맞아요.", "마지막 활동이 N 이상인 사용자 ÷ 코호트 크기",
          "{시각}의 {단위} 코호트가 {N}째 이후에 {사용자} 기준으로 돌아온 비율", ["user", "time", "unit", "n"], "retention", {"mode": "rolling"}, pair=["cohort_size"], fmt="percent", better="up"),
    entry("retention_range", R, "범위 리텐션", "Bracket Retention", "N1~N2일 사이에 한 번이라도 돌아온 비율이에요. 주 단위 유지를 볼 때 하루 변동을 줄여 줘요.", "[a, b] 안에 활동한 사용자 ÷ 코호트 크기",
          "{시각}의 {단위} 코호트가 {범위 시작 N}~{범위 끝 N} 사이에 {사용자} 기준으로 돌아온 비율", ["user", "time", "unit", "from_n", "to_n"], "retention", {"mode": "range"}, pair=["cohort_size"], fmt="percent", better="up"),
    entry("flattening", R, "수평 유지점", "Retention Flattening Point", "유지율 곡선이 더 내려가지 않고 평평해지는 첫 N이에요. 그 높이가 충성 사용자 비율이에요.", "|r(N) − r(N−1)| < ε가 연속으로 이어지는 첫 N",
          "{시각}의 {단위} 코호트의 유지율이 {연속 횟수}번 연속 {허용 오차(%p)}%p 안으로 평평해지는 첫 N({N}까지 관찰)", ["user", "time", "unit", ("n", {"default": 30, "label": "관찰할 최대 N"}), "eps", "run"], "flattening", pair=["cohort_size"], unit="N"),
    entry("churn", R, "이탈률", "Churn Rate", "지난 기간에 쓰던 사람 중 이번에 안 온 비율이에요. 낮을수록 좋고 성장의 가장 큰 적이에요.", "이탈 ÷ 앞 기간 활동자",
          "{시각}의 {단위}마다 앞 기간 {사용자} 중 이번에 안 온 비율(미접속 {미접속 기준(일)}일)", ["user", "time", ("unit", {"default": "month"}), "inactive_days"], "states", {"pick": "churn"}, pair=["churned"], fmt="percent", better="down", hint="counter", window=True),
    entry("resurrection", R, "복귀율", "Resurrection Rate", "한동안 안 오던 사람이 돌아온 비율이에요. 복귀 캠페인의 성과를 볼 때 써요.", "복귀 ÷ 기간 시작 때 휴면자",
          "{시각}의 {단위}마다 휴면 {사용자} 중 돌아온 비율(미접속 {미접속 기준(일)}일)", ["user", "time", ("unit", {"default": "month"}), "inactive_days"], "states", {"pick": "resurrection"}, pair=["resurrected"], fmt="percent", better="up", window=True),
    entry("survival", R, "생존 곡선", "Survival S(t)", "머문 기간 t까지 살아남은(떠나지 않은) 사용자 비율이에요. 아직 남은 사람(중도절단)도 반영해서 중앙 생존 시간을 구해요.", "Kaplan-Meier: S ×= (1 − 떠난 수 ÷ 남은 수)",
          "{시각} 기준 {사용자}가 {미접속 기준(일)}일 이상 안 오면 떠난 것일 때의 생존 곡선", ["user", "time", "churn_days"], "survival", pair=["users"], unit="일"),
    # ---------- 4 수익화·LTV ----------
    entry("gross_revenue", M, "총매출", "Gross Revenue", "수수료·환불을 빼기 전 매출 합계예요.", "금액의 합", "{금액}의 합", ["amount"], "revenue", {"pick": "gross"}, unit="원", better="up", hint="north_star"),
    entry("net_revenue", M, "순매출", "Net Revenue", "환불·부가세·수수료를 빼고 실제로 남는 매출이에요. 수수료·부가세율은 직접 적어요.", "(총매출 − 환불) ÷ (1 + 부가세) × (1 − 수수료)",
          "{금액}의 합에서 {환불 금액}과 부가세 {부가세(%)}%, 수수료 {수수료(%)}%를 뺀 값", ["amount", ("refund", OPT), "fee", "vat"], "revenue", {"pick": "net"}, pair=["gross_revenue", "refund"], unit="원", better="up", hint="north_star"),
    entry("pu", M, "결제 사용자 수", "PU", "기간 안에 한 번이라도 결제한 사람 수예요. 객단가 지표(ARPPU)의 짝이에요.", "금액 > 0인 고유 사용자 수", "{금액}이 0보다 큰 고유 {사용자} 수", ["user", "amount"], "payers", unit="명"),
    entry("npa", M, "신규 결제자", "NPA", "처음 결제한 사람 수예요. 결제 유입이 늘고 있는지 알 수 있어요.", "기간 안 첫 결제가 그 기간인 고유 사용자 수", "{시각}의 {단위}마다 처음 {금액}을 낸 {사용자} 수",
          ["user", "amount", "time", ("unit", {"default": "month"})], "new_payers", pair=["pu"], unit="명", better="up"),
    entry("pur", M, "결제 비율", "PUR (DPUR/MPUR)", "활동 사용자 중 결제한 비율이에요. 단위가 일이면 DPUR, 월이면 MPUR이에요.", "결제자 ÷ 활동 사용자",
          "{시각}의 {단위}마다 활동 {사용자} 중 {금액}을 낸 비율", ["user", "amount", "time", ("unit", {"default": "month"})], "pur", pair=["pu", "uv"], fmt="percent", better="up"),
    entry("arpu", M, "사용자당 매출", "ARPU", "활동 사용자 1명당 매출이에요. 결제 안 한 사람까지 나누므로 PUR과 객단가의 곱이에요. 평균은 고래에 끌려가니 중앙값도 함께 봐요.", "매출 ÷ 활동 사용자",
          "{사용자} 1명당 {금액}의 합", ["user", "amount"], "arpu", pair=["gross_revenue", "uv"], spread=True, unit="원", better="up"),
    entry("arppu", M, "결제자당 매출", "ARPPU", "결제한 사람 1명당 매출이에요. 객단가(얼마나 쓰는지)를 봐요.", "매출 ÷ PU", "{금액}을 낸 {사용자} 1명당 {금액}의 합", ["user", "amount"], "arppu", pair=["gross_revenue", "pu"], spread=True, unit="원", better="up"),
    entry("arpdau", M, "일 활성 사용자당 매출", "ARPDAU", "하루 접속자 1명당 그날 매출이에요. 일 단위로 수익성을 비교할 때 써요.", "매출 합 ÷ 일별 DAU의 합", "{시각}의 일마다 접속한 {사용자} 1명당 {금액}",
          ["user", "time", "amount"], "arpdau", pair=["gross_revenue", "user_days"], spread=True, unit="원", better="up"),
    entry("ltv", M, "고객 생애 가치", "LTV (LTV7/30/90)", "한 사용자가 N일 동안 가져오는 매출이에요. 코호트 누적은 실제로 관찰한 값이고, ARPU÷이탈률은 앞으로를 어림한 값이에요.", "N일 안 코호트 매출 ÷ 코호트 크기 (또는 ARPU ÷ Churn)",
          "{시각}의 {단위} 코호트가 첫 활동 후 {N일} 안에 {금액}을 낸 {사용자} 1명당 값({계산 방식})", ["user", "time", "amount", "n_days", "unit", "method"], "ltv", pair=["cohort_size", "gross_revenue"], unit="원", better="up", hint="north_star"),
    entry("repeat_rate", M, "재구매율", "Repeat Purchase Rate", "한 번이라도 산 사람 중 N번 이상 산 비율이에요. 단골이 얼마나 되는지 알려 줘요.", "N번 이상 산 사용자 ÷ 구매 사용자",
          "{금액}을 낸 {사용자} 중 {N}번 이상 산({주문}) 비율", ["user", "amount", ("orders", OPT), ("n", {"default": 2, "label": "N번 이상"})], "repeat", pair=["pu", "repeat_buyers"], fmt="percent", better="up"),
    entry("tiers", M, "고래·돌고래·잔챙이 비중", "Whale/Dolphin/Minnow Share", "소수의 큰손이 매출을 얼마나 차지하는지예요. 쏠림이 크면 한 명의 이탈이 매출을 흔들어요.", "등급별 매출 ÷ 전체 매출",
          "{금액}을 낸 {사용자}를 {기준 방식} 기준 Whale {Whale 기준}·Dolphin {Dolphin 기준}으로 나눈 매출 비중", ["user", "amount", "mode", "whale", "dolphin"], "tiers", pair=["pu"], fmt="percent"),
    entry("mrr", M, "월 반복 매출", "MRR", "한 달 동안 반복해서 들어오는 매출이에요(구독). 값이 달마다 얼마나 불어나는지 봐요.", "월별 금액의 합", "{시각}의 월마다 {금액}의 합", ["time", "amount"], "recurring", {"pick": "mrr"}, unit="원", better="up", hint="north_star"),
    entry("arr", M, "연 반복 매출", "ARR", "MRR을 1년으로 늘린 값(×12)이에요. 구독 사업의 규모를 한눈에 보여 줘요.", "MRR × 12", "{시각}의 월마다 {금액}의 합 × 12", ["time", "amount"], "recurring", {"pick": "arr"}, pair=["mrr"], unit="원", better="up"),
    entry("nrr", M, "순매출 유지율", "NRR", "지난달 매출을 낸 고객이 이번 달 얼마를 냈는지예요. 100%가 넘으면 새 고객 없이도 매출이 늘어요.", "지난달 매출 있던 고객의 이번 달 매출 ÷ 지난달 매출",
          "{시각}의 월마다 지난달 {금액}을 낸 {사용자}의 이번 달 {금액} ÷ 지난달 {금액}", ["user", "time", "amount"], "nrr", pair=["prev_revenue", "kept_revenue"], fmt="percent", better="up", hint="north_star"),
    # ---------- 5 마케팅·UA ----------
    entry("impressions", K, "노출 수", "Impressions", "광고가 보인 횟수예요. 도달·빈도·클릭률의 바탕이에요.", "노출 수의 합(컬럼이 없으면 행 수)", "{노출 수}의 합", [("impressions", OPT)], "impressions", unit="회"),
    entry("reach", K, "도달 사용자", "Reach", "광고를 한 번이라도 본 서로 다른 사람 수예요.", "고유 사용자 수", "광고를 본 고유 {사용자} 수", ["user"], "reach", unit="명"),
    entry("frequency", K, "노출 빈도", "Frequency", "한 사람이 광고를 평균 몇 번 봤는지예요. 너무 높으면 피로, 너무 낮으면 기억에 안 남아요.", "노출 수 ÷ 도달", "{사용자} 1명당 {노출 수}의 합",
          ["user", ("impressions", OPT)], "frequency", pair=["impressions", "reach"], spread=True, unit="회"),
    entry("ctr", K, "클릭률", "CTR", "노출 중 클릭한 비율이에요. 소재가 눈길을 끄는지 봐요.", "클릭 ÷ 노출", "{클릭 수}의 합 ÷ {노출 수}의 합", ["clicks", "impressions"], "ad_ratio",
          {"num": "clicks", "den": "impressions", "label": "클릭 ÷ 노출"}, pair=["clicks", "impressions"], fmt="percent", better="up"),
    entry("ipm", K, "노출 천 번당 설치", "IPM", "광고가 1,000번 보일 때 설치되는 수예요. 설치 광고의 효율이에요.", "설치 ÷ 노출 × 1000", "{설치 수}의 합 ÷ {노출 수}의 합 × 1000", ["installs", "impressions"], "ad_ratio",
          {"num": "installs", "den": "impressions", "scale": 1000, "label": "설치 ÷ 노출 × 1000"}, pair=["installs", "impressions"], better="up"),
    entry("cpm", K, "노출 천 번당 비용", "CPM", "광고가 1,000번 보이는 데 드는 비용이에요.", "광고비 ÷ 노출 × 1000", "{광고비}의 합 ÷ {노출 수}의 합 × 1000", ["spend", "impressions"], "ad_ratio",
          {"num": "spend", "den": "impressions", "scale": 1000, "label": "광고비 ÷ 노출 × 1000"}, pair=["spend", "impressions"], unit="원", better="down"),
    entry("cpc", K, "클릭당 비용", "CPC", "클릭 한 번에 드는 비용이에요.", "광고비 ÷ 클릭", "{광고비}의 합 ÷ {클릭 수}의 합", ["spend", "clicks"], "ad_ratio",
          {"num": "spend", "den": "clicks", "label": "광고비 ÷ 클릭"}, pair=["spend", "clicks"], unit="원", better="down"),
    entry("cpi", K, "설치당 비용", "CPI", "앱 설치 하나에 드는 비용이에요.", "광고비 ÷ 설치", "{광고비}의 합 ÷ {설치 수}의 합", ["spend", "installs"], "ad_ratio",
          {"num": "spend", "den": "installs", "label": "광고비 ÷ 설치"}, pair=["spend", "installs"], unit="원", better="down"),
    entry("cpa", K, "행동당 비용", "CPA", "가입·구매 같은 목표 행동 하나에 드는 비용이에요.", "광고비 ÷ 전환", "{광고비}의 합 ÷ {전환 수}의 합", ["spend", "conversions"], "ad_ratio",
          {"num": "spend", "den": "conversions", "label": "광고비 ÷ 전환"}, pair=["spend", "conversions"], unit="원", better="down"),
    entry("cac", K, "고객 획득 비용(전체)", "CAC (Blended)", "신규 사용자 1명을 데려오는 데 든 광고비예요. 오가닉으로 온 사람까지 나누므로 Paid CAC보다 낮아요.", "광고비 ÷ 신규 사용자(오가닉 포함)",
          "{광고 테이블}의 {광고비} ÷ {시각} 기준 신규 {사용자} 수(맞춰서)", ["user", "time", *AD_OTHER], "cac", {"paid": False}, pair=["spend", "new_users"], unit="원", better="down", hint="guardrail", cross=True, window=True),
    entry("cac_paid", K, "고객 획득 비용(유료)", "CAC (Paid)", "광고로 온 신규 1명당 광고비예요. 채널 효율을 비교할 때 써요.", "광고비 ÷ 유료 유입 신규 사용자",
          "{광고 테이블}의 {광고비} ÷ {유입 경로}가 {유료 값}인 신규 {사용자} 수(맞춰서)", ["user", "time", "source", "paid_value", *AD_OTHER], "cac", {"paid": True}, pair=["spend", "new_users"], unit="원", better="down", hint="guardrail", cross=True, window=True),
    entry("roas", K, "광고 수익률", "ROAS (D1/D7/D30)", "광고비 대비 N일 안에 돌아온 매출이에요. 1(100%)이 넘으면 N일 안에 본전이에요.", "첫 활동 후 N일 누적 매출 ÷ 광고비",
          "{광고 테이블}의 {광고비} 대비 {사용자}의 첫 활동 후 {N일} 안 {금액}(맞춰서)", ["user", "time", "amount", ("n_days", {"default": 7}), *AD_OTHER], "roas", pair=["revenue_n", "spend"], fmt="percent", better="up", hint="north_star", cross=True, window=True),
    entry("ltv_cac", K, "LTV 대 CAC", "LTV:CAC", "한 사용자가 벌어 주는 돈이 데려오는 비용의 몇 배인지예요. 보통 3배 이상이면 건강하다고 봐요.", "LTV ÷ CAC",
          "{사용자}의 LTV{N일}을 {광고 테이블}의 {광고비}로 만든 CAC로 나눈 값(맞춰서)", ["user", "time", "amount", ("n_days", {"default": 90}), *AD_OTHER], "roas", {"ltv_cac": True}, pair=["ltv", "cac"], unit="배", better="up", hint="north_star", cross=True, window=True),
    entry("payback", K, "회수 기간", "Payback Period", "광고비(CAC)를 매출로 되찾는 데 걸리는 일수예요. 짧을수록 현금 부담이 작아요.", "누적 ARPU ≥ CAC가 되는 첫 Day-N",
          "{광고 테이블}의 {광고비}로 만든 CAC를 {사용자}의 누적 {금액}이 {최대 일수}일 안에 넘는 첫 날(맞춰서)", ["user", "time", "amount", "max_days", *AD_OTHER], "payback", pair=["cac", "new_users"], unit="일", better="down", cross=True, window=True),
    entry("k_factor", K, "바이럴 계수", "K-Factor", "사용자 1명이 데려오는 새 사용자 수예요(사용자당 초대 수 × 초대 전환율). 1이 넘으면 스스로 퍼져요.", "초대 수/사용자 × 초대 전환율", "{사용자} 1명당 {보낸 초대 수}에서 {초대로 가입한 수}로 이어진 비율의 곱",
          ["user", "invites", "joined"], "kfactor", pair=["invites", "joined"], unit="배", better="up"),
    entry("organic_uplift", K, "오가닉 상승분", "Organic Uplift", "유료로 온 신규 1명당 오가닉으로 따라온 신규 수예요. 광고가 입소문·검색을 얼마나 키웠는지 어림해요.", "오가닉 신규 ÷ 유료 신규",
          "{시각} 기준 처음 온 {사용자}의 {유입 경로}가 {유료 값}이 아닌 수 ÷ 인 수", ["user", "time", "source", "paid_value"], "organic_uplift", pair=["paid_installs", "organic_installs"], unit="배"),
    # ---------- 6 게임 경제 ----------
    entry("faucet", E, "재화 공급", "Faucet", "게임 안으로 새로 풀린 재화 총량이에요(얻은 양의 합).", "재화 변화량 > 0의 합", "{재화 변화량}이 0보다 큰 값의 합", ["delta"], "flow", {"pick": "faucet"}),
    entry("sink", E, "재화 소비", "Sink", "게임 안에서 사라진 재화 총량이에요(쓴 양의 합).", "−재화 변화량 < 0의 합", "{재화 변화량}이 0보다 작은 값의 합(절댓값)", ["delta"], "flow", {"pick": "sink"}),
    entry("net_flow", E, "순 유입", "Net Flow", "공급 − 소비예요. 양수가 계속되면 재화가 쌓여 인플레이션 위험이 커요.", "Faucet − Sink", "{재화 변화량}의 공급 − 소비", ["delta"], "flow", {"pick": "net"}, pair=["faucet", "sink"], hint="guardrail"),
    entry("sink_faucet", E, "소비 대 공급 비율", "Sink-to-Faucet Ratio", "푼 재화 중 얼마나 회수했는지예요. 1에 가까우면 균형, 훨씬 낮으면 재화가 넘쳐요.", "Sink ÷ Faucet", "{재화 변화량}의 소비 ÷ 공급", ["delta"], "flow", {"pick": "ratio"}, pair=["sink", "faucet"], fmt="percent", hint="guardrail"),
    entry("money_supply", E, "활성 통화량", "Active Money Supply", "최근 활동한 사용자가 가진 재화의 합이에요. 실제로 시장에서 돌 수 있는 돈의 크기예요.", "활성 사용자의 마지막 잔액 합", "최근 {활성 기준(일)}일 활동한 {사용자}의 {시각} 기준 마지막 {잔액}의 합",
          ["user", "time", "balance", "active_days"], "money_supply"),
    entry("velocity", E, "화폐 유통 속도", "Velocity of Money", "활성 통화량 대비 얼마나 쓰였는지예요. 높을수록 재화가 활발히 돌고 낮으면 쌓여 있어요.", "Sink ÷ 활성 통화량", "{재화 변화량}의 소비 ÷ 최근 {활성 기준(일)}일 활동한 {사용자}의 마지막 {잔액}의 합",
          ["user", "time", "delta", "balance", "active_days"], "velocity", pair=["sink", "active_money_supply"], unit="배"),
    entry("gini", E, "지니 계수", "Gini Coefficient", "재화가 몇 사람에게 쏠렸는지예요(0 = 평등, 1 = 한 명이 독점). 높으면 상위 유저와 신규 유저의 격차가 커요.", "(2Σ i·x(i)) ÷ (nΣx) − (n+1)/n",
          "{사용자}별 {값}({기준})의 지니 계수", ["value", ("user", OPT), ("time", OPT), "basis"], "gini", pair=["holders"], better="down", hint="guardrail"),
    entry("stage_clear", E, "스테이지 클리어율", "Stage Clear Rate", "도전 중 깬 비율이에요. 너무 낮은 스테이지는 이탈 지점이에요.", "클리어 ÷ 도전", "{스테이지}마다 {결과}가 {성공 값}인 도전의 비율", ["stage", "result", "win_value", ("user", OPT)], "stage_clear", {"pick": "clear"},
          pair=["clears", "tries"], fmt="percent"),
    entry("stage_tries", E, "스테이지 도전 횟수", "Stage Try Count", "한 사람이 스테이지를 깨기 위해 평균 몇 번 도전했는지예요. 난이도 곡선을 볼 때 써요.", "도전 수 ÷ 도전한 (사용자, 스테이지) 수", "{스테이지}마다 {사용자} 1명당 도전 횟수",
          ["stage", "result", "win_value", "user"], "stage_clear", {"pick": "tries"}, pair=["tries"], spread=True, unit="회"),
    entry("pick_rate", E, "선택률", "Meta Pick Rate", "여러 선택지 중 그 항목을 고른 비율이에요. 메타가 한쪽으로 쏠렸는지 봐요.", "항목 선택 수 ÷ 전체 선택 수", "{항목}별 선택 비율({결과}가 {성공 값}이면 승리)", ["item", "result", "win_value"], "meta", {"pick": "pick"}, pair=["picks"], fmt="percent"),
    entry("win_rate", E, "승률", "Meta Win Rate", "그 항목을 고른 판 중 이긴 비율이에요. 선택률과 같이 봐야 사기 캐릭터를 찾아요.", "승리 ÷ 선택", "{항목}별 {결과}가 {성공 값}인 비율", ["item", "result", "win_value"], "meta", {"pick": "win"}, pair=["wins", "picks"], fmt="percent"),
    entry("pity_reach", E, "천장 도달률", "Pity Reach Rate", "뽑기를 한 사람 중 천장 횟수까지 간 비율이에요. 높으면 확률 뽑기만으로는 부족해 천장에 의존해요.", "천장 횟수 이상 뽑은 사용자 ÷ 뽑은 사용자",
          "{이벤트}가 {이벤트 값}인 뽑기를 {천장 횟수}번 이상 한 {사용자}의 비율", ["user", ("event", OPT), "event_value", "pity"], "pity", pair=["pulled_users", "reached_users"], spread=True, fmt="percent"),
    # ---------- 7 이커머스 ----------
    entry("gmv", C, "총거래액", "GMV", "취소·반품을 빼기 전(또는 뺀) 주문 금액의 합이에요. 마켓플레이스의 규모예요.", "주문 금액의 합", "{금액}의 합({상태}가 {제외할 값들}인 행 제외)", ["amount", ("status", OPT), "exclude"], "gmv", unit="원", better="up", hint="north_star"),
    entry("take_rate", C, "수수료율", "Take Rate", "거래액 중 플랫폼이 가져가는 비율이에요.", "수수료 수익 ÷ GMV", "{수수료 수익}의 합 ÷ {금액}의 합", ["amount", "fee_col"], "take_rate", pair=["fee_revenue", "gmv"], fmt="percent"),
    entry("aov", C, "평균 주문 금액", "AOV", "주문 1건당 평균 금액이에요. 중앙값과 같이 보면 큰 주문의 영향을 알 수 있어요.", "GMV ÷ 주문 수", "{주문} 1건당 {금액}", ["amount", ("orders", OPT)], "aov", pair=["gmv", "orders"], spread=True, unit="원", better="up"),
    entry("purchase_frequency", C, "구매 빈도", "Purchase Frequency", "구매한 사람이 평균 몇 번 샀는지예요. 재구매 유도가 통하는지 봐요.", "주문 수 ÷ 구매자 수", "{사용자} 1명당 {주문} 수", ["user", ("orders", OPT)], "purchase_frequency", pair=["orders", "buyers"], spread=True, unit="회", better="up"),
    entry("cart_abandon", C, "장바구니 이탈률", "Cart Abandonment Rate", "장바구니에 담고 사지 않은 비율이에요. 결제 단계의 마찰을 보여 줘요.", "1 − (담은 뒤 구매한 사용자 ÷ 담은 사용자)", "{이벤트}가 {장바구니 값}인 {사용자} 중 이후 {구매 값}에 못 간 비율",
          ["user", "time", "event", ("step_a", {"name": "장바구니 값", "label": "장바구니 담기 이벤트 값"}), ("step_b", {"name": "구매 값", "label": "구매 이벤트 값"})], "conversion", {"invert": True}, pair=["users_first", "users_reached"], fmt="percent", better="down", hint="counter"),
    entry("search_exit", C, "검색 이탈률", "Search Exit Rate", "검색한 세션 중 검색이 마지막 행동이었던 비율이에요. 검색 결과가 쓸모없을 때 높아져요.", "검색이 마지막인 세션 ÷ 검색한 세션",
          "{사용자}의 세션(간격 {세션 간격(분)}분) 중 {이벤트}가 {검색 이벤트 값}이 마지막인 비율", [*SES, "event", ("event_value", {"name": "검색 이벤트 값", "label": "검색 이벤트 값", "optional": False})],
          "search_exit", pair=["search_sessions", "exit_sessions"], fmt="percent", better="down", window=True),
    entry("zero_result", C, "검색 결과 없음 비율", "Zero-Result Rate", "검색했는데 결과가 0건이었던 비율이에요. 높으면 동의어·상품 구색을 손봐야 해요.", "결과 0건 검색 ÷ 검색", "{검색 결과 수}가 0인 검색의 비율({이벤트}가 {이벤트 값}인 행만)",
          ["results", ("event", OPT), "event_value"], "zero_result", pair=["searches", "zero_searches"], fmt="percent", better="down", hint="counter"),
    entry("sku_pareto", C, "상품 쏠림(파레토)", "SKU Pareto", "상위 20% 상품이 매출의 몇 %인지예요. 80%에 가까우면 소수 상품에 기대는 구조예요.", "상위 p% 항목의 매출 ÷ 전체 매출", "{항목} 중 상위 {상위 %}%가 차지하는 {금액}의 비중", ["item", "amount", "top"], "pareto", pair=["items"], fmt="percent"),
    # ---------- 8 실험·모델 ----------
    entry("precision", "model", "정밀도", "Precision", "참이라고 한 것 중 실제로 참인 비율이에요. 오탐(헛다리)을 줄이고 싶을 때 봐요.", "TP ÷ (TP + FP)", "{예측}(또는 {점수} ≥ {임계값})이 참인 것 중 {정답}도 참인 비율",
          ["y_true", ("y_pred", OPT), ("score", OPT), "threshold"], "classification", {"pick": "precision"}, pair=["tp", "fp"], fmt="percent", better="up", any_of=[("y_pred", "score")]),
    entry("recall", "model", "재현율", "Recall", "실제 참 중 찾아낸 비율이에요. 놓치면 안 되는 문제(사기·질병)에서 중요해요.", "TP ÷ (TP + FN)", "{정답}이 참인 것 중 {예측}(또는 {점수} ≥ {임계값})도 참인 비율",
          ["y_true", ("y_pred", OPT), ("score", OPT), "threshold"], "classification", {"pick": "recall"}, pair=["tp", "fn"], fmt="percent", better="up", any_of=[("y_pred", "score")]),
    entry("f1", "model", "F1 점수", "F1", "정밀도와 재현율의 조화평균이에요. 둘 중 하나가 낮으면 같이 낮아져요. 둘 다 0이면 0이에요.", "2PR ÷ (P + R)", "{예측}(또는 {점수} ≥ {임계값})의 정밀도와 재현율의 조화평균",
          ["y_true", ("y_pred", OPT), ("score", OPT), "threshold"], "classification", {"pick": "f1"}, pair=["tp", "fp", "fn"], fmt="percent", better="up", any_of=[("y_pred", "score")]),
    entry("pr_auc", "model", "PR 곡선 아래 넓이", "PR-AUC", "점수 기준을 바꿔 가며 정밀도-재현율 곡선 아래 넓이를 구한 평균 정밀도예요. 참이 드문 문제에서 AUC보다 믿을 만해요.", "Σ (R_n − R_(n−1)) · P_n",
          "{정답}에 대한 {점수}의 평균 정밀도", ["y_true", ("score", {"optional": False})], "classification", {"pick": "pr_auc"}, fmt="percent", better="up"),
    entry("ndcg", "model", "추천 순위 품질", "NDCG@K", "추천 상위 K개가 이상적인 순서에 얼마나 가까운지예요. 관련도가 높은 것이 위에 있을수록 1에 가까워요.", "DCG ÷ IDCG, DCG = Σ rel_i ÷ log2(i + 1)",
          "{질의}마다 {순위} 순 상위 {K}개의 {관련도}로 구한 NDCG의 평균", ["qid", "rank", "rel", "k"], "ranking", {"pick": "ndcg"}, pair=["queries"], better="up"),
    entry("map", "model", "추천 평균 정밀도", "MAP@K", "질의마다 맞는 항목이 얼마나 위에 있는지의 평균이에요. 맞는 것을 위로 올릴수록 커져요.", "질의별 AP@K의 평균", "{질의}마다 {순위} 순 상위 {K}개 중 {관련도}가 0보다 큰 것의 평균 정밀도",
          ["qid", "rank", "rel", "k"], "ranking", {"pick": "map"}, pair=["queries"], better="up"),
    entry("rmse", "model", "제곱근 평균제곱오차", "RMSE", "예측 오차를 제곱해 평균한 뒤 제곱근을 씌운 값이에요. 큰 오차에 더 민감해요.", "√ 평균((예측 − 정답)²)", "{정답}과 {예측}의 RMSE",
          [("y_true", {"kinds": "num"}), ("y_pred", {"kinds": "num", "optional": False})], "regression", {"pick": "rmse"}, better="down"),
    entry("mae", "model", "평균 절대 오차", "MAE", "예측과 정답 차이의 절댓값 평균이에요. 단위가 정답과 같아 읽기 쉬워요.", "평균 |예측 − 정답|", "{정답}과 {예측}의 MAE",
          [("y_true", {"kinds": "num"}), ("y_pred", {"kinds": "num", "optional": False})], "regression", {"pick": "mae"}, better="down"),
    entry("mape", "model", "평균 절대 비율 오차", "MAPE", "오차를 정답의 몇 %인지로 평균한 값이에요. 정답이 0인 행은 뺍니다.", "평균 |오차 ÷ 정답|", "{정답}과 {예측}의 MAPE(정답이 0인 행 제외)",
          [("y_true", {"kinds": "num"}), ("y_pred", {"kinds": "num", "optional": False})], "regression", {"pick": "mape"}, fmt="percent", better="down"),
    entry("lift", "model", "리프트", "Lift", "실험군이 대조군보다 몇 % 높은지예요. 크기만 볼 뿐 우연인지는 p-value로 따로 봐요.", "(T − C) ÷ C", "{실험군}이 {대조군 값}인 군 대비 {실험군 값}인 군의 {지표 값} 변화율",
          ["group", "metric", "control", "treat"], "lift", pair=["control_mean", "treat_mean"], fmt="percent", better="up", hint="north_star"),
    entry("pvalue", "model", "유의확률", "p-value", "차이가 우연으로 생겼을 확률이에요. 유의수준보다 작으면 우연이라고 보기 어려워요. 숫자(0/1)면 비율 z, 연속이면 Welch t 검정이에요.", "두 비율 z 검정 또는 Welch t 검정(양측)",
          "{실험군}의 {대조군 값} 대 {실험군 값} 사이 {지표 값} 차이의 p-value(유의수준 {유의수준})", ["group", "metric", "control", "treat", "alpha"], "pvalue", pair=["control_n", "treat_n"]),
    entry("power", "model", "검정력", "Power", "실제로 차이가 있을 때 그걸 잡아낼 확률이에요. 낮으면 차이가 있어도 못 찾으니 표본을 늘려야 해요.", "정규 근사 검정력", "{실험군}의 {대조군 값} 대 {실험군 값} 사이 {지표 값} 차이의 검정력(유의수준 {유의수준})",
          ["group", "metric", "control", "treat", "alpha"], "power", pair=["control_n", "treat_n"], fmt="percent"),
    entry("mde", "model", "최소 검출 효과", "MDE", "지금 표본으로 잡아낼 수 있는 가장 작은 차이예요. 기대 효과가 이보다 작으면 실험을 더 오래 돌려야 해요.", "(z_(1−α/2) + z_power) × 표준오차",
          "{실험군}의 {대조군 값} 대 {실험군 값} 사이 {지표 값}의 MDE(유의수준 {유의수준}, 검정력 {검정력})", ["group", "metric", "control", "treat", "alpha", "power"], "mde", pair=["control_n", "treat_n"]),
    entry("guardrail", "model", "가드레일", "Guardrail", "실험이 지켜야 할 지표예요. 실험군이 대조군보다 한계 넘게 나빠지면 경고해요. 대시보드의 카운터 한계(limits)와 같은 방식이에요.", "Lift ≥ 한계 (또는 ≤)",
          "{실험군}의 {대조군 값} 대 {실험군 값} 사이 {지표 값} 변화율이 {한계(%)}% {방향}을 지키는지", ["group", "metric", "control", "treat", "op", "limit"], "guardrail", pair=["control_mean", "treat_mean"], hint="guardrail"),
]
BY_ID = {e["id"]: e for e in ENTRIES}


# ---------- 찾기·화면용 ----------

def get(entry_id):
    """id의 항목. 없으면 ValueError."""
    found = BY_ID.get(entry_id[len(PREFIX):] if isinstance(entry_id, str) and entry_id.startswith(PREFIX) else entry_id)
    if found is None:
        raise ValueError(f"'{entry_id}' 지표 사전 항목이 없습니다.")
    return found


def is_catalog(template_id):
    """내 지표의 template 값이 지표 사전 항목인지."""
    return isinstance(template_id, str) and template_id.startswith(PREFIX)


def template(entry_id):
    """항목을 문장 틀(sentences.get이 주는 것과 같은 모양)로: 내 지표(my_metrics)의 template으로 쓴다."""
    e = get(entry_id)
    return {"id": PREFIX + e["id"], "kind": "metric", "no": ENTRIES.index(e) + 1, "name": e["name"], "text": e["sentence"], "slots": e["slots"], "uses": [], "period": True, "catalog": True}


def part_name(part_id):
    """짝 절대량 id의 이름(항목 id면 항목 이름)."""
    return BY_ID[part_id]["name"] if part_id in BY_ID else PARTS.get(part_id, part_id)


def catalog(columns=None, tables=None):
    """화면에 보내는 지표 사전 {areas, entries, parts, availability?}. columns({컬럼: 종류})를 주면 그 테이블에서 쓸 수 있는지(availability)도 함께."""
    out = {"areas": AREAS, "entries": [{**e, "pair_names": {p: part_name(p) for p in e["pair"]}} for e in ENTRIES], "parts": {**PARTS, **{i: e["name"] for i, e in BY_ID.items()}},
           "spread": [n for n, _ in SPREAD], "hints": list(HINTS)}
    if columns is not None:
        out["availability"] = available(columns, tables)
    return out


# ---------- 쓸 수 있는지 ----------

def candidates(need, columns):
    """필요한 컬럼(need)에 맞는 컬럼 이름들: 종류가 맞고, 단서가 꼭 필요한 역할이면 이름에 단서가 든 것(단서가 든 것을 앞에)."""
    fits = [c for c, k in columns.items() if sentence_run.fits(need["kinds"], k)]
    hinted = [c for c in fits if any(h in c.casefold() for h in need["hints"])]
    return hinted if need["strict"] else hinted + [c for c in fits if c not in hinted]


def available(columns, tables=None):
    """항목마다 이 테이블(columns = {컬럼: 종류})에서 쓸 수 있는지 {id: {usable, reason, suggest, note?}}. 쓸 수 없으면 이유(‘광고비 컬럼이 있는 테이블이 필요해요’).
    suggest = {빈칸 이름: 추천 컬럼}(겹치지 않게 앞에서부터). 다른 테이블이 필요한 항목은 tables({테이블: {컬럼: 종류}})를 주면 그 중 하나가 맞는지도 본다."""
    out = {}
    for e in ENTRIES:
        missing, suggest, used = [], {}, set()
        for need in e["needs"]:
            if need["other"]:
                continue
            found = [c for c in candidates(need, columns) if c not in used] or candidates(need, columns)
            if found:
                suggest[need["name"]] = found[0]
                used.add(found[0])
            else:
                missing.append(need)
        for group in e["any_of"]:
            roles = [s for s in e["slots"] if s["role"] in group]
            if not any(candidates({"kinds": s["kinds"], "hints": s["hints"], "strict": s["strict"]}, columns) for s in roles):
                missing.append({"name": " 또는 ".join(s["name"] for s in roles)})
        item = {"usable": not missing, "reason": "", "suggest": suggest}
        if missing:
            item["reason"] = "·".join(n["name"] for n in missing) + " 컬럼이 있는 테이블이 필요해요."
        elif e["cross"]:
            near = [n for n in e["needs"] if n["other"] and n["role"] == "spend"]
            if tables is not None and near and not any(candidates(near[0], k) for k in tables.values()):
                item.update(usable=False, reason="광고비 컬럼이 있는 테이블이 필요해요.")
            else:
                item["note"] = "광고비가 든 다른 테이블과 맞춤 구절(기간 단위 또는 공통 키)을 골라야 구할 수 있어요."
        out[e["id"]] = item
    return out


# ---------- 구하기 ----------

class Ctx:
    """계산 맥락. table = 기간·조건까지 건 Ibis 표(없으면 fetch(body, 컬럼들)로 받는다), other = 다른 테이블을 이름으로 주는 함수(광고 테이블), notes = 결과에 실을 알림."""

    def __init__(self, table=None, other=None, fetch=None, align=None):
        self.table, self.other, self.fetch, self.align, self.notes = table, other, fetch, align, []


def used_columns(entry_, slots):
    """빈칸 값에서 이 테이블의 컬럼 이름들(다른 테이블 컬럼은 뺀다). 표를 PC로 받을 때 이 컬럼만 받는다."""
    found = []
    for s in entry_["slots"]:
        if s["type"] in ("column", "columns") and not s.get("other"):
            for v in ([slots.get(s["name"])] if s["type"] == "column" else slots.get(s["name"]) or []):
                name = sentence_run.name_of(v)
                if isinstance(name, str) and name and name not in found:
                    found.append(name)
    return found


def check_number(slot_, v, name):
    """숫자 칸의 값이 숫자이고 범위(min·max) 안인지."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"‘{name}’ 칸에는 숫자를 넣어 주세요.")
    low, high = slot_.get("min"), slot_.get("max")
    if (low is not None and v < low) or (high is not None and v > high):
        raise ValueError(f"‘{name}’ 칸은 {'' if low is None else f'{low:g} 이상'}{' ' if low is not None and high is not None else ''}{'' if high is None else f'{high:g} 이하'}여야 해요.")
    return v


def check_value(slot_, raw, table):
    """빈칸 하나의 값을 검사해 정리한다(컬럼은 표에 있고 종류가 맞아야 한다). 이유가 있으면 ValueError."""
    name, kind = slot_["name"], slot_["type"]
    if kind in ("column", "columns"):
        items = [raw] if kind == "column" else raw
        if not isinstance(items, list):
            raise ValueError(f"‘{name}’ 칸에는 컬럼 목록을 넣어 주세요.")
        out = []
        for item in items:
            if isinstance(item, dict) and item.get("prep"):
                raise ValueError(f"‘{name}’ 칸: 지표 사전에서는 컬럼 가공을 쓸 수 없어요. 전처리나 계산 줄로 컬럼을 먼저 만드세요.")
            col = sentence_run.name_of(item)
            parse.check_name(col, table.columns)
            found = sql_funcs.kind_of(table[col].type())
            if not sentence_run.fits(slot_["kinds"], found):
                raise ValueError(f"‘{name}’ 칸: ‘{col}’은 {flow.kind_text(found)} 컬럼이라 쓸 수 없어요({sql_funcs.kinds_text(slot_['kinds'])} 컬럼이 필요해요).")
            out.append(col)
        return out[0] if kind == "column" else out
    if kind in ("unit", "choice"):
        if raw not in slot_["options"]:
            raise ValueError(f"‘{name}’ 칸은 {', '.join(slot_['options'])} 중 하나예요.")
        return raw
    if kind == "n":
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ValueError(f"‘{name}’ 칸에는 1 이상의 정수를 넣어 주세요.")
        return check_number(slot_, raw, name)
    if kind == "number":
        return check_number(slot_, raw, name)
    if kind == "value":
        if isinstance(raw, (dict, list)) or isinstance(raw, bool):
            raise ValueError(f"‘{name}’ 칸에는 글자나 숫자 하나를 넣어 주세요.")
        return raw
    if kind == "values":
        if not isinstance(raw, list) or not raw or any(isinstance(v, (dict, list)) for v in raw):
            raise ValueError(f"‘{name}’ 칸에는 값을 하나 이상 목록으로 넣어 주세요.")
        return raw
    if not isinstance(raw, str) or not raw.strip():  # table·text
        raise ValueError(f"‘{name}’ 칸에는 글을 넣어 주세요.")
    return raw.strip()


def resolve(e, given, table):
    """빈칸 값을 검사해 역할 이름({user, time, …})으로 정리한다. 비워 둘 수 있는 칸이 비면 빠지고, 기본값이 있으면 채운다."""
    given = given if isinstance(given, dict) else {}
    out = {}
    for s in e["slots"]:
        raw = given.get(s["name"])
        if raw in (None, "", []):
            if s["default"] is not None:
                raw = s["default"]
            elif s["optional"]:
                continue
            else:
                raise ValueError(f"‘{s['name']}’ 칸을 채워 주세요.")
        out[s["role"]] = check_value(s, raw, table)
    unknown = set(given) - {s["name"] for s in e["slots"]}
    if unknown:
        raise ValueError(f"이 지표에 없는 칸이에요: {', '.join(sorted(unknown))}")
    for group in e["any_of"]:
        if not any(r in out for r in group):
            raise ValueError("‘" + "’ 또는 ‘".join(s["name"] for s in e["slots"] if s["role"] in group) + "’ 칸 중 하나는 채워 주세요.")
    if e["id"] == "ftue" and len(out["steps"]) < 2:
        raise ValueError("‘단계들’ 칸에는 단계를 2개 이상 넣어 주세요.")
    return out


def shown_sentence(e, given):
    """항목 문장의 빈칸을 고른 값으로 채운 글(안 채운 칸은 {이름} 그대로)."""
    text = e["sentence"]
    for s in e["slots"]:
        v = (given or {}).get(s["name"], s["default"])
        text = text.replace("{" + s["name"] + "}", sentence_run.shown(v) if v not in (None, "") else "{" + s["name"] + "}")
    return text


def check_limit(value, limit):
    """한계 {op: >=|<=, value}를 벗어났으면 경고 글, 아니면 None. 대시보드 카운터 한계(dash_store.check_limits)와 같은 뜻이다: >=면 값이 한계보다 작아지면, <=면 커지면 경고."""
    if value is None or not isinstance(limit, dict):
        return None
    bad = value < limit["value"] if limit["op"] == ">=" else value > limit["value"]
    return f"지금 값 {value:,.4g}이(가) 한계({limit['value']:,.4g} {'이상' if limit['op'] == '>=' else '이하'})를 벗어났어요." if bad else None


def compute(entry_, slots, body, ctx, align=None, limit=None):
    """항목을 현재 연결에서 구한다. {id, name, value, label, unit, format, better, columns, rows, parts, pair, spread, notes, text, alert?}.
    ctx.table이 없으면 ctx.fetch(body, 컬럼들)로 받는다(원격·파일 연결은 이 PC 사본). 다른 테이블이 필요한 지표는 align이 없으면 이유와 함께 거부한다."""
    import numpy as np  # 계산할 때만 올린다(프로그램이 시작할 때는 올리지 않는다)

    from metrics import metric_kernels as mk
    e = get(entry_) if isinstance(entry_, str) else entry_
    if e["cross"]:
        mk.check_align(align)
    ctx.align = align
    table = ctx.table if ctx.table is not None else ctx.fetch(body, used_columns(e, slots or {}))
    s = resolve(e, slots, table)
    got = mk.KERNELS[e["calc"]](table, s, e["params"], ctx)
    dist = got.get("dist")
    spread = None
    if e["spread"] and dist is not None and len(dist):
        spread = {**{name: float(np.quantile(dist, q)) for name, q in SPREAD}, "n": int(len(dist))}
    parts = got["parts"]
    out = {"id": e["id"], "name": e["name"], "abbr": e["abbr"], "area": e["area"], "value": got["value"], "label": got["label"], "unit": e["unit"], "format": e["format"], "better": e["better"],
           "columns": [{"key": k, "label": v} for k, v in got["cols"].items()], "rows": got["rows"], "parts": parts,
           "pair": [{"id": p, "name": part_name(p), "value": parts.get(p)} for p in e["pair"]], "spread": spread, "notes": [*got["notes"], *ctx.notes], "text": shown_sentence(e, slots)}
    if limit:
        out["alert"] = check_limit(out["value"], limit)
    return out
