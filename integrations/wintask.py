"""Windows 예약 작업: 수집 예약을 작업 스케줄러의 FOLDER(\\DataLoupe\\) 안에 등록·조회·삭제한다. 이 폴더 밖의 작업은 조회도 하지 않는다(예외: 예전 폴더 OLD_FOLDER의 우리 작업을 옮길 때).
작업은 관리자 권한 없이 "사용자가 로그온했을 때만"(InteractiveToken, LeastPrivilege) 돌고, 놓친 시각은 켜진 뒤 한 번(StartWhenAvailable),
이미 돌고 있으면 새 실행은 하지 않는다(IgnoreNew). 동작은 이 프로그램을 '--run "구성 이름"'으로 실행하는 것이다.
schtasks는 execute로만 부른다(테스트는 이 함수를 바꿔 끼워 실제로 등록하지 않는다)."""
import csv
import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from core import scratch

FOLDER = "\\DataLoupe\\"
OLD_FOLDER = "\\ibis_search\\"  # 앱 이름을 바꾸기 전의 폴더. 예전에 등록한 작업을 FOLDER로 옮길 때만 조회·삭제한다(이름은 schedules.json에 있는 것만).
NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")  # 0=월 … 6=일
SCHEMA_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")  # XML에 적는 차례
UNSAFE = re.compile(r"[^\w .-]+")  # 작업 이름에 쓰지 않는 글자(영문·숫자·한글·밑줄·공백·점·빼기만 남긴다)
NEVER = 267011  # 0x41303: 아직 한 번도 돌지 않음
RUNNING = 267009  # 0x41301: 지금 도는 중


def execute(args):
    """명령을 창 없이 실행한다. (종료 코드, 표준 출력, 표준 오류). 출력은 콘솔 코드 페이지(한국어 Windows는 cp949)로 읽는다."""
    done = subprocess.run(args, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    return done.returncode, done.stdout.decode(codepage(), "replace"), done.stderr.decode(codepage(), "replace")


def codepage():
    return f"cp{ctypes.windll.kernel32.GetOEMCP()}"


def readable(task):
    """schtasks 출력에 그대로 보이는 이름인지. 콘솔 코드 페이지로 못 쓰는 글자는 비슷한 글자로 바뀌어 보여(예: Café → Cafe) 조회가 어긋난다."""
    return task.encode(codepage(), "replace").decode(codepage()) == task


def program():
    """작업이 실행할 (프로그램, 앞에 붙는 인자, 작업 폴더). exe면 exe 하나, 소스면 python.exe와 desktop.py의 절대 경로."""
    if getattr(sys, "frozen", False):
        return sys.executable, [], str(Path(sys.executable).parent)
    script = Path(__file__).resolve().parent.parent / "desktop.py"
    return sys.executable, [str(script)], str(script.parent)


def command(name):
    """(명령, 인자 글자, 작업 폴더). 인자는 Windows 명령줄 규칙으로 따옴표를 붙인다."""
    if "%" in name:  # 작업 스케줄러는 인자의 %이름%을 환경 변수로 바꾸므로 다른 이름으로 실행될 수 있다
        raise ValueError("Windows 예약 작업으로 등록하려면 구성 이름에 %를 쓰지 마세요.")
    exe, before, folder = program()
    return exe, subprocess.list2cmdline([*before, "--run", name]), folder


def task_name(name, taken):
    """구성 이름을 작업 이름으로: 쓰지 않는 글자와 콘솔 코드 페이지로 못 쓰는 글자는 밑줄로, 50자까지. taken(이미 쓰는 작업 이름들)과 겹치면(대소문자 무시) _2, _3을 붙인다."""
    base = "".join(c if readable(c) else "_" for c in UNSAFE.sub("_", name)).strip(" .")[:50] or "수집"
    used = {t.lower() for t in taken}
    found, n = base, 1
    while found.lower() in used:
        n += 1
        found = f"{base}_{n}"
    return found


def full(task, folder=FOLDER):
    """폴더를 붙인 작업 경로. 이 폴더 밖이나 아래 폴더를 가리킬 수 없게 작업 이름을 다시 검사한다."""
    if not isinstance(task, str) or not task or UNSAFE.search(task) or task.strip(" .") != task or len(task) > 60:
        raise ValueError("작업 이름이 올바르지 않습니다.")
    return folder + task


def xml(task, name, trigger, start):
    """작업 XML(Task Scheduler 스키마 1.2). trigger는 예약({every, days, minutes}), start는 첫 실행 시각(datetime)."""
    exe, arguments, folder = command(name)
    boundary = start.strftime("%Y-%m-%dT%H:%M:%S")
    if trigger["every"] == "interval":
        when = (f"<TimeTrigger><Repetition><Interval>PT{trigger['minutes']}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
                f"<StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
    elif trigger["every"] == "weekly":
        days = "".join(f"<{d}/>" for d in SCHEMA_DAYS if DAYS.index(d) in trigger["days"])
        when = (f"<CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>"
                f"<ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>")
    else:
        when = (f"<CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled>"
                f"<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")
    text = lambda value: escape(value, {'"': "&quot;"})
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="{NAMESPACE}">
  <RegistrationInfo><Description>{text(f"Data Loupe 수집 예약: {name}")}</Description><URI>{text(full(task))}</URI></RegistrationInfo>
  <Triggers>{when}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>{text(exe)}</Command><Arguments>{text(arguments)}</Arguments><WorkingDirectory>{text(folder)}</WorkingDirectory></Exec></Actions>
</Task>
"""


def failed(doing, err, out=""):
    detail = (err or out).strip().removeprefix("오류:").removeprefix("ERROR:").strip()
    return ValueError(f"Windows 예약 작업을 {doing} 못했습니다. {detail or '알 수 없는 오류'}")


def register(task, name, trigger, start):
    """작업을 만든다(같은 이름이 있으면 새 설정으로 바꾼다). XML은 UTF-16 파일로 잠깐 만들었다가 지운다."""
    folder = scratch.make()
    try:
        path = Path(folder, "task.xml")
        path.write_text(xml(task, name, trigger, start), encoding="utf-16")
        code, out, err = execute(["schtasks", "/Create", "/TN", full(task), "/XML", str(path), "/F"])
    finally:
        scratch.remove(folder)
    if code:
        raise failed("등록하지", err, out)


def delete(task, folder=FOLDER, known=False):
    """작업을 지운다. 이미 없으면 그대로 둔다. known=True: 방금 조회해 있는 것을 아는 호출이라 다시 조회하지 않는다.
    readable이 아닌 이름(예전 Café)은 조회로 있는지 알 수 없어 바로 지우고, 실패하면(이미 없음) 넘어간다."""
    seen = readable(task)
    if not known and seen and not query(task, folder)["registered"]:
        return
    code, out, err = execute(["schtasks", "/Delete", "/TN", full(task, folder), "/F"])
    if code and (known or seen):
        raise failed("지우지", err, out)


def when(text):
    """schtasks가 보여 준 시각(지역 형식, 예: '2026-10-16 오전 9:00:00')을 'YYYY-MM-DD HH:MM'으로. 없으면 None, 모르는 형식은 그대로."""
    text = text.strip()
    found = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2}) +(오전|오후|AM|PM)? *(\d{1,2}):(\d{2})(?::\d{2})? *(AM|PM)?", text, re.I)
    if not found:
        return None if not text or text.upper() == "N/A" or not any(ch.isdigit() for ch in text) else text
    year, month, day, half, hour, minute, after = found.groups()
    hour, half = int(hour), (half or after or "").upper()
    if half in ("오후", "PM") and hour < 12:
        hour += 12
    elif half in ("오전", "AM") and hour == 12:
        hour = 0
    if (int(year), int(month), int(day)) == (1999, 11, 30):  # 한 번도 돌지 않은 작업의 마지막 실행 시각
        return None
    return f"{year}-{int(month):02d}-{int(day):02d} {hour:02d}:{minute}"


def task_info(row):
    """schtasks /V CSV의 작업 행 하나 → {registered, command, next, last}."""
    last, result = when(row[5]), row[6].strip()
    if last is not None and result.lstrip("-").isdigit() and int(result) != NEVER:
        code = int(result)
        last += " · " + ("성공" if code == 0 else "도는 중" if code == RUNNING else f"실패(코드 {code & 0xFFFFFFFF:#x})")
    elif result.lstrip("-").isdigit() and int(result) == NEVER:
        last = None
    return {"registered": True, "command": row[8], "next": when(row[2]), "last": last}


MISSING = {"registered": False, "command": "", "next": None, "last": None}


def query(task, folder=FOLDER, strict=False):
    """작업 하나의 {registered, command, next, last}. schtasks의 /FO CSV /V 열 차례(언어와 상관없음): 1 작업 이름, 2 다음 실행 시간, 5 마지막 실행 시간, 6 마지막 결과, 8 실행할 작업."""
    code, out, err = execute(["schtasks", "/Query", "/TN", full(task, folder), "/FO", "CSV", "/V"])
    rows = [r for r in csv.reader(out.splitlines()) if len(r) > 8]
    if code and strict:  # strict: 작업 스케줄러가 답하지 못한 것(오류)과 그 작업이 없는 것을 구분한다. 목록 조회까지 실패하거나, 목록에는 이름이 있는데 조회만 실패했으면(접근 거부 등) 오류(언어와 상관없이 가린다)
        list_code, list_out, _ = execute(["schtasks", "/Query", "/FO", "CSV", "/NH"])
        if list_code or any(r and r[0].lower() == full(task, folder).lower() for r in csv.reader(list_out.splitlines())):
            raise failed("조회하지", err, out)
    if code or len(rows) < 2 or rows[1][1].lower() != full(task, folder).lower():
        return dict(MISSING)
    return task_info(rows[1])


def query_all():
    """FOLDER 안의 작업 전부 {작업 이름(소문자): {registered, command, next, last}}를 schtasks 한 번으로(작업마다 부르면 작업 수만큼 프로세스를 띄운다). 폴더가 없으면 빈 모음."""
    code, out, _ = execute(["schtasks", "/Query", "/TN", FOLDER, "/FO", "CSV", "/V"])
    found = {}
    for row in csv.reader(out.splitlines()):
        if not code and len(row) > 8 and row[1].lower().startswith(FOLDER.lower()):  # 머리글 줄은 폴더 경로로 시작하지 않는다
            found.setdefault(row[1][len(FOLDER):].lower(), task_info(row))
    return found


def points_here(found):
    """조회한 작업의 명령이 지금 프로그램(같은 exe, 소스면 같은 python.exe·desktop.py)을 가리키는지."""
    exe, before, _ = program()
    plain = lambda text: os.path.normcase(text.replace('"', ""))  # schtasks는 따옴표를 붙이기도 하고 빼기도 한다.
    return plain(found["command"]).startswith(plain(" ".join([exe, *before, "--run", ""])))
