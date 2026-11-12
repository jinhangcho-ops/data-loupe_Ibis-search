"""감사 46(연동): 표 칸의 Slack·Discord 멘션, Windows 예약 알림의 '이번 실행 동안만' 보낼 곳, 저장 경합의 작업 되돌리기, 둥근 따옴표. 값은 여기서 지어내고 밖으로 보내지 않는다."""
import json

import pytest

from core import settings
from integrations import commands, notify, schedule, wintask
from integrations.adapters import slack_out, transport, webhook

TABLE = {"columns": ["store", "memo"], "rows": [{"store": "A", "memo": "```\n<!channel> <https://evil.example|x>"}, {"store": "B", "memo": "@everyone 공지"}]}


def test_table_values_do_not_mention(monkeypatch, folder):
    got = []
    monkeypatch.setattr(transport, "post", lambda url, data, headers, timeout=15: got.append(data) or b'{"ok": true}')
    m = notify.message({"title": "집계", "text": "매장별", "table": TABLE})
    slack_out.send({"token": "t", "channel": "C1"}, m)
    text = json.loads(got[0])["text"]
    assert text.count("```") == 2 and "<!channel>" not in text  # 코드 블록이 닫히지 않고 멘션이 되지 않는다
    webhook.send({"url": "https://discord.com/api/webhooks/1/x", "content": "json", "template": webhook.PRESETS[2]["template"]}, m)
    assert json.loads(got[1])["allowed_mentions"] == {"parse": []}


def test_windows_schedule_refuses_session_destination(monkeypatch, folder):
    monkeypatch.setattr(transport, "post", lambda *a, **k: b'{"ok": true}')
    monkeypatch.setattr(transport, "request", lambda *a, **k: b"{}")
    notify.save("팀 알림", {"kind": "slack", "keep": "session", "token": "xoxb-made-up", "channel": "C1"})
    with pytest.raises(ValueError, match="이번 실행 동안만"):
        schedule.checked({"every": "daily", "time": "09:00", "inapp": False, "windows": True, "notify": "팀 알림", "notify_on": "always"})
    assert schedule.checked({"every": "daily", "time": "09:00", "inapp": True, "windows": False, "notify": "팀 알림"})["notify"] == "팀 알림"  # 프로그램 안 예약은 된다


def test_save_race_keeps_other_task(monkeypatch, folder):
    registered = set()
    item = {"every": "daily", "time": "09:00", "inapp": False, "windows": True}
    monkeypatch.setattr(schedule, "collection", lambda name: {})
    monkeypatch.setattr(wintask, "readable", lambda t: True)

    def register(task, name, trigger, start):
        registered.add(task)
        if not register.done:  # 이 창이 schtasks를 기다리는 사이 다른 창이 같은 예약을 저장해 끝낸다
            register.done = True
            with settings.updating(schedule.FILE):
                settings.write(schedule.FILE, {"매출": {**schedule.checked(item), "task": "매출"}})
    register.done = False
    monkeypatch.setattr(wintask, "register", register)
    monkeypatch.setattr(wintask, "delete", lambda task, folder=wintask.FOLDER, known=False: registered.discard(task))
    with pytest.raises(ValueError, match="다른 곳에서 바뀌었어요"):
        schedule.save("매출", item)
    assert schedule.schedules()["매출"]["task"] in registered  # 파일에 등록됐다고 남은 작업이 실제로 있다


def test_smart_quotes_split():
    assert commands.split("지표 CTR 검색=“이번 달”") == ("지표", ["CTR"], {"검색": "이번 달"})
