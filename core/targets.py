"""목표 파일: 사람이 쓴 목표 값 파일(엑셀·CSV·YAML·JSON·마크다운·텍스트)을 읽어 {name, value, period, column, scope, line} 줄 목록으로 만들고, 이름·기간·대상으로 목표 하나를 찾는다(DB 조회 없음).
- 형식: xlsx·xls(시트 "목표"가 있으면 그것, 없으면 첫 시트, 첫 줄이 머리글), csv(UTF-8, 안 되면 CP949, 첫 줄이 머리글), yaml·yml·json(`목표:` 또는 `targets:` 아래 {이름: {값, 기간, 컬럼, 대상}} 또는 {이름, 값…} 목록),
  md(첫 번째 표의 머리글 | 이름 | 값 | 기간 | … 또는 `- 이름: 값 (기간)` 목록), txt(`이름 = 값 [기간]` 줄, `#`으로 시작하는 줄은 설명).
- 머리글·키 이름: 이름(name·지표·항목), 값(value·목표·target), 기간(period), 컬럼(column), 대상(scope). 이름과 값 칸은 꼭 있어야 한다.
- 값 글자: ₩·쉼표·공백·원은 떼고, 억은 ×1e8, 만은 ×1e4(1억2000만도 됨), %는 ÷100, 0~1 소수는 비율 그대로다.
- 읽다가 문제가 있는 줄은 줄 번호와 함께 errors에 모으고 나머지는 계속 읽는다. 같은 이름·기간·대상이 두 번 나오면 겹친 줄을 모두 뺀다. 파일 자체를 읽을 수 없으면 ValueError.
- 파일 경로별로 (mtime_ns, size)가 같으면 읽은 결과를 다시 쓴다. 경로는 settings(`targets_file.json`)에 저장하는 쪽(라우트)이 정한다."""
import csv
import io
import json
import math
import os
import re
from pathlib import Path

import yaml

from core.compare import ending

EXTENSIONS = (".xlsx", ".xls", ".csv", ".yaml", ".yml", ".json", ".md", ".txt")
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_NODES = 100_000  # YAML 별칭을 펼친 뒤 노드 수 상한(작은 파일이 별칭으로 수백만 개로 불어나는 것을 막는다)
MAX_LINE = 4096  # md·txt 한 줄 글자 수 상한(긴 줄에서 정규식이 오래 되돌아가는 것을 막는다)
SHEET = "목표"
ROOTS = ("목표", "targets")
FIELDS = {"name": ("이름", "name", "지표", "지표명", "항목"), "value": ("값", "value", "목표", "목표값", "target"),
          "period": ("기간", "period"), "column": ("컬럼", "column", "열"), "scope": ("대상", "scope")}
UNIT = {"억": 10**8, "만": 10**4}
VALUE_BODY = re.compile(r"(?:(?:\d+(?:\.\d+)?|\.\d+)[억만])*(?:\d+(?:\.\d+)?|\.\d+)[억만]?")  # 숫자 사이에는 단위가 있어야 한다("1.500.000"·"2026.10.05"는 못 읽음)
NUMBER_RUN = re.compile(r"\d(?:[\d.,]| (?=\d))*")  # 숫자와 그 사이 쉼표·점·한 칸 공백
GROUPED = re.compile(r"\d{1,3}([, ])\d{3}(?:\1\d{3})*(?:\.\d+)?")  # 쉼표나 공백은 세 자리마다만(한 가지로)
VALUE_PART = re.compile(r"(\d+(?:\.\d+)?|\.\d+)([억만]?)")
TABLE_SEPARATOR = re.compile(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*")
BULLET = re.compile(r"\s*[-*+]\s+(.+?)\s*[:：]\s*(.*?)\s*")
PERIOD_WORD = re.compile(r"\d{4}(?:[-./]\d{1,2}(?:[-./]\d{1,2})?|[-./]?[Qq][1-4])?")
TRAILING_PERIOD = re.compile(r"(.*?)\s*[(\[]([^()\[\]]*)[)\]]\s*")
EXCEL_DATE = re.compile(r"(\d{4}-\d{2})(-\d{2}) 00:00:00")  # fastexcel이 날짜 칸을 글자로 읽은 모양
JSON_STRING = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?')  # JSON 글자 한 개와, 키면 뒤의 콜론
CACHE = {}  # 경로 → (mtime_ns, size, rows, errors)


class Pairs(list):
    """YAML·JSON 객체: (키, 값, 줄) 목록. 같은 키가 두 번 나와도 둘 다 남는다."""


class Items(list):
    """YAML·JSON 배열: (값, 줄) 목록."""


# ---------- 값 ----------

def parse_value(text):
    """목표 값 하나를 숫자로. 숫자는 그대로(정수로 떨어지면 int), ₩·쉼표·공백·원을 떼고, 억·만을 곱하고, %는 ÷100. 읽을 수 없으면 ValueError."""
    if isinstance(text, bool):
        raise ValueError("값이 숫자가 아니에요.")
    if isinstance(text, (int, float)):
        number = float(text)
        if not math.isfinite(number):
            raise ValueError("값이 숫자가 아니에요.")
        return tidy(number)
    raw = "" if text is None else str(text).strip()
    if not raw:
        raise ValueError("값이 비어 있어요.")
    loose = any(("," in m[0] or " " in m[0]) and not GROUPED.fullmatch(m[0]) for m in NUMBER_RUN.finditer(raw))
    body = re.sub(r"[₩,\s]", "", raw)
    body = body[:-1] if body.endswith("원") else body
    percent = body.endswith("%")
    body = body[:-1] if percent else body
    sign = -1 if body.startswith("-") else 1
    body = body.lstrip("+-")
    total = sum(float(number) * UNIT.get(unit, 1) for number, unit in VALUE_PART.findall(body)) if VALUE_BODY.fullmatch(body) and not loose else math.nan
    if not math.isfinite(total):  # 읽을 수 없는 글자, 그리고 너무 커서 무한이 된 값
        shown = raw if len(raw) <= 30 else raw[:30] + "…"
        raise ValueError(f"‘{shown}’{ending(shown, '은', '는')} 숫자로 읽을 수 없어요. 예: 5000000, ₩5,000,000, 5,000만, 1.2억, 85%")
    return tidy(round(sign * total / 100, 12) if percent else sign * total)


def tidy(number):
    """정수로 떨어지는 큰 수는 int로(5000000.0 → 5000000)."""
    return int(number) if number == int(number) and abs(number) < 2**53 else number


def make_row(name, value, period, column, scope, line):
    """줄 하나를 {name, value, period, column, scope, line}으로. 이름이 비었거나 값을 읽을 수 없으면 ValueError."""
    name = "" if name is None else str(name).strip()
    if not name:
        raise ValueError("이름이 비어 있어요.")
    clean = lambda v: "" if v is None else str(v).strip()
    return {"name": name, "value": parse_value(value), "period": clean(period), "column": clean(column), "scope": clean(scope), "line": line}


def error(line, message, entry=None):
    """errors에 담을 {line, message}. 메시지 앞에 줄 번호(줄을 모르면 몇 번째 항목인지)를 붙인다."""
    where = f"{line}번째 줄: " if line else f"{entry}번째 항목: " if entry else ""
    return {"line": line, "message": where + message}


def build(parts, line, rows, errors, entry=None):
    """parts = (이름, 값, 기간, 컬럼, 대상)으로 줄을 만들어 rows에 넣고, 문제가 있으면 errors에 넣는다."""
    try:
        rows.append(make_row(*parts, line))
    except ValueError as e:
        errors.append(error(line, str(e), entry))


# ---------- 표 모양(엑셀·CSV·마크다운) ----------

def header_map(cells):
    """머리글 칸 목록에서 {필드: 칸 번호}. 이름과 값 칸이 없으면 None."""
    found = {}
    for index, cell in enumerate(cells):
        text = "" if cell is None else str(cell).strip().casefold()
        for field, names in FIELDS.items():
            if text in names and field not in found:
                found[field] = index
    return found if "name" in found and "value" in found else None


def table_rows(header, body):
    """머리글 칸 목록과 [(줄 번호, 칸 목록)]에서 (rows, errors)."""
    rows, errors = [], []
    columns = header_map(header)
    if columns is None:
        return rows, [error(None, "머리글에서 ‘이름’과 ‘값’ 칸을 찾지 못했어요. 첫 줄에 이름, 값(, 기간, 컬럼, 대상)을 적어 주세요.")]
    for line, cells in body:
        if not any(c is not None and str(c).strip() for c in cells):
            continue
        pick = lambda field: cells[columns[field]] if field in columns and columns[field] < len(cells) else None
        build((pick("name"), pick("value"), pick("period"), pick("column"), pick("scope")), line, rows, errors)
    return rows, errors


def read_text(path):
    """파일 글자. UTF-8(BOM 있어도 됨)이 안 되면 CP949로 읽는다."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("글자 모양을 알 수 없어요. UTF-8 또는 CP949(한글 엑셀의 CSV)로 저장해 주세요.")


def excel_dates(table):
    """fastexcel이 날짜 칸을 읽은 글자('2026-10-01 00:00:00')를 기간 글자로. 열의 날짜가 모두 1일이면 그 열은 달('2026-10'), 아니면 날('2026-10-05'). 다른 칸은 그대로."""
    found = [[EXCEL_DATE.fullmatch(c or "") for c in cells] for cells in table]
    days = {i for row in found for i, f in enumerate(row) if f and f[2] != "-01"}  # 칸에 2026-10을 치면 엑셀은 1일 날짜로 저장하지만, 날마다 적은 열의 1일은 그날이다(서식은 읽을 수 없다)
    return [[(f[1] + f[2] if i in days else f[1]) if f else c for i, (c, f) in enumerate(zip(cells, row))] for cells, row in zip(table, found)]


def read_excel(path):
    """xlsx·xls: 시트 "목표"(없으면 첫 시트)를 글자로 읽는다. 첫 비어 있지 않은 줄이 머리글이고, 줄 번호는 시트의 행 번호(1부터)다."""
    import fastexcel
    try:
        book = fastexcel.read_excel(str(path))
        names = book.sheet_names
        table = book.load_sheet_by_name(SHEET if SHEET in names else names[0], header_row=None, dtypes="string").to_polars().rows()
    except Exception as e:
        raise ValueError(f"엑셀 파일을 읽을 수 없어요: {str(e)[:150]}") from None
    table = excel_dates(table)
    for index, cells in enumerate(table):
        if any(c is not None and str(c).strip() for c in cells):
            return table_rows(list(cells), [(n + 1, list(row)) for n, row in enumerate(table) if n > index])
    return [], [error(None, "시트가 비어 있어요.")]


def read_csv(path):
    """csv: 첫 비어 있지 않은 줄을 머리글로."""
    reader = csv.reader(io.StringIO(read_text(path)))
    header, body = None, []
    for cells in reader:
        if header is None:
            header = cells if any(c.strip() for c in cells) else None
        else:
            body.append((reader.line_num, cells))
    if header is None:
        return [], [error(None, "파일이 비어 있어요.")]
    return table_rows(header, body)


def split_row(line):
    """마크다운 표 줄을 칸으로."""
    text = line.strip()
    text = text[1:] if text.startswith("|") else text
    text = text[:-1] if text.endswith("|") else text
    return [c.strip() for c in text.split("|")]


def text_lines(path):
    """파일 글자를 줄 목록으로. MAX_LINE자보다 긴 줄이 있으면 ValueError."""
    lines = read_text(path).splitlines()
    for n, line in enumerate(lines, 1):
        if len(line) > MAX_LINE:
            raise ValueError(f"{n}번째 줄이 너무 길어요. 한 줄은 {MAX_LINE}자까지 읽어요.")
    return lines


def read_md(path):
    """md: 첫 번째 표를 읽는다. 표가 없으면 `- 이름: 값 (기간)` 목록 줄을 읽는다(콜론이 없는 목록 줄과 그 밖의 글은 건너뛴다)."""
    lines = text_lines(path)
    for i in range(len(lines) - 1):
        if "|" in lines[i] and TABLE_SEPARATOR.fullmatch(lines[i + 1]) and "|" in lines[i + 1]:
            body = []
            for j in range(i + 2, len(lines)):
                if "|" not in lines[j]:
                    break
                body.append((j + 1, split_row(lines[j])))
            return table_rows(split_row(lines[i]), body)
    rows, errors = [], []
    for n, line in enumerate(lines, 1):
        found = BULLET.fullmatch(line)
        if not found:
            continue
        name, rest = found.groups()
        period = TRAILING_PERIOD.fullmatch(rest)
        value, period = (period.group(1), period.group(2)) if period else (rest, "")
        build((name, value, period, None, None), n, rows, errors)
    if not rows and not errors:
        errors.append(error(None, "표나 ‘- 이름: 값 (기간)’ 목록을 찾지 못했어요."))
    return rows, errors


def readable(text):
    """text가 값으로 읽히는지."""
    try:
        parse_value(text)
    except ValueError:
        return False
    return True


def split_value_period(rest):
    """`값 [기간]` 또는 `값 기간`에서 (값 글자, 기간). 끝 낱말이 기간 모양(2026, 2026-10, 2026-Q1…)이거나 전체가 값으로 읽히지 않으면 끝 낱말을 기간으로 본다."""
    found = re.fullmatch(r"(.*?)\s*\[([^\]]*)\]\s*", rest)
    if found:
        return found.group(1), found.group(2)
    head = rest.rsplit(None, 1)
    if len(head) == 2 and readable(head[0]) and (PERIOD_WORD.fullmatch(head[1]) or not readable(rest)):
        return head[0], head[1]
    return rest, ""


def read_txt(path):
    """txt: `이름 = 값 [기간]` 줄. 빈 줄과 #으로 시작하는 줄은 건너뛴다."""
    rows, errors = [], []
    for n, line in enumerate(text_lines(path), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            errors.append(error(n, "‘이름 = 값 [기간]’ 모양이 아니에요."))
            continue
        name, rest = text.split("=", 1)
        value, period = split_value_period(rest.strip())
        build((name, value, period, None, None), n, rows, errors)
    return rows, errors


# ---------- 트리 모양(YAML·JSON) ----------

def yaml_tree(node, loader, count):
    """YAML 노드를 Pairs·Items·값으로(줄 번호 포함). count = [지금까지 만든 노드 수]: 별칭을 펼친 뒤 MAX_NODES를 넘으면 ValueError."""
    count[0] += 1
    if count[0] > MAX_NODES:
        raise ValueError(f"YAML이 너무 커요(별칭 *를 펼친 항목·값이 {MAX_NODES:,}개가 넘어요). 목표를 나누거나 별칭을 줄여 주세요.")
    if isinstance(node, yaml.MappingNode):
        if any(not isinstance(k, yaml.ScalarNode) for k, _ in node.value):  # 키에 둔 별칭은 세지 않고 펼쳐지므로 막는다
            raise ValueError("YAML의 키(이름)는 글자여야 해요.")
        return Pairs((loader.construct_object(k, deep=True), yaml_tree(v, loader, count), k.start_mark.line + 1) for k, v in node.value)
    if isinstance(node, yaml.SequenceNode):
        return Items((yaml_tree(n, loader, count), n.start_mark.line + 1) for n in node.value)
    return loader.construct_object(node, deep=True)


def key_lines(text):
    """JSON 글자에서 {키 글자(따옴표 포함): [그 키가 나오는 줄 번호…]}. 한 번 훑어 만들고, 같은 줄은 한 번만 센다."""
    found = {}
    for n, line in enumerate(text.splitlines(), 1):
        for m in JSON_STRING.finditer(line):
            hits = found.setdefault(m.group(1), []) if m.group(2) else None
            if hits is not None and (not hits or hits[-1] != n):
                hits.append(n)
    return found


def json_tree(value, index, used):
    """json.loads(object_pairs_hook=Pairs)의 결과를 줄 번호를 어림해 붙인 트리로. index = key_lines(글자). 같은 키는 나오는 줄을 차례로 쓴다."""
    if isinstance(value, Pairs):
        out = Pairs()
        for key, inner in value:
            hits = index.get(json.dumps(key, ensure_ascii=False), [])
            seen = used.get(key, 0)
            used[key] = seen + 1
            out.append((key, json_tree(inner, index, used), hits[seen] if seen < len(hits) else (hits[-1] if hits else None)))
        return out
    if isinstance(value, list):
        return Items((json_tree(item, index, used), None) for item in value)
    return value


def pick_field(pairs, field):
    """Pairs에서 필드(name·value·period·column·scope)의 값. 없으면 None."""
    for key, value, _ in pairs:
        if str(key).strip().casefold() in FIELDS[field]:
            return value
    return None


def read_tree(tree):
    """`목표:`/`targets:` 아래를 줄 목록으로. 이름을 키로 둔 객체와 {이름, 값…} 목록을 모두 읽는다."""
    if not isinstance(tree, Pairs):
        raise ValueError('파일은 "목표:" 또는 "targets:" 아래에 목표를 적은 객체여야 해요.')
    bodies = [value for key, value, _ in tree if str(key).strip().casefold() in ROOTS]
    if not bodies:
        raise ValueError('"목표:" 또는 "targets:" 아래에 목표를 적어 주세요. 예: 목표: {매출: {값: 5000만, 기간: 2026-10}}')
    rows, errors = [], []
    for body in bodies:
        if isinstance(body, Pairs):
            for name, fields, line in body:
                if isinstance(fields, Pairs):
                    parts = (name, pick_field(fields, "value"), pick_field(fields, "period"), pick_field(fields, "column"), pick_field(fields, "scope"))
                elif isinstance(fields, (Pairs, Items)):
                    errors.append(error(line, f"‘{name}’은 {{값, 기간…}} 모양이어야 해요."))
                    continue
                else:
                    parts = (name, fields, None, None, None)
                build(parts, line, rows, errors)
        elif isinstance(body, Items):
            for n, (item, line) in enumerate(body, 1):
                if isinstance(item, Pairs):
                    build((pick_field(item, "name"), pick_field(item, "value"), pick_field(item, "period"), pick_field(item, "column"), pick_field(item, "scope")), line, rows, errors, n)
                else:
                    errors.append(error(line, "{이름, 값…} 모양이어야 해요.", n))
        else:
            raise ValueError("목표가 비어 있어요.")
    return rows, errors


def read_yaml(path):
    try:
        loader = yaml.SafeLoader("")
        node = yaml.compose(read_text(path), Loader=yaml.SafeLoader)
        tree = yaml_tree(node, loader, [0]) if node is not None else None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise ValueError(f"YAML 형식이 올바르지 않아요{f' ({mark.line + 1}번째 줄)' if mark else ''}.") from None
    return read_tree(tree)


def read_json(path):
    text = read_text(path)
    try:
        tree = json_tree(json.loads(text, object_pairs_hook=Pairs), key_lines(text), {})
    except ValueError as e:
        raise ValueError(f"JSON 형식이 올바르지 않아요: {str(e)[:150]}") from None
    return read_tree(tree)


PARSERS = {".xlsx": read_excel, ".xls": read_excel, ".csv": read_csv, ".yaml": read_yaml, ".yml": read_yaml, ".json": read_json, ".md": read_md, ".txt": read_txt}


# ---------- 읽기·찾기 ----------

def drop_overlaps(rows, errors):
    """같은 이름·기간·대상이 두 번 이상이면 그 줄을 모두 빼고 "겹침" 오류를 줄마다 더한다."""
    groups = {}
    for row in rows:
        groups.setdefault((row["name"].casefold(), row["period"], row["scope"].casefold()), []).append(row)
    drop = {id(r) for same in groups.values() if len(same) > 1 for r in same}
    for same in groups.values():
        if len(same) > 1:
            for row in same:
                others = [str(r["line"]) for r in same[:6] if r is not row and r["line"]][:5]  # 앞 몇 줄만(다 적으면 겹친 줄 수의 제곱만큼 길어진다)
                others = "·".join(others) + ("…" if len(same) > 6 else "")
                shown = row["name"] + (f" {row['period']}" if row["period"] else "") + (f" {row['scope']}" if row["scope"] else "")
                errors.append(error(row["line"], f"‘{shown}’ 목표가 겹침" + (f" ({others}번째 줄과 같은 이름·기간·대상)" if others else "") + ". 겹친 줄은 모두 뺐어요."))
    errors.sort(key=lambda e: e["line"] or 0)
    return [r for r in rows if id(r) not in drop], errors


def read(path, offset=0, limit=None):
    """목표 파일을 읽어 {rows, errors, error_total}. rows는 {name, value, period, column, scope, line}, errors는 {line, message}(줄 순서)의 offset부터 limit개(None이면 끝까지), error_total은 전체 문제 줄 수.
    파일이 없거나 지원하지 않는 형식·너무 큰 파일·읽을 수 없는 파일이면 ValueError. 같은 경로의 수정 시각·크기가 그대로면 이전 결과를 다시 쓴다."""
    path = Path(path)
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError("목표 파일은 " + ", ".join(EXTENSIONS) + " 파일이어야 합니다.")
    try:
        stat = os.stat(path)
    except OSError:
        raise ValueError("목표 파일을 찾을 수 없어요. 경로를 확인해 주세요.") from None
    if stat.st_size > MAX_FILE_BYTES:
        raise ValueError(f"목표 파일은 {MAX_FILE_BYTES // 1024 // 1024}MB까지 읽어요.")
    key = str(path.resolve())
    hit = CACHE.get(key)
    if not hit or hit[:2] != (stat.st_mtime_ns, stat.st_size):
        try:
            rows, errors = drop_overlaps(*PARSERS[path.suffix.lower()](path))
        except OSError as e:
            raise ValueError(f"목표 파일을 읽을 수 없어요: {e.strerror or e}") from None
        except RecursionError:
            raise ValueError("목표 파일의 괄호가 너무 깊게 겹쳐 있어요. 목표는 몇 단계 안으로 적어 주세요.") from None
        rows.sort(key=lambda r: r["line"] or 0)
        hit = CACHE[key] = (stat.st_mtime_ns, stat.st_size, rows, errors)
    errors = hit[3][offset:None if limit is None else offset + limit]
    return {"rows": [dict(r) for r in hit[2]], "errors": [dict(e) for e in errors], "error_total": len(hit[3])}  # 줄은 글자·숫자뿐이라 dict 복사로 캐시가 지켜진다


def clear():
    """읽어 둔 결과를 모두 버린다."""
    CACHE.clear()


def miss(message, ambiguous=False):
    return {"row": None, "error": message, "ambiguous": ambiguous}


def lookup(rows, name, period=None, scope=None):
    """rows(read의 rows)에서 이름(과 기간·대상)에 맞는 목표 하나를 {row, error, ambiguous}로. 기간·대상을 주면 그것과 같은 줄, 없으면 기간·대상이 빈 줄(모두에 해당)을 쓴다.
    기간·대상을 주지 않았는데 여러 개가 있으면 어느 것인지 알 수 없다고 알린다(ambiguous=True)."""
    wanted = str(name).strip().casefold()
    found = [r for r in rows if r["name"].casefold() == wanted]
    if not found:
        return miss(f"‘{name}’ 목표를 찾지 못했어요.")
    if period:
        found = [r for r in found if r["period"] == str(period).strip()] or [r for r in found if not r["period"]]
        if not found:
            return miss(f"‘{name}’의 {period} 목표를 찾지 못했어요.")
    if scope:
        found = [r for r in found if r["scope"].casefold() == str(scope).strip().casefold()] or [r for r in found if not r["scope"]]
        if not found:
            return miss(f"‘{name}’의 {scope} 목표를 찾지 못했어요.")
    for field, word in (("scope", "대상"), ("period", "기간")):
        kinds = sorted({r[field] for r in found})
        if len(kinds) > 1:
            shown = ", ".join(k or "전체" for k in kinds)
            return miss(f"‘{name}’ 목표가 여러 개라 어느 {word}인지 알 수 없어요 ({shown}). {word}{ending(word, '을', '를')} 정해 주세요.", True)
    return {"row": found[0], "error": None, "ambiguous": False}
