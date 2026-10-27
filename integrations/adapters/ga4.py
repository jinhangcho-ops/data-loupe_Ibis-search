"""Google Analytics 4에서 가져오기 어댑터: 서비스 계정 JSON으로 토큰을 받아 GA4 Data API(runReport, REST)로 보고서를 받는다.
- 인증: 서비스 계정 JSON의 개인 키로 JWT(RS256)를 서명해 토큰 주소에서 접근 토큰(읽기 전용 범위)을 받는다. 서명은 cryptography.
  JSON 파일은 앱에 저장하지 않고 경로만 둔다(파일 안의 개인 키는 그 파일에만 있다).
- 보고서: 측정기준(dimensions)·측정항목(metrics)·기간을 받아 쪽(PAGE_ROWS행)씩 받고, 수는 측정항목 타입대로 정수·실수로 바꾼다.
GA4 속성에 그 서비스 계정 이메일을 [뷰어]로 추가해 두어야 한다."""
import base64
import json
import re
import time
import urllib.parse
from pathlib import Path

from integrations.adapters import transport

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
API = "https://analyticsdata.googleapis.com/v1beta"
PAGE_ROWS = 100_000  # runReport 한 번에 받을 행(API 상한 250,000)
MAX_ROWS = 2_000_000  # 한 번에 가져올 최대 행
MAX_FIELDS = 9  # 측정기준·측정항목 수(API 상한: 측정기준 9, 측정항목 10)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|today|yesterday|\d+daysAgo")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_:]{0,99}")
INTEGER_TYPES = ("TYPE_INTEGER",)
TOKEN_URI = "https://oauth2.googleapis.com/token"


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def read_key(path):
    """서비스 계정 JSON을 읽어 {client_email, private_key, token_uri}. 오류 문구에는 키를 넣지 않는다."""
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise ValueError(f"서비스 계정 JSON을 읽을 수 없습니다: {e.strerror or e}") from None
    except ValueError:
        raise ValueError("서비스 계정 JSON의 형식이 올바르지 않습니다.") from None
    if not isinstance(found, dict) or found.get("type") != "service_account" or not found.get("client_email") or not found.get("private_key"):
        raise ValueError('서비스 계정 키 JSON이 아닙니다("type": "service_account", client_email, private_key가 있어야 함).')
    token_uri = found.get("token_uri") or TOKEN_URI
    if urllib.parse.urlsplit(token_uri).scheme != "https":
        raise ValueError("token_uri는 https여야 합니다.")
    return {"client_email": found["client_email"], "private_key": found["private_key"], "token_uri": token_uri}


def assertion(key):
    """서비스 계정 JWT(RS256)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(json.dumps({"iss": key["client_email"], "scope": SCOPE, "aud": key["token_uri"], "iat": now, "exp": now + 3600}).encode())
    try:
        private = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    except (ValueError, TypeError):
        raise ValueError("서비스 계정 JSON의 private_key를 읽을 수 없습니다.") from None
    signature = private.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{b64(signature)}"


def access_token(key):
    """토큰 주소에서 접근 토큰을 받는다(1시간짜리, 저장하지 않음)."""
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion(key)}).encode()
    try:
        reply = json.loads(transport.post(key["token_uri"], body, {"Content-Type": "application/x-www-form-urlencoded"}))
    except ValueError as e:
        raise ValueError(f"Google 인증에 실패했습니다: {e}") from None
    if not reply.get("access_token"):
        raise ValueError("Google 인증 답에 접근 토큰이 없습니다.")
    return reply["access_token"]


def property_id(value):
    value = str(value).strip().removeprefix("properties/")
    if not value.isdigit():
        raise ValueError("GA4 속성 ID는 숫자입니다(관리 → 속성 설정의 속성 ID).")
    return value


def names(value, label, limit):
    items = [n.strip() for n in (value.split(",") if isinstance(value, str) else value or []) if str(n).strip()]
    bad = [n for n in items if not NAME.fullmatch(n)]
    if bad:
        raise ValueError(f"{label} 이름이 올바르지 않습니다: {', '.join(bad[:5])} (예: date, country, activeUsers)")
    if len(items) > limit:
        raise ValueError(f"{label}은 {limit}개까지입니다.")
    return items


def check_report(spec):
    """보고서 요청 {property, start, end, dimensions, metrics}를 검사해 정리한다."""
    if not isinstance(spec, dict):
        raise ValueError("보고서 설정이 올바르지 않습니다.")
    dims, mets = names(spec.get("dimensions", ""), "측정기준", MAX_FIELDS), names(spec.get("metrics", ""), "측정항목", MAX_FIELDS + 1)
    if not mets:
        raise ValueError("측정항목을 하나 이상 넣으세요(예: activeUsers, sessions).")
    start, end = str(spec.get("start", "")).strip(), str(spec.get("end", "")).strip()
    if not DATE.fullmatch(start) or not DATE.fullmatch(end):
        raise ValueError("기간은 YYYY-MM-DD, today, yesterday, NdaysAgo 중 하나로 쓰세요.")
    return {"property": property_id(spec.get("property", "")), "start": start, "end": end, "dimensions": dims, "metrics": mets}


def run_report(token, spec, offset, limit):
    body = {"dateRanges": [{"startDate": spec["start"], "endDate": spec["end"]}], "dimensions": [{"name": d} for d in spec["dimensions"]],
            "metrics": [{"name": m} for m in spec["metrics"]], "limit": limit, "offset": offset, "keepEmptyRows": False}
    try:
        return json.loads(transport.post(f"{API}/properties/{spec['property']}:runReport", json.dumps(body).encode(),
                                         {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, 120))
    except ValueError as e:
        raise ValueError(f"GA4 보고서를 받지 못했습니다: {e}") from None


def fetch(key_path, spec, notes=None):
    """보고서 전체를 받아 Polars 표로. 측정기준은 글자, 측정항목은 타입대로 정수·실수. (표, 전체 행 수). 쪽마다 표로 만들어 모은다(파이썬 목록에 행을 쌓지 않는다).
    notes(목록)를 주면 GA4가 알린 데이터 손실·표본 추출 안내를 넣는다."""
    import polars as pl
    spec = check_report(spec)
    token = access_token(read_key(key_path))
    columns = spec["dimensions"] + spec["metrics"]
    frames, total, offset, types = [], None, 0, {}
    while total is None or offset < total:
        page = run_report(token, spec, offset, PAGE_ROWS)
        total = int(page.get("rowCount", 0))
        if total > MAX_ROWS:
            raise ValueError(f"보고서가 {total:,}행으로 너무 큽니다({MAX_ROWS:,}행까지). 기간이나 측정기준을 줄이세요.")
        types = {h["name"]: h.get("type", "") for h in page.get("metricHeaders", [])} or types
        meta = page.get("metadata") or {}
        if notes is not None and offset == 0:
            if meta.get("dataLossFromOtherRow"):
                notes.append("GA4가 행이 많아 나머지를 '(other)' 한 줄로 합쳤어요. 측정기준을 줄이거나 기간을 나눠 받으세요.")
            if meta.get("samplingMetadatas"):
                notes.append("GA4가 표본 추출한 값이에요(전체 데이터가 아님). 기간을 줄이면 표본 없이 받을 수 있어요.")
        got = page.get("rows", [])
        if not got:
            break
        frames.append(pl.DataFrame([[v.get("value") for v in r.get("dimensionValues", [])] + [v.get("value") for v in r.get("metricValues", [])] for r in got],
                                   schema={c: pl.String for c in columns}, orient="row"))
        offset += len(got)
    frame = pl.concat(frames) if frames else pl.DataFrame(schema={c: pl.String for c in columns})
    frame = frame.with_columns(pl.col(m).cast(pl.Int64 if types.get(m) in INTEGER_TYPES else pl.Float64, strict=False) for m in spec["metrics"])
    return frame, total or 0


def check(key_path, prop):
    """보고서를 받지 않고 확인: 서비스 계정으로 토큰을 받고, 그 속성의 메타데이터를 읽어 본다."""
    key = read_key(key_path)
    token = access_token(key)
    try:
        meta = json.loads(transport.request("GET", f"{API}/properties/{property_id(prop)}/metadata", None, {"Authorization": f"Bearer {token}"}))
    except ValueError as e:
        raise ValueError(f"토큰은 받았지만 속성을 읽지 못했습니다(속성에 {key['client_email']}을 뷰어로 추가했는지 확인): {e}") from None
    return f"연결됨: {key['client_email']} · 측정기준 {len(meta.get('dimensions', []))}개, 측정항목 {len(meta.get('metrics', []))}개를 쓸 수 있습니다."
