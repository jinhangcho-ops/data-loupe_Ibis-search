"""(B9) 목록 줄의 표시: 목록 카드마다 사용자가 만든 표시 칸(view.row_marks)의 값을 설정 폴더 dash_marks\\<대시보드 id>\\<카드 id>.parquet에 둔다
(설명 marks.json = 암호화 묶음·소금). 암호화는 대시보드 받기 설정(pull.crypt, B11)과 같은 열쇠·같은 DuckDB Parquet 암호화(dash_pull.parquet_key)를 쓴다.
원본 DB는 건드리지 않는다. 명세: plans/dash-blocks.md "B9 세부 명세"."""
import base64
import json
import math
import secrets
import threading
from pathlib import Path

import polars as pl

from core import dash_store, result, server, settings
from dashboard import dash_keys, dash_pull
from lazy import ibis

MARKS_DIR = "dash_marks"  # 설정 폴더 아래 표시 폴더
MANIFEST = "marks.json"  # 대시보드 표시 폴더의 설명 {"v", "crypt": {"group", "salt"}}
VERSION = 1  # marks.json 모양 번호
KEY_COL = "__key"  # 표시 파일의 열쇠 컬럼(열쇠 컬럼들의 값을 key_expr로 이은 글자)
TEMP_PREFIX = "~"  # 쓰는 중인 파일 이름 앞글자(다 쓰면 바꿔치기)
FILE_SUFFIX = ".parquet"  # 카드 하나의 표시 파일 확장자
MAX_KEY = 2000  # 줄 열쇠 글자의 글자 수(열쇠 컬럼 여러 개를 이은 것)
READS = (".csv", ".tsv", ".parquet", ".xlsx")  # 가져올 수 있는 형식(result.EXPORTS 가운데 다시 읽는 길이 있는 것, server.PICK_KINDS["marks"]와 같은 차례)
SAVE_NAMES = {".csv": "CSV", ".xlsx": "Excel", ".parquet": "Parquet", ".tsv": "TSV", ".txt": "고정폭 텍스트"}  # 저장 대화상자의 형식 이름(result.EXPORTS의 확장자 전부)
PICK_KIND = "marks"  # server.PICK_KINDS의 가져오기 종류
MARK_DTYPES = {"choice": pl.Utf8, "text": pl.Utf8, "date": pl.Utf8, "number": pl.Float64}  # 표시 칸 종류 → 표시 파일 컬럼 타입(날짜는 YYYY-MM-DD 글자)
LOCK = threading.RLock()  # 표시 파일·설명을 읽고 고쳐 쓰기를 하나씩
NOT_LIST = "표시를 붙일 목록 카드를 찾지 못했어요. 대시보드를 저장한 뒤 다시 해 주세요."
NO_FIELDS = "이 목록에는 표시 칸이 없어요. 카드 설정에서 표시 칸을 더하세요."
NO_MARKS = "이 목록에는 아직 붙인 표시가 없어요."
NO_WINDOW = "파일 창을 열 수 없어요(앱 창에서만 돼요)."
CRYPT_OFF = "암호화 설정과 표시 파일의 암호화가 달라요. 대시보드 설정에서 암호화를 다시 저장해 주세요."


def key_expr(keys: list[str]) -> pl.Expr:
    """[SPECIFICATION] plans/dash-blocks.md#marks_key_expr
    열쇠 컬럼들(글자로 바꿀 Polars 컬럼)을 열쇠 글자 하나({"0": 값, "1": 값, …}의 JSON)로 잇는 식."""
    if not keys:
        raise ValueError("이 목록에는 표시를 붙이는 열쇠 컬럼이 없어요.")
    return pl.struct([pl.col(c).cast(pl.Utf8).alias(str(i)) for i, c in enumerate(keys)]).struct.json_encode()


def folder(dash_id: str) -> Path:
    """[SPECIFICATION] plans/dash-blocks.md#marks_folder
    그 대시보드의 표시 폴더 경로(대시보드 id 모양을 검사한다)."""
    if not (isinstance(dash_id, str) and dash_store.ID.fullmatch(dash_id)):
        raise ValueError("대시보드 id가 맞지 않아요.")
    return settings.PATH.parent / MARKS_DIR / dash_id


def list_card(dash: dict, card_id: object) -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#marks_list_card
    저장한 대시보드에서 표시 칸이 있는 목록 카드 하나를 찾아 돌려준다(없으면 ValueError)."""
    card = next((c for c in dash["cards"] if c["id"] == card_id and c["kind"] == "list"), None)
    if card is None:
        raise ValueError(NOT_LIST)
    if not (card["view"].get("row_marks") or {}).get("fields"):
        raise ValueError(NO_FIELDS)
    return card


def manifest(dash_id: str) -> dict | None:
    """[SPECIFICATION] plans/dash-blocks.md#marks_manifest
    그 대시보드 표시 폴더의 marks.json(없으면 None, 모양이 틀리면 ValueError)."""
    path = folder(dash_id) / MANIFEST
    if not path.is_file():
        return None
    man = json.loads(path.read_text(encoding="utf-8"))
    if not (isinstance(man, dict) and set(man) == {"v", "crypt"} and man["v"] == VERSION
            and isinstance(man["crypt"], dict) and set(man["crypt"]) == {"group", "salt"}):
        raise ValueError(f"표시 설명 파일이 깨졌어요: {path}")
    return man


def key_of(dash_id: str) -> str | None:
    """[SPECIFICATION] plans/dash-blocks.md#marks_key_of
    그 대시보드 표시 파일을 지금 쓴 열쇠(marks.json의 묶음·소금으로 dash_keys.slot_key). 설명이 없거나 암호화 없음이면 None."""
    man = manifest(dash_id)
    if man is None:
        return None
    return dash_keys.slot_key(man["crypt"]["group"], dash_id, man["crypt"]["salt"])


def read(dash_id: str, card_id: str, key: str | None) -> pl.DataFrame:
    """[SPECIFICATION] plans/dash-blocks.md#marks_read
    카드 하나의 표시 파일을 열쇠 key로 읽은 표(KEY_COL + 칸 id 컬럼들). 파일이 없으면 KEY_COL만 있는 빈 표."""
    if not dash_store.ID.fullmatch(card_id):
        raise ValueError("카드 id가 맞지 않아요.")
    path = folder(dash_id) / f"{card_id}{FILE_SUFFIX}"
    if not path.is_file():
        return pl.DataFrame(schema={KEY_COL: pl.Utf8})
    con = ibis.duckdb.connect()
    try:
        read_enc = dash_pull.parquet_key(con, key, set())[1]
        return con.read_parquet([str(path)], **read_enc).to_polars()
    finally:
        con.disconnect()


def write(dash: dict, card_id: str, frame: pl.DataFrame) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#marks_write
    카드 하나의 표시 표를 대시보드의 지금 암호화로 바꿔치기해 쓴다(빈 표면 파일을 지운다)."""
    with LOCK:
        if not dash_store.ID.fullmatch(card_id):
            raise ValueError("카드 id가 맞지 않아요.")
        base = folder(dash["id"])
        path = base / f"{card_id}{FILE_SUFFIX}"
        group = dash_keys.group_of(dash["id"], dash_store.check_pull(dash.get("pull"))["crypt"])
        man = manifest(dash["id"])
        if man is not None and man["crypt"]["group"] != group:
            raise ValueError(CRYPT_OFF)
        if frame.height == 0:
            path.unlink(missing_ok=True)
            return
        if man is None:
            salt = base64.b64encode(secrets.token_bytes(dash_keys.SALT_BYTES)).decode() if group.startswith("pass:") else ""
            base.mkdir(parents=True, exist_ok=True)
            settings.write_atomic(base / MANIFEST, {"v": VERSION, "crypt": {"group": group, "salt": salt}})
        else:
            salt = man["crypt"]["salt"]
        key = dash_keys.slot_key(group, dash["id"], salt)
        tmp = base / f"{TEMP_PREFIX}{card_id}{FILE_SUFFIX}"
        con = ibis.duckdb.connect()
        try:
            con.to_parquet(ibis.memtable(frame.to_arrow()), tmp, **dash_pull.parquet_key(con, key, set())[0])
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            con.disconnect()


def named(dash_id: str, card: dict) -> pl.DataFrame:
    """[SPECIFICATION] plans/dash-blocks.md#marks_named
    목록 카드의 표시를 KEY_COL + 칸 이름 컬럼(칸 종류의 타입)으로 읽은 표. 저장하지 않은 대시보드("")나 파일이 없으면 빈 표."""
    fields = (card["view"].get("row_marks") or {}).get("fields") or []
    raw = pl.DataFrame(schema={KEY_COL: pl.Utf8}) if dash_id == "" else read(dash_id, card["id"], key_of(dash_id))
    return raw.select(
        pl.col(KEY_COL),
        *[(pl.col(f["id"]).cast(MARK_DTYPES[f["kind"]]) if f["id"] in raw.columns else pl.lit(None, dtype=MARK_DTYPES[f["kind"]])).alias(f["name"])
          for f in fields],
    )


def set_mark(dash: dict, card: dict, key: object, values: object) -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#marks_set_mark
    줄 하나(열쇠 key)의 표시 칸 값들을 검사해 바꿔 쓰고 {key, values: {칸 id: 값}}(그 줄의 지금 칸 전부)를 돌려준다."""
    if not (isinstance(key, str) and 1 <= len(key) <= MAX_KEY):
        raise ValueError(f"줄 열쇠(key)는 1~{MAX_KEY}자 글자예요.")
    if not isinstance(values, dict) or not values:
        raise ValueError("바꿀 표시(values)는 {표시 칸 id: 값} 객체예요.")
    fields = {f["id"]: f for f in card["view"]["row_marks"]["fields"]}
    clean: dict[str, object] = {}
    for fid, v in values.items():
        if fid not in fields:
            raise ValueError(f"표시 칸 '{fid}'이 이 목록에 없어요.")
        f = fields[fid]
        name = f["name"]
        if v is None or v == "":
            clean[fid] = None
        elif f["kind"] == "choice":
            if not (isinstance(v, str) and v in f["choices"]):
                raise ValueError(f"'{name}' 칸의 값은 {', '.join(f['choices'])} 가운데 하나예요.")
            clean[fid] = v
        elif f["kind"] == "text":
            if not isinstance(v, str) or len(v.strip()) > dash_store.MAX_ROW_MARK_TEXT:
                raise ValueError(f"'{name}' 칸의 글은 {dash_store.MAX_ROW_MARK_TEXT}자까지예요.")
            clean[fid] = v.strip() or None
        elif f["kind"] == "date":
            if not (isinstance(v, str) and dash_store.DAY.fullmatch(v)
                    and pl.Series([v]).str.to_date("%Y-%m-%d", strict=False)[0] is not None):
                raise ValueError(f"'{name}' 칸의 날짜는 YYYY-MM-DD 모양의 있는 날짜예요.")
            clean[fid] = v
        else:
            if not (type(v) in (int, float) and math.isfinite(v)):
                raise ValueError(f"'{name}' 칸은 숫자예요.")
            clean[fid] = float(v)
    with LOCK:
        frame = read(dash["id"], card["id"], key_of(dash["id"]))
        old = frame.filter(pl.col(KEY_COL) == key)
        row = {fid: (old[fid][0] if old.height and fid in old.columns else None) for fid in fields} | clean
        keep = frame.filter(pl.col(KEY_COL) != key)
        if any(v is not None for v in row.values()):
            frame = pl.concat(
                [keep, pl.DataFrame({KEY_COL: [key], **{fid: [row[fid]] for fid in fields}},
                                    schema={KEY_COL: pl.Utf8, **{fid: MARK_DTYPES[fields[fid]["kind"]] for fid in fields}})],
                how="diagonal_relaxed")
        else:
            frame = keep
        if frame.height > dash_store.MAX_ROW_MARK_ROWS:
            raise ValueError(f"표시는 목록 하나에 {dash_store.MAX_ROW_MARK_ROWS:,}줄까지 붙일 수 있어요.")
        write(dash, card["id"], frame)
    return {"key": key, "values": row}


def save_file(dash: dict, card: dict) -> str | None:
    """[SPECIFICATION] plans/dash-blocks.md#marks_save_file
    저장 대화상자(server.ask_save)에서 고른 경로·형식으로 표시를 내보내고 경로를 돌려준다(취소하면 None)."""
    if server.ask_save is None:
        raise ValueError(NO_WINDOW)
    frame = named(dash["id"], card)
    if frame.height == 0:
        raise ValueError(NO_MARKS)
    path = server.ask_save(f"{card['title']}{next(iter(result.EXPORTS))}", tuple(f"{SAVE_NAMES[s]} (*{s})" for s in result.EXPORTS))
    if path is None:
        return None
    keys = card["view"]["row_marks"]["keys"]
    k = pl.col(KEY_COL).str.json_decode(pl.Struct({str(i): pl.Utf8 for i in range(len(keys))}))
    out = frame.select(*[k.struct.field(str(i)).alias(c) for i, c in enumerate(keys)], pl.exclude(KEY_COL))
    result.export(ibis.memtable(out.to_arrow()), path)
    return path


def load_file(dash: dict, card: dict) -> dict | None:
    """[SPECIFICATION] plans/dash-blocks.md#marks_load_file
    열기 대화상자(server.ask_open)로 고른 파일의 표시를 카드의 겹치는 줄 규칙(clash)대로 합쳐 쓰고 {path, added, clashed, clash}를 돌려준다(취소하면 None)."""
    if server.ask_open is None:
        raise ValueError(NO_WINDOW)
    path = server.ask_open(PICK_KIND)
    if path is None:
        return None
    suffix = Path(path).suffix.lower()
    if suffix not in READS:
        raise ValueError(f"가져올 파일은 {', '.join(READS)} 가운데 하나예요.")
    if suffix == ".parquet":
        got = pl.read_parquet(path)
    elif suffix == ".xlsx":
        got = pl.read_excel(path)
    else:
        got = pl.read_csv(path, separator="\t" if suffix == ".tsv" else ",", infer_schema_length=0)
    m = card["view"]["row_marks"]
    keys = m["keys"]
    miss = [c for c in keys if c not in got.columns]
    if miss:
        raise ValueError(f"가져올 파일에 열쇠 컬럼 '{', '.join(miss)}'이 없어요.")
    fields_in = [f for f in m["fields"] if f["name"] in got.columns]
    if not fields_in:
        raise ValueError("가져올 파일에 이 목록의 표시 칸 이름이 하나도 없어요.")
    if got.filter(pl.any_horizontal([pl.col(c).is_null() for c in keys])).height:
        raise ValueError("가져올 파일에 열쇠가 빈 줄이 있어요.")
    new = got.select(key_expr(keys).alias(KEY_COL), *[pl.col(f["name"]).cast(pl.Utf8).str.strip_chars().alias(f["id"]) for f in fields_in])
    new = new.with_columns(*[pl.when(pl.col(f["id"]) == "").then(None).otherwise(pl.col(f["id"])).alias(f["id"]) for f in fields_in])
    dup = new.height - new[KEY_COL].n_unique()
    if dup:
        raise ValueError(f"가져올 파일에 같은 열쇠가 {dup}개 겹쳐요.")
    for f in fields_in:
        fid, name = f["id"], f["name"]
        col = pl.col(fid)
        if f["kind"] == "choice":
            bad, msg = col.is_not_null() & ~col.is_in(f["choices"]), f"'{name}' 칸에 고를 값({', '.join(f['choices'])})이 아닌 값이"
        elif f["kind"] == "number":
            bad, msg = col.is_not_null() & col.cast(pl.Float64, strict=False).is_null(), f"'{name}' 칸에 숫자가 아닌 값이"
        elif f["kind"] == "date":
            bad = col.is_not_null() & (~col.str.contains(r"^\d{4}-\d{2}-\d{2}$") | col.str.to_date("%Y-%m-%d", strict=False).is_null())
            msg = f"'{name}' 칸에 YYYY-MM-DD 모양의 있는 날짜가 아닌 값이"
        else:
            bad, msg = col.str.len_chars() > dash_store.MAX_ROW_MARK_TEXT, f"'{name}' 칸에 {dash_store.MAX_ROW_MARK_TEXT}자를 넘는 글이"
        wrong = new.filter(bad).height
        if wrong:
            raise ValueError(f"{msg} {wrong}줄 있어요.")
    new = new.with_columns(*[pl.col(f["id"]).cast(pl.Float64) for f in fields_in if f["kind"] == "number"])
    with LOCK:
        old = read(dash["id"], card["id"], key_of(dash["id"]))
        old = old.with_columns(*[pl.lit(None, dtype=MARK_DTYPES[f["kind"]]).alias(f["id"]) for f in fields_in if f["id"] not in old.columns])
        both = old.with_columns(pl.lit(True).alias("__old")).join(
            new.with_columns(pl.lit(True).alias("__new")), on=KEY_COL, how="full", coalesce=True, suffix="__in")
        was, now = pl.col("__old").fill_null(False), pl.col("__new").fill_null(False)
        added = both.filter(now & ~was).height
        clashed = both.filter(now & was).height
        clash = m["clash"]
        merged_cols = []
        for f in fields_in:
            fid = f["id"]
            if clash == "keep":
                merged = pl.when(was).then(pl.col(fid)).otherwise(pl.col(fid + "__in"))
            elif clash == "replace":
                merged = pl.when(now).then(pl.col(fid + "__in")).otherwise(pl.col(fid))
            else:
                merged = pl.coalesce(pl.col(fid), pl.col(fid + "__in"))
            merged_cols.append(merged.alias(fid))
        merged = both.with_columns(*merged_cols).select(old.columns)
        if merged.height > dash_store.MAX_ROW_MARK_ROWS:
            raise ValueError(f"표시는 목록 하나에 {dash_store.MAX_ROW_MARK_ROWS:,}줄까지 붙일 수 있어요.")
        write(dash, card["id"], merged)
    return {"path": path, "added": added, "clashed": clashed, "clash": clash}


def orphans(dash_id: str, card_id: str, present: list[str]) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#marks_orphan_keys
    표시를 붙인 열쇠 가운데 지금 데이터의 열쇠(present)에 없는 것들."""
    return read(dash_id, card_id, key_of(dash_id)).filter(~pl.col(KEY_COL).is_in(present))[KEY_COL].sort().to_list()


def drop_keys(dash: dict, card_id: str, keys: list[str]) -> int:
    """[SPECIFICATION] plans/dash-blocks.md#marks_drop_keys
    고른 열쇠들의 표시를 지우고 지운 줄 수를 돌려준다."""
    with LOCK:
        frame = read(dash["id"], card_id, key_of(dash["id"]))
        keep = frame.filter(~pl.col(KEY_COL).is_in(keys))
        n = frame.height - keep.height
        if n:
            write(dash, card_id, keep)
        return n


def recrypt(dash: dict, old_key: str | None) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#marks_recrypt
    암호화를 바꾼 대시보드의 표시 파일을 옛 열쇠로 읽어 지금 암호화로 다시 쓰고 다시 쓴 카드 id들을 돌려준다."""
    with LOCK:
        base = folder(dash["id"])
        if manifest(dash["id"]) is None:
            return []
        ids = sorted(p.stem for p in base.glob("*" + FILE_SUFFIX) if dash_store.ID.fullmatch(p.stem))
        frames = {cid: read(dash["id"], cid, old_key) for cid in ids}
        group = dash_keys.group_of(dash["id"], dash_store.check_pull(dash.get("pull"))["crypt"])
        salt = base64.b64encode(secrets.token_bytes(dash_keys.SALT_BYTES)).decode() if group.startswith("pass:") else ""
        key = dash_keys.slot_key(group, dash["id"], salt)
        con = ibis.duckdb.connect()
        try:
            w = dash_pull.parquet_key(con, key, set())[0]
            for cid, fr in frames.items():
                con.to_parquet(ibis.memtable(fr.to_arrow()), base / f"{TEMP_PREFIX}{cid}{FILE_SUFFIX}", **w)
        finally:
            con.disconnect()
        settings.write_atomic(base / MANIFEST, {"v": VERSION, "crypt": {"group": group, "salt": salt}})
        for cid in ids:
            (base / f"{TEMP_PREFIX}{cid}{FILE_SUFFIX}").replace(base / f"{cid}{FILE_SUFFIX}")
        return ids


def forget(dash_id: str) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#marks_forget
    지운 대시보드의 표시 폴더에서 앱이 만든 파일만 지우고 비면 폴더도 지운다."""
    with LOCK:
        base = folder(dash_id)
        if not base.is_dir():
            return
        for p in list(base.iterdir()):
            if p.name == MANIFEST or (p.suffix == FILE_SUFFIX and dash_store.ID.fullmatch(p.stem.removeprefix(TEMP_PREFIX))):
                p.unlink()
        if not any(base.iterdir()):
            base.rmdir()
