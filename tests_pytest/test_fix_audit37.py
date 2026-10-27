"""37차: 콘솔 코드 페이지로 못 쓰는 글자(é)의 작업 이름. schtasks 출력에는 Café가 Cafe로 보여 조회가 어긋났다."""
from integrations import schedule, wintask


def test_task_name_drops_unreadable_letters(monkeypatch):
    monkeypatch.setattr(wintask, "codepage", lambda: "cp949")
    assert wintask.task_name("Café 매출", []) == "Caf_ 매출"
    assert wintask.task_name("주문 수집", []) == "주문 수집"


def test_old_unreadable_task_is_replaced(monkeypatch):
    monkeypatch.setattr(wintask, "codepage", lambda: "cp949")
    calls = []
    monkeypatch.setattr(wintask, "execute", lambda args: calls.append(args[:4]) or ((1, "", "없음") if args[1] == "/Delete" else (0, "", "")))
    item = {"every": "daily", "time": "09:00", "days": [], "task": "Café"}
    assert schedule.register("Café", item, []) == "Caf_"
    assert calls[0] == ["schtasks", "/Delete", "/TN", wintask.FOLDER + "Café"]  # 지우지 못해도(이미 없음) 새 이름으로 등록한다
    assert calls[1] == ["schtasks", "/Create", "/TN", wintask.FOLDER + "Caf_"]
