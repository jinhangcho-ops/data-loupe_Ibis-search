"""대시보드 저장소: 대시보드 모음을 설정 폴더의 dashboards.dat 한 파일에 둔다. 내용 {"v":1,"blob": base64(DPAPI(JSON))}.
카드에 구한 값이 들어 있어 credentials의 DPAPI(protect·reveal)로 암호화한다(이 PC·이 계정만 풀 수 있다). 풀 수 없으면 locked로 읽고 파일은 건드리지 않는다.
쓸 때는 임시 파일에 쓴 뒤 바꿔치기하고 .bak을 한 세대 남긴다. 예전 평문 dashboards.json은 처음 읽을 때 옮기고 이름만 .migrated로 바꾼다(지우지 않는다).
연결을 가리킬 때는 계정 이름과 비밀번호를 뺀 source_key를 쓴다. 비밀번호·계정 이름은 어디에도 저장하지 않는다."""
import datetime as dt
import hashlib
import copy
import json
import os
import re
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

from core import credentials
from core import result
from core import server
from core import settings
from core import source
from core import use_counts
from core import watch

FILE = "dashboards.dat"
PLAIN = "dashboards.json"  # 예전 평문 파일(그래프 이름별 조회만 있었다)
VERSION = 1
LOCKED = "이 PC에서 만든 대시보드가 아니라 값을 읽을 수 없어요. 파일은 그대로 두었어요."
MAX_DASHBOARDS = 100  # 대시보드 수
MIN_REFRESH = 3600  # 최신화 주기의 최소(초)
MAX_CARDS = 48  # 대시보드 하나의 카드 수(템플릿이 카드가 많다)
MAX_CARD_BYTES = 400_000  # 카드 하나의 크기(JSON 글자)
MAX_STORE_BYTES = 32 * 1024 * 1024  # 전체 크기
MAX_DAYS = 3660  # 시간별 카드에 두는 일 값의 최대 일수
MAX_PALETTE = 12  # 대시보드 팔레트의 색 수
MAX_COLORS = 100  # 카드 하나의 지정 색 수
KINDS = ("chart", "analysis", "metric", "kpi", "donut_kpi", "gauge", "summary", "recent_rows", "dual_trend", "stripes", "blocks",
         "target_trend", "map", "alerts", "deviation", "funnel", "table", "status", "sentence", "geomap")  # 앞의 셋만 1단계에서 만든다. geomap: 좌표 지도(경계·점·영역별 색)
CHART_TYPES = {"bar": "막대", "hbar": "가로 막대", "stacked": "누적 막대", "line": "라인", "polyline": "꺾인 선", "area": "영역형 라인",
               "doughnut": "도넛", "pie": "파이", "radar": "레이더", "scatter": "산점도", "bubble": "버블"}
TIME_TYPES = ("bar", "stacked", "line", "polyline", "area")  # 날짜 X축 카드에서 고를 수 있는 종류
UNITS = {"day": "일", "week": "주", "month": "월", "year": "연"}
RINGS = ("stack", "double", "half", "ticks")  # 도넛 KPI 고리 4방식: 한 고리 누적, 겹친 두 고리, 반원 게이지, 고리+목표 눈금
ICONS = ("chart", "up", "user", "cart", "coin", "clock")  # KPI 카드 아이콘
FORMATS = ("number", "percent", "currency")  # 값 모양(compare.py와 같다)
MAX_SLOTS = 5  # 도넛 KPI의 칸(가운데 + 네 꼭지점)
DEFAULT_PALETTE = ["#11706f", "#e8590c", "#2b8a3e", "#c2255c", "#1c7ed6", "#7048e8", "#e67700", "#5c7080"]  # 화면의 라이트 색 묶음(--c1~--c8)과 같다
HEX = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{6})", re.I)
ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
SECRET_KEYS = {"password", "passwd", "user", "username"}  # 저장하는 값 안의 이런 이름의 항목은 거부한다
LOCK = threading.RLock()  # 읽고 고쳐 쓰는 동안(요청이 여럿 겹쳐도)


def def_hash(definition):
    """정의(JSON으로 쓸 수 있는 값)의 지문: 키를 정렬한 JSON 글자의 sha256 앞 16자."""
    return hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def finite(value):
    """dict·list 안의 숫자 값을 result.number 규칙(NaN·무한은 None, Decimal은 실수)으로 바꾼 사본. JSON에 NaN이 들어가면 브라우저가 읽지 못한다."""
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return result.number(value)


FILE_SCHEMES = ("sqlite", "duckdb")  # 주소가 파일 경로인 종류


def file_key(scheme, netloc, path):
    """파일 주소의 열쇠: scheme + 절대 경로(소문자, / 구분, 드라이브 글자 유지). ibis가 sqlite·duckdb 주소를 읽는 규칙(netloc과 path를 이어 절대 경로로)을 따르고,
    sqlite:///C:/x.db, 역슬래시를 쓴 sqlite:///C:, sqlite://C:, 그리고 sqlite://C:/x.db 처럼 같은 파일을 가리키는 쓰기는 같은 열쇠가 된다. 예외를 내지 않는다."""
    if (netloc, path) in (("", ""), ("", "/:memory:"), (":memory:", ""), ("", ":memory:")) or not (netloc + path).strip("/\\"):
        return f"url:{scheme}://:memory:"
    if netloc:
        joined = netloc + ("/" + path.lstrip("/\\") if path.strip("/\\") else "")
    else:
        joined = path[1:] if re.match(r"[/\\][A-Za-z]:", path) else path  # /C:/x → C:/x
    full = os.path.abspath(joined.replace("\\", "/"))
    return f"url:{scheme}://" + full.replace("\\", "/").lower()


def source_key(recent):
    """연결 요청 값(source.recent)에서 user·password를 뺀 열쇠. 파일 연결 = 종류 + 경로(소문자), 주소 연결 = scheme + host + port + database.
    sqlite·duckdb 주소(그리고 host·port를 읽을 수 없는 주소)는 파일 경로로 보고 file_key로 만든다. 어떤 글자가 와도 예외를 내지 않는다."""
    if recent.get("kind") == "folder":
        return "folder:" + os.path.abspath(recent.get("path", "")).replace("\\", "/").lower() + (("#" + use_counts.files_part(recent)) if use_counts.files_part(recent) else "")
    url = str(recent.get("url", "") or "")
    scheme, sep, rest = url.partition("://")
    scheme = scheme.lower()
    if not sep:
        return f"url:{url.split('@')[-1]}"  # scheme://가 없는 이상한 값: 계정 부분만 뺀다
    if scheme in FILE_SCHEMES:
        return file_key(scheme, *re.match(r"([^/\?#]*)([^?#]*)", rest).groups())
    try:
        parts = urlsplit(url)
        return f"url:{parts.scheme}://{parts.hostname or ''}:{parts.port or ''}/{parts.path.strip('/')}"
    except ValueError:  # host·port를 읽을 수 없는 주소(예: 포트가 숫자가 아님)는 경로로 본다
        netloc, path = re.match(r"([^/\?#]*)([^?#]*)", rest).groups()
        return f"url:{scheme}://{netloc.split('@')[-1].lower()}/{path.replace(chr(92), '/').strip('/')}"  # 계정 부분만 빼고 그대로


def canon_key(key):
    """저장해 둔 예전 열쇠를 지금 모양으로. 예전 sqlite·duckdb 열쇠(url:sqlite://:/경로)는 file_key로 다시 만들고, 그 밖의 열쇠는 그대로 둔다. 예외를 내지 않는다."""
    if isinstance(key, str):
        for scheme in FILE_SCHEMES:
            head = f"url:{scheme}://:/"
            if key.startswith(head):
                path = key[len(head):]
                return file_key(scheme, "", "/" + path)
    return key


def source_label(recent):
    """화면에 보일 연결 이름: 파일 연결은 폴더 이름, 주소 연결은 host/database."""
    if recent.get("kind") == "folder":
        path = recent.get("path", "")
        return Path(path).name or path
    parts = urlsplit(recent.get("url", ""))
    return (parts.hostname or "") + (f"/{parts.path.strip('/')}" if parts.path.strip("/") else "")


# ---------- 검사 ----------

def text(value, what, high, low=0):
    """글자 길이 검사(글자가 아니면 빈 글자로 본다)."""
    value = value.strip() if isinstance(value, str) else ""
    if not low <= len(value) <= high:
        raise ValueError(f"{what}은 {low}자 이상 {high}자 이하로 입력하세요." if low else f"{what}은 {high}자 이하로 입력하세요.")
    return value


def check_hex(value, what="색"):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError(f"{what} '{value}'은 #rgb나 #rrggbb 모양의 HEX 색이어야 합니다.")
    return value


def check_palette(palette):
    if not isinstance(palette, list) or len(palette) > MAX_PALETTE:
        raise ValueError(f"팔레트는 색 {MAX_PALETTE}개까지입니다.")
    return [check_hex(c, "팔레트 색") for c in palette]


def no_secrets(value, what):
    """저장할 값 안에 계정 이름·비밀번호 항목이 없는지(중첩 안까지)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in SECRET_KEYS:
                raise ValueError(f"{what}에 '{k}' 항목을 저장할 수 없습니다. 비밀번호와 계정 이름은 저장하지 않습니다.")
            no_secrets(v, what)
    elif isinstance(value, list):
        for v in value:
            no_secrets(v, what)


def check_layout(layout):
    """12칸 격자의 자리: x·y는 0 이상, w·h는 2~12, x + w는 12 이하."""
    ok = isinstance(layout, dict) and all(type(layout.get(k)) is int for k in "xywh")
    if not ok or not (0 <= layout["x"] and 0 <= layout["y"] <= 999 and 2 <= layout["w"] <= 12 and 2 <= layout["h"] <= 12 and layout["x"] + layout["w"] <= 12):
        raise ValueError("카드 자리(layout)는 12칸 격자의 x·y와 너비 w·높이 h(2~12)여야 합니다.")
    return {k: layout[k] for k in "xywh"}


def check_colors(colors):
    """지정 색 {series: {이름: hex}, parts: {이름: hex}}과 라벨이 처음 나온 순서(order·parts_order). 모르는 항목은 거부한다."""
    if colors is None:
        return {"series": {}, "parts": {}, "order": [], "parts_order": []}
    if not isinstance(colors, dict) or set(colors) - {"series", "parts", "order", "parts_order"}:
        raise ValueError("색(colors)은 series·parts 객체와 order·parts_order 목록만 쓸 수 있습니다.")
    out = {}
    for name in ("series", "parts"):
        found = colors.get(name) or {}
        if not isinstance(found, dict) or len(found) > MAX_COLORS:
            raise ValueError(f"colors.{name}은 이름별 색 객체(최대 {MAX_COLORS}개)여야 합니다.")
        out[name] = {text(k, "색의 이름", 100, 1): check_hex(v, f"'{k}'의 색") for k, v in found.items()}
    for name in ("order", "parts_order"):
        found = colors.get(name) or []
        if not isinstance(found, list) or len(found) > MAX_COLORS * 2 or not all(isinstance(v, str) and len(v) <= 100 for v in found):
            raise ValueError(f"colors.{name}은 글자 목록이어야 합니다.")
        out[name] = found
    return out


ARROWS = ("none", "up", "down", "auto")  # 조건 색 규칙의 화살표
BGS = ("rack", "network", "image")  # 배치도 바탕: 서버실 칸 / 네트워크 / 그림 파일
BLANK_KINDS = ("value", "prev", "delta", "asof")  # 문장 빈칸: 이번 기간 값 / 직전 기간 값 / 비교 문장 / 기준일
BUCKETS = ("hour", "day", "week", "month")
MAX_RULES = 20
EXTRA_VIEW = ("icon", "format", "unit_text", "ring", "slots", "target", "full", "cells", "size", "rows", "group", "window",
              "cond_rules", "rules", "stale", "entity", "time_col", "kind_col", "link_col", "links", "positions", "bg", "image_path", "group_cols", "formats", "total",
              "template", "blanks", "bucket", "baseline", "threshold", "geo")  # 3단계 카드 종류의 설정


def number_of(value, what, low=None, high=None):
    """설정의 숫자(참거짓 제외). 범위를 주면 그 안인지도 본다."""
    if type(value) not in (int, float) or (low is not None and not low <= value <= high):
        raise ValueError(f"{what}은 숫자여야 합니다." if low is None else f"{what}은 {low}~{high} 사이의 숫자여야 합니다.")
    return value


def check_more(view):
    """표·상태·경고·배치도·편차·문장 카드의 설정: cond_rules(조건 색 규칙 {column, match, color, arrow, scope}), rules(상태 규칙, watch.py), stale(끊김 분),
    entity·time_col·kind_col·link_col(엔티티·시각·종류·연결 대상 컬럼), links(연결선 [[출발, 도착]]), positions({이름: [x, y]} 0~1), bg(rack·network·image)와 image_path,
    group_cols·formats·total(표), template·blanks(문장), bucket·baseline·threshold(편차 표)."""
    out = {}
    if view.get("cond_rules") is not None:
        found = view["cond_rules"]
        if not isinstance(found, list) or len(found) > MAX_RULES or not all(isinstance(r, dict) for r in found):
            raise ValueError(f"조건 색 규칙은 객체 목록이며 {MAX_RULES}개까지입니다.")
        out["cond_rules"] = [{"column": text(r.get("column"), "규칙의 컬럼", 100, 1), "match": text(r.get("match"), "규칙의 식", 100, 1), "color": check_hex(r.get("color"), "규칙의 색"),
                              "arrow": r.get("arrow") if r.get("arrow") in ARROWS else "none", "scope": "row" if r.get("scope") == "row" else "cell"} for r in found]
    if view.get("rules") is not None:
        out["rules"] = watch.check_rules(view["rules"])
    for name, high in (("entity", 200), ("time_col", 200), ("kind_col", 200), ("link_col", 200), ("image_path", 500), ("template", 500)):
        if view.get(name):
            out[name] = text(view[name], name, high)
    if view.get("stale") is not None:
        out["stale"] = number_of(view["stale"], "끊김 분", 1, 100_000)
    for name in ("baseline", "threshold"):
        if view.get(name) is not None:
            out[name] = number_of(view[name], name, -1e15, 1e15)
    if view.get("links") is not None:
        links = view["links"]
        if not isinstance(links, list) or len(links) > 200 or not all(isinstance(l, list) and len(l) == 2 and all(isinstance(n, str) and 0 < len(n) <= 100 for n in l) for l in links):
            raise ValueError("연결선(links)은 [출발, 도착] 글자 쌍의 목록이며 200개까지입니다.")
        out["links"] = links
    if view.get("positions") is not None:
        found = view["positions"]
        if not isinstance(found, dict) or len(found) > 500 or not all(isinstance(k, str) and isinstance(v, list) and len(v) == 2 and all(type(n) in (int, float) and 0 <= n <= 1 for n in v) for k, v in found.items()):
            raise ValueError("점 위치(positions)는 {이름: [x, y]}이고 x·y는 0~1 사이 비율이어야 합니다(500개까지).")
        out["positions"] = found
    if view.get("bg"):
        if view["bg"] not in BGS:
            raise ValueError(f"배치도 바탕은 {', '.join(BGS)} 중 하나입니다.")
        out["bg"] = view["bg"]
    if view.get("bucket"):
        if view["bucket"] not in BUCKETS:
            raise ValueError(f"시간대 묶음은 {', '.join(BUCKETS)} 중 하나입니다.")
        out["bucket"] = view["bucket"]
    if view.get("group_cols") is not None:
        found = view["group_cols"]
        if not isinstance(found, list) or len(found) > 3 or not all(isinstance(c, str) and c for c in found):
            raise ValueError("표의 묶음 컬럼(group_cols)은 3개까지입니다.")
        out["group_cols"] = found
    if view.get("formats") is not None:
        found = view["formats"]
        if not isinstance(found, dict) or len(found) > 50:
            raise ValueError("표의 값 모양(formats)은 {컬럼: 모양} 객체입니다.")
        out["formats"] = {text(k, "컬럼 이름", 100, 1): v for k, v in found.items() if v in FORMATS}
    if view.get("total") is not None:
        out["total"] = bool(view["total"])
    if view.get("blanks") is not None:
        found = view["blanks"]
        if not isinstance(found, list) or len(found) > 10 or not all(isinstance(b, dict) for b in found):
            raise ValueError("문장의 빈칸(blanks)은 객체 목록이며 10개까지입니다.")
        out["blanks"] = [{"name": text(b.get("name"), "빈칸 이름", 30, 1), "y": text(b.get("y"), "빈칸 컬럼", 200), "agg": text(b.get("agg"), "빈칸 집계", 20),
                          "kind": b.get("kind") if b.get("kind") in BLANK_KINDS else "value", "format": b.get("format") if b.get("format") in FORMATS else "number"} for b in found]
    return out


GEO_AGGS = ("count", "sum", "mean", "min", "max")  # 지도 카드가 영역마다 구하는 값
GEO_KEYS = {"regions", "name", "geometry", "region_value", "lat", "lon", "value", "agg", "points", "tiles", "color"}


def check_geo(geo):
    """지도 카드(geomap)의 설정 view.geo: regions({table[, schema]} 또는 {path}: GeoJSON 영역), name(영역 이름 컬럼), geometry(도형 JSON 컬럼, 기본 geometry), region_value(영역 테이블의 값 컬럼),
    lat·lon(점 컬럼), value·agg(영역마다 구할 값: 점의 값 컬럼과 count·sum·mean·min·max), points(점 그리기), tiles(OpenStreetMap 배경, 기본 꺼짐), color(HEX 바탕 색). 영역이나 점이 있어야 한다."""
    if not isinstance(geo, dict) or set(geo) - GEO_KEYS:
        raise ValueError(f"지도 설정(geo)에 쓸 수 없는 항목이 있습니다. 쓰는 항목: {', '.join(sorted(GEO_KEYS))}")
    out = {}
    if geo.get("regions") is not None:
        regions = geo["regions"]
        if not isinstance(regions, dict) or set(regions) - {"table", "schema", "path"} or bool(regions.get("table")) == bool(regions.get("path")):
            raise ValueError("영역(regions)은 {table[, schema]} 또는 {path} 중 하나입니다.")
        out["regions"] = {k: text(v, f"영역의 {k}", 500) for k, v in regions.items() if v}
    for name in ("name", "geometry", "region_value", "lat", "lon", "value"):
        if geo.get(name):
            out[name] = text(geo[name], f"지도의 {name}", 200)
    if geo.get("agg"):
        if geo["agg"] not in GEO_AGGS:
            raise ValueError(f"영역마다 구하는 값(agg)은 {', '.join(GEO_AGGS)} 중 하나입니다.")
        out["agg"] = geo["agg"]
    for name in ("points", "tiles"):
        if name in geo:
            if not isinstance(geo[name], bool):
                raise ValueError(f"{name}은 켬(true)이나 끔(false)입니다.")
            out[name] = geo[name]
    if geo.get("color"):
        out["color"] = check_hex(geo["color"], "지도 색")
    if bool(out.get("lat")) != bool(out.get("lon")):
        raise ValueError("점은 위도 컬럼과 경도 컬럼을 함께 고르세요.")
    if not out.get("regions") and not out.get("lat"):
        raise ValueError("지도 카드에는 영역(GeoJSON 테이블)이나 점의 위도·경도 컬럼이 있어야 합니다.")
    if out.get("regions") and not out.get("name"):
        raise ValueError("영역의 이름 컬럼을 고르세요.")
    return out


def check_extra(view):
    """KPI·도넛 KPI·게이지·요약·최근 행·두 계열 추이·칸 채우기·블록·목표 추이 카드의 설정: icon, format(number·percent·currency), unit_text(값 뒤 글자),
    ring(도넛 고리 4방식), slots(도넛의 칸 {label, y, agg, format, unit_text, color}, 최대 5), target({value} 또는 목표 파일의 {name, period, scope}),
    full(칸 채우기가 가득 찬 값), cells(칸 수), size(블록 한 칸의 값), rows(최근 행 수), group(요약의 기여 분해 컬럼), window(보는 구간 {from, to})."""
    out = {}
    for name, allowed in (("icon", ICONS), ("format", FORMATS), ("ring", RINGS)):
        if view.get(name):
            if view[name] not in allowed:
                raise ValueError(f"{name}은 {', '.join(allowed)} 중 하나입니다.")
            out[name] = view[name]
    for name, high in (("unit_text", 10), ("group", 200)):
        if view.get(name):
            out[name] = text(view[name], name, high)
    for name, low, high in (("full", 0, 1e15), ("size", 0, 1e15), ("cells", 1, 100), ("rows", 1, 100)):
        if view.get(name) is not None:
            out[name] = number_of(view[name], name, low, high)
    if view.get("target") is not None:
        target = view["target"]
        if not isinstance(target, dict) or set(target) - {"value", "name", "period", "scope"} or not (("value" in target) != bool(target.get("name"))):
            raise ValueError("목표(target)는 {value: 숫자} 또는 목표 파일의 {name, period, scope} 중 하나여야 합니다.")
        out["target"] = {"value": number_of(target["value"], "목표 값")} if "value" in target else {k: text(target.get(k), f"목표의 {k}", 100) for k in ("name", "period", "scope") if target.get(k)}
    if view.get("slots") is not None:
        slots = view["slots"]
        if not isinstance(slots, list) or len(slots) > MAX_SLOTS or not all(isinstance(s, dict) for s in slots):
            raise ValueError(f"칸(slots)은 객체 목록이며 {MAX_SLOTS}개까지입니다.")
        out["slots"] = [{"label": text(s.get("label"), "칸 이름", 30), "format": s.get("format") if s.get("format") in FORMATS else "number", "unit_text": text(s.get("unit_text"), "칸 단위", 10),
                         "color": check_hex(s["color"], "칸 색") if s.get("color") else ""} for s in slots]
    out.update(check_more(view))
    if view.get("geo") is not None:
        out["geo"] = check_geo(view["geo"])
    if view.get("window") is not None:
        found = view["window"]
        if not isinstance(found, dict) or not all(isinstance(found.get(k), str) and DAY.fullmatch(found[k]) for k in ("from", "to")):
            raise ValueError("보는 구간(window)은 {from, to}(YYYY-MM-DD)여야 합니다.")
        out["window"] = {"from": found["from"], "to": found["to"]}
    return out


def check_view(view):
    """보기 설정: type(차트 종류, ""이면 계열마다 정한 대로), unit(일·주·월·연), compare.mode(span·point), better(up·down).
    차트 탭에서 온 것은 styles(계열마다 {type, dash, axis})·ranges(세로축 범위)·ratio(가로/세로), 분석 카드는 analysis_view."""
    view = {} if view is None else view
    if not isinstance(view, dict) or set(view) - {"type", "unit", "compare", "better", "styles", "ranges", "ratio", "analysis_view", "points", "trend", "show_r", *EXTRA_VIEW}:
        raise ValueError("보기(view)에 쓸 수 없는 항목이 있습니다.")
    out = {"type": view.get("type") or "", "unit": view.get("unit") or "day", "compare": view.get("compare") or {"mode": "span"}, "better": view.get("better") or "up"}
    if out["type"] and out["type"] not in CHART_TYPES:
        raise ValueError(f"차트 종류는 {', '.join(CHART_TYPES)} 중 하나입니다.")
    if out["unit"] not in UNITS:
        raise ValueError(f"시간 단위는 {', '.join(UNITS)} 중 하나입니다.")
    if not isinstance(out["compare"], dict) or out["compare"].get("mode") not in ("span", "point"):
        raise ValueError("비교 방식(compare.mode)은 span이나 point입니다.")
    out["compare"] = {"mode": out["compare"]["mode"]}
    if out["better"] not in ("up", "down"):
        raise ValueError("줄수록 좋음(better)은 up이나 down입니다.")
    if "styles" in view:
        styles = view["styles"]
        if not isinstance(styles, list) or len(styles) > MAX_COLORS or not all(isinstance(s, dict) for s in styles):
            raise ValueError("계열 모양(styles)은 계열마다 하나씩인 객체 목록이어야 합니다.")
        out["styles"] = [{"type": s.get("type") if s.get("type") in ("bar", "line") else "bar", "dash": "dashed" if s.get("dash") == "dashed" else "",
                          "axis": "right" if s.get("axis") == "right" else "left"} for s in styles]
    if "ranges" in view:
        ranges = view["ranges"]
        if not isinstance(ranges, dict) or not all(isinstance(ranges.get(a, {}), dict) and all(type(v) in (int, float) for v in ranges.get(a, {}).values()) for a in ("y", "y1")):
            raise ValueError("세로축 범위(ranges)는 y·y1의 min·max 숫자여야 합니다.")
        out["ranges"] = {a: {k: v for k, v in ranges.get(a, {}).items() if k in ("min", "max")} for a in ("y", "y1")}
    if "ratio" in view:
        if type(view["ratio"]) not in (int, float) or not 0.2 <= view["ratio"] <= 5:
            raise ValueError("가로/세로 비율(ratio)은 0.2~5 사이의 숫자입니다.")
        out["ratio"] = view["ratio"]
    if "points" in view:  # 산점도·버블: 버블의 [크기] 컬럼
        if not isinstance(view["points"], dict) or set(view["points"]) - {"size"}:
            raise ValueError("점 설정(points)은 {size: 컬럼} 모양이어야 합니다.")
        out["points"] = {"size": text(view["points"].get("size"), "크기 컬럼 이름", 200)}
    for name in ("trend", "show_r"):  # 추세선, 상관계수 r 보이기
        if name in view:
            if not isinstance(view[name], bool):
                raise ValueError(f"{name}은 켬(true)이나 끔(false)입니다.")
            out[name] = view[name]
    out.update(check_extra(view))
    if "analysis_view" in view:
        out["analysis_view"] = text(view["analysis_view"], "분석 보기 이름", 20)
    return out


def check_source(source):
    """다시 구할 기록: key·label(연결), schema·table, 그리고 body(chart·metric)·recipe+analysis(analysis)·metric_id·flow_id. 계정·비밀번호 항목은 거부한다."""
    source = {} if source is None else source
    if not isinstance(source, dict):
        raise ValueError("다시 구할 기록(source)은 객체여야 합니다.")
    if unknown := set(source) - {"key", "label", "schema", "table", "body", "recipe", "analysis", "metric_id", "flow_id", "full", "sample", "date"}:
        raise ValueError(f"source에 쓸 수 없는 항목이 있습니다: {', '.join(sorted(unknown))}")
    no_secrets(source, "source")
    out = {name: text(source.get(name), f"source.{name}", 500) for name in ("key", "label", "schema", "table", "metric_id", "flow_id", "date")}
    out["key"] = canon_key(out["key"])  # 예전 모양의 열쇠도 지금 모양으로
    now = current_source()
    if now and out["key"]:
        out["key"] = use_counts.renewed(out["key"], now["key"])  # 같은 연결의 예전 열쇠(폴더만)는 저장할 때 지금 열쇠로 덮는다
    for name in ("body", "recipe", "analysis"):
        if source.get(name) is not None:
            if not isinstance(source[name], dict):
                raise ValueError(f"source.{name}은 객체여야 합니다.")
            out[name] = source[name]
    for name in ("full", "sample"):
        if name in source:
            out[name] = source[name]
    return {k: v for k, v in out.items() if v != ""}


def check_data(data, kind):
    """구한 값: computed_at·def_hash·note·x·by_unit(시간별: 일 값과 단위별 값)·values(항목별 값, 지표, 분석 결과). recent_rows는 행 값을 거부한다."""
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValueError("구한 값(data)은 객체여야 합니다.")
    allowed = {"computed_at", "def_hash", "note", "x", "columns"} if kind == "recent_rows" else {"computed_at", "def_hash", "note", "x", "columns", "by_unit", "values", "points", "x_kind", "num_cols", "date_cols", "text", "sentences", "target", "pair", "since", "window"}
    if unknown := set(data) - allowed:
        if kind == "recent_rows":
            raise ValueError("recent_rows 카드에는 행 값을 저장할 수 없습니다. 보일 때마다 연결에서 읽습니다.")
        raise ValueError(f"data에 쓸 수 없는 항목이 있습니다: {', '.join(sorted(unknown))}")
    # 값 안(그룹 이름 "user" 등)은 사용자 데이터라 검사하지 않는다. 최상위 키는 위 허용 목록이 이미 막는다
    for name in ("computed_at", "def_hash", "note", "x"):
        if name in data and not isinstance(data[name], str):
            raise ValueError(f"data.{name}은 글자여야 합니다.")
    return finite(data)


def check_card(card):
    """카드 하나를 검사해 정리한 사본을 돌려준다(없는 항목은 기본값). 카드 하나의 크기는 MAX_CARD_BYTES(JSON 글자) 이하."""
    if not isinstance(card, dict):
        raise ValueError("카드는 객체여야 합니다.")
    if card.get("kind") not in KINDS:
        raise ValueError(f"카드 종류(kind)는 {', '.join(KINDS)} 중 하나입니다.")
    card_id = card.get("id") or secrets.token_hex(8)
    if not isinstance(card_id, str) or not ID.fullmatch(card_id):
        raise ValueError("카드 id는 영문·숫자·_·- 32자까지입니다.")
    out = {"id": card_id, "kind": card["kind"], "title": text(card.get("title"), "카드 이름", 100, 1),
           "layout": check_layout(card["layout"]) if card.get("layout") is not None else {"x": 0, "y": 0, "w": 6, "h": 6},
           "colors": check_colors(card.get("colors")), "view": check_view(card.get("view")),
           "source": check_source(card.get("source")), "data": check_data(card.get("data"), card["kind"])}
    if out["kind"] == "funnel":  # 흐름(퍼널) 카드: 다시 구할 전처리(recipe)와 흐름 분석(analysis.kind = funnel)이 있어야 한다
        how = out["source"].get("analysis") or {}
        if not isinstance(out["source"].get("recipe"), dict) or how.get("kind") != "funnel" or not isinstance(how.get("steps"), list) or not 1 <= len(how["steps"]) <= 10:
            raise ValueError("흐름 카드에는 다시 구할 분석(source.recipe, source.analysis의 kind=funnel, 단계 1~10개)이 있어야 합니다.")
    size = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if size > MAX_CARD_BYTES:
        raise ValueError(f"카드 '{out['title']}'이 {size:,}자로 카드 하나의 상한 {MAX_CARD_BYTES:,}자를 넘습니다.")
    return out


def check_every(found, name):
    """주기 {every: "7일"} (비면 쓰지 않음). 글자를 읽는 규칙은 2단계가 맡고 여기서는 모양만 본다."""
    if found is not None and not isinstance(found, dict):
        raise ValueError(f"{name}은 {{every: 글자}} 객체여야 합니다.")
    return {"every": text((found or {}).get("every"), f"{name}.every", 20)}


LIMIT_OPS = (">=", "<=")  # 카운터 지표의 한계: 값이 이 선보다 작아지면(>=) 또는 커지면(<=) 경고


def check_limits(limits, counters):
    """카운터 지표의 한계 {카드 id: {op: >=|<=, value: 숫자}}. 카운터로 고른 카드에만 둘 수 있다."""
    if limits in (None, {}):
        return {}
    if not isinstance(limits, dict) or any(k not in counters for k in limits):
        raise ValueError("카운터 지표의 한계(limits)는 카운터로 고른 카드의 {op, value}여야 합니다.")
    out = {}
    for card_id, item in limits.items():
        if not isinstance(item, dict) or item.get("op") not in LIMIT_OPS or isinstance(item.get("value"), bool) or not isinstance(item.get("value"), (int, float)):
            raise ValueError(f"카운터 지표의 한계는 연산자({', '.join(LIMIT_OPS)})와 숫자여야 합니다.")
        out[card_id] = {"op": item["op"], "value": item["value"]}
    return out


MAX_TEMPLATES = 30  # 내 템플릿 수
TEMPLATES = "dash_templates.json"  # 설정 폴더의 내 템플릿 {templates: [{id, name, cards: [{kind, title, layout, colors}]}]}. 값·연결·조회는 저장하지 않는다


def check_template(t):
    """내 템플릿 하나를 검사해 정리한 사본(kind·title·layout·colors만 남긴다). source·data는 받지 않고 버린다."""
    if not isinstance(t, dict):
        raise ValueError("템플릿은 객체여야 합니다.")
    cards = t.get("cards")
    if not isinstance(cards, list) or not 1 <= len(cards) <= MAX_CARDS:
        raise ValueError(f"템플릿에는 카드가 1개 이상 {MAX_CARDS}개 이하여야 합니다.")
    out = []
    for c in cards:
        if not isinstance(c, dict) or c.get("kind") not in KINDS:
            raise ValueError(f"템플릿 카드의 종류(kind)는 {', '.join(KINDS)} 중 하나입니다.")
        out.append({"kind": c["kind"], "title": text(c.get("title"), "카드 이름", 100, 1), "layout": check_layout(c.get("layout")), "colors": check_colors(c.get("colors"))})
    return {"id": t.get("id") or secrets.token_hex(8), "name": text(t.get("name"), "템플릿 이름", 50, 1), "cards": out}


def load_templates():
    """저장한 내 템플릿 목록(깨진 항목은 뺀다)."""
    found = []
    for t in (settings.read(TEMPLATES) or {}).get("templates", []):
        try:
            found.append(check_template(t))
        except ValueError:
            continue
    return found


def save_template(t):
    """내 템플릿을 저장한다(같은 이름이면 바꾼다). 저장한 목록을 돌려준다."""
    new = check_template(t)
    with settings.updating(TEMPLATES):
        found = [x for x in load_templates() if x["name"] != new["name"]]
        if len(found) >= MAX_TEMPLATES:
            raise ValueError(f"내 템플릿은 {MAX_TEMPLATES}개까지 저장할 수 있습니다. 쓰지 않는 것을 지우세요.")
        found.append(new)
        settings.write(TEMPLATES, {"templates": found})
    return found


def delete_template(template_id):
    with settings.updating(TEMPLATES):
        found = [x for x in load_templates() if x["id"] != template_id]
        settings.write(TEMPLATES, {"templates": found})
    return found


def check_dashboard(d, old=None):
    """대시보드 하나를 검사해 정리한 사본을 돌려준다(id·카드 id는 없으면 새로 만든다). 이름 겹침과 개수는 저장할 때 본다.
    old: 이미 저장된 같은 대시보드. 기준일이 저장된 값 그대로이면 오늘 뒤인지는 다시 보지 않는다(예전에 미래 날짜로 저장된 것도 카드 옮기기 같은 다른 편집은 저장되게)."""
    if not isinstance(d, dict):
        raise ValueError("대시보드는 객체여야 합니다.")
    dash_id = d.get("id") or secrets.token_hex(8)
    if not isinstance(dash_id, str) or not ID.fullmatch(dash_id):
        raise ValueError("대시보드 id는 영문·숫자·_·- 32자까지입니다.")
    cards = d.get("cards") or []
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        raise ValueError(f"카드는 {MAX_CARDS}개까지 넣을 수 있습니다.")
    cards = [check_card({**c, "layout": c.get("layout") or {"x": i % 2 * 6, "y": i // 2 * 6, "w": 6, "h": 6}} if isinstance(c, dict) else c) for i, c in enumerate(cards)]
    ids = [c["id"] for c in cards]
    if len(set(ids)) != len(ids):
        raise ValueError("카드 id가 겹칩니다.")
    as_of = d.get("as_of") or ""
    if as_of:
        try:
            if not DAY.fullmatch(as_of):
                raise ValueError
            if dt.date.fromisoformat(as_of) > dt.date.today() and as_of != (old or {}).get("as_of"):
                raise OverflowError
        except (ValueError, TypeError):
            raise ValueError("기준일(as_of)은 비워 두거나 YYYY-MM-DD 모양의 있는 날짜여야 합니다.") from None
        except OverflowError:
            raise ValueError("기준일(as_of)은 오늘 뒤의 날은 쓸 수 없습니다.") from None
    star = d.get("north_star") or {}
    if not isinstance(star, dict) or not isinstance(star.get("counters", []), list):
        raise ValueError("북극성(north_star)은 {card, counters: [카드 id]} 모양이어야 합니다.")
    north = {"card": star.get("card") or "", "counters": star.get("counters", [])}
    if limits := check_limits(star.get("limits"), north["counters"]):
        north["limits"] = limits
    if any(c and c not in ids for c in [north["card"], *north["counters"]]):
        raise ValueError("북극성·카운터 지표로 고른 카드가 이 대시보드에 없습니다.")
    return {"id": dash_id, "name": text(d.get("name"), "대시보드 이름", 50, 1), "template": text(d.get("template"), "템플릿 이름", 50),
            "palette": check_palette(d.get("palette") or []), "period": text(d.get("period") or "", "기간", 40), "as_of": as_of,
            "refresh": check_every(d.get("refresh"), "refresh"), "live": check_every(d.get("live"), "live"), "cards": cards, "created": text(d.get("created"), "만든 시각", 40),
            "updated": text(d.get("updated"), "고친 시각", 40), "north_star": north}


# ---------- 읽고 쓰기 ----------

def path_of(name=FILE):
    return settings.PATH.parent / name


def now_text():
    return dt.datetime.now().isoformat(timespec="seconds")


CACHE = {"stamp": None, "text": None, "dashboards": None}  # 마지막으로 푼 파일의 (경로, mtime_ns, 크기), 푼 JSON 글자, 읽기 전용으로 쓰는 푼 결과


def unpack(text):
    """저장된 JSON 글자를 푼다. NaN·무한(Infinity)은 풀면서 None으로(JSON에 NaN이 있으면 브라우저가 읽지 못한다. 값마다 되짚지 않는다)."""
    return json.loads(text, parse_constant=lambda _: None)


def tidy(dashboards):
    """푼 모음을 고쳐 쓴다: 예전 모양의 연결 열쇠를 지금 모양으로(다음에 쓸 때 저장된다). NaN·무한은 unpack이 이미 None으로 풀었다."""
    for d in dashboards.values():
        for c in d.get("cards", []) if isinstance(d, dict) else []:
            if isinstance(c, dict) and isinstance(c.get("source"), dict) and c["source"].get("key"):
                c["source"]["key"] = canon_key(c["source"]["key"])
    return dashboards


def load(fresh=False):
    """저장된 대시보드 {"dashboards": {id: 대시보드}, "locked": 풀 수 없는지}. 풀 수 없으면(다른 PC·계정, 깨짐) 빈 모음과 locked=True이고 파일은 지우지 않는다.
    .dat이 없고 예전 평문 dashboards.json이 있으면 먼저 옮긴다. 파일이 그대로((경로, mtime_ns, 크기)가 같으면) 다시 풀지 않고 푼 결과를 쓴다:
    그 결과는 읽기 전용이다(고쳐 쓸 때는 fresh=True로 새 사본을 받는다)."""
    with LOCK:
        migrate_plain()
        path = path_of()
        if not path.exists():
            return {"dashboards": {}, "locked": False}
        info = path.stat()
        stamp = (str(path), info.st_mtime_ns, info.st_size)
        if CACHE["stamp"] != stamp:
            found = settings.read(FILE) or {}
            blob = found.get("blob") if found.get("v") == VERSION else None
            opened = credentials.reveal(blob) if isinstance(blob, str) else None
            try:
                dashboards = unpack(opened)["dashboards"] if opened else None
            except (ValueError, KeyError, TypeError):
                dashboards = None
            if not isinstance(dashboards, dict):
                return {"dashboards": {}, "locked": True}
            CACHE.update(stamp=stamp, text=opened, dashboards=tidy(dashboards))
        return {"dashboards": tidy(unpack(CACHE["text"])["dashboards"]) if fresh else CACHE["dashboards"], "locked": False}


def write(dashboards):
    """모음 전체를 암호화해 쓴다. 임시 파일에 쓴 뒤 바꿔치기하고, 바꾸기 전 파일은 .bak으로 한 세대 남긴다(중간에 꺼져도 깨지지 않는다)."""
    body = json.dumps({"dashboards": dashboards}, ensure_ascii=False, separators=(",", ":"))
    size = len(body.encode("utf-8"))
    if size > MAX_STORE_BYTES:
        raise ValueError(f"대시보드 전체가 {size / 1024 / 1024:.1f}MB로 상한 {MAX_STORE_BYTES // 1024 // 1024}MB를 넘습니다. 쓰지 않는 대시보드를 지우세요.")
    path = path_of()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps({"v": VERSION, "blob": credentials.protect(body)}), encoding="utf-8")
    settings.backup(path)
    os.replace(temp, path)


def editable():
    """읽고 고쳐 쓸 모음(새 사본). 풀 수 없는 파일이면 덮어쓰지 않도록 오류를 낸다."""
    store = load(fresh=True)
    if store["locked"]:
        raise ValueError(LOCKED)
    return store["dashboards"]


def listing():
    """목록 [{id, name, cards(수), updated}]을 이름 순으로."""
    store = load()
    items = [{"id": d["id"], "name": d["name"], "cards": len(d.get("cards", [])), "updated": d.get("updated", "")} for d in store["dashboards"].values()]
    return {"dashboards": sorted(items, key=lambda i: i["name"]), "locked": store["locked"]}


def get_dashboard(dash_id):
    found = load()["dashboards"].get(dash_id)
    if found is None:
        raise ValueError("대시보드를 찾을 수 없습니다.")
    return copy.deepcopy(found)


def keep_newer(d, old):
    """자동 저장이 최신화가 쓴 값을 옛 값으로 덮지 않게: 저장된 카드의 data가 들어온 것보다 늦게 구했고(computed_at) 정의 지문(def_hash)이 같으면 저장된 data를 둔다."""
    saved = {c["id"]: c.get("data") or {} for c in (old or {}).get("cards", []) if isinstance(c, dict)}
    for card in d["cards"]:
        was, now = saved.get(card["id"]) or {}, card["data"]
        if was.get("def_hash") and was.get("def_hash") == now.get("def_hash") and str(was.get("computed_at") or "") > str(now.get("computed_at") or ""):
            card["data"] = was


def save_dashboard(d):
    """대시보드 하나를 검사해 저장하고(id가 같으면 덮어쓴다) 저장한 모양을 돌려준다. 이름이 겹치거나 100개를 넘으면 오류."""
    with LOCK:
        dashboards = editable()
        d = check_dashboard(d, dashboards.get(d.get("id")) if isinstance(d, dict) and isinstance(d.get("id"), str) else None)
        old = dashboards.get(d["id"])
        if any(o["name"] == d["name"] and o["id"] != d["id"] for o in dashboards.values()):
            raise ValueError(f"'{d['name']}' 이름의 대시보드가 이미 있습니다.")
        if old is None and len(dashboards) >= MAX_DASHBOARDS:
            raise ValueError(f"대시보드는 {MAX_DASHBOARDS}개까지 저장할 수 있습니다. 쓰지 않는 것을 지우세요.")
        keep_newer(d, old)
        d["created"], d["updated"] = (old or {}).get("created") or now_text(), now_text()
        write({**dashboards, d["id"]: d})
        return d


def delete_dashboard(dash_id):
    with LOCK:
        dashboards = editable()
        if dash_id not in dashboards:
            raise ValueError("대시보드를 찾을 수 없습니다.")
        write({k: v for k, v in dashboards.items() if k != dash_id})


def add_card(dash_id, name, card):
    """저장한 대시보드(dash_id) 아래에 카드를 더한다. dash_id가 비면 name으로 새 대시보드를 만든다. 새 카드는 맨 아래 왼쪽에 놓는다."""
    with LOCK:
        if dash_id:
            d = {**get_dashboard(dash_id)}
        else:
            d = {"name": name, "cards": []}
        bottom = max([c["layout"]["y"] + c["layout"]["h"] for c in d["cards"]], default=0)
        return save_dashboard({**d, "cards": [*d["cards"], {**card, "layout": {"x": 0, "y": min(bottom, 999), "w": 6, "h": 6}}]})


KEPT = "kept"  # set_cards_data: 저장된 값이 더 늦게 구한 것이라 새 값을 쓰지 않았다


def set_cards_data(dash_id, items):
    """저장한 대시보드의 카드들의 값(data)만 한 번에 바꾼다(최신화 결과). items: [(카드 id, data, same)]. 저장소는 한 번만 풀고 한 번만 쓴다(카드마다 하면 큰 저장소는 장당 몇 초).
    카드 크기 상한을 다시 검사한다. 카드마다 결과: 카드가 없으면(그사이 지워졌으면) False, same(지금 저장된 카드 → 참거짓)이 거짓(그사이 정의를 고쳤다)이면 쓰지 않고 None, 썼으면 True(저장된 값이 더 늦게 구한 것이라 그대로 뒀으면 KEPT), 이 카드의 값이 검사에 걸리면 그 오류(다른 카드는 쓴다)."""
    with LOCK:
        dashboards = editable()
        d, found = dashboards.get(dash_id), []
        for card_id, data, same in items:
            card = next((c for c in (d or {}).get("cards", []) if c["id"] == card_id), None)
            if card is None:
                found.append(False)
            elif same is not None and not same(card):
                found.append(None)
            else:
                try:
                    new = {"cards": [check_card({**card, "data": data})]}
                    keep_newer(new, {"cards": [card]})  # 저장된 값이 더 늦게 구한 것이면(같은 정의) 그것을 둔다
                    kept = new["cards"][0]["data"] is not None and new["cards"][0]["data"] is card.get("data")  # 저장값이 이겼다(쓴 것이 없다)
                    card["data"] = new["cards"][0]["data"]
                    found.append(KEPT if kept else True)
                except Exception as e:  # 이 카드만 실패(예: 크기 상한). 나머지 카드는 쓴다
                    found.append(e)
        if True in found:
            write(dashboards)
        return found


def update_settings(dash_id, fields):
    """저장한 대시보드의 기준일(as_of)·기간(period)·최신화(refresh)·실시간(live)만 바꾼다(카드는 그대로). 바뀐 대시보드를 돌려준다."""
    with LOCK:
        dashboards = editable()
        if dash_id not in dashboards:
            raise ValueError("대시보드를 찾을 수 없습니다.")
        old = dashboards[dash_id]
        new = check_dashboard({**old, **{k: v for k, v in fields.items() if k in ("as_of", "period", "refresh", "live")}}, old)
        dashboards[dash_id] = {**old, **{k: new[k] for k in ("as_of", "period", "refresh", "live")}}
        write(dashboards)
        return dashboards[dash_id]


# ---------- 예전 평문 파일 옮기기 ----------

def plain_card(chart):
    """예전 그래프 하나({title, ratio, ranges, query, used, kind})를 새 카드로. 값은 없다(다시 구해야 한다)."""
    query, used = chart.get("query") or {}, chart.get("used") or []
    kind = "metric" if chart.get("kind") == "metric" else "chart"
    view = {"ratio": chart["ratio"]} if type(chart.get("ratio")) in (int, float) else {}
    if isinstance(chart.get("ranges"), dict):
        view["ranges"] = chart["ranges"]
    if kind == "chart":
        view["styles"] = [u for u in used if isinstance(u, dict)]
    return {"kind": kind, "title": str(chart.get("title") or "그래프")[:100], "view": view,
            "source": {"schema": query.get("schema") or "", "table": query.get("base") or "", "body": query}}


def migrate_plain():
    """.dat이 없고 예전 평문 dashboards.json이 있으면 새 모양으로 바꿔 .dat에 쓰고 평문은 dashboards.json.migrated로 이름만 바꾼다(지우지 않는다).
    새 모양으로 바꿀 수 없는 그래프는 빼고 옮긴다(원본은 .migrated에 그대로 있다)."""
    plain = path_of(PLAIN)
    if path_of().exists() or not plain.exists():
        return
    old = settings.read_file(plain)
    if old is None:
        return
    found = {}
    for name, board in old.items():
        cards = []
        for chart in (board.get("charts") if isinstance(board, dict) else None) or []:
            try:
                cards.append(check_card(plain_card(chart)))
            except (ValueError, TypeError, AttributeError):
                continue
        if isinstance(name, str) and 1 <= len(name.strip()) <= 50:
            d = check_dashboard({"name": name.strip(), "cards": cards[:MAX_CARDS]})
            d["created"] = d["updated"] = now_text()
            found[d["id"]] = d
    write(found)
    os.replace(plain, plain.with_name(plain.name + ".migrated"))


def current_source():
    """지금 연결의 {key, label}(비밀번호·계정 이름 없음). 연결이 없으면 None."""
    if server.state["opened"] is None:
        return None
    recent = {**source.recent(server.state["opened"]), **{k: v for k, v in server.state["opened"].items() if k in ("file", "files")}}
    return {"key": source_key(recent), "label": source_label(recent)}
