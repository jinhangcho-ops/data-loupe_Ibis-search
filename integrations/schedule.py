"""수집 예약(4단계): 예약 검사·저장(schedules.json), 다음 시각 계산, 구성 잠금(locks\\), 실행 기록(runs.json)·알림,
예약처럼 한 번 실행(execute), 프로그램 안 예약 스레드(watch), 창 없이 실행(run_alone, desktop.py --run).
비밀번호는 저장하지 않으므로 비밀번호가 필요한 구성은 예약하지 않는다(password_reason 하나로 판단)."""
import copy
import datetime as dt
import math
import re
import secrets
import threading
import time

import psutil

from core import filelock
from core import scratch
from core import settings
from lazy import Later

collect_config = Later("collect.collect_config")  # 수집·알림 모듈은 예약을 돌릴 때 올라온다(프로그램을 켤 때는 예약 확인만 한다)
collect_job = Later("collect.collect_job")
collect_store = Later("collect.collect_store")
load_kafka = Later("collect.load_kafka")
notify = Later("integrations.notify")
wintask = Later("integrations.wintask")

FILE = "schedules.json"
RUNS = "runs.json"
MAX_RUNS = 200
MAX_SCHEDULES = settings.MAX_SAVED
EVERY = ("daily", "weekly", "interval")
NOTIFY_ON = ("fail", "always")
DEFAULT = {"every": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4], "minutes": 60, "inapp": True, "windows": False, "notify": "", "notify_on": "fail"}  # 화면(collect.html)도 이 값을 쓴다
MINUTES = (5, 1440)  # interval 예약의 간격(분) 범위
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
ANCHOR = dt.datetime(2000, 1, 1)  # interval 예약의 기준 시각: 이때부터 minutes분마다(프로그램 안 예약과 Windows 작업이 같은 시각에 돈다)
TICK = 20  # 프로그램 안 예약이 확인하는 간격(초)
SKIP_RUNNING = "다른 수집이 돌고 있어 건너뛰었습니다."
SKIP_LOCKED = "같은 구성이 다른 곳(프로그램 안 예약, Windows 예약 작업)에서 돌고 있어 건너뛰었습니다."
LOCKED = "같은 구성이 다른 곳(프로그램 안 예약, Windows 예약 작업)에서 돌고 있습니다. 끝난 뒤에 실행하세요."
GONE = "프로그램이 중간에 끝나 결과를 모릅니다."
TITLES = {"끝": "수집이 끝났습니다", "실패": "수집이 실패했습니다", "건너뜀": "수집을 건너뛰었습니다"}

clock = dt.datetime.now  # 지금 시각. 테스트는 바꿔 끼운다.
starting = threading.Lock()  # 이 프로세스 안에서 "도는 작업 확인 → 잠금 → 시작"을 한 번에 하나씩
writing = threading.Lock()  # 실행 기록 파일
planned = {}  # 프로그램 안 예약의 다음 시각 {이름: datetime}. watch 스레드가 채운다.
watcher = {"thread": None, "halt": threading.Event()}


# ---------- 예약 ----------

def schedules():
    """저장한 예약 {구성 이름: 예약}. 각 예약에는 Windows 작업 이름(task)이 들어 있을 수 있다."""
    return {name: item for name, item in settings.named(FILE).items() if isinstance(item, dict)}


def public(item):
    """화면에 줄 예약(안에서만 쓰는 task는 뺀다). 저장할 때 검사한 것이라 빠진 칸만 기본값으로 채운다."""
    return {**DEFAULT, **{k: v for k, v in item.items() if k in DEFAULT}}


def collection(name):
    """저장한 구성(검사를 마친 것). 없거나 올바르지 않거나 비밀번호가 필요하면 이유를 담은 ValueError."""
    found = settings.named(collect_config.COLLECTIONS).get(name)
    if found is None:
        raise ValueError(f"구성 '{name}'이 없습니다. 구성을 먼저 저장하세요.")
    try:
        config = collect_config.normalize(found)
    except ValueError as e:
        raise ValueError(f"구성 '{name}'이 올바르지 않습니다: {e}") from None
    reason = password_reason(config)
    if reason:
        raise ValueError(reason)
    return config


def password_reason(config):
    """비밀번호가 필요해 예약할 수 없는 구성이면 그 이유, 아니면 None. 비밀번호는 저장하지 않으므로 예약 실행에는 넣을 수 없다."""
    load = config["load"]
    if load["target"] == "table" and load["table"]["connection"]["kind"] == "url" and load["table"]["connection"]["user"].strip():
        return "DB 테이블에 적재할 때 비밀번호가 필요한 연결(아이디가 있는 주소)이라 예약할 수 없습니다. 비밀번호는 저장하지 않기 때문입니다."
    if load["target"] == "kafka":
        found = load_kafka.destinations().get(load["kafka"]["destination"])
        if isinstance(found, dict) and str(found.get("security", "")).startswith("SASL"):
            return "Kafka에 적재할 때 비밀번호가 필요한 곳(SASL)이라 예약할 수 없습니다. 비밀번호는 저장하지 않기 때문입니다."
    return None


def checked(item):
    """예약 하나를 검사하고 빠진 칸을 기본값으로 채운다."""
    if not isinstance(item, dict):
        raise ValueError("예약이 올바르지 않습니다.")
    unknown = [k for k in item if k not in DEFAULT]
    if unknown:
        raise ValueError(f"예약에 쓸 수 없는 값이 있습니다: {', '.join(unknown)}")
    found = {**DEFAULT, **item}
    if found["every"] not in EVERY:
        raise ValueError(f"예약의 every는 {', '.join(EVERY)} 중 하나입니다.")
    if not isinstance(found["time"], str) or not TIME.fullmatch(found["time"]):
        raise ValueError("시각은 HH:MM(00:00~23:59)으로 쓰세요.")
    days = found["days"]
    if not isinstance(days, list) or not all(type(d) is int and 0 <= d <= 6 for d in days) or len(set(days)) != len(days):
        raise ValueError("요일(days)은 0(월)~6(일)의 겹치지 않는 목록이어야 합니다.")
    if found["every"] == "weekly" and not days:
        raise ValueError("매주 예약은 요일을 하나 이상 고르세요.")
    if type(found["minutes"]) is not int or not MINUTES[0] <= found["minutes"] <= MINUTES[1]:
        raise ValueError(f"간격(minutes)은 {MINUTES[0]}~{MINUTES[1]}분의 정수여야 합니다.")
    for key in ("inapp", "windows"):
        if type(found[key]) is not bool:
            raise ValueError(f"예약의 {key}는 true·false여야 합니다.")
    if found["inapp"] and found["windows"]:
        raise ValueError("프로그램 안 예약(inapp)과 Windows 작업(windows)은 같이 고를 수 없습니다. 하나만 고르세요.")
    if not isinstance(found["notify"], str) or (found["notify"] and found["notify"] not in settings.named(notify.FILE)):
        raise ValueError(f"보낼 곳 '{found['notify']}'이 없습니다." if isinstance(found["notify"], str) else "보낼 곳 이름은 글자여야 합니다.")
    if found["notify_on"] not in NOTIFY_ON:
        raise ValueError(f"예약의 notify_on은 {', '.join(NOTIFY_ON)} 중 하나입니다.")
    found["days"] = sorted(days)
    return found


def next_time(item, after):
    """after(지역 시각, 초 포함) 뒤의 첫 예약 시각. after와 같은 시각은 넣지 않는다."""
    if item["every"] == "interval":
        step = item["minutes"] * 60
        return ANCHOR + dt.timedelta(seconds=(math.floor((after - ANCHOR).total_seconds() / step) + 1) * step)
    hour, minute = map(int, item["time"].split(":"))
    for ahead in range(8):  # 매주는 오늘부터 7일 뒤(같은 요일)까지 보면 반드시 있다.
        day = after.date() + dt.timedelta(days=ahead)
        at = dt.datetime.combine(day, dt.time(hour, minute))
        if at > after and (item["every"] == "daily" or day.weekday() in item["days"]):
            return at
    raise ValueError("다음 시각을 찾지 못했습니다.")


def register(name, item, taken):
    """Windows 작업을 등록하고 작업 이름을 돌려준다. 이미 이름이 있으면 그 이름으로 바꾼다."""
    task = item.get("task") or wintask.task_name(name, taken)
    wintask.register(task, name, item, next_time(item, clock()))
    return task


def save(name, item):
    """예약을 저장한다(None이면 지운다). windows에 따라 Windows 작업을 등록·삭제한다. 바뀐 전체를 돌려준다."""
    name = name.strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= 50:
        raise ValueError("구성 이름은 1자 이상 50자 이하로 입력하세요.")
    with settings.updating(FILE):  # 읽고-고치고-쓰기를 한 번에 하나씩(동시에 저장한 예약이 사라지지 않게)
        found = schedules()
        old = found.get(name, {})
        if item is None:
            if old.get("task"):
                wintask.delete(old["task"])
            found.pop(name, None)
            settings.write(FILE, found)
            return found
        item = checked(item)
        collection(name)
        if name not in found and len(found) >= MAX_SCHEDULES:
            raise ValueError(f"예약은 {MAX_SCHEDULES}개까지입니다. 쓰지 않는 것을 지우세요.")
        if item["windows"]:
            taken = [s["task"] for n, s in found.items() if n != name and s.get("task")]
            item["task"] = register(name, {**item, "task": old.get("task")}, taken)
        elif old.get("task"):
            wintask.delete(old["task"])
        found[name] = item
        settings.write(FILE, found)
        return found


def tasks(found):
    """Windows 작업으로 등록하기로 한 예약의 작업 상태 {이름: {registered, path_ok, next, last}}. 작업 스케줄러 폴더를 한 번만 조회한다."""
    wanted = {name: item["task"] for name, item in found.items() if item.get("windows") and item.get("task")}
    known = wintask.query_all() if wanted else {}
    view = {}
    for name, task in wanted.items():
        state = known.get(task.lower()) or wintask.MISSING
        view[name] = {"registered": state["registered"], "path_ok": state["registered"] and wintask.points_here(state), "next": state["next"], "last": state["last"]}
    return view


def overview():
    """GET /api/schedules의 모양."""
    found = schedules()
    states = tasks(found)
    now = clock()
    nexts = {name: (planned.get(name) or next_time(public(item), now)).strftime("%Y-%m-%d %H:%M") if item.get("inapp") else None
             for name, item in found.items()}
    return {"schedules": {name: public(item) for name, item in found.items()}, "tasks": states, "next_inapp": nexts,
            "stale": [name for name, state in states.items() if state["registered"] and not state["path_ok"]]}


def reregister():
    """명령 경로가 지금 프로그램과 다른 작업을 지금 경로로 다시 등록한다."""
    found = schedules()
    for name in overview()["stale"]:
        register(name, found[name], [])


# ---------- 잠금 ----------
# 잠금 파일은 filelock에 있다. 수집은 결과 파일 경로마다 collect_job.start가 잡는다(같은 결과 파일에 두 곳이 쓰지 않게).
locks, lock_path, hold, let_go = filelock.locks, filelock.lock_path, filelock.hold, filelock.let_go


# ---------- 실행 기록 ----------

def read_runs():
    found = settings.read(RUNS) or {}
    runs = found.get("runs")
    return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []


def change_runs(change):
    """실행 기록을 읽어 change(목록)로 고치고 최근 MAX_RUNS개만 저장한다. 다른 프로세스(Windows 작업)와 번갈아 쓴다."""
    with writing, filelock.guarded("runs.lock"):
        runs = read_runs()
        before = copy.deepcopy(runs)
        result = change(runs)
        if runs[:MAX_RUNS] != before:  # 바뀐 게 없으면 쓰지 않는다(화면이 목록을 새로 불러올 때마다 파일을 다시 쓰지 않게)
            settings.write(RUNS, {"runs": runs[:MAX_RUNS]})
        return result


def stamp_time(at=None):
    return (at or clock()).strftime("%Y-%m-%d %H:%M:%S")


def add_run(name, how):
    run = {"id": secrets.token_hex(8), "name": name, "how": how, "start": stamp_time(), "end": None, "result": "도는 중", "rows": None, "message": "",
           "seen": False, "owner": scratch.stamp()}
    change_runs(lambda runs: runs.insert(0, run))
    return run["id"]


def end_run(run_id, result, rows=None, message=""):
    def change(runs):
        for run in runs:
            if run.get("id") == run_id:
                run.update(end=stamp_time(), result=result, rows=rows, message=message)
    change_runs(change)


def runs_view():
    """GET /api/runs의 모양. 도는 중으로 남았지만 그 프로세스가 끝난 기록은 실패로 고친다."""
    def change(runs):
        for run in runs:
            if run.get("result") == "도는 중":
                try:
                    pid, started = str(run.get("owner", "")).split()
                    gone = scratch.ended(pid, started)
                except (ValueError, psutil.Error):  # 모르는 기록은 그대로 둔다.
                    gone = False
                if gone:
                    run.update(result="실패", message=GONE)
        return runs
    runs = change_runs(change)
    keys = ("name", "how", "start", "end", "result", "rows", "message")
    return {"runs": [{k: run.get(k) for k in keys} for run in runs], "unseen": sum(1 for r in runs if r.get("result") == "실패" and not r.get("seen"))}


def mark_seen():
    change_runs(lambda runs: [run.update(seen=True) for run in runs if run.get("result") == "실패"])


VALUE_ERRORS = re.compile(r"Constraint|Conversion|Could not convert|Invalid Input|Out of Range|Parser Error|Duplicate key", re.I)  # DuckDB 등이 데이터 값을 문장에 넣는 오류 유형
SEE_LOG = "(자세한 내용은 프로그램의 실행 기록)"


KNOWN_NOTICES = ((re.compile(r"연결이? 거부|connection refused|ECONNREFUSED|WinError 10061", re.I), "연결이 거부되었습니다"),
                 (re.compile(r"시간 ?초과|timed? ?out", re.I), "시간이 초과되었습니다"),
                 (re.compile(r"디스크|저장 공간|no space left|disk full|WinError 112", re.I), "디스크 공간이 부족합니다"),
                 (re.compile(r"권한|permission denied|access (is )?denied|WinError 5\b", re.I), "권한이 없습니다"),
                 (re.compile(r"찾을 수 없|not found|no such file|does not exist|FileNotFound", re.I), "찾을 수 없습니다"))  # 정해진 어휘(오류 글에서 이것을 알아보면 이 문구만 보낸다)


def notice_text(message):
    """Slack·웹훅 같은 밖으로 나가는 알림에 넣을 오류 글. 이 프로그램의 정해진 문구나 위 어휘(연결 거부·시간 초과·디스크 부족·권한·없음)로 알아본 것만 보내고(글 그대로가 아니라 정해진 문구),
    값이 든 오류 유형과 그 밖의 오류는 일반 문구로 보낸다(오류 글에는 DB가 데이터 값을 넣기도 한다. 전체는 실행 기록에 있다)."""
    message = (message or "").strip()
    if not message:
        return ""
    if message in (SKIP_RUNNING, SKIP_LOCKED, GONE):
        return message
    if VALUE_ERRORS.search(message):
        return f"데이터 값 문제로 실패했습니다 {SEE_LOG}"
    for known, text in KNOWN_NOTICES:
        if known.search(message):
            return f"{text} {SEE_LOG}"
    return f"실패했습니다 {SEE_LOG}"


def tell(name, result, rows, seconds, message):
    """예약의 보낼 곳으로 알린다(notify_on이 fail이면 끝난 것은 알리지 않는다). 보내지 못하면 그 문구를 돌려준다."""
    item = schedules().get(name) or {}
    if not item.get("notify") or (result == "끝" and item.get("notify_on") != "always"):
        return ""
    lines = [f"구성: {name}", f"행 수: {rows:,}" if rows is not None else "", f"걸린 시간: {notify.duration_text(seconds)}", f"오류: {notice_text(message)}" if message and result != "끝" else ""]
    try:
        notify.send(item["notify"], {"title": TITLES[result], "text": "\n".join(x for x in lines if x), "rows": rows, "seconds": seconds})
    except ValueError as e:
        return f"알림을 보내지 못했습니다: {e}"
    except Exception as e:  # 연결 도중 끊김(IncompleteRead) 같은 것: 글에 주소가 들 수 있어 종류만 적는다. 실행 기록은 끝내야 한다
        return f"알림을 보내지 못했습니다: {type(e).__name__}"
    return ""


def finish(run_id, name, result, rows=None, seconds=0.0, message=""):
    """기록을 끝내고 알린다. 알림이 실패하면 그 문구를 기록에 덧붙인다. message는 이미 가린 것이다(수집 상태의 오류는 collect_job이 가린다)."""
    problem = ""
    try:
        problem = tell(name, result, rows, seconds, message)
    finally:  # 알림에서 무슨 일이 있어도 "도는 중"으로 남기지 않는다
        end_run(run_id, result, rows, " ".join(x for x in (message, problem) if x))


# ---------- 실행 ----------

class Skip(Exception):
    """이번 실행을 건너뛴다(다른 수집이 돌고 있거나 같은 구성이 다른 곳에서 돌고 있음)."""


def execute(name, how):
    """구성 name을 예약처럼 한 번 실행한다(기록·잠금·알림). (기록 번호, 끝을 기다려 기록하는 스레드). 시작하지 못했으면(기록은 남김) 스레드는 None."""
    run_id = add_run(name, how)
    began = time.time()
    try:
        config = collection(name)
        with starting:
            if collect_job.running():
                raise Skip(SKIP_RUNNING)
            try:
                collect_job.start(config)  # 결과 파일의 잠금은 start가 잡고, 작업이 끝나면 work가 푼다
            except collect_job.Busy:
                raise Skip(SKIP_LOCKED) from None
            job = collect_store.current["thread"]
    except Skip as e:
        finish(run_id, name, "건너뜀", None, time.time() - began, str(e))
        return run_id, None
    except Exception as e:
        finish(run_id, name, "실패", None, time.time() - began, str(e) or type(e).__name__)
        return run_id, None

    def follow():
        job.join()
        state = collect_job.snapshot()
        bad = (state.get("load") or {}).get("failures")  # 적재에서 넣지 못한 행이 있으면 끝났어도 실패로 알린다
        done = state["phase"] == "끝" and not bad
        message = state["message"] if done else (state["error"] or state["message"] or f"수집이 {state['phase']} 상태로 끝났습니다.")
        finish(run_id, name, "끝" if done else "실패", state["rows"], state["elapsed"], message)

    thread = threading.Thread(target=follow, daemon=True)
    thread.start()
    return run_id, thread


def run_now(name):
    """POST /api/schedules/run: 다른 수집이 돌고 있거나 구성에 문제가 있으면 기록 없이 거부하고, 아니면 예약처럼 실행한다."""
    if collect_job.running():
        raise ValueError(collect_job.RUNNING)
    lock = collect_job.lock_of(collection(name))
    if not hold(lock):
        raise ValueError(LOCKED)
    let_go(lock)
    execute(name, "now")


def run_alone(name):
    """desktop.py --run "구성 이름": 서버·메인 창 없이 실행하고 끝날 때까지 기다린다. 종료 코드: 끝 0, 아니면 1."""
    from collect import collect_browser
    scratch.sweep()  # 지난번에 강제 종료돼 남은 임시 폴더
    run_id, follower = execute(name, "windows")
    try:
        if follower is not None:
            follower.join()
    finally:
        collect_browser.stop_child()  # 웹페이지 방식이 띄운 수집용 창
    run = next((r for r in read_runs() if r.get("id") == run_id), {})
    return 0 if run.get("result") == "끝" else 1


# ---------- 프로그램 안 예약 ----------

def tick(due):
    """한 번 확인한다. due({이름: (예약, 다음 시각)})는 부르는 쪽이 들고 있는다. 처음 본 예약·바뀐 예약은 지금 뒤의 시각부터(놓친 것은 돌리지 않는다)."""
    now = clock()
    found = {name: item for name, item in schedules().items() if item.get("inapp") and not item.get("windows")}  # Windows 작업이 도는 예약은 여기서 또 돌리지 않는다
    for name in list(due):
        if name not in found:
            due.pop(name)
    for name, item in found.items():
        item = public(item)
        if name not in due or due[name][0] != item:
            due[name] = (item, next_time(item, now))
        elif now >= due[name][1]:
            due[name] = (item, next_time(item, now))
            execute(name, "inapp")
    planned.clear()
    planned.update({name: at for name, (_, at) in due.items()})


def watch():
    due = {}
    while not watcher["halt"].is_set():
        try:
            tick(due)
        except Exception:  # 예약 파일을 잠깐 읽지 못해도 스레드는 계속 돈다.
            pass
        watcher["halt"].wait(TICK)


def start_watch():
    """프로그램 안 예약 스레드를 띄운다(창을 띄울 때 한 번)."""
    if watcher["thread"] is None:
        watcher["thread"] = threading.Thread(target=watch, daemon=True)
        watcher["thread"].start()


def stop_watch():
    watcher["halt"].set()
