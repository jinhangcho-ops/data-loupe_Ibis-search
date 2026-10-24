"""사용 횟수: 연결과 저장한 검색을 쓸 때마다 세어, 목록을 많이 쓴 순(같으면 최근에 쓴 순)으로 보인다.
설정 폴더의 usage.json에 {종류: {열쇠: {"count", "last", "info"(연결만: 칸을 채울 값)}}}로 둔다. 비밀번호는 넣지 않는다(연결의 info는 source.recent가 만든 것).
예전 버전이 남긴 recent.json(마지막 연결 하나)은 처음 읽을 때 1번 쓴 연결로 옮겨 담는다(recent.json은 지우지 않는다)."""
import datetime as dt
import os

from core import settings

FILE = "usage.json"
LEGACY_RECENT = "recent.json"
MAX_CONNECTIONS = 30  # 기억할 연결 수. 넘으면 적게·오래전에 쓴 것부터 뺀다.
count_of = lambda entry: entry.get("count") if type(entry.get("count")) is int else 0  # 손으로 고친 파일의 이상한 값은 0번


def files_part(info):
    """연결 요청에 든 고른 파일(file 하나, files 여러 개)을 경로 정규화·정렬해 '#'로 이은 글자. 없으면 ''. 같은 폴더의 a.duckdb와 b.duckdb를 가른다."""
    return "#".join(sorted(os.path.abspath(p).replace("\\", "/").lower() for p in (info.get("file"), *info.get("files", [])) if p))


def connection_key(info):
    """연결을 알아보는 열쇠(비밀번호 없음): 폴더는 경로(고른 file·files가 info에 있으면 그것도), 주소는 주소와 아이디."""
    if info["kind"] == "folder":
        return "folder:" + os.path.normcase(os.path.abspath(info.get("path", ""))) + (("#" + files_part(info)) if files_part(info) else "")
    return f"url:{info.get('url', '')}|{info.get('user', '')}"


def same_source(saved, now):
    """저장해 둔 연결 열쇠(saved)가 지금 연결의 열쇠(now)와 같은 연결인지. 같은 폴더의 파일을 따로 가르기 전에 저장한 예전 열쇠(폴더만, '#' 없음)는 지금 열쇠의 폴더 부분이 같으면 같다고 본다."""
    return saved == now or (isinstance(saved, str) and "#" not in saved and now.split("#")[0] == saved)


def renewed(saved, now):
    """저장할 때 쓸 열쇠: 없거나 같은 연결의 예전 열쇠면 지금 열쇠로 덮고(그다음부터 정확히 비교), 다른 연결의 것이면 그대로 둔다."""
    return now if not saved or same_source(saved, now) else saved


def load():
    """사용 기록 전체. 예전 recent.json만 있으면 그것을 1번 쓴 연결로 옮겨 담은 모양으로 돌려준다."""
    found = settings.read(FILE) or {}
    data = {kind: {k: v for k, v in entries.items() if isinstance(v, dict)} for kind, entries in found.items() if isinstance(entries, dict)}
    if "connections" not in data:
        legacy = settings.read(LEGACY_RECENT)
        data["connections"] = {connection_key(legacy): {"count": 1, "last": "", "info": legacy}} if legacy and legacy.get("kind") in ("folder", "url") else {}
    return data


def use(kind, key, info=None):
    """kind의 key를 한 번 썼다고 센다. info를 주면 함께 둔다(바꾼다)."""
    use_many(kind, [key], info)


def use_many(kind, keys, info=None):
    """use를 keys 차례로 한 것과 같되(같은 키가 여러 번이면 그만큼 센다) 잠금·읽기·백업·쓰기는 한 번만 한다."""
    if not keys:
        return
    with settings.updating(FILE):
        data = load()
        for key in keys:
            _bump(data, kind, key, info)
        settings.write(FILE, data)


def _bump(data, kind, key, info):
    entries = data.setdefault(kind, {})
    entry = entries.get(key, {})
    entry = {**entry, "count": count_of(entry) + 1, "last": dt.datetime.now().isoformat(timespec="microseconds")}  # 같은 초에 두 번 써도 차례가 갈리게
    if info is not None:
        entry["info"] = info
    entries[key] = entry
    if kind == "connections" and len(entries) > MAX_CONNECTIONS:
        for old in ranked(entries)[MAX_CONNECTIONS:]:
            if old != key:  # 방금 쓴 연결은 첫 사용이라 순위가 맨 뒤여도 지우지 않는다
                entries.pop(old)


def forget(kind, key):
    with settings.updating(FILE):
        data = load()
        if data.get(kind, {}).pop(key, None) is not None:
            settings.write(FILE, data)


def ranked(entries, keys=None):
    """많이 쓴 순, 같으면 최근에 쓴 순, 그다음 이름 순인 열쇠 목록. keys를 주면 그것만(쓴 적 없는 것은 0번으로 맨 뒤에)."""
    keys = list(entries) if keys is None else list(keys)
    entry = lambda k: entries.get(k, {})
    by_name = sorted(keys)
    by_last = sorted(by_name, key=lambda k: str(entry(k).get("last", "")), reverse=True)
    return sorted(by_last, key=lambda k: count_of(entry(k)), reverse=True)


def connections():
    """기억하는 연결을 많이 쓴 순으로: [{"info", "count", "last"}]."""
    entries = load()["connections"]
    return [{"info": entries[k]["info"], "count": count_of(entries[k]), "last": entries[k].get("last", "")}
            for k in ranked(entries) if isinstance(entries[k].get("info"), dict)]


def last_connection():
    """가장 최근에 쓴 연결의 칸 값. 없으면 None."""
    found = connections()
    return max(found, key=lambda c: str(c["last"]))["info"] if found else None


def order(kind, names):
    """names(저장한 이름들)를 많이 쓴 순으로."""
    return ranked(load().get(kind, {}), names)
