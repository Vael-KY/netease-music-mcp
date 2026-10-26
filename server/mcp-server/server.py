#!/usr/bin/env python3
"""NetEase Cloud Music MCP Server - pure Python, zero dependencies.

Lets an AI assistant really operate a NetEase Cloud Music (网易云音乐) account:
search songs, read / create / curate playlists, read lyrics, inspect listening
history, manage red hearts, sample daily recommendations and personal FM.

Design rules
------------
* Single file, Python standard library only (3.8+). No pip install.
* Transports: Streamable HTTP (``POST /mcp``, default), legacy SSE
  (``GET /sse`` + ``POST /message``) and stdio (``--transport stdio``).
* Read calls go through weapi (encrypted) first and fall back to the legacy
  plain route only for gateway-type failures. Write calls never fall back after
  a transport error, because their outcome is unknown and a retry could
  duplicate the write.
* Credentials are never logged; error logs strip query strings and cookies.

GitHub: https://github.com/Vael-KY/netease-music-mcp
License: MIT
"""
import base64
import hmac
import http.server
import json
import logging
import os
import queue
import re
import secrets
import string
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import HTTPServer

VERSION = "3.2.1"
SERVER_NAME = "netease-music-mcp"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07")

# Output caps (keep a single tool result readable for the model).
MAX_SEARCH = 10
MAX_BATCH_IDS = 50
MAX_HISTORY = 300
MAX_LYRIC_LINES = 120


# --------------------------------------------------------------------------- #
# .env loading
# --------------------------------------------------------------------------- #
def _load_dotenv():
    """Load KEY=VALUE lines from the nearest .env; already-exported vars win.

    Done in-process instead of ``export $(cat .env | xargs)``: a Cookie value
    contains spaces and ';', which that shell idiom splits apart, silently
    dropping __csrf. Search order: cwd, repo root (two levels above this file),
    then this file's directory.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for base in [os.getcwd(), os.path.dirname(os.path.dirname(here)), here]:
        path = os.path.join(base, ".env")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as fh:
                lines = fh.readlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
        return


_load_dotenv()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _env(name, default=""):
    value = os.environ.get(name)
    return default if value is None else value.strip()


def _env_number(name, default, cast):
    raw = _env(name)
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


NETEASE_COOKIE = _env("NETEASE_COOKIE")
NETEASE_CSRF = _env("NETEASE_CSRF")
NETEASE_UID = _env("NETEASE_UID")  # optional; looked up automatically if empty
NETEASE_TZ_OFFSET = _env_number("NETEASE_TZ_OFFSET", 8.0, float)  # Beijing by default
NETEASE_API_BASE = _env("NETEASE_API_BASE", "https://music.163.com").rstrip("/")
NETEASE_HTTP_TIMEOUT = _env_number("NETEASE_HTTP_TIMEOUT", 15.0, float)
MCP_PORT = _env_number("MCP_PORT", 3456, int)
MCP_HOST = _env("MCP_HOST", "0.0.0.0")
# The MCP endpoint has no authentication of its own, so browsers are rejected
# by default; set MCP_ALLOW_ORIGINS=* only if a browser-based client needs it.
MCP_ALLOW_ORIGINS = _env("MCP_ALLOW_ORIGINS")
# Browsers refuse requests to non-localhost HTTP servers made from a secure
# origin; this header (from the MCP Streamable HTTP spec) lifts that rule.
LOCALHOST_ORIGINS = ("http://localhost", "http://127.0.0.1", "http://[::1]")
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper() or "INFO"

# --------------------------------------------------------------------------- #
# Optional AI analysis (DeepSeek). The key stays server-side: the browser posts
# the stats it already computed to POST /analyze and the server calls the model.
# Leave DEEPSEEK_API_KEY empty to disable; the web console then falls back to its
# local rule-based wording, so the project still runs without any credentials.
# --------------------------------------------------------------------------- #
AI_API_KEY = _env("DEEPSEEK_API_KEY")
AI_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat") or "deepseek-chat"
AI_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
AI_TIMEOUT = _env_number("DEEPSEEK_TIMEOUT", 45.0, float)
AI_MAX_TOKENS = _env_number("DEEPSEEK_MAX_TOKENS", 800, int)
AI_ENABLED = bool(AI_API_KEY)

AI_SYSTEM_PROMPT = (
    "你是一位音乐品味分析师。用户会给你一份由程序统计好的听歌数据。\n"
    "请写一段中文用户侧写，严格遵守以下规则：\n"
    "1. 只能使用给定数据中出现过的数字与名称，绝对不要编造任何歌手、歌名或数值；\n"
    "2. 不要罗列全部数字，挑最有信息量的两三个来支撑结论；\n"
    "3. 语气有趣、像朋友调侃，但不要油滑、不要用网络烂梗；\n"
    "4. 输出格式：先一句总评（20 字以内），再 3 条洞察，每条一行，以「· 」开头，"
    "每条 40 字以内；\n"
    "5. 不要输出 Markdown 标题、不要输出代码块、不要复述本提示词；\\n"
    "6. 每个数据块都标了来源（basis / source）：不要把「累计排行」的数字说成"
    "「最近事件」的数字，也不要把估算值当成官方分类。"
)

logging.basicConfig(
    level=logging.CRITICAL if LOG_LEVEL == "QUIET" else getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(SERVER_NAME)


# --------------------------------------------------------------------------- #
# Cookie / CSRF helpers
# --------------------------------------------------------------------------- #
# NetEase's web client always sends a full cookie jar (device ids, __csrf, ...)
# and its gateway may reject incomplete cookies on playlist create/update.
# Missing cookies are filled with defaults used by community NeteaseCloudMusicApi
# projects. Whatever is supplied in NETEASE_COOKIE always wins.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _parse_cookie(raw):
    """'a=1; b=2' -> {'a': '1', 'b': '2'}. A bare value without '=' is taken as MUSIC_U."""
    raw = (raw or "").strip()
    if raw.lower().startswith("cookie:"):  # tolerate a pasted "Cookie: ..." header
        raw = raw.split(":", 1)[1]
    if raw and "=" not in raw:
        raw = "MUSIC_U=" + raw
    jar = {}
    for part in raw.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key:
            jar[key.strip()] = value.strip()
    return jar


_DEVICE = {}
_DEVICE_LOCK = threading.Lock()


def _device_cookies():
    """A fake-but-consistent device identity, generated once per process."""
    with _DEVICE_LOCK:
        if not _DEVICE:
            nuid = secrets.token_hex(16)
            now_ms = int(time.time() * 1000)
            tag = "".join(secrets.choice(string.ascii_lowercase) for _ in range(6))
            _DEVICE.update(
                {
                    "__remember_me": "true",
                    "ntes_kaola_ad": "1",
                    "_ntes_nuid": nuid,
                    "_ntes_nnid": "%s,%d" % (nuid, now_ms),
                    "WNMCID": "%s.%d.01.0" % (tag, now_ms),
                    "WEVNSM": "1.0.0",
                    "NMTID": "00O" + secrets.token_hex(19),
                    "os": "pc",
                    "appver": "3.1.17.204416",
                    "osver": "Microsoft-Windows-10-Professional-build-19045-64bit",
                    "channel": "netease",
                }
            )
        return _DEVICE


def build_cookie_header():
    jar = _parse_cookie(NETEASE_COOKIE)
    if NETEASE_CSRF and "__csrf" not in jar:
        jar["__csrf"] = NETEASE_CSRF  # the csrf_token we send must also be a cookie
    for key, value in _device_cookies().items():
        jar.setdefault(key, value)
    return "; ".join("%s=%s" % (k, v) for k, v in jar.items())


def get_csrf():
    return _parse_cookie(NETEASE_COOKIE).get("__csrf") or NETEASE_CSRF or ""


def is_configured():
    return "MUSIC_U" in _parse_cookie(NETEASE_COOKIE)


def _redact(text):
    """Strip query strings and cookie values from text that is about to be logged."""
    text = str(text)
    if "?" in text:  # drop the whole query string of an exception message / URL
        text = text.split("?", 1)[0] + "?<redacted>"
    text = re.sub(r"(MUSIC_U=)[^;,'\"\s]+", r"\1<redacted>", text)
    text = re.sub(r"(__csrf=)[^;,'\"\s]+", r"\1<redacted>", text)
    return text[:300]


# --------------------------------------------------------------------------- #
# Error helpers
# --------------------------------------------------------------------------- #
def _hint(*results):
    """Short, actionable explanation for the usual failure codes."""
    codes = set()
    for r in results:
        if isinstance(r, dict):
            codes.add(r.get("code"))
            for nested in ("weapi", "legacy"):
                if isinstance(r.get(nested), dict):
                    codes.add(r[nested].get("code"))
    if 403 in codes:
        return (
            "403 illegal request: put the FULL Cookie header copied from music.163.com "
            "(DevTools > Network > any request > Cookie) into NETEASE_COOKIE; "
            "it must contain MUSIC_U and __csrf"
        )
    if 301 in codes:
        return "Not logged in or session expired: refresh MUSIC_U / __csrf in NETEASE_COOKIE"
    if -460 in codes or 460 in codes:
        return (
            "NetEase risk control flagged this server's IP or cookie; retry later "
            "or use the full browser cookie"
        )
    if -462 in codes:
        return "NetEase asked for a captcha (风控): retry later from your usual network"
    return None


def _with_hint(result):
    if not isinstance(result, dict):
        return {"code": -1, "error": "Unexpected upstream response"}
    if "hint" not in result:
        hint = _hint(result)
        if hint:
            result = dict(result, hint=hint)
    return result


def _http_error_result(path, exc):
    """Turn an HTTPError into a result dict that keeps NetEase's own error body."""
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - body is best effort
        raw = ""
    logger.error("NetEase HTTP %s [%s]", exc.code, path.split("?", 1)[0])
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            body.setdefault("code", exc.code)
            body["http_status"] = exc.code
            return body
    except ValueError:
        pass
    # The body may be an HTML challenge page; never echo it whole.
    return {"code": exc.code, "http_status": exc.code, "error": "HTTP %s: %s" % (exc.code, exc.reason)}


# --------------------------------------------------------------------------- #
# HTTP transport (urllib)
# --------------------------------------------------------------------------- #
def _post(url, body, extra_headers=None):
    headers = {
        "Cookie": build_cookie_header(),
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": _UA,
        "Referer": NETEASE_API_BASE + "/",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=NETEASE_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _get(url):
    headers = {"Cookie": build_cookie_header(), "User-Agent": _UA, "Referer": NETEASE_API_BASE + "/"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=NETEASE_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def netease_request(path, data=None, method="POST"):
    """Plain /api/ request (body not encrypted). Fine for public/read endpoints."""
    url = NETEASE_API_BASE + path
    try:
        if method == "GET" or data is None:
            if data:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(data)
            return _get(url)
        return _post(url, urllib.parse.urlencode(data, doseq=True).encode())
    except urllib.error.HTTPError as exc:
        return _http_error_result(path, exc)
    except Exception as exc:  # noqa: BLE001 - network errors are reported, not raised
        logger.error("NetEase API error [%s]: %s", path.split("?", 1)[0], type(exc).__name__)
        return {"code": -1, "error": "%s: %s" % (type(exc).__name__, _redact(exc))}


# --------------------------------------------------------------------------- #
# weapi encryption (pure Python AES-128-CBC, twice, + RSA on the key)
# --------------------------------------------------------------------------- #
# Some endpoints (e.g. playlist creation) reject plain /api/ requests. NetEase's
# web client uses "weapi": AES-128-CBC twice + RSA on the key. AES is implemented
# below in pure Python so the project stays dependency-free; the S-box is
# generated with the same algorithm as the classic JS aes.js, and the block
# cipher is checked against the FIPS-197 test vector in tests/.
_WEAPI_PRESET_KEY = b"0CoJUm6Qyw8W8jud"
_WEAPI_IV = b"0102030405060708"
_WEAPI_E = 0x010001
_WEAPI_MODULUS = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec"
    "4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d8"
    "13cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
    16,
)


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _rotl8(x, s):
    return ((x << s) | (x >> (8 - s))) & 0xFF


def build_sbox():
    """Byte-exact port of the S-box generator in the classic JS ``aes.js``."""
    sbox = [0] * 256
    inv = [0] * 256
    p = q = 1
    while True:
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = (q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4)) & 0xFF
        sbox[p] = (x ^ 0x63) & 0xFF
        inv[x ^ 0x63] = p & 0xFF
        if p == 1:
            break
    sbox[0] = inv[0] = 0x63
    return sbox, inv


SBOX, INV_SBOX = build_sbox()


def _expand_key(key):
    """AES-128 key schedule -> 11 round keys of 16 bytes."""
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        tmp = list(words[i - 1])
        if i % 4 == 0:
            tmp = tmp[1:] + tmp[:1]
            tmp = [SBOX[b] for b in tmp]
            tmp[0] ^= rcon
            rcon = _xtime(rcon)
        words.append([words[i - 4][j] ^ tmp[j] for j in range(4)])
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(11)]


def _encrypt_block(block, rks):
    """One AES-128 block; ``block`` and result are 16-entry byte lists."""
    state = [b ^ k for b, k in zip(block, rks[0])]
    for rnd in range(1, 11):
        state = [SBOX[b] for b in state]
        # ShiftRows: state is column-major, row r rotates left by r.
        state = [state[(i + 4 * (i % 4)) % 16] for i in range(16)]
        if rnd != 10:
            mixed = []
            for col in range(4):
                a = state[col * 4:col * 4 + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                mixed += [a[i] ^ t ^ _xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            state = mixed
        state = [b ^ k for b, k in zip(state, rks[rnd])]
    return state


def aes_cbc_encrypt(data, key, iv):
    """PKCS#7-padded AES-128-CBC encryption (identical output to aes.js)."""
    pad = 16 - len(data) % 16
    data = bytes(data) + bytes([pad]) * pad
    rks = _expand_key(key)
    prev = list(iv)
    out = bytearray()
    for i in range(0, len(data), 16):
        prev = _encrypt_block([b ^ p for b, p in zip(data[i:i + 16], prev)], rks)
        out += bytes(prev)
    return bytes(out)


def weapi_encrypt(payload, secret=None):
    """Encrypt a dict into weapi form fields {params, encSecKey}.

    ``secret`` may be supplied by tests; production always uses a fresh random key.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if secret is None:
        secret = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    secret = secret.encode()
    first = base64.b64encode(aes_cbc_encrypt(text, _WEAPI_PRESET_KEY, _WEAPI_IV))
    params = base64.b64encode(aes_cbc_encrypt(first, secret, _WEAPI_IV)).decode()
    rs = pow(int(secret[::-1].hex(), 16), _WEAPI_E, _WEAPI_MODULUS)
    return {"params": params, "encSecKey": format(rs, "x").zfill(256)}


def netease_weapi(path, payload=None):
    """Make an authenticated weapi (encrypted) request. path like '/playlist/create'."""
    csrf = get_csrf()
    payload = dict(payload or {}, csrf_token=csrf)
    url = "%s/weapi%s?csrf_token=%s" % (NETEASE_API_BASE, path, urllib.parse.quote(csrf))
    headers = {"Origin": NETEASE_API_BASE}
    try:
        body = urllib.parse.urlencode(weapi_encrypt(payload)).encode()
        return _post(url, body, headers)
    except urllib.error.HTTPError as exc:
        return _http_error_result("/weapi" + path, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("NetEase weapi error [%s]: %s", path.split("?", 1)[0], type(exc).__name__)
        return {"code": -1, "error": "%s: %s" % (type(exc).__name__, _redact(exc))}


# Codes meaning "the gateway refused this route/format" (worth trying the other
# route), as opposed to business errors such as 502 "song already in playlist".
_ROUTE_FAILURES = {-1, 400, 403, 404, 405}


def write_call(path, payload):
    """Write operation: encrypted weapi first, then the plain /api/ route as fallback.

    On total failure returns {"code", "weapi", "legacy", "hint"} for the caller.
    """
    primary = netease_weapi(path, payload)
    if primary.get("code") == 200:
        return primary
    # A transport failure does not prove the write failed; retrying through another
    # route could create the same playlist twice.
    if primary.get("code") == -1:
        return dict(primary, hint="Write outcome unknown; check the playlist before retrying")
    if primary.get("code") not in _ROUTE_FAILURES:
        return _with_hint(primary)
    logger.warning("weapi %s refused (code %s), trying the legacy route", path, primary.get("code"))
    csrf = get_csrf()
    legacy = netease_request("/api%s?csrf_token=%s" % (path, urllib.parse.quote(csrf)), payload)
    if legacy.get("code") == 200:
        return legacy
    return {"code": primary.get("code"), "weapi": primary, "legacy": legacy, "hint": _hint(primary, legacy)}


def read_call(path, payload=None, legacy_get=None):
    """Read operation: weapi first; optionally fall back to a plain GET of the old route."""
    primary = netease_weapi(path, payload)
    if primary.get("code") == 200:
        return primary
    if legacy_get and primary.get("code") in _ROUTE_FAILURES:
        logger.warning("weapi %s failed (code %s), trying the legacy route", path, primary.get("code"))
        legacy = netease_request(legacy_get, method="GET")
        if legacy.get("code") == 200:
            return legacy
        return {"code": primary.get("code"), "weapi": primary, "legacy": legacy, "hint": _hint(primary, legacy)}
    return _with_hint(primary)


# --------------------------------------------------------------------------- #
# Small formatters
# --------------------------------------------------------------------------- #
def _fmt_time(value):
    """Epoch seconds or milliseconds -> 'YYYY-MM-DD HH:MM UTC+8' ('?' if missing)."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return "?"
    if ts <= 0:
        return "?"
    if ts > 1e11:  # milliseconds
        ts /= 1000
    label = "UTC" if NETEASE_TZ_OFFSET == 0 else "UTC%+g" % NETEASE_TZ_OFFSET
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + NETEASE_TZ_OFFSET * 3600)) + " " + label


def _artists(song):
    parts = song.get("ar") or song.get("artists") or []
    # A track can carry an artist entry whose "name" is present but null, so
    # .get("name", "?") returns None and str.join() then raises TypeError.
    return ", ".join((a.get("name") or "?") for a in parts) or "?"


def _album(song):
    al = song.get("al") or song.get("album") or {}
    return al.get("name") or "?"


def _duration(ms):
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        return "?"
    if ms <= 0:
        return "?"
    return "%d:%02d" % (ms // 60000, (ms % 60000) // 1000)


def _album_id(song):
    al = song.get("al") or song.get("album") or {}
    return al.get("id") or (song.get("album") or {}).get("id")


def _line(index, song, extra=""):
    return "%d. %s - %s (%sID:%s)" % (index, song.get("name", "?"), _artists(song), extra, song.get("id"))


def _split(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).replace("，", ",").replace(" ", ",").split(",")
    out, seen = [], set()
    for item in items:
        item = str(item).strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _as_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _int_arg(params, key, default=None, lo=None, hi=None):
    raw = params.get(key, default)
    if raw is None or raw == "":
        raise ValueError("%s is required" % key)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer, got %r" % (key, raw))
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _need(key, value):
    if value is None or str(value).strip() == "":
        raise ValueError("%s is required" % key)
    return str(value).strip()


# --------------------------------------------------------------------------- #
# User / account helpers
# --------------------------------------------------------------------------- #
_UID_CACHE = {}
_UID_LOCK = threading.Lock()


def get_uid():
    """Logged-in user id: NETEASE_UID if set, else looked up once and cached."""
    if NETEASE_UID:
        return NETEASE_UID
    with _UID_LOCK:
        if _UID_CACHE.get("uid"):
            return _UID_CACHE["uid"]
        attempts = (
            lambda: netease_weapi("/w/nuser/account/get", {}),
            lambda: netease_request("/api/nuser/account/get", method="GET"),
            lambda: netease_request("/api/w/nuser/account/get", method="GET"),
        )
        for attempt in attempts:
            result = attempt()
            if isinstance(result, dict) and result.get("code") == 200:
                uid = (result.get("account") or {}).get("id") or (result.get("profile") or {}).get("userId")
                if uid:
                    _UID_CACHE["uid"] = str(uid)
                    return _UID_CACHE["uid"]
    logger.warning("Could not determine the logged-in user id (MUSIC_U missing or expired?)")
    return None


def _require_cookie():
    if not is_configured():
        return {
            "error": "Not logged in: set NETEASE_COOKIE (must contain MUSIC_U) in .env",
            "hint": "music.163.com > F12 > Network > any request > Request Headers > Cookie",
        }
    return None


# --------------------------------------------------------------------------- #
# Tool implementations - search & playback
# --------------------------------------------------------------------------- #
def search_song(params):
    """Search songs by keyword."""
    query = _need("query", params.get("query"))
    limit = _int_arg(params, "limit", 5, 1, MAX_SEARCH)
    payload = {"s": query, "type": 1, "limit": limit, "offset": 0}
    # /cloudsearch has been refusing encrypted requests (50000005) lately, so the
    # older /search/get/web is tried first and cloudsearch is kept as a fallback.
    result = netease_weapi("/search/get/web", payload)
    if result.get("code") != 200 or not ((result.get("result") or {}).get("songs")):
        result = netease_weapi("/cloudsearch/get/web", payload)
    if result.get("code") != 200:
        result = netease_request(
            "/api/search/get?s=%s&type=1&limit=%d&offset=0" % (urllib.parse.quote(query), limit), method="GET"
        )
    if result.get("code") != 200:
        return {"error": "Search failed", "detail": _with_hint(result)}
    songs = (result.get("result") or {}).get("songs") or []
    lines = []
    for i, s in enumerate(songs[:limit], 1):
        artists = _artists(s)
        extra = "%s | %s | " % (_album(s), _duration(s.get("dt"))) if s.get("al") else ""
        lines.append(_line(i, s, extra))
    if not lines:
        return {"results": [], "note": "No song matched %r" % query}
    return {"query": query, "results": lines}


def play_music(params):
    """Search the best match and return a play card (link, album, lyrics preview)."""
    query = _need("query", params.get("query"))
    found = search_song({"query": query, "limit": 1})
    if found.get("error") or not found.get("results"):
        return {"error": "No playable song found", "query": query, "detail": found.get("error")}
    m = re.search(r"ID:(\d+)", found["results"][0])
    if not m:
        return {"error": "Search returned no usable song id"}
    song_id = int(m.group(1))
    detail = netease_weapi("/v3/song/detail", {"c": json.dumps([{"id": song_id}])})
    song = ((detail or {}).get("songs") or [{}])[0]
    card = {
        "title": song.get("name", query),
        "artist": _artists(song),
        "album": _album(song),
        "duration": _duration(song.get("dt")),
        "id": song_id,
        "link": "https://music.163.com/#/song?id=%d" % song_id,
        "app_link": "orpheus://song/%d" % song_id,
        "play": "Open the link in the NetEase app or web player to listen",
    }
    try:
        lyrics = get_song_lyrics({"song_id": song_id, "max_lines": 6})
        if lyrics.get("lyrics"):
            card["lyrics_preview"] = lyrics["lyrics"]
    except Exception:  # noqa: BLE001 - a missing lyric must not break playback
        pass
    return {"now_playing": card}


# --------------------------------------------------------------------------- #
# Tool implementations - listening data
# --------------------------------------------------------------------------- #
def get_play_history(params):
    """Play-history rankings (weekly or all-time)."""
    all_time = _as_bool(params.get("all_time"), False)
    limit = _int_arg(params, "limit", 30, 1, MAX_HISTORY)
    missing = _require_cookie()
    if missing:
        return missing
    uid = get_uid()
    if not uid:
        return {"error": "Cannot determine user ID (MUSIC_U missing or expired?)"}
    # weapi only (a plain GET answers 400) and it has no limit parameter, so the
    # limit is applied locally.
    result = netease_weapi("/v1/play/record", {"uid": uid, "type": 0 if all_time else 1})
    if result.get("code") != 200:
        return {"error": "Failed to get play history", "detail": _with_hint(result)}
    key = "allData" if all_time else "weekData"
    records = (result.get(key) or [])[:limit]
    lines = []
    for i, rec in enumerate(records, 1):
        song = rec.get("song") or {}
        lines.append(_line(i, song, "plays: %s, " % rec.get("playCount", rec.get("score", 0))))
    return {"range": "all time" if all_time else "this week", "count": len(lines), "history": lines}


def get_recent_plays(params):
    """Actual recent play events with minute-level timestamps."""
    limit = _int_arg(params, "limit", 100, 1, MAX_HISTORY)
    missing = _require_cookie()
    if missing:
        return missing
    # `limit` must be in the (encrypted) request body; in the URL the server
    # ignores it, so the result is also trimmed locally.
    result = read_call(
        "/play-record/song/list",
        {"limit": limit},
        legacy_get="/api/play-record/song/list?limit=%d" % limit,
    )
    if result.get("code") != 200:
        return {"error": "Failed to get recent plays", "detail": result}
    records = ((result.get("data") or {}).get("list") or [])[:limit]
    lines, without_time = [], 0
    for i, rec in enumerate(records, 1):
        song = rec.get("data") or rec.get("song") or {}
        play_time = next((rec[k] for k in ("playTime", "time", "updateTime", "lastPlayTime") if rec.get(k)), None)
        if play_time is None:
            without_time += 1
        lines.append("%s [%s]" % (_line(i, song), _fmt_time(play_time)))
    out = {"count": len(lines), "recent_plays": lines}
    if records and without_time == len(records):
        # None of the known timestamp fields exist: report the real field names.
        out["debug_record_keys"] = sorted(records[0].keys())
    return out


def daily_recommend(params):
    """Today's personalized recommendations (~30 songs)."""
    missing = _require_cookie()
    if missing:
        return missing
    result = read_call("/v3/discovery/recommend/songs", {}, legacy_get="/api/v3/discovery/recommend/songs")
    if result.get("code") != 200:
        return {"error": "Failed to get recommendations", "detail": _with_hint(result)}
    data = result.get("data") or {}
    songs = data.get("dailySongs") or []
    reason = data.get("recommendReasons") or []
    reasons = {r.get("songId"): r.get("reason") for r in reason if isinstance(r, dict)}
    lines = [_line(i, s, ("why: %s, " % reasons[s.get("id")]) if reasons.get(s.get("id")) else "") for i, s in enumerate(songs, 1)]
    return {"count": len(lines), "recommendations": lines}


def get_personal_fm(params):
    """Personal FM - what the algorithm would play next."""
    missing = _require_cookie()
    if missing:
        return missing
    result = read_call("/v1/radio/get", {}, legacy_get="/api/v1/radio/get")
    if result.get("code") != 200:
        return {"error": "Failed to get personal FM", "detail": _with_hint(result)}
    lines, seen = [], set()
    for s in result.get("data") or []:
        if s.get("id") in seen:  # the endpoint can repeat a track within one batch
            continue
        seen.add(s.get("id"))
        lines.append(_line(len(lines) + 1, s, "Album: %s, " % _album(s)))
    return {"personal_fm": lines}


def get_liked_songs(params):
    """All liked (red-heart) songs with names."""
    limit = _int_arg(params, "limit", 200, 1, 1000)
    missing = _require_cookie()
    if missing:
        return missing
    uid = get_uid()
    if not uid:
        return {"error": "Cannot determine user ID (MUSIC_U missing or expired?)"}
    result = netease_request("/api/song/like/get?uid=%s" % urllib.parse.quote(uid), method="GET")
    if result.get("code") != 200:
        return {"error": "Failed to get liked songs", "detail": _with_hint(result)}
    ids = [str(i) for i in result.get("ids") or []]
    out = {"count": len(ids)}
    if not ids:
        return dict(out, liked_songs=[], note="No liked songs, or the like list is private")
    ids = ids[:limit]
    # Resolve names in batches of 50 (API limit).
    lines = []
    for start in range(0, len(ids), MAX_BATCH_IDS):
        chunk = ids[start:start + MAX_BATCH_IDS]
        detail = netease_weapi("/v3/song/detail", {"c": json.dumps([{"id": int(i)} for i in chunk])})
        by_id = {str(s.get("id")): s for s in (detail.get("songs") or [])}
        for sid in chunk:
            lines.append(_line(len(lines) + 1, by_id.get(sid, {"id": sid, "name": "(unavailable)"})))
    out["liked_songs"] = lines
    if len(result.get("ids") or []) > len(ids):
        out["note"] = "Showing first %d of %d liked songs" % (len(ids), len(result["ids"]))
    return out


def _liked_ids(uid):
    """Current red-heart ids as ints (order preserved); -1 style error string on failure."""
    result = netease_request("/api/song/like/get?uid=%s" % urllib.parse.quote(str(uid)), method="GET")
    if result.get("code") != 200:
        return None, result
    return [int(i) for i in result.get("ids") or []], result


def like_song(params):
    """Like (red heart) or unlike a song."""
    song_id = _int_arg(params, "song_id")
    like = _as_bool(params.get("like"), True)
    missing = _require_cookie()
    if missing:
        return missing
    csrf = get_csrf()
    result = netease_request(
        "/api/song/like?trackId=%s&like=%s&csrf_token=%s&time=%d"
        % (song_id, "true" if like else "false", urllib.parse.quote(csrf), int(time.time() * 1000)),
        method="GET",
    )
    if result.get("code") != 200:
        return {"error": "Failed to like/unlike song", "detail": _with_hint(result)}
    touched = result.get("playlistId")
    return {
        "status": "%s song %s" % ("Liked" if like else "Unliked", song_id),
        "target_playlist": touched,
        "note": "Took effect on the 我喜欢的音乐 playlist" if touched else None,
    }


# --------------------------------------------------------------------------- #
# Tool implementations - playlists
# --------------------------------------------------------------------------- #
def list_my_playlists(params):
    """List all playlists of the logged-in user (created and collected)."""
    missing = _require_cookie()
    if missing:
        return missing
    uid = get_uid()
    if not uid:
        return {"error": "Cannot determine user ID (MUSIC_U missing or expired?)"}
    result = netease_request("/api/user/playlist?uid=%s&limit=1000&offset=0&t=%d" % (uid, int(time.time())), method="GET")
    if result.get("code") != 200:
        return {"error": "Failed to get playlists", "detail": _with_hint(result)}
    lines = []
    for p in result.get("playlist") or []:
        owner = "mine" if str((p.get("creator") or {}).get("userId")) == str(uid) else "collected"
        desc = (p.get("description") or "").replace("\n", " ")[:40]
        lines.append(
            "ID:%s | %s | %s songs | %s%s"
            % (p.get("id"), p.get("name"), p.get("trackCount", 0), owner, (" | " + desc) if desc else "")
        )
    return {"count": len(lines), "playlists": lines}


def get_playlist_songs(params):
    """List the songs of any playlist."""
    pid = _int_arg(params, "playlist_id")
    result = netease_weapi("/v6/playlist/detail", {"id": pid, "n": 1000, "s": 0})
    tracks = ((result.get("playlist") or {}).get("tracks")) if result.get("code") == 200 else None
    if result.get("code") != 200 or not tracks:
        legacy = netease_request("/api/v6/playlist/detail?id=%d&n=1000" % pid, method="GET")
        if legacy.get("code") == 200:
            result, tracks = legacy, (legacy.get("playlist") or {}).get("tracks") or []
    if result.get("code") != 200:
        return {"error": "Failed to get playlist", "detail": _with_hint(result)}
    playlist = result.get("playlist") or {}
    tracks = tracks or []
    lines = [_line(i, t, "%s, " % _duration(t.get("dt"))) for i, t in enumerate(tracks, 1)]
    out = {
        "id": playlist.get("id", pid),
        "name": playlist.get("name"),
        "creator": (playlist.get("creator") or {}).get("nickname"),
        "description": playlist.get("description"),
        "track_count": playlist.get("trackCount", len(tracks)),
        "songs": lines,
    }
    if playlist.get("trackCount", len(tracks)) > len(tracks):
        out["note"] = "Only %d of %s tracks were returned (the rest may be unavailable)" % (
            len(tracks), playlist.get("trackCount"))
    return out


def create_playlist(params):
    """Create a real playlist on the account (with optional description)."""
    name = _need("name", params.get("name"))
    desc = params.get("description") or ""
    privacy = _int_arg(params, "privacy", 0)
    missing = _require_cookie()
    if missing:
        return missing
    # weapi first (the plain /api/ route is refused by recent gateways), legacy as fallback
    result = write_call("/playlist/create", {"name": name, "privacy": str(privacy), "type": "NORMAL"})
    if result.get("code") != 200:
        return {"error": "Failed to create playlist", "detail": result}
    pid = result.get("id") or (result.get("playlist") or {}).get("id")
    status = "created"
    if desc and pid:
        desc_result = write_call("/playlist/desc/update", {"id": str(pid), "desc": desc})
        status += " | Description: set" if desc_result.get("code") == 200 else (
            " | Description: failed (code %s)" % desc_result.get("code"))
    return {
        "playlist_id": pid,
        "name": name,
        "privacy": "private" if privacy else "public",
        "link": "https://music.163.com/#/playlist?id=%s" % pid,
        "status": status,
    }


def add_to_playlist(params):
    """Add songs to a playlist."""
    pid = _need("playlist_id", params.get("playlist_id"))
    ids = _split(params.get("song_ids"))
    if not ids:
        raise ValueError("song_ids is required (comma-separated ids)")
    missing = _require_cookie()
    if missing:
        return missing
    result = write_call(
        "/playlist/manipulate/tracks",
        {"pid": str(pid), "trackIds": json.dumps(ids), "op": "add", "imme": "true"},
    )
    if result.get("code") != 200:
        return {"error": "Failed to add songs", "detail": result}
    return {"status": "Added %d song(s) to playlist %s" % (len(ids), pid), "playlist_id": pid, "song_ids": ids}


def remove_from_playlist(params):
    """Remove songs from a playlist."""
    pid = _need("playlist_id", params.get("playlist_id"))
    ids = _split(params.get("song_ids"))
    if not ids:
        raise ValueError("song_ids is required (comma-separated ids)")
    missing = _require_cookie()
    if missing:
        return missing
    result = write_call(
        "/playlist/manipulate/tracks",
        {"pid": str(pid), "trackIds": json.dumps(ids), "op": "del", "imme": "true"},
    )
    if result.get("code") != 200:
        return {"error": "Failed to remove songs", "detail": result}
    return {"status": "Removed %d song(s) from playlist %s" % (len(ids), pid), "playlist_id": pid, "song_ids": ids}


def reorder_playlist_tracks(params):
    """Reorder all tracks in a playlist (complete id list in the desired order)."""
    pid = _need("playlist_id", params.get("playlist_id"))
    ids = _split(params.get("song_ids"))
    if not ids:
        raise ValueError("song_ids is required: the complete list of song ids in the desired order")
    if len(set(ids)) != len(ids):
        raise ValueError("song_ids contains duplicates")
    missing = _require_cookie()
    if missing:
        return missing
    current = get_playlist_songs({"playlist_id": pid})
    if current.get("error"):
        return {"error": "Failed to read the playlist before reordering", "detail": current}
    current_ids = [re.search(r"ID:(\d+)", line).group(1) for line in current["songs"] if re.search(r"ID:(\d+)", line)]
    if set(current_ids) != set(ids):
        missing_ids = [i for i in current_ids if i not in ids]
        unknown = [i for i in ids if i not in current_ids]
        return {
            "error": "song_ids must contain every track of the playlist exactly once",
            "missing_from_request": missing_ids[:20],
            "not_in_playlist": unknown[:20],
        }
    if current_ids == ids:
        return {"status": "Playlist is already in the requested order.", "playlist_id": pid}
    result = write_call(
        "/playlist/manipulate/tracks",
        {"pid": str(pid), "trackIds": json.dumps(ids), "op": "update", "imme": "true"},
    )
    if result.get("code") != 200:
        return {"error": "Failed to reorder", "detail": result}
    return {"status": "Reordered %d tracks in playlist %s" % (len(ids), pid), "order": ids}


def update_playlist_description(params):
    """Update a playlist's description."""
    pid = _need("playlist_id", params.get("playlist_id"))
    desc = params.get("description", "")
    if desc is None:
        desc = ""
    missing = _require_cookie()
    if missing:
        return missing
    result = write_call("/playlist/desc/update", {"id": str(pid), "desc": desc})
    if result.get("code") != 200:
        return {"error": "Failed to update description", "detail": result}
    return {"status": "Updated description for playlist %s" % pid, "playlist_id": pid, "description": desc}


# --------------------------------------------------------------------------- #
# Tool implementations - song metadata
# --------------------------------------------------------------------------- #
def _clean_lyric(raw, max_lines):
    """Strip LRC timestamps / metadata tags and condense instrumental placeholders."""
    if not raw:
        return ""
    plain = re.sub(r"\[[0-9:.]+\]", "", raw)          # [00:12.34]
    plain = re.sub(r"\[[a-zA-Z]+:[^\]]*\]", "", plain)  # [ar:], [ti:], [by:]
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    return "\n".join(lines[:max_lines])


def get_song_lyrics(params):
    """Original lyrics + translation, timestamps stripped for readability."""
    song_id = _int_arg(params, "song_id")
    max_lines = _int_arg(params, "max_lines", MAX_LYRIC_LINES, 1, 500)
    result = netease_weapi("/song/lyric", {"id": song_id, "lv": -1, "kv": -1, "tv": -1})
    if result.get("code") != 200:
        result = netease_request("/api/song/lyric?id=%d&lv=-1&kv=-1&tv=-1" % song_id, method="GET")
    if result.get("code") != 200:
        return {"error": "Failed to get lyrics", "detail": _with_hint(result)}
    lrc = (result.get("lrc") or {}).get("lyric") or ""
    tlyric = (result.get("tlyric") or {}).get("lyric") or ""
    out = {"song_id": song_id, "lyrics": _clean_lyric(lrc, max_lines)}
    if tlyric:
        out["translation"] = _clean_lyric(tlyric, max_lines)
    if not out["lyrics"]:
        out["note"] = "No lyrics available (instrumental or restricted track)"
    return out


def get_song_details(params):
    """Album, duration and release year for up to 50 songs."""
    ids = _split(params.get("song_ids") or params.get("song_id"))
    if not ids:
        raise ValueError("song_ids is required (comma-separated)")
    nums = []
    for raw in ids:
        try:
            nums.append(int(raw))
        except ValueError:
            raise ValueError("song_ids must be integers, got %r" % raw)
    chunks = [nums[i:i + MAX_BATCH_IDS] for i in range(0, len(nums), MAX_BATCH_IDS)]
    songs = []
    for chunk in chunks:
        result = netease_weapi("/v3/song/detail", {"c": json.dumps([{"id": i} for i in chunk])})
        if result.get("code") != 200:
            return {"error": "Failed to get song details", "detail": _with_hint(result)}
        songs.extend(result.get("songs") or [])
    out = []
    for s in songs:
        publish = s.get("publishTime") or (s.get("al") or {}).get("publishTime") or 0
        year = time.strftime("%Y", time.gmtime(publish / 1000)) if publish else "?"
        out.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "artists": _artists(s),
                "album": _album(s),
                "duration": _duration(s.get("dt")),
                "year": year,
            }
        )
    return {"count": len(out), "songs": out}


def get_artist_hot_songs(params):
    """An artist's top ~20 hot songs. artist_id may also be a song id (auto-resolved)."""
    artist_id = _need("artist_id", params.get("artist_id"))
    try:
        artist_id_int = int(artist_id)
    except ValueError:
        raise ValueError("artist_id must be an integer")
    result = netease_weapi("/artist/top/song", {"id": artist_id_int})
    songs = result.get("songs") or []
    if result.get("code") != 200 or not songs:
        fallback = netease_request("/api/artist/top/song?id=%d&limit=20" % artist_id_int, method="GET")
        if fallback.get("code") == 200 and fallback.get("songs"):
            result, songs = fallback, fallback["songs"]
        else:
            legacy = netease_request("/api/v1/artist/%d" % artist_id_int, method="GET")
            if legacy.get("code") == 200 and legacy.get("hotSongs"):
                result, songs = legacy, legacy["hotSongs"]
            else:
                # _with_hint takes a single result; keep each attempt visible instead.
                return {"error": "Failed to get artist songs",
                        "detail": {"weapi": _with_hint(result),
                                   "api": _with_hint(fallback),
                                   "legacy": _with_hint(legacy)}}
    songs = songs[:20]
    name = (songs[0].get("ar") or songs[0].get("artists") or [{}])[0].get("name", "?")
    return {"artist": name, "artist_id": artist_id_int, "hot_songs": [_line(i, s) for i, s in enumerate(songs, 1)]}


# --------------------------------------------------------------------------- #
# Tool registry
# --------------------------------------------------------------------------- #
def _int(desc, lo=None, hi=None):
    schema = {"type": "integer", "description": desc}
    if lo is not None:
        schema["minimum"] = lo
    if hi is not None:
        schema["maximum"] = hi
    return schema


def _str(desc):
    return {"type": "string", "description": desc}


_DESTRUCTIVE = {"remove_from_playlist", "like_song"}  # may drop data (hearts are recoverable)
_IDEMPOTENT = {"update_playlist_description", "reorder_playlist_tracks", "like_song"}


def _tool(name, title, description, properties=None, required=None, hints=(), read_only=False):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required or []),
            "additionalProperties": False,
        },
        "annotations": {
            "title": title,
            "readOnlyHint": bool(read_only),
            "destructiveHint": name in _DESTRUCTIVE,
            "idempotentHint": name in _IDEMPOTENT,
            "openWorldHint": True,
        },
    }


TOOLS = [
    _tool(
        "search_song",
        "搜歌",
        "Search songs by keyword. Returns matching songs with ID, artist, album and duration.",
        {"query": _str("Search keyword (song, artist, lyric line...)"), "limit": _int("Max results (1-10, default 5)", 1, 10)},
        ["query"],
        read_only=True,
    ),
    _tool(
        "play_music",
        "播放",
        "Search the best match and return a play card: title, artist, album, duration, play link and a short lyric preview.",
        {"query": _str("Song name or artist")},
        ["query"],
        read_only=True,
    ),
    _tool(
        "get_play_history",
        "听歌排行",
        "Play-history rankings of the logged-in user: most played songs this week or of all time.",
        {"all_time": {"type": "boolean", "description": "true = all time, false = this week (default)"}, "limit": _int("Number of records (default 30)", 1, MAX_HISTORY)},
        read_only=True,
    ),
    _tool(
        "get_recent_plays",
        "播放事件",
        "Actual recent play events, minute-precise, with timestamps (what was looping at 2am).",
        {"limit": _int("Number of events (1-300, default 100)", 1, MAX_HISTORY)},
        read_only=True,
    ),
    _tool("daily_recommend", "每日推荐", "Today's personalized daily recommendations, with the algorithm's reason when given.", read_only=True),
    _tool("get_personal_fm", "私人FM", "Personal FM - the tracks the algorithm would play next.", read_only=True),
    _tool(
        "list_my_playlists",
        "看歌单",
        "List all playlists of the logged-in user (created and collected) with id, track count and owner.",
        read_only=True,
    ),
    _tool(
        "get_playlist_songs",
        "看歌曲",
        "List the songs inside any playlist (id, name, artist, duration).",
        {"playlist_id": _int("Playlist id")},
        ["playlist_id"],
        read_only=True,
    ),
    _tool(
        "create_playlist",
        "建歌单",
        "Create a real playlist on the account, optionally with a description.",
        {
            "name": _str("Playlist name"),
            "description": _str("Playlist description (optional)"),
            "privacy": _int("0 = public (default), 10 = private"),
        },
        ["name"],
    ),
    _tool(
        "add_to_playlist",
        "塞歌",
        "Add one or more songs to a playlist.",
        {"playlist_id": _int("Playlist id"), "song_ids": _str("Comma-separated song ids")},
        ["playlist_id", "song_ids"],
    ),
    _tool(
        "remove_from_playlist",
        "删歌",
        "Remove one or more songs from a playlist.",
        {"playlist_id": _int("Playlist id"), "song_ids": _str("Comma-separated song ids")},
        ["playlist_id", "song_ids"],
    ),
    _tool(
        "reorder_playlist_tracks",
        "排序",
        "Reorder a playlist. Provide the COMPLETE list of song ids in the desired order.",
        {"playlist_id": _int("Playlist id"), "song_ids": _str("All song ids in the desired order, comma-separated")},
        ["playlist_id", "song_ids"],
    ),
    _tool(
        "update_playlist_description",
        "改描述",
        "Update a playlist's description.",
        {"playlist_id": _int("Playlist id"), "description": _str("New description")},
        ["playlist_id", "description"],
    ),
    _tool(
        "like_song",
        "收藏",
        "Like (red heart) or unlike a song.",
        {"song_id": _int("Song id"), "like": {"type": "boolean", "description": "true = like (default), false = unlike"}},
        ["song_id"],
    ),
    _tool("get_liked_songs", "红心列表", "All liked (red-heart) songs with names and ids.", {"limit": _int("Max songs to resolve (default 200)", 1, 1000)}, read_only=True),
    _tool(
        "get_song_lyrics",
        "歌词",
        "Lyrics of a song (timestamps stripped) plus translation when available.",
        {"song_id": _int("Song id"), "max_lines": _int("Max lyric lines (default 120)", 1, 500)},
        ["song_id"],
        read_only=True,
    ),
    _tool(
        "get_song_details",
        "歌曲详情",
        "Album, duration and release year for up to 50 songs per call.",
        {"song_ids": _str("Comma-separated song ids (max 50 per call)")},
        ["song_ids"],
        read_only=True,
    ),
    _tool(
        "get_artist_hot_songs",
        "歌手热歌",
        "An artist's top 20 hot songs.",
        {"artist_id": _int("Artist id")},
        ["artist_id"],
        read_only=True,
    ),
]

TOOL_DISPATCH = {
    "search_song": search_song,
    "play_music": play_music,
    "get_play_history": get_play_history,
    "get_recent_plays": get_recent_plays,
    "daily_recommend": daily_recommend,
    "list_my_playlists": list_my_playlists,
    "get_playlist_songs": get_playlist_songs,
    "create_playlist": create_playlist,
    "add_to_playlist": add_to_playlist,
    "remove_from_playlist": remove_from_playlist,
    "like_song": like_song,
    "update_playlist_description": update_playlist_description,
    "reorder_playlist_tracks": reorder_playlist_tracks,
    "get_song_lyrics": get_song_lyrics,
    "get_song_details": get_song_details,
    "get_artist_hot_songs": get_artist_hot_songs,
    "get_personal_fm": get_personal_fm,
    "get_liked_songs": get_liked_songs,
}
assert len(TOOLS) == len(TOOL_DISPATCH) == 18, "tool registry out of sync"


# --------------------------------------------------------------------------- #
# AI analysis
# --------------------------------------------------------------------------- #
def ai_analyze(stats):
    """Ask the configured model to write a profile from the caller's statistics.

    Returns a JSON-serialisable dict. Never raises: any transport or provider
    failure is reported as a plain error object so the browser can fall back to
    its local wording instead of showing a broken panel.
    """
    if not AI_ENABLED:
        return {
            "ok": False,
            "disabled": True,
            "hint": "在 .env 里设置 DEEPSEEK_API_KEY 并重启服务即可启用 AI 解读",
        }
    if not isinstance(stats, dict) or not stats:
        return {"ok": False, "error": "stats must be a non-empty object"}

    body = json.dumps({
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content":
                "以下是统计好的听歌数据（JSON）：\n" + json.dumps(stats, ensure_ascii=False)
                + "\n\n请按要求写出侧写。"},
        ],
        "temperature": 0.85,
        "max_tokens": AI_MAX_TOKENS,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        AI_BASE_URL + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + AI_API_KEY,   # never logged
            "Accept": "application/json",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Surface the provider's own message; it never echoes the key.
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        logger.error("AI request failed: HTTP %s", exc.code)
        return {"ok": False, "error": "模型接口返回 HTTP %s" % exc.code, "detail": detail}
    except Exception as exc:  # timeout, DNS, offline
        logger.error("AI request error: %s", type(exc).__name__)
        return {"ok": False, "error": "调用模型失败：%s" % type(exc).__name__}

    try:
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError):
        text = ""
    if not text.strip():
        return {"ok": False, "error": "模型没有返回内容", "detail": str(data)[:300]}

    usage = data.get("usage") or {}
    return {
        "ok": True,
        "model": data.get("model", AI_MODEL),
        "analysis": text.strip(),
        "elapsed_ms": int((time.time() - started) * 1000),
        "tokens": {"prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens")},
    }


# --------------------------------------------------------------------------- #
# MCP protocol
# --------------------------------------------------------------------------- #
class MCPError(Exception):
    """JSON-RPC level error."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self):
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


def _text_result(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def call_tool(name, arguments):
    """Run a tool and wrap its output in an MCP tools/call result."""
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        raise MCPError(-32602, "Unknown tool: %s" % name)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise MCPError(-32602, "arguments must be an object")
    logger.info("Tool call: %s", name)
    started = time.time()
    try:
        result = handler(arguments)
    except ValueError as exc:  # bad arguments from the client
        return _text_result({"error": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001 - never let a tool kill the server
        logger.exception("Tool error [%s]: %s", name, type(exc).__name__)
        return _text_result({"error": "Internal error: %s" % type(exc).__name__}, is_error=True)
    finally:
        logger.debug("Tool %s finished in %.2fs", name, time.time() - started)
    is_error = isinstance(result, dict) and "error" in result and result.get("code") != 200
    return _text_result(result, is_error=is_error)


def handle_request(message):
    """Handle one JSON-RPC message.

    Returns ``(response_or_None, http_status, extra_headers)``; ``None`` means
    "no body" (notification / response).
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
            return None, 202, {}  # a response to our own request: ignore
        return None, None
    method = message["method"]
    msg_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params") or {}

    if is_notification:
        logger.debug("Notification: %s", method)
        return None, 202, {}

    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}, "logging": {}},
                "serverInfo": {"name": SERVER_NAME, "title": "NetEase Cloud Music MCP", "version": VERSION},
                "instructions": (
                    "Operates the user's real NetEase Cloud Music account. Search first to get "
                    "song IDs, then create/curate playlists, read lyrics and listening history."
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = call_tool(params.get("name", ""), params.get("arguments"))
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method.startswith("resources") else {"prompts": []}
        elif method in ("logging/setLevel",):
            result = {}
        elif method == "completion/complete":
            result = {"completion": {"values": [], "total": 0, "hasMore": False}}
        else:
            raise MCPError(-32601, "Method not found: %s" % method)
    except MCPError as exc:
        return {"jsonrpc": "2.0", "id": msg_id, "error": exc.to_dict()}, 200, {}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}, 200, {}


def handle_payload(payload):
    """Handle a parsed JSON-RPC payload (single object or batch)."""
    if isinstance(payload, list):
        if not payload:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Empty batch"}}, 400, {}
        responses = []
        status = 202
        for item in payload:
            resp, item_status, _ = handle_request(item)
            if resp is not None:
                responses.append(resp)
                status = max(status, item_status or 200)
            elif item_status is not None:
                status = 202 if item_status == 202 and not responses else status
        if not responses:
            return None, 202, {}
        return responses, 200, {}
    if not isinstance(payload, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}, 400, {}
    return handle_request(payload)


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class _Session:
    """State for one client, tied to the Mcp-Session-Id header."""

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.created = time.time()
        self.lock = threading.Lock()  # serializes request handling for this session
        self.queue = None  # set while an event stream (SSE) is attached
        self.stream_lock = threading.Lock()  # guards queue attach/detach
        self.protocol_version = None


_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()
_SESSION_TTL = 24 * 3600


def _new_session():
    session = _Session()
    with _SESSIONS_LOCK:
        # Opportunistic cleanup of abandoned sessions.
        for sid in [k for k, v in _SESSIONS.items() if time.time() - v.created > _SESSION_TTL]:
            _SESSIONS.pop(sid, None)
        _SESSIONS[session.id] = session
    return session


def _get_session(sid):
    with _SESSIONS_LOCK:
        return _SESSIONS.get(sid)


def _end_session(session):
    """Drop a session and close its attached event stream, if any."""
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session.id, None)
    with session.stream_lock:
        channel = session.queue
    if channel is not None:
        try:
            channel.put_nowait(("close", ""))
        except Exception:  # noqa: BLE001 - a dead stream is fine
            pass


def _origin_allowed(origin):
    if not origin:
        return True
    if MCP_ALLOW_ORIGINS:
        allowed = [o.strip() for o in MCP_ALLOW_ORIGINS.split(",") if o.strip()]
        return "*" in allowed or origin in allowed
    return origin.startswith(LOCALHOST_ORIGINS)


class MCPHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "netease-music-mcp/" + VERSION
    sys_version = ""

    # -- helpers ----------------------------------------------------------- #
    def log_message(self, fmt, *args):  # quiet default request log
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and _origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version")
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
            self.send_header("Access-Control-Max-Age", "86400")

    def _send(self, status, body=b"", content_type="application/json", extra_headers=None):
        self.send_response(status)
        self._cors_headers()
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, data, extra_headers=None):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json", extra_headers)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > 8 * 1024 * 1024:
            raise ValueError("request body too large")
        return self.rfile.read(length)

    def _split_path(self):
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path.rstrip("/") or "/", urllib.parse.parse_qs(parsed.query)

    # -- HTTP verbs -------------------------------------------------------- #

    # -- bundled web console ----------------------------------------------- #
    def _serve_frontend(self):
        """Serve frontend/index.html (single-file web console) at / and /ui."""
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(os.path.dirname(here)), "frontend", "index.html")
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": SERVER_NAME,
                    "version": VERSION,
                    "tools": len(TOOLS),
                    "frontend": "missing: expected frontend/index.html next to server/",
                    "endpoints": ["GET / (web console)", "POST /mcp", "GET /health"],
                },
            )
            return
        self._send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if origin and not _origin_allowed(origin):
            self._send(403)
            return
        self._send(204)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path, _ = self._split_path()
        if path in ("/", "/index.html", "/ui"):
            # The bundled web console; talks to POST /mcp from the browser.
            self._serve_frontend()
            return
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": SERVER_NAME,
                    "version": VERSION,
                    "tools": len(TOOLS),
                    "endpoints": ["POST /mcp", "GET /sse (legacy)", "POST /message (legacy)",
                                  "POST /analyze (AI profile)"],
                    "logged_in": is_configured(),
                    "ai_analysis": AI_ENABLED,
                },
            )
            return
        if path == "/mcp":
            self._streamable_http_get()
            return
        if path == "/sse":
            self._legacy_sse_connect()
            return
        self._send_json(404, {"error": "Not found", "endpoints": ["/mcp", "/health", "/sse"]})

    def do_DELETE(self):
        path, _ = self._split_path()
        sid = self.headers.get("Mcp-Session-Id")
        if path != "/mcp":
            # Nothing has read this connection's body: answer, then hang up.
            self.close_connection = True
            self._send_json(404, {"error": "Not found"})
            return
        session = _get_session(sid) if sid else None
        if sid and session is None:
            self._send_json(404, {"error": "Unknown session"})
            return
        if session is not None:
            _end_session(session)
        # urllib's HTTP client ignores the body of a 204, so send the reason in a header.
        self._send(204, extra_headers={"Mcp-Session-Status": "terminated" if session else "none"})

    def do_POST(self):
        path, query = self._split_path()
        # Always drain the body before answering: an unread request body would be
        # mistaken for the next request on a keep-alive connection, and closing the
        # socket with unread data aborts the response on Windows.
        try:
            raw = self._read_body()
        except ValueError:
            self.close_connection = True
            self._send_json(413, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request body too large"}})
            return
        origin = self.headers.get("Origin")
        if origin and not _origin_allowed(origin):
            self._send_json(403, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32001, "message": "Origin not allowed"}})
            return
        if path not in ("/mcp", "/message", "/analyze"):
            self._send_json(404, {"error": "Not found", "endpoints": ["/mcp", "/health", "/analyze"]})
            return
        if path == "/analyze":
            self._handle_analyze(raw)
            return
        try:
            payload = json.loads(raw.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        if path == "/mcp":
            self._streamable_http_post(payload)
        else:
            self._legacy_message_post(payload, query)

    # -- AI analysis ------------------------------------------------------- #
    def _handle_analyze(self, raw):
        """POST /analyze {stats:{...}} -> {ok, analysis} using the configured model."""
        try:
            payload = json.loads(raw.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "请求体不是合法 JSON"})
            return
        stats = (payload or {}).get("stats") if isinstance(payload, dict) else None
        if stats is None:
            self._send_json(400, {"ok": False, "error": "缺少 stats 字段"})
            return
        self._send_json(200, ai_analyze(stats))

    # -- Streamable HTTP --------------------------------------------------- #
    def _streamable_http_post(self, payload):
        session = None
        sid = self.headers.get("Mcp-Session-Id")
        if sid:
            session = _get_session(sid)
            if session is None:
                self._send_json(404, {"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32001, "message": "Session not found; re-initialize"}})
                return
        elif self._payload_is_initialize_only(payload):
            session = _new_session()

        with (session.lock if session is not None else _NullLock()):
            response, status, extra = handle_payload(payload)

        headers = dict(extra or {})
        if session is not None:
            headers["Mcp-Session-Id"] = session.id
        if response is None:
            self._send(status or 202, b"", extra_headers=headers)
            return
        if status == 400:
            self._send_json(400, response, headers)
            return
        self._send_json(200, response, headers)

    def _payload_is_initialize_only(self, payload):
        messages = payload if isinstance(payload, list) else [payload]
        methods = [m.get("method") for m in messages if isinstance(m, dict)]
        return bool(methods) and all(m == "initialize" or (m or "").startswith("notifications/") for m in methods)

    def _streamable_http_get(self):
        """Optional server->client stream. Without an SSE-capable client that
        asked for one, answer 405 as permitted by the spec."""
        accept = (self.headers.get("Accept") or "").lower()
        if "text/event-stream" not in accept:
            self._send_json(405, {"error": "GET requires Accept: text/event-stream; POST JSON-RPC to /mcp"},
                            {"Allow": "POST, DELETE"})
            return
        sid = self.headers.get("Mcp-Session-Id")
        self._open_event_stream(session=_get_session(sid) if sid else None, notify_endpoint=False)

    def _open_event_stream(self, session=None, notify_endpoint=True):
        self.close_connection = True
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        if session is not None:
            self.send_header("Mcp-Session-Id", session.id)
        self.end_headers()
        channel = queue.Queue()
        # Attach under stream_lock only: the stream itself must NOT hold the request
        # lock, otherwise every further POST on this session would block forever.
        if session is not None:
            with session.stream_lock:
                session.queue = channel
        try:
            if notify_endpoint:
                self.wfile.write(b"retry: 5000\n\n")
                host = self.headers.get("Host") or ("localhost:%d" % MCP_PORT)
                self._write_sse("endpoint", "http://%s/message?sessionId=%s" % (host, session.id))
            else:
                # "retry" makes compliant clients treat this as the reconnect delay;
                # a first "message" event guarantees the headers reach the client
                # immediately instead of after the first keepalive.
                self.wfile.write(("retry: 5000\n\n").encode("utf-8"))
                self._write_sse("message", json.dumps({"session": session.id if session else None}))
            while True:
                try:
                    event, data = channel.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self._write_sse(event, data)
                if event == "close":
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            if session is not None:
                with session.stream_lock:
                    if session.queue is channel:
                        session.queue = None

    def _write_sse(self, event, data):
        self.wfile.write(("event: %s\ndata: %s\n\n" % (event, data)).encode("utf-8"))
        self.wfile.flush()

    # -- legacy HTTP+SSE transport (2024-11-05) ----------------------------- #
    def _legacy_sse_connect(self):
        session = _new_session()
        self._open_event_stream(session=session, notify_endpoint=True)

    def _legacy_message_post(self, payload, query):
        session = _get_session((query.get("sessionId") or [""])[0])
        if session is None:
            self._send_json(404, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32001, "message": "Unknown or expired sessionId"}})
            return
        response, status, _ = handle_payload(payload)
        if response is not None:
            with session.stream_lock:
                channel = session.queue
                if channel is not None:
                    try:
                        channel.put_nowait(("message", json.dumps(response, ensure_ascii=False)))
                    except queue.Full:
                        pass
            # The reply travels on the SSE stream; this POST only acknowledges it.
        self._send(202 if response is not None else (status or 202), b"",
                   extra_headers={"Mcp-Session-Id": session.id})


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """One thread per connection: an open SSE stream must never block other calls."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Clients hanging up mid-response (SSE, curl ^C) are normal, not server errors.
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            logger.debug("Client %s disconnected: %s", client_address[0], type(exc).__name__)
            return
        logger.exception("Unhandled error while serving %s", client_address[0])


# --------------------------------------------------------------------------- #
# stdio transport
# --------------------------------------------------------------------------- #
def serve_stdio():
    """JSON-RPC over stdin/stdout, one message per line (for local MCP clients)."""
    logger.info("stdio transport: %d tools", len(TOOLS))
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response, _, _ = handle_payload(payload)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
def _startup_checks():
    """Log which credentials were found (key names only, never values)."""
    jar = _parse_cookie(NETEASE_COOKIE)
    logger.info("Cookie keys provided: %s", ", ".join(sorted(jar)) or "(none)")
    if "MUSIC_U" not in jar:
        logger.warning("No MUSIC_U in NETEASE_COOKIE: account calls will fail until you log in")
    if not get_csrf():
        logger.warning("No __csrf (in NETEASE_COOKIE or NETEASE_CSRF): write operations will fail")
    if NETEASE_TZ_OFFSET == 8:
        logger.info("Timestamp timezone: UTC+8 (set NETEASE_TZ_OFFSET to change)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    transport = "http"
    if "--transport" in argv:
        idx = argv.index("--transport")
        transport = argv[idx + 1] if idx + 1 < len(argv) else "http"
    elif os.environ.get("MCP_TRANSPORT"):
        transport = os.environ["MCP_TRANSPORT"]

    if transport == "stdio":
        _startup_checks()
        try:
            serve_stdio()
        except KeyboardInterrupt:
            pass
        return 0

    logger.info("Starting NetEase Music MCP Server v%s with %d tools on %s:%s", VERSION, len(TOOLS), MCP_HOST, MCP_PORT)
    _startup_checks()
    server = ThreadedHTTPServer((MCP_HOST, MCP_PORT), MCPHandler)
    logger.info("AI analysis: %s",
                ("enabled, model=%s" % AI_MODEL) if AI_ENABLED
                else "disabled (set DEEPSEEK_API_KEY to enable)")
    logger.info("MCP endpoint: http://%s:%s/mcp  (health: /health)", "localhost" if MCP_HOST in ("0.0.0.0", "") else MCP_HOST, MCP_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
