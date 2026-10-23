"""Kafka 적재: 일부 행 전달이 실패하면 다시 시도 때 실패한 행만 다시 보낸다(가짜 Producer, 실제 Kafka 접속 없음)."""
import pyarrow as pa
import pytest

from collect import load_kafka


class FakeMsg:
    def __init__(self, n):
        self.n = n

    def offset(self): return self.n
    def partition(self): return 0
    def value(self): return b"v"
    def key(self): return None


class FakeProducer:
    """fail에 든 id의 메시지는 첫 번째 전달만 실패 보고를 한다."""

    def __init__(self, fail):
        self.fail, self.sent, self.queue = set(fail), [], []

    def produce(self, topic, body, key, on_delivery):
        self.sent.append(body)
        self.queue.append((body, on_delivery))

    def poll(self, t): pass

    def flush(self, t):
        for body, cb in self.queue:
            row = int(body.decode().split(":")[1].strip(" }"))
            if row in self.fail:
                self.fail.discard(row)
                cb("broker error", None)
            else:
                cb(None, FakeMsg(row))
        self.queue = []
        return 0


def make_sink(monkeypatch, fake):
    monkeypatch.setattr(load_kafka, "destination", lambda name: {"topic": "t"})
    monkeypatch.setattr(load_kafka, "producer", lambda item, password, on_error=None: fake)
    config = {"load": {"kafka": {"destination": "x", "key": ""}}}
    return load_kafka.Sink(config, "", pa.schema([("id", pa.int64())]), None)


def test_retry_resends_only_failed_rows(monkeypatch):
    fake = FakeProducer(fail={1, 3})
    sink = make_sink(monkeypatch, fake)
    data = pa.table({"id": [0, 1, 2, 3, 4]})
    with pytest.raises(ValueError, match="2행"):
        sink.put(data)
    assert len(fake.sent) == 5
    assert sink.put(data) == (5, 0, 0)  # 같은 묶음으로 다시: 실패한 두 행만 다시 보낸다
    assert len(fake.sent) == 7 and fake.sent[5:] == [b'{"id": 1}', b'{"id": 3}']
    assert sink.placed["partitions"] == {0: (0, 4)}
    assert sink.put(pa.table({"id": [9]})) == (1, 0, 0)  # 새 묶음은 처음부터
