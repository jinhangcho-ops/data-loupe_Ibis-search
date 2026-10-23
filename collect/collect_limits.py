"""수집의 사용 한도: 묶음(키 하나에 걸린 한도)마다 초·분·시간·하루 횟수를 센다. 다시 시도와 실패한 요청도 센다.
초·분·시간은 보내는 순간 기준의 이동 창, 하루는 묶음의 초기화 시각(reset·tz) 기준 기간이다.
기록은 설정 폴더의 USAGE 파일에 남겨 다시 켜도 이어지고, 잠금 파일로 다른 프로세스(다른 창)와 함께 써도 한도를 지킨다.
기록 파일을 읽지 못하면 한도를 다 쓴 것으로 본다(broken). 초기화(reset)로만 풀린다."""
import datetime as dt
import json
import math
import os
import re
import threading
import time
import zoneinfo

from core import settings
from collect.collect_http import STOPPED, Halt

GROUPS = "limits.json"  # 묶음 {이름: 묶음}
USAGE = "limits_usage.json"  # 사용 기록 {이름: {"sent": [보낸 시각…(최근 1시간)], "day": [기간 시작, 횟수]}}
LOCK = "limits_usage.lock"
STALE_LOCK = 10  # 이보다 오래된 잠금 파일은 끝난 프로세스가 남긴 것으로 보고 지운다(초).
MAX_GROUPS = 50
MAX_GROUP_SIZE = 2_000
WINDOWS = {"per_second": 1, "per_minute": 60, "per_hour": 3600}
DEFAULT = {"per_second": None, "per_minute": None, "per_hour": None, "per_day": None, "concurrency": 2, "min_interval": 0.0, "reset": "00:00", "tz": "Asia/Seoul", "margin": 0}
NUMBERS = {"concurrency": (int, 1, 32, "동시 수(concurrency)는", ""), "min_interval": (float, 0, 3600, "최소 간격(min_interval)은", "초"),
           "margin": (float, 0, 50, "여유(margin)는", "%")}  # (종류, 최소, 최대, 오류 문구의 앞, 단위)
SEOUL = dt.timezone(dt.timedelta(hours=9), "Asia/Seoul")
server_seen = {}  # 묶음 이름 → 서버가 알려 준 남은 횟수·초기화 {"remaining", "reset"} (메모리에만)
here = threading.Lock()  # 같은 프로세스 안의 스레드끼리


def groups():
    return settings.named(GROUPS)


def check(group):
    """묶음 값 검사. 고친 묶음을 돌려준다."""
    if not isinstance(group, dict) or set(group) - set(DEFAULT):
        raise ValueError(f"사용 한도에 쓸 수 없는 값이 있습니다: {', '.join(sorted(set(group) - set(DEFAULT))) if isinstance(group, dict) else '모양'}")
    found = {**DEFAULT, **group}
    for key in ("per_second", "per_minute", "per_hour", "per_day"):
        if found[key] is not None and (type(found[key]) is not int or found[key] < 1):
            raise ValueError(f"'{key}'는 비우거나 1 이상의 정수로 쓰세요.")
    for key, (kind, low, high, label, unit) in NUMBERS.items():
        if type(found[key]) not in ((int,) if kind is int else (int, float)) or not low <= found[key] <= high:
            raise ValueError(f"{label} {low}~{high}{unit}{'의 정수' if kind is int else ''}로 쓰세요.")
    if not isinstance(found["reset"], str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", found["reset"]):
        raise ValueError("초기화 시각(reset)은 HH:MM 모양으로 쓰세요.")
    zone(found["tz"])
    return found


def save(name, group):
    """묶음을 저장한다(None이면 지운다). 지우면 사용 기록도 지운다."""
    if group is None:
        found = settings.save_named(GROUPS, "묶음", MAX_GROUP_SIZE, name, None, MAX_GROUPS)
        reset(name.strip() if isinstance(name, str) else "")
        return found
    return settings.save_named(GROUPS, "묶음", MAX_GROUP_SIZE, name, check(group), MAX_GROUPS)


def zone(tz):
    """tz 이름을 시간대로. local은 이 PC의 시간대. Windows에 시간대 자료(tzdata)가 없으면 Asia/Seoul은 UTC+9로 대신한다."""
    if tz == "local":
        return dt.datetime.now().astimezone().tzinfo
    if tz == "UTC":
        return dt.timezone.utc
    if not isinstance(tz, str):
        raise ValueError("시간대(tz)가 올바르지 않습니다.")
    try:
        return zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        if tz == "Asia/Seoul":
            return SEOUL
        raise ValueError(f"알 수 없는 시간대입니다: {tz}") from None


def period(group, now):
    """now(초)가 든 하루 기간의 (시작 초, 끝 초). 기간은 묶음의 tz에서 reset 시각에 바뀐다."""
    tz = zone(group["tz"])
    local = dt.datetime.fromtimestamp(now, tz)
    hour, minute = map(int, group["reset"].split(":"))
    start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if start > local:
        start -= dt.timedelta(days=1)
    end = (start + dt.timedelta(days=1)).replace(hour=hour, minute=minute)  # 하루 뒤 같은 시각(서머타임이 있어도)
    return start.timestamp(), end.timestamp()


def reset_text(group, now):
    end = dt.datetime.fromtimestamp(period(group, now)[1], zone(group["tz"]))
    return f"{end:%Y-%m-%d %H:%M} ({group['tz']})"


def allowed(group, key):
    """여유(margin)를 뺀 한도. 한도가 없으면 None. 적어도 1."""
    limit = group.get(key)
    return None if limit is None else max(1, math.floor(limit * (1 - group.get("margin", 0) / 100)))


class Locked:
    """설정 폴더의 잠금 파일을 배타적으로 만들어 잡는다. 오래된 잠금 파일은 지우고 다시 잡는다."""

    def __enter__(self):
        settings.PATH.parent.mkdir(parents=True, exist_ok=True)
        path = settings.PATH.parent / LOCK
        here.acquire()
        while True:
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > STALE_LOCK:
                        path.unlink()
                except OSError:
                    pass
                time.sleep(0.01)
            except OSError:
                here.release()
                raise

    def __exit__(self, *args):
        try:
            (settings.PATH.parent / LOCK).unlink()
        except OSError:
            pass
        here.release()


def load():
    """사용 기록 전체. 파일이 없으면 빈 것, 읽을 수 없으면 None(broken)."""
    path = settings.PATH.parent / USAGE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.values():
            if not all(isinstance(t, (int, float)) for t in item["sent"]) or len(item["day"]) != 2:
                raise ValueError
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def item_of(data, name):
    """묶음 name의 사용 기록. 없으면 새 빈 기록(take가 고쳐 쓰므로 매번 새로 만든다)."""
    return data.get(name, {"sent": [], "day": [0, 0]})


def write(data):
    """사용 기록을 임시 파일에 쓴 뒤 바꿔치기한다. 바꾸기 전에 원래 파일을 .bak으로 남긴다(깨진 기록은 .bak으로 되살리지 않고 한도를 다 쓴 것으로 본다)."""
    path = settings.PATH.parent / USAGE
    settings.backup(path)
    settings.write_atomic(path, data)


def counts(item, group, now):
    """기록 하나에서 (초, 분, 시간 안의 보낸 수, 오늘 기간의 수)."""
    sent = item["sent"]
    start = period(group, now)[0]
    day = item["day"][1] if item["day"][0] == start else 0
    return [sum(1 for t in sent if t > now - w) for w in WINDOWS.values()], day


def usage(name, now=None):
    """묶음의 사용량(약속의 모양). 묶음이 없으면 None."""
    group = groups().get(name)
    if group is None:
        return None
    now = time.time() if now is None else now
    data = load()
    item = item_of(data or {}, name)
    (second, minute, hour), day = counts(item, group, now)
    limit = allowed(group, "per_day")
    return {"second": second, "minute": minute, "hour": hour, "day": day, "day_limit": group["per_day"],
            "day_left": None if limit is None else 0 if data is None else max(0, limit - day), "reset_at": reset_text(group, now),
            "server": server_seen.get(name), "broken": data is None}


def broken_message():
    return "사용 기록 파일을 읽지 못해 한도를 다 쓴 것으로 봅니다. 사용 한도에서 [초기화]를 누른 뒤 다시 시작하세요."


def take(name, stop):
    """묶음 name의 한도 안에서 요청 하나를 보낼 수 있을 때까지 기다렸다가 기록한다. 하루 한도에 닿으면 Halt.
    stop(Event)이 켜지면 기다리기를 그만둔다."""
    while True:
        group = groups().get(name)
        if group is None:
            raise Halt(f"사용 한도 묶음 '{name}'이 없습니다.")
        with Locked():
            data, now = load(), time.time()
            if data is None:
                raise Halt(broken_message())
            item = item_of(data, name)
            item["sent"] = [t for t in item["sent"] if t > now - 3600]
            windows, day = counts(item, group, now)
            if allowed(group, "per_day") is not None and day >= allowed(group, "per_day"):
                raise Halt(f"오늘 한도를 다 썼습니다. {reset_text(group, now)} 이후 이어 받을 수 있습니다.", failed=False)
            wait = max(0.0, (max(item["sent"]) + group["min_interval"] - now) if item["sent"] else 0.0)
            for (key, width), used in zip(WINDOWS.items(), windows):
                limit = allowed(group, key)
                if limit is not None and used >= limit:
                    inside = sorted(t for t in item["sent"] if t > now - width)
                    wait = max(wait, inside[used - limit] + width - now)  # 창에서 하나가 빠질 때까지
            if wait <= 0:
                start = period(group, now)[0]
                item["sent"].append(now)
                item["day"] = [start, day + 1]
                data[name] = item
                write(data)
                return
        if stop.wait(min(wait, 1.0) + 0.001):
            raise Halt(STOPPED, failed=False)


def reset(name):
    """묶음의 사용 기록을 지운다. 기록 파일이 깨졌으면 파일 전체를 새로 만든다."""
    with Locked():
        data = load()
        if data is None:
            data = {}
        data.pop(name, None)
        write(data)


def server_says(name, headers):
    """응답 헤더의 남은 횟수·초기화(X-RateLimit-*, RateLimit-*)를 기억하고, 남은 것이 0이면 초기화까지 쉴 초를 돌려준다(없으면 None)."""
    get = lambda *names: next((v for k, v in headers for n in names if k.lower() == n), None)
    remaining, reset_value = get("x-ratelimit-remaining", "ratelimit-remaining"), get("x-ratelimit-reset", "ratelimit-reset")
    if remaining is None:
        return None
    try:
        left = int(float(remaining.split(",")[0].split(";")[0]))
    except ValueError:
        return None
    wait = None
    if reset_value is not None:
        try:
            value = float(reset_value.split(",")[0].split(";")[0])
            wait = max(0.0, value - time.time()) if value > 1e9 else value  # 큰 값은 시각(초), 작은 값은 남은 초
        except ValueError:
            wait = None
    if name:
        server_seen[name] = {"remaining": left, "reset": reset_value}
    return (wait if wait is not None else 60.0) if left <= 0 else None
