"""업로드 기록(원장): 수집이 저장한 파일과, 적재로 DB 테이블·Kafka 토픽에 넣은 것을 한 번마다 남긴다(uploads.json, MAX_ENTRIES개).
나중에 지워야 할 때 무엇을 어디에 얼마나 넣었는지 바로 찾게 하는 것이 목적이다. 프로그램은 지우지 않고, 지울 때 쓸 명령만 만들어 보여 준다.
기록 하나: {id, kind(file·table·kafka), started, finished, status(done·partial·failed·stopped), source(결과 파일 경로), target(어디에),
           rows(넣은 행), bytes(보낸·쓴 바이트, DB 테이블은 None), range(Kafka: 파티션마다 [첫 offset, 끝 offset], 테이블: 적재 ID 컬럼·키 컬럼의 최소~최대), resumed, error}
비밀번호·토큰은 넣지 않는다(연결은 load_table.ident처럼 경로나 주소·아이디만)."""
import datetime as dt
import json
import threading
import uuid
from pathlib import Path

import filelock
import settings

FILE = "uploads.json"
MAX_ENTRIES = 2000  # 넘으면 오래된 것부터 뺀다
LOAD_ID = "_ibis_load_id"  # 적재 ID 컬럼(적재할 때 고르면 행마다 그 적재의 ID를 넣는다)
STATUSES = {"done": "끝", "partial": "일부", "failed": "실패", "stopped": "멈춤"}
lock = threading.Lock()


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:16]


def entries():
    """기록 전체(새것이 앞). 이상한 항목은 뺀다."""
    found = settings.read(FILE) or {}
    items = found.get("entries") if isinstance(found, dict) else None
    return [e for e in items if isinstance(e, dict) and isinstance(e.get("id"), str)] if isinstance(items, list) else []


def put(entry):
    """기록 하나를 넣거나(같은 id면) 바꾼다. 맨 앞에 둔다."""
    with lock, filelock.guarded("uploads.lock"):  # 프로그램 창과 Windows 예약 작업이 함께 읽고 쓴다
        items = [e for e in entries() if e["id"] != entry["id"]]
        settings.write(FILE, {"entries": ([entry] + items)[:MAX_ENTRIES]})
    return entry


def forget(entry_id):
    """기록 하나를 목록에서 뺀다(데이터는 건드리지 않는다)."""
    with lock, filelock.guarded("uploads.lock"):
        items = entries()
        settings.write(FILE, {"entries": [e for e in items if e["id"] != entry_id]})


def file_size(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def log_file(path, rows):
    """수집이 결과 파일을 저장했다."""
    return put({"id": new_id(), "kind": "file", "started": now(), "finished": now(), "status": "done", "source": str(path),
                "target": {"path": str(path)}, "rows": rows, "bytes": file_size(path), "range": {}, "resumed": False})


class Run:
    """적재 한 번의 기록. start에 남기고, 넣을 때마다 add로 더하고, end로 끝낸다(실패해도 넣은 데까지 남는다)."""

    def __init__(self, kind, source, target, load_id=None, resumed=False):
        self.entry = {"id": new_id(), "kind": kind, "started": now(), "finished": None, "status": "partial", "source": str(source),
                      "target": target, "rows": 0, "bytes": None if kind == "table" else 0, "range": {**({"load_id": load_id, "column": LOAD_ID} if load_id else {})},
                      "resumed": resumed}
        put(self.entry)

    def add(self, rows, size=0, partitions=None, keys=None):
        """넣은 행 수·바이트와 범위(Kafka 파티션 {p: [첫, 끝]}, 키 {컬럼: [최소, 최대]})를 더한다. 파일에는 save가 쓴다(한 건씩 넣을 때 매번 쓰지 않게)."""
        e = self.entry
        e["rows"] += rows
        if e["bytes"] is not None:  # DB 테이블은 바이트를 재지 않는다
            e["bytes"] += size
        for p, (lo, hi) in (partitions or {}).items():
            old = e["range"].setdefault("partitions", {}).get(str(p))
            e["range"]["partitions"][str(p)] = [min(lo, old[0]), max(hi, old[1])] if old else [lo, hi]
        for col, (lo, hi) in (keys or {}).items():
            old = e["range"].setdefault("keys", {}).get(col)
            e["range"]["keys"][col] = [lo if old is None else min(lo, old[0]), hi if old is None else max(hi, old[1])]

    def save(self):
        put(self.entry)

    def end(self, status, error=""):
        self.entry.update(status=status, finished=now(), **({"error": error[:300]} if error else {}))
        put(self.entry)


# ---------- 지울 때 쓸 명령(보여 주기만 한다) ----------

def quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def literal(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def removal(entry):
    """기록 하나를 지울 때 쓸 명령과 주의 {how, text, note}. 바로 실행하지 않는다."""
    t, r = entry.get("target", {}), entry.get("range", {})
    if entry["kind"] == "file":
        return {"how": "파일 지우기", "text": t.get("path", ""), "note": "이 파일을 지우면 됩니다(적재한 곳은 따로 지워야 합니다)."}
    if entry["kind"] == "kafka":
        parts = r.get("partitions", {})
        body = {"partitions": [{"topic": t.get("topic"), "partition": int(p), "offset": hi + 1} for p, (lo, hi) in sorted(parts.items(), key=lambda x: int(x[0]))],
                "version": 1}
        return {"how": "kafka-delete-records --offset-json-file", "text": json.dumps(body, ensure_ascii=False, indent=2),
                "note": "Kafka는 범위만 골라 지울 수 없습니다. 이 명령은 각 파티션에서 이 적재의 끝 offset까지(그 앞의 다른 메시지도 함께) 지웁니다. "
                        "이 적재의 메시지만 가려야 하면 소비하는 쪽에서 아래 offset 범위를 걸러 주세요: "
                        + ", ".join(f"파티션 {p}: {lo}~{hi}" for p, (lo, hi) in sorted(parts.items(), key=lambda x: int(x[0]))) if parts else "기록된 offset이 없습니다."}
    name = ".".join(quote(x) for x in (t.get("schema"), t.get("name")) if x)
    if r.get("load_id"):
        return {"how": "SQL", "text": f"DELETE FROM {name} WHERE {quote(r.get('column', LOAD_ID))} = {literal(r['load_id'])};",
                "note": "적재 ID로 이 적재의 행만 정확히 고릅니다(이어 넣은 행도 같은 ID)."}
    if t.get("how") == "new" and not entry.get("resumed"):
        return {"how": "SQL", "text": f"DROP TABLE {name};", "note": "이 적재로 새로 만든 테이블입니다. 다른 적재로 더 넣지 않았는지 확인하세요."}
    keys = r.get("keys", {})
    if keys:
        where = " AND ".join(f"{quote(c)} BETWEEN {literal(lo)} AND {literal(hi)}" for c, (lo, hi) in keys.items())
        return {"how": "SQL", "text": f"DELETE FROM {name} WHERE {where};",
                "note": "적재 ID 없이 키의 최소~최대로 고른 것이라, 그 범위의 다른 행도 함께 지워질 수 있습니다. 먼저 SELECT로 확인하세요."}
    return {"how": "", "text": "", "note": "적재 ID도 키도 없어 이 적재의 행만 가려낼 방법이 없습니다. 다음부터 [적재 ID 컬럼 넣기]를 켜세요."}
