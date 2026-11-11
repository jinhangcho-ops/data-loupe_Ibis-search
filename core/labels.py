"""행 단위 수동 라벨(태그): 이 PC 설정 폴더에 테이블마다 본 파일(labels_<지문>.json, 합친 상태의 DPAPI 봉투)과
변경분 기록(labels_<지문>.log, 한 줄 = 한 번의 변경을 DPAPI로 감싼 것, 덧붙이기)으로 {키 값: [태그…]}를 두고 기록이 차면 합친다.
검색·대시보드·전처리(@탭)에서 그 테이블을 쓸 때 고유 키로 left join해 '태그' 컬럼(글자, 태그 차례대로 ", "로 이음)을 붙인다.
원격 DB에는 라벨을 올리지 않는다: 태그를 켠 요청은 기간·조건까지의 결과를 이 PC(held)에 받아 그 위에서 붙인다(needs_pc)."""
import json
import os
from typing import TYPE_CHECKING

from lazy import ibis
from core import credentials
from core import dash_store
from core import handoff
from core import held
from core import parse
from core import server
from core import settings

if TYPE_CHECKING:
    import polars as pl

TAG_COLUMN = "태그"  # 붙이는 컬럼 이름(글자: 태그를 사전 차례대로 TAG_SEP로 이음, 태그가 없으면 빈 값)
TAG_SEP = ", "  # 한 행의 여러 태그를 잇는 글자. 태그 이름에는 쉼표를 쓸 수 없다
KEY_COLUMN = "__label_key"  # 라벨 표(memtable)의 키 컬럼
TAG_NAME = "__label_tag"  # 긴 라벨 표(키, 태그 하나)의 태그 컬럼
FILE_PREFIX = "labels_"  # 설정 폴더의 파일 이름 앞(뒤는 dash_store.def_hash([연결 열쇠, 스키마, 테이블]) + ".json")
VERSION = 1  # 파일 모양 {"v": VERSION, "blob": DPAPI 암호문}
KEY_TYPES = ("int", "string")  # 고유 키 컬럼으로 쓸 수 있는 타입(정수·글자)
MAX_LABELED = 1_000_000  # 태그가 달린 키 수(테이블 하나). 태그 종류 수·이름 길이는 상한이 없다(사용자 결정)
MAX_APPLY = 10_000  # 한 번에 고른 행으로 달거나 떼는 키 수(조건으로 한꺼번에는 MAX_LABELED까지)
QUICK_KEYS = 10  # 숫자 키 1~9, 0으로 고르는 앞 태그 수(화면이 SERVER.labels로 받는다)
LOG_SUFFIX = ".log"  # 변경분 기록 파일 이름 끝(본 파일 이름의 .json 자리)
MAX_LOG_LINES = 200  # 기록 줄이 이만큼 되면 합친다(차가운 읽기의 재생 시간을 묶는다)
MAX_LOG_BYTES = 4 * 1024 * 1024  # 기록 파일이 이만큼 되면 합친다
LOCKED = "이 PC에서 만든 태그 파일이 아니라 읽을 수 없어요. 파일은 그대로 두었어요."
LOG_BROKEN = "태그 변경 기록의 가운데 줄이 깨져 읽을 수 없어요. 파일은 그대로 두었어요."
NO_STORE = "이 테이블에는 아직 태그를 켜지 않았어요. 결과 표의 [태그]에서 고유 키 컬럼을 먼저 고르세요."
ONLY_TABLE = "태그는 연결의 테이블에만 붙일 수 있어요(쿼리 탭 @이름·이어붙이기 검색은 안 돼요)."
PC_TOO_BIG = (f"원격 DB의 표에 태그를 붙이려면 기간·조건까지의 결과를 이 PC로 받아야 하는데 {held.MAX_ROWS:,}행"
              f"(또는 {held.MAX_BYTES // 1024 // 1024}MB)을 넘어 받지 않았어요. 기간·조건을 좁히세요.")
CACHE = {}  # 푼 상태 {본 파일 이름: ((본 파일 (mtime_ns, 크기), 기록 (mtime_ns, 크기) 또는 None), 메모리 저장 내용(쌍 표 pairs), 깨진 마지막 줄이 있는지)}. 요청마다 다시 풀지 않는다
FRAMES = {}  # frame 결과 한 벌 {"pairs": 그 쌍 표 객체, "vocab": 사전 튜플, "frame": 표}. 같은 표·사전이면 검색마다 다시 묶지 않는다


def check_tag(name: object) -> str:
    """[SPECIFICATION] plans/phase-4.md#check_tag
    태그 이름 하나를 검사해 그대로 돌려준다(빈 이름·앞뒤 공백·쉼표·줄바꿈·탭은 거절, 길이 상한 없음)."""
    if not isinstance(name, str) or name.strip() == "":
        raise ValueError("태그 이름은 비어 있지 않은 글자예요.")
    if name != name.strip():
        raise ValueError("태그 이름 앞뒤에 공백을 둘 수 없어요.")
    if "," in name:
        raise ValueError("태그 이름에는 쉼표를 쓸 수 없어요.")
    if any(c in name for c in "\n\r\t"):
        raise ValueError("태그 이름에는 줄바꿈·탭을 쓸 수 없어요.")
    return name


def check_use(value: object) -> dict | None:
    """[SPECIFICATION] plans/phase-4.md#check_use
    검색 요청의 labels 값(None·{}·{any: [태그…]}·{all: [태그…]}·{none: true})을 검사해 정리한 dict(없으면 None)를 돌려준다."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"any", "all", "none"}:
        raise ValueError("태그(labels)는 {any: [태그…]}·{all: [태그…]}·{none: true} 객체예요.")
    use = {}
    if "none" in value:
        if not isinstance(value["none"], bool):
            raise ValueError("태그 없는 행만(none)은 켬(true)이나 끔(false)이에요.")
        if value["none"]:
            use["none"] = True
    for k in ("any", "all"):
        if k in value:
            names = value[k]
            if not isinstance(names, list) or not names:
                raise ValueError(f"태그 거르기({k})는 태그 1개 이상의 목록이에요.")
            use[k] = list(dict.fromkeys(check_tag(n) for n in names))
    if len(use) > 1:
        raise ValueError("태그 거르기는 any·all·none 중 하나만 쓸 수 있어요.")
    return use


def ref_of(body: dict) -> dict:
    """[SPECIFICATION] plans/phase-4.md#ref_of
    검색 요청의 기준 테이블과 지금 연결로 라벨 파일을 가리키는 {source, schema, table}을 만든다."""
    base = body.get("base")
    if not isinstance(base, str) or base.startswith(handoff.PREFIX) or body.get("union"):
        raise ValueError(ONLY_TABLE)
    server.get_con()
    now = dash_store.current_source()
    return {"source": now["key"], "schema": body.get("schema") or "", "table": base}


def file_name(ref: dict) -> str:
    """[SPECIFICATION] plans/phase-4.md#file_name
    라벨 파일 이름(labels_<지문 16자>.json)을 돌려준다."""
    return FILE_PREFIX + dash_store.def_hash([ref["source"], ref["schema"], ref["table"]]) + ".json"


def read_store(name: str) -> dict | None:
    """[SPECIFICATION] plans/phase-4.md#read_store
    본 파일 name을 풀고 변경분 기록을 재생한 저장 내용(없으면 None)을 돌려준다(캐시, 깨진 마지막 줄은 무시)."""
    path = settings.PATH.parent / name
    log = path.with_name(name[: -len(".json")] + LOG_SUFFIX)
    if not path.exists():
        return None
    stamp = lambda p: (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None  # noqa: E731
    stamps = (stamp(path), stamp(log))
    if CACHE.get(name, (None,))[0] != stamps:
        found = settings.read(name) or {}  # 깨진 JSON이면 settings.read가 .bak을 읽는다
        blob = found.get("blob") if found.get("v") == VERSION else None
        opened = credentials.reveal(blob) if isinstance(blob, str) else None
        if opened is None:
            raise ValueError(LOCKED)
        base = json.loads(opened)
        text = settings.retrying(lambda: log.read_text(encoding="ascii")) if log.exists() else ""
        lines = text.split("\n")
        torn = lines[-1] != ""  # 줄바꿈 없이 끝남 = 쓰다 끊긴 마지막 줄 → 무시
        lines = lines[:-1]  # 마지막 칸은 빈 글자(정상) 또는 깨진 줄
        store = from_json(base)
        df, vocab, rev = store["pairs"], store["vocab"], store["rev"]
        for line in lines:  # 기록 줄 수(≤ MAX_LOG_LINES)만큼
            plain = credentials.reveal(line)
            if plain is None:
                raise ValueError(LOG_BROKEN)  # 끝이 아닌 줄이 깨짐: 파일은 건드리지 않는다
            op = json.loads(plain)
            if op["rev"] <= rev:
                continue  # 이미 합친 줄(합치기 도중 꺼짐)
            df, vocab, _, _ = replay(df, vocab, op)
            rev = op["rev"]
        CACHE[name] = (stamps, {**store, "pairs": df, "vocab": vocab, "rev": rev, "labeled": df[KEY_COLUMN].n_unique()}, torn)
    return CACHE[name][1]


def load(ref: dict) -> dict | None:
    """[SPECIFICATION] plans/phase-4.md#load
    ref의 라벨 저장 내용(본 파일 + 기록 재생, 없으면 None)을 돌려준다."""
    return read_store(file_name(ref))


def replay(found: "pl.DataFrame", vocab: list[str], op: dict) -> tuple["pl.DataFrame", list[str], int, int]:
    """[SPECIFICATION] plans/phase-4.md#replay
    변경 하나(apply·vocab·rename·drop)를 (키, 태그) 쌍 표와 사전에 입힌 (새 쌍 표, 새 사전, 더한 쌍 수, 뺀 쌍 수)를 돌려준다(기록 재생과 지금 변경이 함께 쓴다)."""
    import polars as pl
    kind = op["op"]
    if kind == "apply":
        dtype = found.schema[KEY_COLUMN]
        sel = pl.DataFrame({KEY_COLUMN: op["keys"]}, schema={KEY_COLUMN: dtype}).unique()
        tags_df = lambda names: pl.DataFrame({TAG_NAME: names}, schema={TAG_NAME: pl.String})  # noqa: E731
        kept = found.join(sel.join(tags_df(op["remove"]), how="cross"), on=[KEY_COLUMN, TAG_NAME], how="anti")
        new = sel.join(tags_df(op["add"]), how="cross").join(kept, on=[KEY_COLUMN, TAG_NAME], how="anti")
        return pl.concat([kept, new]), [*vocab, *[t for t in op["add"] if t not in vocab]], new.height, found.height - kept.height
    if kind == "vocab":
        return found, list(op["vocab"]), 0, 0
    if kind == "rename":
        return (found.with_columns(pl.col(TAG_NAME).replace(op["old"], op["new"])),
                [op["new"] if t == op["old"] else t for t in vocab], 0, 0)
    if kind == "drop":
        kept = found.filter(pl.col(TAG_NAME) != op["name"])
        return kept, [t for t in vocab if t != op["name"]], 0, found.height - kept.height
    raise ValueError(LOG_BROKEN)


def commit(ref: dict, store: dict, op: dict) -> tuple[dict, int, int]:
    """[SPECIFICATION] plans/phase-4.md#commit
    변경 하나를 입힌 새 메모리 저장 내용(rev + 1)을 만들고 기록에 한 줄 덧붙인 뒤(차면 합침) (새 내용, 더한 쌍 수, 뺀 쌍 수)를 돌려준다. 잠금은 부르는 쪽."""
    name = file_name(ref)
    df, vocab, added, removed = replay(store["pairs"], store["vocab"], op)
    labeled = df[KEY_COLUMN].n_unique()
    if labeled > MAX_LABELED:
        raise ValueError(f"태그가 달린 행이 {labeled:,}개로 테이블 하나의 상한 {MAX_LABELED:,}개를 넘어요. 쓰지 않는 태그를 떼세요.")  # 쓰지 않는다
    out = {**store, "pairs": df, "vocab": vocab, "labeled": labeled, "rev": store["rev"] + 1}  # 목록으로 바꾸지 않는다(빠른 길)
    log = settings.PATH.parent / (name[: -len(".json")] + LOG_SUFFIX)
    if len(op.get("keys") or []) > MAX_APPLY:  # 큰 변경: 기록에 쓰지 않고 합친다
        return compact(name, out), added, removed
    if CACHE[name][2]:  # 깨진 마지막 줄: 지금까지를 먼저 합쳐 기록을 지운 뒤 새 변경을 한 줄로 쓴다
        compact(name, store)
    line = credentials.protect(json.dumps({**op, "rev": out["rev"]}, ensure_ascii=False, separators=(",", ":"))) + "\n"
    with log.open("ab") as f:  # 덧붙이기(앞 줄은 다시 쓰지 않는다)
        f.write(line.encode("ascii"))
        f.flush()
        os.fsync(f.fileno())
    info = log.stat()
    if log.read_bytes().count(b"\n") >= MAX_LOG_LINES or info.st_size >= MAX_LOG_BYTES:
        return compact(name, out), added, removed
    p = settings.PATH.parent / name
    CACHE[name] = (((p.stat().st_mtime_ns, p.stat().st_size), (info.st_mtime_ns, info.st_size)), out, False)
    return out, added, removed


def compact(name: str, store: dict) -> dict:
    """[SPECIFICATION] plans/phase-4.md#compact
    합친 저장 내용을 본 파일에 쓰고(.bak·바꿔치기) 기록 파일을 지운 뒤 그 내용을 돌려준다."""
    body = json.dumps(to_json(store), ensure_ascii=False, separators=(",", ":"))  # 목록으로 바꾸는 것은 합칠 때만
    settings.write(name, {"v": VERSION, "blob": credentials.protect(body)})  # 폴더·.bak·바꿔치기(원자적)
    log = settings.PATH.parent / (name[: -len(".json")] + LOG_SUFFIX)
    settings.retrying(lambda: log.unlink(missing_ok=True))  # 본 파일을 쓴 다음에만 지운다
    p = settings.PATH.parent / name
    CACHE[name] = (((p.stat().st_mtime_ns, p.stat().st_size), None), store, False)
    return store


def compact_all() -> int:
    """[SPECIFICATION] plans/phase-4.md#compact_all
    설정 폴더의 기록이 남은 라벨 파일을 모두 합치고(앱을 끌 때) 합친 수를 돌려준다."""
    count = 0
    for log in sorted(settings.PATH.parent.glob(FILE_PREFIX + "*" + LOG_SUFFIX)):  # 파일 목록만큼
        name = log.name[: -len(LOG_SUFFIX)] + ".json"
        if not (settings.PATH.parent / name).exists():
            continue  # 기록만 있는 파일은 모르는 것이라 남긴다
        with settings.updating(name):
            compact(name, read_store(name))
        count += 1
    return count


def store_of(body: dict) -> dict:
    """[SPECIFICATION] plans/phase-4.md#store_of
    검색 요청의 기준 테이블 라벨 저장 내용(없으면 NO_STORE)을 돌려준다."""
    found = load(ref_of(body))
    if found is None:
        raise ValueError(NO_STORE)
    return found


def check_key(table: "ibis.expr.types.Table", key: str) -> str:
    """[SPECIFICATION] plans/phase-4.md#check_key
    키 컬럼이 정수·글자이고 빈 값 없이 테이블 전체에서 고유한지 집계 한 번으로 보고 키 타입("int"·"string")을 돌려준다."""
    parse.check_name(key, table.columns, "키 컬럼")
    dtype = table[key].type()
    if not (dtype.is_integer() or dtype.is_string()):
        raise ValueError(f"키 컬럼은 정수나 글자 컬럼이어야 해요('{key}'은 {dtype}).")
    c = table[key]
    row = table.aggregate(n=table.count(), u=c.nunique(), nulls=c.isnull().sum()).to_pyarrow().to_pylist()[0]
    n, u, nulls = row["n"], row["u"], row["nulls"] or 0
    if nulls:  # 먼저 본다: Polars의 nunique는 빈 값도 센다
        raise ValueError(f"키 컬럼 '{key}'에 빈 값이 {nulls:,}개 있어 키로 쓸 수 없어요.")
    if u != n:
        raise ValueError(f"키 컬럼 '{key}'의 값이 고유하지 않아요(행 {n:,}개, 서로 다른 값 {u:,}개). 행마다 다른 컬럼을 고르세요.")
    return "int" if dtype.is_integer() else "string"


def setup(ref: dict, table: "ibis.expr.types.Table", key: str, text: str, vocab: list) -> dict:
    """[SPECIFICATION] plans/phase-4.md#setup
    테이블의 라벨을 켠다(키 검사, 글 컬럼·태그 사전 저장). 이미 있으면 키는 그대로 두고 글 컬럼·사전만 바꾼다."""
    words = list(dict.fromkeys(check_tag(v) for v in vocab))
    if text:
        parse.check_name(text, table.columns, "글 컬럼")
        if not table[text].type().is_string():
            raise ValueError("글 컬럼은 글자 컬럼이어야 해요.")
    with settings.updating(file_name(ref)):
        old = load(ref)
        if old and old["key"] == key:  # 같은 키: 다시 세지 않는다
            missing = set(old["pairs"][TAG_NAME].unique().to_list()) - set(words)
            if missing:
                raise ValueError(f"행에 달린 태그는 사전에서 뺄 수 없어요(지우려면 [지우기]): {', '.join(sorted(missing))}")
            return compact(file_name(ref), {**old, "text": text or "", "vocab": words, "rev": old["rev"] + 1})
        if old and old["labeled"]:
            raise ValueError("이미 태그가 달린 행이 있어 키 컬럼을 바꿀 수 없어요. 태그를 모두 뗀 뒤 바꾸세요.")
        return compact(file_name(ref), from_json({"source": ref["source"], "schema": ref["schema"], "table": ref["table"], "key": key, "key_type": check_key(table, key),
                                                  "text": text or "", "vocab": words, "keys": [], "tags": [], "rev": (old["rev"] if old else 0) + 1}))


def set_vocab(ref: dict, vocab: list) -> dict:
    """[SPECIFICATION] plans/phase-4.md#set_vocab
    태그 사전(차례 포함)을 바꾼다. 행에 달린 태그는 사전에서 뺄 수 없다."""
    words = list(dict.fromkeys(check_tag(v) for v in vocab))
    with settings.updating(file_name(ref)):
        old = load(ref)
        if old is None:
            raise ValueError(NO_STORE)
        missing = set(old["pairs"][TAG_NAME].unique().to_list()) - set(words)
        if missing:
            raise ValueError(f"행에 달린 태그는 사전에서 뺄 수 없어요(지우려면 [지우기]): {', '.join(sorted(missing))}")
        return commit(ref, old, {"op": "vocab", "vocab": words})[0]


def rename_tag(ref: dict, old: str, new: str) -> dict:
    """[SPECIFICATION] plans/phase-4.md#rename_tag
    사전과 이미 달린 모든 행에서 태그 이름을 바꾸고 저장 내용을 돌려준다."""
    new = check_tag(new)
    with settings.updating(file_name(ref)):
        store = load(ref)
        if store is None:
            raise ValueError(NO_STORE)
        if old not in store["vocab"]:
            raise ValueError(f"사전에 없는 태그예요: {old}")
        if new in store["vocab"]:
            raise ValueError(f"이미 있는 태그 이름이에요: {new}")
        return commit(ref, store, {"op": "rename", "old": old, "new": new})[0]


def drop_tag(ref: dict, name: str) -> dict:
    """[SPECIFICATION] plans/phase-4.md#drop_tag
    사전과 이미 달린 모든 행에서 태그를 지우고 {removed, labeled, vocab}를 돌려준다."""
    with settings.updating(file_name(ref)):
        store = load(ref)
        if store is None:
            raise ValueError(NO_STORE)
        if name not in store["vocab"]:
            raise ValueError(f"사전에 없는 태그예요: {name}")
        out, _, removed = commit(ref, store, {"op": "drop", "name": name})
    return {"removed": removed, "labeled": out["labeled"], "vocab": out["vocab"]}


def revision(body: dict) -> int | None:
    """[SPECIFICATION] plans/phase-4.md#revision
    태그를 켠 요청이면 그 테이블 라벨 파일의 변경 번호(rev), 아니면 None(캐시 열쇠에 넣는다)."""
    if check_use(body.get("labels")) is None:
        return None
    found = load(ref_of(body))
    if found is None:
        raise ValueError(NO_STORE)
    return found["rev"]


def apply(ref: dict, keys: list, add: list, remove: list) -> dict:
    """[SPECIFICATION] plans/phase-4.md#apply
    키 목록의 행들에 태그를 달고(사전에 없으면 더함) 떼어 저장하고 {changed, labeled, vocab}를 돌려준다."""
    add = list(dict.fromkeys(check_tag(t) for t in add))
    remove = list(dict.fromkeys(check_tag(t) for t in remove))
    if not add and not remove:
        raise ValueError("달거나 뗄 태그를 고르세요.")
    if set(add) & set(remove):
        raise ValueError("같은 태그를 달고 떼는 것을 함께 할 수 없어요.")
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_LABELED:
        raise ValueError(f"태그를 달 행의 키를 1~{MAX_LABELED:,}개 주세요.")
    with settings.updating(file_name(ref)):
        store = load(ref)
        if store is None:
            raise ValueError(NO_STORE)
        is_int = store["key_type"] == "int"
        if not (all(type(k) is int for k in keys) if is_int else all(isinstance(k, str) for k in keys)):  # bool 거부
            raise ValueError(f"키 값은 {'정수' if is_int else '글자'}여야 해요.")
        unknown = [t for t in remove if t not in store["vocab"]]
        if unknown:
            raise ValueError(f"사전에 없는 태그예요: {', '.join(unknown)}")
        out, added, removed = commit(ref, store, {"op": "apply", "keys": keys, "add": add, "remove": remove})  # MAX_LABELED는 commit이 본다
    return {"added": added, "removed": removed, "labeled": out["labeled"], "vocab": out["vocab"]}


def frame(store: dict) -> "pl.DataFrame":
    """[SPECIFICATION] plans/phase-4.md#frame
    저장 내용의 쌍 표를 (KEY_COLUMN, TAG_COLUMN 글자: 사전 차례로 이음) Polars 표로 만든다(같은 rev면 한 번만 만든다)."""
    import polars as pl
    vocab = tuple(store["vocab"])
    if FRAMES.get("pairs") is not store["pairs"] or FRAMES.get("vocab") != vocab:  # 같은 표 객체와 같은 사전이면 다시 묶지 않는다
        order = pl.DataFrame({TAG_NAME: store["vocab"], "__i": list(range(len(store["vocab"])))}, schema={TAG_NAME: pl.String, "__i": pl.Int64})
        f = (store["pairs"].join(order, on=TAG_NAME).sort([KEY_COLUMN, "__i"])
             .group_by(KEY_COLUMN, maintain_order=True).agg(pl.col(TAG_NAME).str.join(TAG_SEP).alias(TAG_COLUMN)))
        FRAMES.clear()  # 한 벌만 둔다
        FRAMES.update(pairs=store["pairs"], vocab=vocab, frame=f)
    return FRAMES["frame"]


def from_json(data: dict) -> dict:
    """[SPECIFICATION] plans/phase-4.md#from_json
    본 파일에 쓰는 모양(keys·tags 목록)을 메모리 모양(쌍 표 pairs, 달린 키 수 labeled)으로 바꾼다."""
    import polars as pl
    dtype = pl.Int64 if data["key_type"] == "int" else pl.String
    f = pl.DataFrame({KEY_COLUMN: data["keys"], TAG_NAME: data["tags"]}, schema={KEY_COLUMN: dtype, TAG_NAME: pl.List(pl.String)})
    pairs = f.explode(TAG_NAME)
    return {**{k: v for k, v in data.items() if k not in ("keys", "tags")}, "pairs": pairs, "labeled": f.height}


def to_json(store: dict) -> dict:
    """[SPECIFICATION] plans/phase-4.md#to_json
    메모리 모양을 본 파일에 쓰는 모양(키 차례, 태그는 사전 차례인 keys·tags 목록)으로 바꾼다(합칠 때만)."""
    import polars as pl
    order = pl.DataFrame({TAG_NAME: store["vocab"], "__i": list(range(len(store["vocab"])))}, schema={TAG_NAME: pl.String, "__i": pl.Int64})
    g = (store["pairs"].unique().join(order, on=TAG_NAME).sort([KEY_COLUMN, "__i"])
         .group_by(KEY_COLUMN, maintain_order=True).agg(pl.col(TAG_NAME)))
    return {**{k: v for k, v in store.items() if k not in ("pairs", "labeled")}, "keys": g[KEY_COLUMN].to_list(), "tags": g[TAG_NAME].to_list()}


def attach(expr: "ibis.expr.types.Table", store: dict, use: dict) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-4.md#attach
    식에 키로 라벨 표를 left join해 TAG_COLUMN을 붙이고, use의 any·none으로 행을 거른다(실행하지 않는다)."""
    key = store["key"]
    parse.check_name(key, expr.columns, "키 컬럼")
    if TAG_COLUMN in expr.columns:
        raise ValueError(f"'{TAG_COLUMN}' 컬럼이 이미 있어 태그를 붙일 수 없어요. 그 컬럼 이름을 바꾸세요.")
    if use.get("any") or use.get("all"):
        expr = tagged(expr, store, use.get("any") or use["all"], "all" in use)
    m = ibis.memtable(frame(store).to_arrow())
    m = m.mutate(**{KEY_COLUMN: m[KEY_COLUMN].cast(expr[key].type())})
    if use.get("none"):
        other = m.view()  # 같은 표를 두 번 조인하면 Ibis가 어느 쪽 컬럼인지 모른다(Ambiguous field reference)
        expr = expr.anti_join(other, expr[key] == other[KEY_COLUMN])
    j = expr.left_join(m, expr[key] == m[KEY_COLUMN])
    return j.select(*[j[c] for c in expr.columns], j[TAG_COLUMN])


def attached(body: dict, expr: "ibis.expr.types.Table") -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-4.md#attached
    검색 요청이 태그를 켰으면(labels) 그 테이블의 라벨을 붙인 식, 아니면 expr 그대로를 돌려준다."""
    use = check_use(body.get("labels"))
    if use is None:
        return expr
    return attach(expr, store_of(body), use)


def needs_pc(body: dict) -> bool:
    """[SPECIFICATION] plans/phase-4.md#needs_pc
    태그를 켠 요청이 원격 DB 연결이라 이 PC 사본(held) 위에서 붙여야 하는지."""
    return check_use(body.get("labels")) is not None and held.remote(server.get_con())


def keep_key(body: dict, keep: list | None) -> list | None:
    """[SPECIFICATION] plans/phase-4.md#keep_key
    태그를 켠 요청이면 쓸 컬럼 목록(keep)에 라벨 키 컬럼을 더한다(줄인 식·PC 사본에도 키가 있어야 붙인다). keep이 비면 그대로."""
    if not keep or check_use(body.get("labels")) is None:
        return keep
    key = store_of(body)["key"]
    return keep if key in keep else [*keep, key]


def keys_of(expr: "ibis.expr.types.Table", key: str) -> list:
    """[SPECIFICATION] plans/phase-4.md#keys_of
    식의 서로 다른 키 값(빈 값 제외)을 MAX_LABELED개까지 받아 목록으로, 넘으면 거부한다."""
    t = expr.select(k=expr[key])
    t = t.filter(t.k.notnull()).distinct()
    found = t.limit(MAX_LABELED + 1).to_pyarrow().column("k").to_pylist()
    if len(found) > MAX_LABELED:
        raise ValueError(f"검색 결과의 키가 {MAX_LABELED:,}개를 넘어 한꺼번에 달 수 없어요. 기간·조건을 좁히세요.")
    return found


def tagged(expr: "ibis.expr.types.Table", store: dict, tags: list, every: bool) -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-4.md#tagged
    고른 태그가 하나라도(every=False) 또는 모두(every=True) 달린 행만 남긴 식(semi join). 태그를 고르지 않으면 태그가 하나라도 있는 행."""
    import polars as pl
    p = store["pairs"]
    if tags:
        p = p.filter(pl.col(TAG_NAME).is_in(tags))
    ks = p.group_by(KEY_COLUMN).agg(n=pl.col(TAG_NAME).n_unique())
    if tags and every:
        ks = ks.filter(pl.col("n") == len(set(tags)))  # 고른 태그가 모두 달린 키
    ks = ibis.memtable(ks.select(KEY_COLUMN).to_arrow())
    ks = ks.mutate(**{KEY_COLUMN: ks[KEY_COLUMN].cast(expr[store["key"]].type())})
    return expr.semi_join(ks, expr[store["key"]] == ks[KEY_COLUMN])


def tag_counts(expr: "ibis.expr.types.Table", store: dict, tags: list) -> list[dict]:
    """[SPECIFICATION] plans/phase-4.md#tag_counts
    식의 행에 달린 태그별 행 수 [{w, n}](많은 순, 고른 태그만, 고르지 않으면 사전 전부)."""
    import polars as pl
    wanted = tags or store["vocab"]
    unknown = [t for t in wanted if t not in store["vocab"]]
    if unknown:
        raise ValueError(f"사전에 없는 태그예요: {', '.join(unknown)}")
    p = ibis.memtable(store["pairs"].filter(pl.col(TAG_NAME).is_in(wanted)).to_arrow())
    p = p.mutate(**{KEY_COLUMN: p[KEY_COLUMN].cast(expr[store["key"]].type())})
    k = expr.select(k=expr[store["key"]])
    j = k.inner_join(p, k.k == p[KEY_COLUMN])
    found = {r[TAG_NAME]: int(r["n"]) for r in j.group_by(TAG_NAME).aggregate(n=j.count()).to_pyarrow().to_pylist()}  # 태그 수만큼의 작은 결과
    return sorted(({"w": t, "n": found[t]} for t in wanted if found.get(t)), key=lambda x: (-x["n"], store["vocab"].index(x["w"])))
