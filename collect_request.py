"""수집 요청 다루기: 붙여 넣은 예시(curl·HTTP 요청 글자·JSON) 읽기, 비밀 찾기와 {env:이름}으로 바꾸기, env 파일 읽기,
자리 표시 채우기, 응답에서 기록·값 뽑기. 입출력은 env 파일 읽기뿐이고 보내기는 collect_http.py가 한다."""
import base64
import json
import re
import shlex
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlencode, urlsplit, urlunsplit

from collect_http import SECRET_HEADERS
from notify import BODIES

# 자리 표시. 이 모양만 바꾸고 다른 중괄호는 그대로 둔다. 무리 1: env 이름, 2: page·offset·cursor, 3: today·now, 4: 날짜 형식
PLACE = re.compile(r"\{(?:env:([A-Za-z_][A-Za-z0-9_]*)|(page|offset|cursor)|(today|now)(?::([^{}]+))?)\}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 이름을 낱말로 나눴을 때(밑줄·하이픈·대소문자 경계) 이 낱말이 있거나, 이름이 SECRET_ENDS로 끝나면 비밀로 본다. keyword·author 같은 이름은 아니다.
SECRET_WORDS = {"key", "apikey", "token", "secret", "password", "passwd", "pwd", "auth", "sig", "signature", "session", "sessionid", "sid",
                "credential", "credentials", "jwt", "cookie"}
SECRET_ENDS = ("key", "token", "secret", "password", "passwd")
DROP_HEADERS = {"host", "content-length", "accept-encoding", "connection"}  # 예시에서 가져오지 않는 헤더(보낼 때 정해진다)
DATE_FORMATS = {"today": "%Y-%m-%d", "now": "%Y-%m-%d %H:%M:%S"}
BODY_KINDS = ("none", "json", "form", "text")
ENV_MARK = "__ibis_env__"  # JSON 본문을 읽어 볼 때 {env:…} 자리에 넣는 글자(값이 이미 env인지 알아보려고)


# ---------- 예시 읽기 ----------

def parse(text):
    """붙여 넣은 예시를 request 모양으로. (request, 형식, 읽지 못한 부분 목록)"""
    text = (text or "").strip().lstrip("\ufeff")
    if not text:
        raise ValueError("예시를 붙여 넣으세요.")
    for kind, looks, read in (("json", lambda: text.startswith("{"), parse_json), ("curl", lambda: re.match(r"curl(\.exe)?\s", text, re.I), parse_curl),
                              ("http", lambda: re.match(r"[A-Z]+ \S+ HTTP/\d", text), parse_http)):
        if looks():
            request, unread = read(text)
            return request, kind, unread
    raise ValueError("curl 명령, HTTP 요청 글자, JSON({\"method\", \"url\", …}) 중 하나로 붙여 넣으세요.")


def new_request(method, url, headers, data):
    """주소의 쿼리는 query 목록으로 나누고, 버릴 헤더는 빼고, 본문 종류를 정해 request 모양으로 만든다."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("주소는 http:// 또는 https://로 시작해야 합니다.")
    query = [[k, v] for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    kept = [[k.strip(), v.strip()] for k, v in headers if k.strip().lower() not in DROP_HEADERS]
    content_type = next((v.lower() for k, v in kept if k.lower() == "content-type"), "")
    return {"method": (method or ("POST" if data is not None else "GET")).upper(), "url": urlunsplit(parts._replace(query="", fragment="")),
            "query": query, "headers": kept, "body_kind": body_kind(data, content_type), "body": data or ""}


def body_kind(data, content_type):
    """본문 종류: Content-Type을 먼저 보고, 없으면 모양으로 정한다."""
    if not data:
        return "none"
    if "json" in content_type:
        return "json"
    if "x-www-form-urlencoded" in content_type:
        return "form"
    if not content_type:
        try:
            if isinstance(json.loads(data), (dict, list)):
                return "json"
        except ValueError:
            pass
        if re.fullmatch(r"[^=&\s]+=[^&]*(&[^=&\s]+=[^&]*)*", data):
            return "form"
    return "text"


def ansi_c(match):
    """bash의 $'…' 글자를 풀어 작은따옴표 글자로 다시 쓴다(shlex가 읽을 수 있게)."""
    escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "0": "\0"}
    decoded = re.sub(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)",
                     lambda e: chr(int(e[1][1:], 16)) if e[1][0] in "ux" and len(e[1]) > 1 else escapes.get(e[1], "\\" + e[1]), match[1])
    return "'" + decoded.replace("'", "'\"'\"'") + "'"


# curl 옵션: 값을 받는 것과 받지 않는 것. 둘 다 아닌 옵션은 unread에 넣는다.
CURL_VALUE = {"-X": "method", "--request": "method", "-H": "header", "--header": "header", "-d": "data", "--data": "data", "--data-ascii": "data",
              "--data-binary": "data", "--data-raw": "raw", "--data-urlencode": "urlencode", "--json": "json", "--url": "url", "-u": "user", "--user": "user",
              "-b": "cookie", "--cookie": "cookie", "-A": "agent", "--user-agent": "agent"}
CURL_FLAG = {"-G": "get", "--get": "get", "-I": "head", "--head": "head", "--compressed": "", "-L": "", "--location": "", "-s": "", "--silent": "",
             "-S": "", "--show-error": "", "-i": "", "--include": "", "-v": "", "--verbose": "", "-g": "", "--globoff": ""}


def curl_tokens(text):
    """줄 이음(\\ ^ `)을 풀고 낱말로 나눈다. cmd 형식(^ 이음)이면 ^ 탈출도 푼다."""
    cmd = bool(re.search(r"\^\s*\n", text))
    text = re.sub(r"[\\^`][ \t]*\r?\n", " ", text)
    if cmd:
        text = re.sub(r"\^(.)", r"\1", text)
    text = re.sub(r"\$'((?:[^'\\]|\\.)*)'", ansi_c, text)
    try:
        return shlex.split(text, posix=True)
    except ValueError as e:
        raise ValueError(f"curl 명령을 낱말로 나눌 수 없습니다(따옴표 확인): {e}") from None


def parse_curl(text):
    tokens, unread = curl_tokens(text)[1:], []
    method, url, headers, data, get, user = None, None, [], [], False, None
    i = 0
    while i < len(tokens):
        token, i = tokens[i], i + 1
        name, value = token, None
        if token.startswith("--") and "=" in token:
            name, value = token.split("=", 1)
        elif re.fullmatch(r"-[A-Za-z].+", token) and token[:2] in CURL_VALUE:  # -XPOST처럼 붙여 쓴 값
            name, value = token[:2], token[2:]
        flags = ["-" + c for c in token[1:]] if re.fullmatch(r"-[A-Za-z]{2,}", token) else [name]  # -sSL처럼 묶은 깃발
        if all(f in CURL_FLAG for f in flags):
            get = get or any(CURL_FLAG[f] == "get" for f in flags)
            method = "HEAD" if any(CURL_FLAG[f] == "head" for f in flags) else method
            continue
        if name not in CURL_VALUE:
            if not token.startswith("-") and url is None:
                url = token
            else:
                unread.append(token)
            continue
        if value is None:
            if i >= len(tokens):
                unread.append(token)
                break
            value, i = tokens[i], i + 1
        kind = CURL_VALUE[name]
        if kind == "method":
            method = value
        elif kind == "url":
            url = value
        elif kind == "header":
            key, colon, val = value.partition(":")
            if colon:
                headers.append([key, val])
            else:
                unread.append(f"{name} {value}")
        elif kind == "agent":
            headers.append(["User-Agent", value])
        elif kind == "cookie":
            if "=" in value:
                headers.append(["Cookie", value])
            else:
                unread.append(f"{name} {value} (쿠키 파일은 읽지 않습니다)")
        elif kind == "user":
            user = value
        elif kind == "json":
            if value.startswith("@"):
                unread.append(f"{name} {value} (파일 첨부는 읽지 않습니다)")
                continue
            data.append(value)
            headers += [h for h in (["Content-Type", "application/json"], ["Accept", "application/json"]) if h[0].lower() not in {k.lower() for k, _ in headers}]
        elif kind == "urlencode":
            key, eq, val = value.partition("=")
            if not eq and "@" in value:  # name@파일, @파일
                unread.append(f"{name} {value} (파일 첨부는 읽지 않습니다)")
            else:
                data.append(f"{key}={quote_plus(val)}" if eq and key else quote_plus(val if eq else value))
        elif kind == "data" and value.startswith("@"):
            unread.append(f"{name} {value} (파일 첨부는 읽지 않습니다)")
        else:  # data, raw
            data.append(value)
    if not url:
        raise ValueError("curl 명령에서 주소를 찾지 못했습니다.")
    if not re.match(r"https?://", url, re.I):
        url = "http://" + url if "://" not in url else url  # curl은 주소에 방식이 없으면 http로 본다
    if user is not None:  # -u는 주소의 user:pass@ 자리로 옮긴다(보낼 때 Basic 인증 헤더가 된다).
        parts = urlsplit(url)
        login, _, password = user.partition(":")
        url = urlunsplit(parts._replace(netloc=f"{quote(login, safe='')}{':' + quote(password, safe='') if password else ''}@{parts.netloc.rpartition('@')[2]}"))
    body = "&".join(data) if data else None
    if get and body is not None:  # -G: 본문 대신 쿼리로 보낸다.
        url += ("&" if "?" in url else "?") + body
        body = None
        method = method or "GET"
    return new_request(method, url, headers, body), unread


def parse_http(text):
    head, body = (re.split(r"\r?\n[ \t]*\r?\n", text, maxsplit=1) + [""])[:2]
    lines = head.splitlines()
    method, target, _ = lines[0].split(" ", 2)
    headers, unread = [], []
    for line in lines[1:]:
        key, colon, value = line.partition(":")
        if colon and key.strip():
            headers.append([key, value])
        elif line.strip():
            unread.append(line)
    host = next((v.strip() for k, v in headers if k.strip().lower() == "host"), "")
    if re.match(r"https?://", target, re.I):
        url = target
    elif host:
        url = ("http://" if host.endswith(":80") else "https://") + host + target
    else:
        raise ValueError("HTTP 요청 글자에 Host 헤더가 없습니다.")
    return new_request(method, url, headers, body if body.strip() else None), unread


def pairs(value, what):
    """{이름: 값} 또는 [[이름, 값], …]을 [[이름, 값], …]으로. 값은 글자로."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = list(value.items())
    if not isinstance(value, list) or not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in value):
        raise ValueError(f"{what}는 객체나 [[이름, 값], …] 모양이어야 합니다.")
    return [[str(k), v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)] for k, v in value]


def parse_json(text):
    try:
        given = json.loads(text)
    except ValueError as e:
        raise ValueError(f"JSON으로 읽을 수 없습니다: {e}") from None
    if not isinstance(given, dict) or not isinstance(given.get("url"), str):
        raise ValueError('JSON 예시에는 "url"이 있어야 합니다.')
    unread = [f"{k}" for k in given if k not in ("method", "url", "headers", "query", "body")]
    body = given.get("body")
    if body is not None and not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False)
    headers = pairs(given.get("headers"), "headers")
    if isinstance(given.get("body"), (dict, list)) and not any(k.lower() == "content-type" for k, _ in headers):
        headers.append(["Content-Type", "application/json"])
    request = new_request(given.get("method"), given["url"], headers, body or None)
    request["query"] += pairs(given.get("query"), "query")
    return request, unread


# ---------- 비밀 ----------

def has_env(value):
    return any(m[1] for m in PLACE.finditer(str(value)))


def json_scan(text, put):
    """JSON 본문 글자에서 자리 표시를 put(match, 글자 안인지)의 값으로 바꾼다. 글자 안·밖을 따옴표와 역슬래시로 따진다."""
    out, i, inside = [], 0, False
    while i < len(text):
        c = text[i]
        if inside and c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == '"':
            inside = not inside
        elif c == "{":
            m = PLACE.match(text, i)
            if m:
                out.append(put(m, inside))
                i = m.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


def json_body(text):
    """자리 표시가 든 JSON 본문을 읽어 본다(env 자리는 ENV_MARK, 나머지는 0이나 빈 글자). 읽을 수 없으면 None."""
    probe = json_scan(text, lambda m, inside: (ENV_MARK if m[1] else "") if inside else (f'"{ENV_MARK}"' if m[1] else "0"))
    try:
        return json.loads(probe)
    except ValueError:
        return None


def json_secrets(value, path=""):
    """JSON 값에서 이름이 비밀 같은 키의 글자 그대로인 값: [(경로, 키)]."""
    found = []
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
    for key, item in items:
        where = f"{path}.{key}" if path else str(key)
        if isinstance(item, (dict, list)):
            found += json_secrets(item, where)
        elif isinstance(key, str) and secret_name(key) and literal(item):
            found.append((where, key))
    return found


def secret_name(name):
    """이름이 비밀 값의 이름처럼 보이는지(serviceKey, X-API-KEY, access_token 등)."""
    words = {w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", name)}
    return bool(words & SECRET_WORDS) or re.sub(r"[^a-z0-9]", "", name.lower()).endswith(SECRET_ENDS)


def literal(value):
    """비밀 자리에 값이 글자 그대로 들어 있는지(비어 있거나 env 자리 표시가 들어 있으면 아님)."""
    if value is None or isinstance(value, bool):
        return False
    text = str(value)
    return bool(text.strip()) and not has_env(text) and ENV_MARK not in text


def secret_header(name, value):
    """헤더 하나가 비밀(Authorization 같은 헤더이거나 이름이 비밀 같음)인데 값이 글자 그대로인지."""
    return (name.lower() in SECRET_HEADERS or secret_name(name)) and literal(value)


def url_secrets(url):
    """주소 안의 비밀: user:pass@의 비밀번호와 이름이 비밀 같은 쿼리. [(where, name)]"""
    parts, found = urlsplit(url), []
    password = parts.netloc.rpartition("@")[0].partition(":")[2]
    if literal(unquote(password)):
        found.append(("url", "password"))
    found += [("url", k) for k, v in parse_qsl(parts.query, keep_blank_values=True) if secret_name(k) and literal(v)]
    return found


def secrets(request):
    """request에서 비밀처럼 보이는데 값이 글자 그대로인 자리: [{"where", "name", "path"}]"""
    found = [{"where": w, "name": n, "path": ""} for w, n in url_secrets(request.get("url", ""))]
    found += [{"where": "header", "name": k, "path": ""} for k, v in request.get("headers", []) if secret_header(k, v)]
    found += [{"where": "query", "name": k, "path": ""} for k, v in request.get("query", []) if secret_name(k) and literal(v)]
    kind, body = request.get("body_kind"), request.get("body", "")
    if kind == "form":
        found += [{"where": "body", "name": k, "path": ""} for k, v in parse_qsl(body, keep_blank_values=True) if secret_name(k) and literal(v)]
    elif kind == "json":
        found += [{"where": "body", "name": key, "path": path} for path, key in json_secrets(json_body(body))]
    return found


def secret_value(value, env):
    """비밀 값을 {env:이름}으로. Authorization 같은 'Bearer 값' 모양이면 앞의 방식 낱말은 남긴다."""
    scheme = re.match(r"([A-Za-z][\w-]*) +\S", value)
    return f"{scheme[1]} {{env:{env}}}" if scheme else f"{{env:{env}}}"


def secretize(request, replace):
    """replace의 자리 [{"where", "name", "path", "env"}]의 값을 {env:이름}으로 바꾼 새 request."""
    request = json.loads(json.dumps(request))
    for item in replace:
        where, name, env = item.get("where"), item.get("name", ""), item.get("env", "")
        if not isinstance(env, str) or not ENV_NAME.fullmatch(env):
            raise ValueError(f"env 이름은 영문·숫자·밑줄로(숫자로 시작하지 않게) 쓰세요: {env}")
        if where == "header":
            for pair in request["headers"]:
                if pair[0].lower() == name.lower():
                    pair[1] = secret_value(pair[1], env) if pair[0].lower() in ("authorization", "proxy-authorization") else f"{{env:{env}}}"
        elif where == "query":
            for pair in request["query"]:
                if pair[0] == name:
                    pair[1] = f"{{env:{env}}}"
        elif where == "url":
            request["url"] = url_secretize(request["url"], name, env)
        elif where == "body" and request.get("body_kind") == "form":
            request["body"] = "&".join(f"{k}={{env:{env}}}" if unquote(k.replace("+", " ")) == name else part
                                       for part in request["body"].split("&") for k in [part.partition("=")[0]])
        elif where == "body" and request.get("body_kind") == "json":
            request["body"] = json_secretize(request["body"], item.get("path", ""), name, env)
        else:
            raise ValueError(f"바꿀 수 없는 자리입니다: {where} {name}")
    return request


def url_secretize(url, name, env):
    parts = urlsplit(url)
    if name == "password":
        login, at, host = parts.netloc.rpartition("@")
        return urlunsplit(parts._replace(netloc=f"{login.partition(':')[0]}:{{env:{env}}}{at}{host}"))
    query = "&".join(f"{k}={{env:{env}}}" if unquote(k) == name else part for part in parts.query.split("&") for k in [part.partition("=")[0]])
    return urlunsplit(parts._replace(query=query))


def json_secretize(body, path, name, env):
    """JSON 본문에서 path 자리의 값을 "{env:이름}"으로. 모양(들여쓰기·순서)을 지키려고 글자에서 그 키와 값을 찾아 바꾼다."""
    value = json_body(body)
    for key in path.split(".") if path else []:
        value = value[int(key)] if isinstance(value, list) else value.get(key) if isinstance(value, dict) else None
    if value is None:
        raise ValueError(f"본문에서 '{path}'를 찾지 못했습니다.")
    for ascii_only in (False, True):
        pattern = "(" + re.escape(json.dumps(name, ensure_ascii=ascii_only)) + r"\s*:\s*)" + re.escape(json.dumps(value, ensure_ascii=ascii_only))
        changed, n = re.subn(pattern, lambda m: m[1] + f'"{{env:{env}}}"', body)
        if n:
            return changed
    raise ValueError(f"본문에서 '{path}'의 값을 찾지 못했습니다.")


def literal_places(config):
    """설정에서 비밀처럼 보이는 자리에 글자 그대로 남은 값의 위치 이름들(값은 넣지 않는다).
    config["plain"]에 든 이름(대소문자 무시)은 사용자가 비밀이 아니라고 한 것이라 뺀다. 단 Authorization·Cookie 같은 헤더와 주소의 비밀번호는 늘 비밀이다."""
    label = {"header": "헤더", "query": "쿼리", "body": "본문"}
    request, file, web = config.get("request") or {}, config.get("file") or {}, config.get("web") or {}
    plain = {n.lower() for n in config.get("plain", [])}
    always = lambda where, name: name == "password" and where == "url" or name.lower() in SECRET_HEADERS
    found = [(s["where"], s["name"], ("주소의 비밀번호" if s["name"] == "password" else f"주소의 {s['name']}") if s["where"] == "url"
              else f"{label[s['where']]} {s['path'] or s['name']}") for s in secrets(request)]
    found += [("url", n, f"파일 주소의 {'비밀번호' if n == 'password' else n}") for _, n in url_secrets(file.get("url", ""))]
    found += [("url", n, f"웹페이지 주소의 {'비밀번호' if n == 'password' else n}") for _, n in url_secrets(web.get("url", ""))]
    found += [("header", k, f"파일 헤더 {k}") for k, v in file.get("headers", []) if secret_header(k, v)]
    return [text for where, name, text in found if always(where, name) or name.lower() not in plain]


def env_names(config):
    """설정에 쓰인 {env:이름}의 이름들(처음 나온 순서)."""
    names = [m[1] for m in PLACE.finditer(json.dumps(config, ensure_ascii=False)) if m[1]]
    return list(dict.fromkeys(names))


# ---------- env 파일 ----------

def read_env(path):
    """KEY=VALUE 줄의 env 파일을 {이름: 값}으로. export 앞말, 따옴표, # 주석, BOM, 빈 줄을 다룬다. 오류 문구에는 값을 넣지 않는다."""
    try:
        text = open(path, encoding="utf-8-sig").read()
    except OSError as e:
        raise ValueError(f"env 파일을 읽을 수 없습니다: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise ValueError("env 파일은 UTF-8이어야 합니다.") from None
    found = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, eq, value = re.sub(r"^export\s+", "", line).partition("=")
        name, value = name.strip(), value.strip()
        if not eq or not ENV_NAME.fullmatch(name):
            raise ValueError(f"env 파일 {number}번째 줄을 읽을 수 없습니다(이름=값 모양이어야 합니다).")
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            while value[0] == '"' and end > 0 and value[end - 1] == "\\":
                end = value.find('"', end + 1)
            if end < 0:
                raise ValueError(f"env 파일 {number}번째 줄의 따옴표가 닫히지 않았습니다.")
            inner = value[1:end]
            value = re.sub(r"\\([nt\"\\])", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), inner) if value[0] == '"' else inner
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        found[name] = value
    return found


def hidden(env):
    """오류·표시에서 가릴 env 값들. 주소에 넣은 모양(quote)도 함께."""
    values = [v for v in env.values() if v]
    return values + [quote(v, safe="") for v in values]


# ---------- 자리 채우기 ----------

def fill(text, ctx, escape=lambda value: value):
    """text의 자리 표시를 ctx 값으로 채운다. ctx: {"env": {…}, "page", "offset", "cursor", "now": datetime}."""
    return PLACE.sub(lambda m: escape(place_value(m, ctx)), text)


def place_value(m, ctx):
    if m[1]:
        if m[1] not in ctx["env"]:
            raise ValueError(f"env 파일에 없는 이름입니다: {m[1]}")
        return ctx["env"][m[1]]
    if m[2]:
        value = ctx.get(m[2])
        return "" if value is None else str(value)
    return ctx["now"].strftime(m[4] or DATE_FORMATS[m[3]])


def fill_url(url, ctx):
    """주소의 자리는 경로에 맞게 quote한다. 주소 맨 앞의 자리(예: {env:BASE})는 주소 자체라 그대로 넣는다."""
    return PLACE.sub(lambda m: place_value(m, ctx) if m.start() == 0 else quote(place_value(m, ctx), safe=""), url)


def fill_json(body, ctx):
    """JSON 본문: 글자 안의 자리는 JSON 글자로 이스케이프하고, 밖의 자리(예: "page": {page})는 그대로 넣는다. 결과가 JSON이 아니면 거부."""
    escape = BODIES["json"][1]
    filled = json_scan(body, lambda m, inside: escape(place_value(m, ctx)) if inside else place_value(m, ctx))
    try:
        json.loads(filled)
    except ValueError:
        raise ValueError("자리를 채운 본문이 JSON이 아닙니다. 본문과 자리 표시를 확인하세요.") from None
    return filled


def build(request, ctx, url=None):
    """request와 자리 값으로 보낼 요청 {method, url, headers, body}를 만든다. url을 주면(next_url 방식) 주소·쿼리는 그것을 그대로 쓴다.
    주소의 user:pass@는 Basic 인증 헤더로 옮긴다."""
    if url is None:
        url = fill_url(request["url"], ctx)
        query = [(fill(k, ctx), fill(v, ctx)) for k, v in request.get("query", [])]
        if query:
            url += ("&" if urlsplit(url).query else "?") + urlencode(query)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("주소는 http:// 또는 https://로 시작해야 합니다.")
    headers = [[fill(k, ctx), fill(v, ctx)] for k, v in request.get("headers", [])]
    if any(re.search(r"[\r\n]", k + v) for k, v in headers):
        raise ValueError("헤더 이름·값에 줄바꿈을 넣을 수 없습니다.")
    login, at, host = parts.netloc.rpartition("@")
    if at:
        user, _, password = login.partition(":")
        token = base64.b64encode(f"{unquote(user)}:{unquote(password)}".encode()).decode()
        headers = [h for h in headers if h[0].lower() != "authorization"] + [["Authorization", f"Basic {token}"]]
        url = urlunsplit(parts._replace(netloc=host))
    kind, body = request.get("body_kind", "none"), None
    if kind != "none":
        text = fill_json(request.get("body", ""), ctx) if kind == "json" else fill(request.get("body", ""), ctx, BODIES[kind][1])
        body = text.encode("utf-8")
        if not any(k.lower() == "content-type" for k, _ in headers):
            headers.append(["Content-Type", BODIES[kind][0]])
    return {"method": request.get("method", "GET"), "url": url, "headers": headers, "body": body}


# ---------- 응답 ----------

MISSING = object()


def dig(value, path):
    """점으로 나눈 경로의 값(숫자는 배열 번호). 경로가 ""이면 전체. 없으면 MISSING."""
    for key in path.split(".") if path else []:
        if isinstance(value, list) and re.fullmatch(r"-?\d+", key) and -len(value) <= int(key) < len(value):
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return MISSING
    return value


def records(data, path):
    """응답에서 기록 목록. 경로에 객체 하나가 있으면 기록 하나, 빈 값·빈 글자는 빈 목록. 경로가 없으면 None."""
    found = dig(data, path)
    if found is MISSING:
        return None
    if found is None or found == "":
        return []
    return found if isinstance(found, list) else [found]


def scalar(value):
    """표에 넣을 값: 목록·객체는 JSON 글자."""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def flatten(value, prefix="", out=None):
    """묶인 객체를 a.b 컬럼으로 푼다. 목록은 JSON 글자."""
    out = {} if out is None else out
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict) and item:
            flatten(item, name, out)
        else:
            out[name] = scalar(item)
    return out


def rows(found, columns):
    """기록들을 행 {컬럼: 값}으로. columns가 비면 전부 풀고, 있으면 그 경로들만. 객체가 아닌 기록은 {"value": 기록}."""
    out = []
    for record in found:
        if not isinstance(record, dict):
            record = {"value": record}
        if columns:
            out.append({c["name"]: None if (v := dig(record, c["path"])) is MISSING else scalar(v) for c in columns})
        else:
            out.append(flatten(record))
    return out


def extract(data, path):
    """응답에서 한 값(다음 커서·다음 주소·전체 수). 경로가 비었거나 없거나 빈 값이면 None."""
    if not path:
        return None
    found = dig(data, path)
    return None if found is MISSING or found is None or found == "" else found


def kind_of(value):
    """값의 종류: int, float, bool, str (빈 값은 None). int64를 넘는 정수는 str로 본다."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int" if -2**63 <= value < 2**63 else "str"
    if isinstance(value, float):
        return "float"
    return "str"


def column_type(kinds):
    """한 컬럼에 나온 값 종류들로 정한 타입(DuckDB 타입 이름). 섞이면 글자. 정수와 실수만 섞이면 실수."""
    kinds = set(kinds)
    if kinds == {"int"}:
        return "BIGINT"
    if kinds and kinds <= {"int", "float"}:
        return "DOUBLE"
    if kinds == {"bool"}:
        return "BOOLEAN"
    return "VARCHAR"


TYPE_TEXT = {"BIGINT": "정수", "DOUBLE": "실수", "BOOLEAN": "참거짓", "VARCHAR": "문자"}


def mapped(data, response):
    """응답 하나를 (행들, 기록 경로가 있었는지)로."""
    found = records(data, response.get("records", ""))
    return rows(found or [], response.get("columns") or []), found is not None
