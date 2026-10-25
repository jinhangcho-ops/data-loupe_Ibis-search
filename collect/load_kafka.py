"""적재할 곳: Kafka 토픽(confluent-kafka). 등록(kafka.json)에는 비밀번호를 두지 않는다. 비밀번호는 인자로만 받아 Producer 설정에 넣고 작업이 끝나면 버린다.
한 행 = JSON 메시지 하나(키 컬럼이 있으면 그 값을 글자로 메시지 키에). librdkafka의 로그는 받지 않고 버린다(설정이 찍힐 때 비밀번호가 나오지 않게)."""
import base64
import datetime as dt
import decimal
import json
import logging
import math
import re

from core import settings
from core import source
from collect.collect_http import HIDE  # 오류 문구에서 비밀번호를 바꿔 넣는 글자(수집과 같은 것)

FILE = "kafka.json"
MAX_DESTINATIONS = 30
MAX_SIZE = 5_000  # 등록 하나의 크기(JSON 글자 수)
SECURITY = ("PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL")
MECHANISMS = ("PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512")
DEFAULT = {"servers": "", "topic": "", "security": "PLAINTEXT", "mechanism": "PLAIN", "user": "", "ca": ""}
SERVERS = re.compile(r"[A-Za-z0-9._\-\[\]:]+:\d{1,5}(,[A-Za-z0-9._\-\[\]:]+:\d{1,5})*")  # host:port[,host:port…]
TOPIC = re.compile(r"[A-Za-z0-9._-]{1,249}")  # Kafka가 받는 토픽 이름
CHECK_TIMEOUT = 10  # 접속 확인(list_topics)을 기다리는 시간(초)
SEND_TIMEOUT = 60  # 메시지 하나가 전달되기를 기다리는 시간(초). 넘으면 실패로 센다.
PERMANENT = ("MSG_SIZE_TOO_LARGE", "INVALID_MSG", "CORRUPT_MESSAGE", "INVALID_RECORD", "RECORD_LIST_TOO_LARGE")  # 그 행 자체가 안 되는 오류(다시 보내도 같다). 그 밖의 오류(시간 초과·연결 끊김…)는 일시 오류로 보고 다시 시도한다
FLUSH_MARGIN = 10  # 묶음을 다 보낸 뒤 기다리는 시간 = SEND_TIMEOUT + 이것(초). 그 안에 실패 보고까지 온다.
CHUNK = 10_000  # 묶음을 Python 값으로 바꿀 때 한 번에 바꾸는 행 수

QUIET = logging.getLogger("data_loupe.kafka")  # librdkafka의 로그를 받아 버리는 곳
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


# ---------- 등록 ----------

def destinations():
    return settings.named(FILE)


def checked(item):
    """등록 하나를 검사하고 빠진 칸을 채운다. 비밀번호 칸은 받지 않는다."""
    if not isinstance(item, dict):
        raise ValueError("적재할 곳이 올바르지 않습니다.")
    unknown = [k for k in item if k not in DEFAULT]
    if unknown:
        if any("pass" in k.lower() or "secret" in k.lower() for k in unknown):
            raise ValueError("비밀번호는 저장하지 않습니다. 확인·시작할 때 따로 입력하세요.")
        raise ValueError(f"적재할 곳에 쓸 수 없는 값이 있습니다: {', '.join(unknown)}")
    found = {**DEFAULT, **item}
    if not all(isinstance(v, str) for v in found.values()):
        raise ValueError("적재할 곳의 칸은 모두 글자여야 합니다.")
    found = {k: v.strip() for k, v in found.items()}
    if not SERVERS.fullmatch(found["servers"]):
        raise ValueError("서버는 host:port 모양으로, 여럿이면 쉼표로 이어 쓰세요(예: host1:9092,host2:9092).")
    if not TOPIC.fullmatch(found["topic"]):
        raise ValueError("토픽 이름은 영문·숫자·점·밑줄·빼기로 1~249자입니다.")
    if found["security"] not in SECURITY:
        raise ValueError(f"보안 방식은 {', '.join(SECURITY)} 중 하나입니다.")
    if found["mechanism"] not in MECHANISMS:
        raise ValueError(f"SASL 방식은 {', '.join(MECHANISMS)} 중 하나입니다.")
    if found["security"].startswith("SASL") and not found["user"]:
        raise ValueError("SASL을 쓰면 아이디를 입력하세요.")
    if max(len(found["user"]), len(found["ca"])) > 1000:
        raise ValueError("아이디·CA 인증서 경로는 1,000자까지입니다.")
    return found


def save(name, item):
    """item이 None이면 그 이름을 지운다."""
    return settings.save_named(FILE, "적재할 곳", MAX_SIZE, name, None if item is None else checked(item), MAX_DESTINATIONS)


def destination(name):
    found = destinations().get(name)
    if not isinstance(found, dict):
        raise ValueError(f"적재할 곳 '{name}'이 등록되어 있지 않습니다." if name else "적재할 Kafka 곳을 고르세요.")
    return checked(found)


# ---------- 구성 ----------

def ready(config):
    destination(config["load"]["kafka"]["destination"])


def where(config):
    name = config["load"]["kafka"]["destination"]
    return f"{name} · {destination(name)['topic']}"


def sign(config):
    """이어 넣기를 정하는 대상: 서버, 토픽, 키 컬럼(업로드 기록의 대상에도 쓴다)."""
    kafka = config["load"]["kafka"]
    item = destination(kafka["destination"])
    return {"servers": item["servers"], "topic": item["topic"], "key": kafka["key"]}


# ---------- 보내기 ----------

class NotInstalled(ValueError):
    """수집 기능을 설치하지 않아 confluent_kafka가 없다."""


def producer(item, password, on_error=None):
    """등록과 비밀번호로 Producer를 만든다. 모든 전달은 acks=all·멱등(같은 메시지가 두 번 쓰이지 않게)으로."""
    try:
        from confluent_kafka import Producer
    except ImportError:  # confluent_kafka는 설치할 때 [수집] 기능에만 들어 있다
        raise NotInstalled("Kafka 적재는 수집 기능에 들어 있어요. [설정]에서 수집 기능을 추가하세요.") from None
    conf = {"bootstrap.servers": item["servers"], "security.protocol": item["security"], "acks": "all", "enable.idempotence": True,
            "message.timeout.ms": SEND_TIMEOUT * 1000, "logger": QUIET}
    if on_error is not None:  # 연결이 끊기는 것 같은 전체 오류(메시지별 실패는 전달 보고로 온다)
        conf["error_cb"] = on_error
    if item["security"].startswith("SASL"):
        conf.update({"sasl.mechanism": item["mechanism"], "sasl.username": item["user"], "sasl.password": password})
    if item["ca"]:
        conf["ssl.ca.location"] = item["ca"]
    return Producer(conf)


def check(name, password):
    """접속과 토픽만 확인한다(메시지는 보내지 않음). 토픽 이름을 콕 집어 묻지 않는다(자동으로 만드는 서버가 있어서)."""
    item = destination(name)
    try:
        found = producer(item, password).list_topics(timeout=CHECK_TIMEOUT)
    except NotInstalled:
        raise
    except Exception as e:
        raise ValueError(f"Kafka에 접속하지 못했습니다: {source.mask(str(e), [password], HIDE)}") from None
    return {"ok": True, "topic": item["topic"] in found.topics, "brokers": len(found.brokers)}


def plain(value):
    """JSON에 넣을 값: 날짜·시간은 ISO 글자, Decimal은 글자, bytes는 base64, NaN·무한은 null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, dt.timedelta)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def message(row):
    return json.dumps(plain(row), ensure_ascii=False, allow_nan=False).encode("utf-8")


def key_of(value):
    value = plain(value)
    return None if value is None else (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).encode("utf-8")


class Sink:
    """한 번의 적재 동안 쓰는 Producer. put으로 묶음(pyarrow 표)을 보내고 전달 보고를 기다린다."""

    unit = None  # 지금 보내는 묶음(같은 묶음을 다시 put하면 전달하지 못한 행만 다시 보낸다)

    def __init__(self, config, password, arrow_schema, resume, load_id=None):
        kafka = config["load"]["kafka"]
        self.item, self.key, self.password = destination(kafka["destination"]), kafka["key"], password
        if self.key and self.key not in arrow_schema.names:
            raise ValueError(f"메시지 키 컬럼 '{self.key}'이 결과 파일에 없습니다.")
        self.last, self.fresh = "", False
        self.placed = {}  # 마지막 put이 전달한 것(업로드 기록): {"size": 바이트, "partitions": {파티션: [첫 offset, 끝 offset]}}
        self.producer = producer(self.item, password, self.note)

    def put(self, data):
        """(전달된 수, 0, 실패 수). 메시지가 너무 큰 행처럼 보내지 못한 행은 실패로 센다. 전달하지 못한 행이 있으면 오류(다시 시도는 부르는 쪽이).
        같은 묶음(data)으로 다시 부르면 이미 전달된 행은 빼고 전달하지 못한 행만 다시 보낸다."""
        from confluent_kafka import KafkaException
        if data is not self.unit:  # 새 묶음
            self.unit, self.chunk, self.todo, self.ok, self.failed, self.partitions, self.size = data, (None, []), range(data.num_rows), set(), 0, {}, 0
            self.dead = set()  # 영구 실패로 센 행 번호(이어 받기는 min(todo) 앞의 것만 센다. 뒤의 것은 다시 보내져 다시 세어진다)
        sent, reports, seen = set(), {}, set()  # seen: 앞 회에서 이미 처리한(영구 오류로 센) 행. 이번 회에 새로 온 오류만 보고 멈춘다

        def report(i, error, msg):  # 전달 보고: 전달된 메시지의 파티션·offset으로 범위를 넓힌다
            reports[i] = error
            if error is None and msg is not None and msg.offset() is not None and msg.offset() >= 0:
                p, o = msg.partition(), msg.offset()
                lo, hi = self.partitions.get(p, (o, o))
                self.partitions[p] = (min(lo, o), max(hi, o))
                self.size += len(msg.value() or b"") + len(msg.key() or b"")
        pending = list(self.todo)
        while True:
            skipped = set()
            for n, i in enumerate(pending):
                row = self.row(i)
                try:
                    body = message(row)
                    key = key_of(row[self.key]) if self.key else None
                    while True:
                        try:
                            self.producer.produce(self.item["topic"], body, key, on_delivery=lambda error, msg, i=i: report(i, error, msg))
                            break
                        except BufferError:  # 보낼 줄이 가득 참: 조금 보내고 다시
                            self.producer.poll(1)
                    sent.add(i)
                except (KafkaException, ValueError, TypeError) as e:
                    self.failed += 1
                    self.dead.add(i)
                    self.note(e)
                if self.key:  # 키가 있으면 같은 키의 순서가 중요하다: 전달 실패가 보고되면 뒤 행은 보내지 않고 다시 시도 때 차례대로 보낸다
                    self.producer.poll(0)
                    if any(e is not None and i not in seen for i, e in reports.items()):
                        skipped = set(pending[n + 1:])
                        break
            self.producer.flush(SEND_TIMEOUT + FLUSH_MARGIN)
            for e in [e for e in reports.values() if e is not None][-1:]:
                self.note(e)
            delivered = {i for i in sent if i in reports and reports[i] is None}
            forever = {i for i in sent if reports.get(i) is not None and any(name in str(reports[i]) for name in PERMANENT)}  # 이 행만 안 되는 오류: 실패로 세고 계속한다
            if not skipped or sent - delivered - forever:  # 멈춘 까닭이 영구 오류뿐이면(일시 오류가 없으면) 남은 행을 이어 보낸다
                break
            seen = {i for i, e in reports.items() if e is not None}
            pending = sorted(skipped)
        self.failed += len(forever)
        self.dead |= forever
        self.ok |= delivered
        self.todo = sorted(skipped | (sent - delivered - forever))  # 일시 오류이거나 보고가 끝내 오지 않은 행(+ 보내지 않고 둔 행): 다음에 이것만 차례대로 다시 보낸다
        self.placed = {"size": self.size, "partitions": self.partitions}
        if sent and not delivered and not forever:
            raise ValueError(f"Kafka에 전달하지 못했습니다: {self.last or '응답 없음'}")
        if self.todo:  # 일부만 전달됐다: 오류로 알려 다시 시도하게 한다(끝내 안 되면 부른 쪽이 멈추고 partial로 넣은 데까지 남긴다)
            raise ValueError(f"Kafka에 {len(self.todo)}행을 전달하지 못했습니다: {self.last or '응답 없음'}")
        return len(self.ok), 0, self.failed

    def row(self, i):
        """지금 묶음의 i번째 행(dict). 묶음을 CHUNK행씩 Python 값으로 바꿔 둔다(100만 행 묶음을 통째로 바꾸면 GB 메모리). 행은 앞에서부터 차례로 읽는다."""
        at = i - i % CHUNK
        if self.chunk[0] != at:
            self.chunk = at, self.unit.slice(at, CHUNK).to_pylist()
        return self.chunk[1][i - at]

    def partial(self):
        """지금 묶음에서 끝내 못 넣고 멈출 때 남길 것: (앞에서부터 이어서 처리된 행 수, 전달된 행 수, 영구 실패로 센 행 수). 이어 넣기가 그 뒤부터 하게 한다(앞 구멍 뒤에 전달된 행은 다시 보내져 겹칠 수 있다)."""
        if self.unit is None:
            return 0, 0, 0
        done = min(self.todo) if self.todo else self.unit.num_rows
        return done, len(self.ok), sum(1 for i in self.dead if i < done)

    def note(self, error):
        self.last = source.mask(str(error), [self.password], HIDE)

    def done(self):
        pass

    def close(self):
        self.producer.flush(SEND_TIMEOUT)
        self.producer = None
